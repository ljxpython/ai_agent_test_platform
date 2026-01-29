#!/usr/bin/env python3

"""DeepSeek outbound streaming smoke test.

This script uses the OpenAI-compatible chat completions API.

Env vars (preferred):
- DEEPSEEK_API_KEY
- DEEPSEEK_BASE_URL (default: https://api.deepseek.com)
- DEEPSEEK_MODEL (default: deepseek-chat)

Compat env vars (common in OpenAI-compatible setups):
- OPENAI_API_KEY
- OPENAI_BASE_URL
- OPENAI_MODEL

If required vars are missing, the script prints SKIP and exits 0.
"""

from __future__ import annotations

import os
import sys


def _env(name: str) -> str | None:
    v = os.getenv(name)
    if not v:
        return None
    v = v.strip()
    return v or None


def _env_any(*names: str) -> str | None:
    for n in names:
        v = _env(n)
        if v:
            return v
    return None


def _skip(reason: str) -> int:
    print(f"SKIP: {reason}", file=sys.stderr)
    return 0


def main() -> int:
    api_key = _env_any(
        "DEEPSEEK_API_KEY",
        "deepseek_api_key",
        "OPENAI_API_KEY",
        "openai_api_key",
    )
    base_url = _env_any(
        "DEEPSEEK_BASE_URL",
        "deepseek_base_url",
        "OPENAI_BASE_URL",
        "openai_base_url",
    ) or "https://api.deepseek.com"
    model = _env_any(
        "DEEPSEEK_MODEL",
        "deepseek_model",
        "OPENAI_MODEL",
        "openai_model",
    ) or "deepseek-chat"

    if not api_key:
        return _skip("missing DEEPSEEK_API_KEY (or OPENAI_API_KEY)")

    # Keep stdout for streamed output; put metadata on stderr.
    print(
        f"INFO: deepseek streaming smoke model={model} base_url={base_url}",
        file=sys.stderr,
    )

    try:
        from openai import OpenAI
    except Exception as e:
        print(
            f"ERROR: OpenAI client import failed: {type(e).__name__}: {e}",
            file=sys.stderr,
        )
        return 1

    client = OpenAI(api_key=api_key, base_url=base_url)

    received_non_empty = False
    printed = 0
    max_print = 512
    prompt = "Reply with exactly: pong"

    try:
        stream = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            stream=True,
            temperature=0,
        )
        for evt in stream:
            # OpenAI-compatible streaming: delta.content
            try:
                delta = evt.choices[0].delta.content  # type: ignore[attr-defined]
            except Exception:
                delta = None
            if not delta:
                continue
            received_non_empty = True

            if printed < max_print:
                to_print = delta[: max_print - printed]
                sys.stdout.write(to_print)
                sys.stdout.flush()
                printed += len(to_print)

        if received_non_empty:
            sys.stdout.write("\n" if printed < max_print else "\n... (truncated)\n")
            return 0

        print("ERROR: streaming yielded no non-empty chunks", file=sys.stderr)
        return 1
    except Exception as e:
        # Do not print secrets; keep error minimal.
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
