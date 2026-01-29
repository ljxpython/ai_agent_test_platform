#!/usr/bin/env python3

"""Zhipu outbound streaming smoke test.

Contract:
- If ZHIPUAI_API_KEY (or fallback ZHIPU_API_KEY) is missing OR ZHIPU_MODEL missing:
  print "SKIP: ..." and exit 0.
- Otherwise: stream a trivial prompt and exit 0 only if at least 1 non-empty chunk is received.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterable
from typing import Any

import httpx
from langchain_community.chat_models import ChatZhipuAI
from langchain_core.messages import HumanMessage

try:
    # Raised when the response isn't an SSE stream (e.g. JSON error body).
    from httpx_sse import SSEError
except Exception:  # pragma: no cover
    SSEError = None  # type: ignore[assignment,misc]


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _skip(reason: str) -> int:
    print(f"SKIP: {reason}")
    return 0


def _extract_text(chunk: Any) -> str:
    """Best-effort text extraction across LangChain chunk types."""

    if chunk is None:
        return ""

    if isinstance(chunk, str):
        return chunk

    # Common: AIMessageChunk(content="...")
    content = getattr(chunk, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)

    # Some variants wrap a message: ChatGenerationChunk(message=AIMessageChunk(...))
    msg = getattr(chunk, "message", None)
    msg_content = getattr(msg, "content", None)
    if isinstance(msg_content, str):
        return msg_content

    return ""


def _redact(text: str, secrets: Iterable[str]) -> str:
    redacted = text
    for s in secrets:
        if s and s in redacted:
            redacted = redacted.replace(s, "<redacted>")
    return redacted


def _probe_httpx_chat_completions(*, api_base: str, api_key: str, model: str, prompt: str) -> None:
    """Best-effort probe to surface status/content-type/body on SSE negotiation errors."""

    try:
        # Reuse LangChain's token generation to match the real request.
        from langchain_community.chat_models.zhipuai import _get_jwt_token

        token = _get_jwt_token(api_key)
    except Exception as e:
        # Do not print the API key even if the upstream error includes it.
        print(f"PROBE: token generation failed: {type(e).__name__}", file=sys.stderr)
        return

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream, application/json",
    }

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
    }

    try:
        timeout = httpx.Timeout(15.0, connect=10.0, read=5.0)
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            with client.stream("POST", api_base, headers=headers, json=payload) as resp:
                content_type = resp.headers.get("content-type", "")
                body_bytes = b""
                # Read a small prefix, then close the stream.
                for part in resp.iter_bytes():
                    if not part:
                        continue
                    body_bytes += part
                    if len(body_bytes) >= 512:
                        break

        body_text = body_bytes.decode("utf-8", errors="replace")
        body_text = body_text.replace("\r", " ").replace("\n", " ").strip()
        body_prefix = body_text[:120]

        print(
            f"PROBE: status={resp.status_code} content-type={content_type!r} body_prefix={body_prefix!r}",
            file=sys.stderr,
        )
    except Exception as e:
        print(f"PROBE: request failed: {type(e).__name__}", file=sys.stderr)


def _derive_api_base_from_legacy(legacy_base: str) -> str:
    base = (legacy_base or "").strip()
    if not base:
        return ""

    # If caller already provided the full endpoint, keep it.
    if "/chat/completions" in base:
        return base

    base = base.rstrip("/")

    # Legacy env may already include the API root.
    if "/api/paas/v4" in base:
        derived = base + "/chat/completions"
    else:
        derived = base + "/api/paas/v4/chat/completions"

    # Defensive: avoid accidental duplicate joins.
    while "/api/paas/v4/api/paas/v4" in derived:
        derived = derived.replace("/api/paas/v4/api/paas/v4", "/api/paas/v4")

    return derived


def main() -> int:
    api_key = _env("ZHIPUAI_API_KEY")
    if not api_key:
        fallback = _env("ZHIPU_API_KEY")
        if fallback:
            # LangChain's Zhipu integration reads ZHIPUAI_API_KEY.
            os.environ["ZHIPUAI_API_KEY"] = fallback
            api_key = fallback

    model = _env("ZHIPU_MODEL")
    api_base = _env("ZHIPUAI_API_BASE")
    api_base_source = ""
    if api_base:
        api_base_source = "ZHIPUAI_API_BASE"
    else:
        # Legacy env var used by this repo's .env.
        legacy_base = _env("ZHIPU_BASE_URL")
        if legacy_base:
            api_base_source = "ZHIPU_BASE_URL"
            api_base = _derive_api_base_from_legacy(legacy_base)

    if api_base:
        os.environ["ZHIPUAI_API_BASE"] = api_base

    if not api_key:
        return _skip("missing ZHIPUAI_API_KEY (or ZHIPU_API_KEY)")
    if not model:
        return _skip("missing ZHIPU_MODEL")

    # Keep stdout for streamed output; put metadata on stderr.
    base_note = f" api_base={api_base} ({api_base_source})" if api_base else ""
    print(f"INFO: zhipu streaming smoke model={model}{base_note}", file=sys.stderr)

    llm = ChatZhipuAI(model=model, streaming=True)
    prompt = "Reply with exactly: pong"

    received_non_empty = False
    printed = 0
    max_print = 512

    try:
        for chunk in llm.stream([HumanMessage(content=prompt)]):
            delta = _extract_text(chunk)
            if not delta:
                continue

            received_non_empty = True
            if printed < max_print:
                to_print = delta[: max_print - printed]
                sys.stdout.write(to_print)
                sys.stdout.flush()
                printed += len(to_print)

        if received_non_empty:
            if printed >= max_print:
                sys.stdout.write("\n... (truncated)\n")
            else:
                sys.stdout.write("\n")
            return 0

        print("ERROR: streaming yielded no non-empty chunks", file=sys.stderr)
        return 1
    except Exception as e:
        # If the server responds with a non-SSE payload (JSON errors, empty content-type),
        # httpx-sse raises SSEError without exposing the response. Do a safe probe.
        if SSEError is not None and isinstance(e, SSEError):
            msg = _redact(str(e), [api_key])
            print(f"ERROR: SSEError: {msg}", file=sys.stderr)
            _probe_httpx_chat_completions(
                api_base=api_base,
                api_key=api_key,
                model=model,
                prompt=prompt,
            )
            return 1

        # Do not print secrets.
        msg = _redact(str(e), [api_key])
        print(f"ERROR: {type(e).__name__}: {msg}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
