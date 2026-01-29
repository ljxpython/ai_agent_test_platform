# pyright: reportMissingImports=false

import json
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Header, HTTPException, Request
from starlette.responses import StreamingResponse

from omo_platform.events.canonical import (
    CanonicalEvent,
    EVENT_MESSAGE_TEXT,
    EVENT_RUN_ERROR,
    EVENT_RUN_FINISHED,
    EVENT_RUN_STARTED,
)
from omo_platform.langgraph_proxy.stream_adapter import iter_langgraph_events_to_canonical


router = APIRouter()


def _sse_data_line(payload: dict[str, Any]) -> bytes:
    # Minimal SSE: only emit data lines.
    return ("data: " + json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n\n").encode(
        "utf-8"
    )


def _agui_thread_id(thread_id: str | None, run_id: str | None) -> str:
    # AG-UI requires a threadId; for threadless runs we use threadId = runId.
    if isinstance(thread_id, str) and thread_id:
        return thread_id
    if isinstance(run_id, str) and run_id:
        return run_id
    # Defensive fallback: should not happen for well-formed canonical events.
    return ""


def _should_emit_canonical_event(
    evt: CanonicalEvent, *, seen: set[tuple[str, int]]
) -> bool:
    """按 (run_id, seq) 做幂等去重。

    约定：
    - 仅当 run_id 为非空字符串且 seq 为 int 时参与去重；否则一律放行。
    - 不尝试重排乱序事件；只保证重复 key 不会二次输出。
    """

    run_id = evt.get("run_id")
    seq = evt.get("seq")
    if not isinstance(run_id, str) or not run_id:
        return True
    if not isinstance(seq, int):
        return True
    key = (run_id, seq)
    if key in seen:
        return False
    seen.add(key)
    return True


async def _iter_canonical_events_to_agui_sse(
    events: AsyncIterator[CanonicalEvent],
    *,
    is_disconnected: Callable[[], Awaitable[bool]] | None = None,
) -> AsyncIterator[bytes]:
    next_message_num = 1
    seen: set[tuple[str, int]] = set()

    def emit(payload: dict[str, Any]) -> bytes:
        return _sse_data_line(payload)

    def _payload_text_content(payload: Any) -> str:
        if not isinstance(payload, dict):
            return ""
        content = payload.get("content")
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        return str(content)

    async for evt in events:
        if is_disconnected is not None and await is_disconnected():
            return

        if not _should_emit_canonical_event(evt, seen=seen):
            continue

        evt_type = evt["type"]
        run_id = evt.get("run_id")
        thread_id = evt.get("thread_id")
        payload = evt.get("payload")

        if evt_type == EVENT_RUN_STARTED:
            # Canonical adapter should only emit this once run_id is known.
            if not isinstance(run_id, str) or not run_id:
                continue
            yield emit(
                {
                    "type": "RUN_STARTED",
                    "threadId": _agui_thread_id(thread_id, run_id),
                    "runId": run_id,
                }
            )
            continue

        if evt_type == EVENT_MESSAGE_TEXT:
            content = _payload_text_content(payload)
            # AG-UI requires non-empty delta. If empty, skip the whole triplet.
            if not content:
                continue

            message_id = f"msg-{next_message_num}"
            next_message_num += 1

            yield emit(
                {
                    "type": "TEXT_MESSAGE_START",
                    "messageId": message_id,
                    "role": "assistant",
                }
            )
            yield emit(
                {
                    "type": "TEXT_MESSAGE_CONTENT",
                    "messageId": message_id,
                    "delta": content,
                }
            )
            yield emit({"type": "TEXT_MESSAGE_END", "messageId": message_id})
            continue

        if evt_type == EVENT_RUN_FINISHED:
            # Canonical adapter always emits finished at EOF; skip if run_id is missing.
            if not isinstance(run_id, str) or not run_id:
                continue
            yield emit(
                {
                    "type": "RUN_FINISHED",
                    "threadId": _agui_thread_id(thread_id, run_id),
                    "runId": run_id,
                }
            )
            continue

        if evt_type == EVENT_RUN_ERROR:
            error_obj = None
            if isinstance(payload, dict):
                error_obj = payload.get("error")
            if isinstance(error_obj, str):
                error = error_obj
            elif error_obj is None:
                error = ""
            else:
                error = str(error_obj)
            yield emit({"type": "RUN_ERROR", "message": error or "Unknown error"})
            continue


@router.post("/api/agui/agent")
async def agui_agent(
    request: Request,
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    x_project_id: Optional[str] = Header(default=None, alias="X-Project-Id"),
):
    if not x_user_id or not x_project_id:
        raise HTTPException(status_code=401, detail="Missing X-User-Id or X-Project-Id")

    try:
        body = await request.json()
        if not isinstance(body, dict):
            body = {"input": body}
    except Exception:
        body = {}

    base_url = os.getenv("LANGGRAPH_BASE_URL", "http://127.0.0.1:2024").rstrip("/")
    upstream_url = f"{base_url}/runs/stream"

    async def gen() -> AsyncIterator[bytes]:
        timeout = httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", upstream_url, json=body) as resp:
                resp.raise_for_status()
                async for chunk in _iter_canonical_events_to_agui_sse(
                    iter_langgraph_events_to_canonical(resp.aiter_lines()),
                    is_disconnected=request.is_disconnected,
                ):
                    yield chunk

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
