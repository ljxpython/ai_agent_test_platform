"""LangGraph Server SSE -> canonical events (MVP).

This module is a small, dependency-free adapter that parses LangGraph Server
SSE (Server-Sent Events) lines and emits the platform's canonical event
envelope.

Tiny usage example (not runnable as-is):

```python
import httpx

from omo_platform.langgraph_proxy.stream_adapter import (
    iter_langgraph_events_to_canonical,
)


async def consume(resp: httpx.Response) -> None:
    async for evt in iter_langgraph_events_to_canonical(resp.aiter_lines()):
        # evt is a CanonicalEvent dict
        print(evt["seq"], evt["type"], evt["payload"])
```
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from omo_platform.events.canonical import (
    CanonicalEvent,
    EVENT_MESSAGE_TEXT,
    EVENT_RUN_ERROR,
    EVENT_RUN_FINISHED,
    EVENT_RUN_STARTED,
    EVENT_UI_CARD,
    new_event,
)


async def parse_sse_events(lines: AsyncIterator[str]) -> AsyncIterator[tuple[str | None, str]]:
    """Parse SSE lines into (event_name, data) tuples.

    Supports only `event:` and `data:` fields. Data may span multiple lines.
    Unknown/unsupported SSE fields are ignored.
    """

    event_name: str | None = None
    data_lines: list[str] = []

    async for raw in lines:
        # httpx yields lines without the trailing "\n", but may include "\r".
        line = raw.rstrip("\r")

        if line == "":
            if event_name is not None or data_lines:
                yield event_name, "\n".join(data_lines)
            event_name = None
            data_lines = []
            continue

        if line.startswith(":"):
            # SSE comment / keepalive.
            continue

        if line.startswith("event:"):
            event_name = line[len("event:") :].strip() or None
            continue

        if line.startswith("data:"):
            # Per SSE spec, an optional leading space after ':' is ignored.
            data_lines.append(line[len("data:") :].lstrip())
            continue

        # Ignore unsupported fields (id:, retry:, etc.).

    # Flush a final (possibly unterminated) event at EOF.
    if event_name is not None or data_lines:
        yield event_name, "\n".join(data_lines)


def _safe_json_loads(text: str) -> Any | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _extract_run_id_from_metadata(obj: Any) -> str | None:
    if not isinstance(obj, dict):
        return None
    run_id = obj.get("run_id")
    if isinstance(run_id, str) and run_id:
        return run_id
    return None


def _extract_ui_card(obj: Any) -> tuple[str, dict[str, object], str | None] | None:
    """Return (name, props, message_id) if obj is a UI card custom event."""

    if not isinstance(obj, dict):
        return None
    if obj.get("type") != "ui":
        return None

    name = obj.get("name")
    if not isinstance(name, str) or not name:
        return None

    props_any = obj.get("props")
    if not isinstance(props_any, dict):
        props_any = {}
    props: dict[str, object] = {}
    for k, v in props_any.items():
        if isinstance(k, str):
            props[k] = v

    message_id: str | None = None
    metadata = obj.get("metadata")
    if isinstance(metadata, dict):
        mid = metadata.get("message_id")
        if isinstance(mid, str) and mid:
            message_id = mid

    return name, props, message_id


def _extract_messages_container(obj: Any) -> list[Any] | None:
    if not isinstance(obj, dict):
        return None

    # LangGraph Server commonly wraps values under {"event": "values", "data": {...}}.
    state: Any = obj.get("data") if "data" in obj else obj

    if isinstance(state, dict):
        messages = state.get("messages")
        if isinstance(messages, list):
            return messages
        # Some shapes nest values deeper.
        values = state.get("values")
        if isinstance(values, dict):
            messages = values.get("messages")
            if isinstance(messages, list):
                return messages

    return None


def _is_assistantish(msg: dict[str, Any]) -> bool:
    role = msg.get("role")
    if isinstance(role, str) and role.lower() in {"assistant", "ai"}:
        return True
    typ = msg.get("type")
    if isinstance(typ, str) and typ.lower() in {"assistant", "ai"}:
        return True
    return False


def _extract_first_assistant_text(messages: list[Any]) -> str | None:
    for m in messages:
        if not isinstance(m, dict):
            continue
        msg = m  # narrow
        if not _is_assistantish(msg):
            continue

        content = msg.get("content")
        if isinstance(content, str) and content:
            return content

        # Support OpenAI-style content blocks: [{"type": "text", "text": "..."}, ...]
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                text = part.get("text")
                if isinstance(text, str) and text:
                    return text

    return None


async def iter_langgraph_events_to_canonical(
    lines: AsyncIterator[str],
) -> AsyncIterator[CanonicalEvent]:
    """Convert LangGraph Server SSE lines into canonical events.

    MVP behavior:
    - seq starts at 1 and increments per emitted canonical event.
    - run_id is tracked only from `event: metadata` JSON payloads.
    - emits at most one `message.text` per run.
    """

    seq = 1
    run_id: str | None = None
    run_started_emitted = False
    text_emitted = False

    def emit(event_type: str, payload: dict[str, object]) -> CanonicalEvent:
        nonlocal seq
        evt = new_event(seq, event_type, payload, run_id=run_id)
        seq += 1
        return evt

    try:
        async for event_name, data in parse_sse_events(lines):
            if event_name == "metadata":
                obj = _safe_json_loads(data)
                rid = _extract_run_id_from_metadata(obj)
                if rid is not None:
                    run_id = rid
                    if not run_started_emitted:
                        run_started_emitted = True
                        yield emit(EVENT_RUN_STARTED, {})
                continue

            if event_name == "custom":
                obj = _safe_json_loads(data)
                card = _extract_ui_card(obj)
                if card is None:
                    continue
                name, props, message_id = card
                payload: dict[str, object] = {"name": name, "props": props}
                if message_id is not None:
                    payload["message_id"] = message_id
                yield emit(EVENT_UI_CARD, payload)
                continue

            if event_name == "values" and not text_emitted:
                obj = _safe_json_loads(data)
                messages = _extract_messages_container(obj)
                if messages is None:
                    continue
                text = _extract_first_assistant_text(messages)
                if text is None:
                    continue
                text_emitted = True
                yield emit(EVENT_MESSAGE_TEXT, {"content": text})
                continue

        # EOF
        yield emit(EVENT_RUN_FINISHED, {})
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        yield new_event(seq, EVENT_RUN_ERROR, {"error": str(exc)}, run_id=run_id)


__all__ = [
    "parse_sse_events",
    "iter_langgraph_events_to_canonical",
]
