# flake8: noqa
# pyright: reportMissingImports=false
# ruff: noqa

"""LangGraph proxy router.

This router exposes a single proxy endpoint under `/api/lg/{path:path}` and
forwards requests to a LangGraph Server instance.
"""

from __future__ import annotations

import asyncio
from functools import partial
import json
import logging
import os
from urllib.parse import urlsplit
import uuid
from typing import Any, AsyncIterator, Dict, Iterable, Optional, Tuple

import httpx
from fastapi import APIRouter, HTTPException, Request
from starlette.responses import Response, StreamingResponse

from sqlalchemy import select


from omo_platform.db.models import (
    ApprovalDecision,
    ApprovalDecisionStatus,
    Project,
    RunAudit,
    RunAuditStatus,
)
from omo_platform.db.session import SessionLocal

router = APIRouter()

logger = logging.getLogger(__name__)


_USER_ID_HEADER = "X-User-Id"
_PROJECT_ID_HEADER = "X-Project-Id"

_HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


def _get_identity_headers(request: Request) -> Tuple[str, str]:
    user_id = request.headers.get(_USER_ID_HEADER)
    project_id = request.headers.get(_PROJECT_ID_HEADER)
    if not user_id or not project_id:
        raise HTTPException(status_code=401, detail="Missing identity headers")
    return user_id, project_id


def _is_json_request(request: Request) -> bool:
    content_type = (request.headers.get("content-type") or "").lower()
    mime = content_type.split(";", 1)[0].strip()
    return mime == "application/json" or mime.endswith("+json")


def _filtered_upstream_headers(request: Request) -> Dict[str, str]:
    # Forward most headers as-is, excluding hop-by-hop headers and ones that the
    # http client will set correctly.
    out: Dict[str, str] = {}
    for k, v in request.headers.items():
        lk = k.lower()
        if lk in _HOP_BY_HOP_HEADERS:
            continue
        if lk in {"host", "content-length"}:
            continue
        out[k] = v
    return out


def _content_location_only(resp: httpx.Response) -> Dict[str, str]:
    value = resp.headers.get("content-location")
    if not value:
        return {}
    return {"Content-Location": value}


def _extract_thread_id_from_content_location(value: str) -> str | None:
    """Extract thread_id from LangGraph Content-Location header.

    Supported forms (may be prefixed with extra segments):
    - /threads/{thread_id}/runs/{run_id}
    - /api/lg/threads/{thread_id}/runs/{run_id}
    """

    if not value:
        return None

    # Content-Location can be a path or full URL; ignore query/fragment.
    path = urlsplit(value).path or ""
    if not path:
        return None

    parts = [p for p in path.split("/") if p]
    # Find any occurrence of: threads/{thread_id}/runs/{run_id}
    for i in range(len(parts) - 3):
        if parts[i] != "threads":
            continue
        thread_id = parts[i + 1]
        if not thread_id:
            continue
        if parts[i + 2] != "runs":
            continue
        run_id = parts[i + 3]
        if not run_id:
            continue
        return thread_id

    return None


def _extract_thread_id_from_request_path(path: str) -> str | None:
    """Extract thread_id from proxied request path.

    Supported forms (may be prefixed with extra segments):
    - threads/{thread_id}/runs/stream
    - /api/lg/threads/{thread_id}/runs/stream
    """

    if not path:
        return None

    # Be tolerant: callers may pass a full URL or include query/fragment.
    parsed_path = urlsplit(path).path or ""
    if not parsed_path:
        return None

    parts = [p for p in parsed_path.split("/") if p]
    # Find any occurrence of: threads/{thread_id}/runs/stream
    for i in range(len(parts) - 3):
        if parts[i] != "threads":
            continue
        thread_id = parts[i + 1]
        if not thread_id:
            continue
        if parts[i + 2] != "runs":
            continue
        if parts[i + 3] != "stream":
            continue
        return thread_id

    return None


async def _stream_bytes_and_close(
    resp: httpx.Response, client: httpx.AsyncClient
) -> AsyncIterator[bytes]:
    try:
        async for chunk in resp.aiter_raw():
            yield chunk
    finally:
        await resp.aclose()
        await client.aclose()


def _safe_json_loads(text: str) -> Any | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


async def _run_db_best_effort(fn, *, op: str) -> None:
    # DB writes must never break or stall streaming.
    try:
        await asyncio.wait_for(asyncio.to_thread(fn), timeout=0.5)
    except asyncio.TimeoutError:
        logger.warning("audit db op timed out: %s", op)
    except Exception as err:
        logger.warning("audit db op failed: %s: %s", op, err)


def _resolve_project_id_by_name(session, project_name: str) -> uuid.UUID | None:
    project = (
        session.execute(
            select(Project).where(Project.name == project_name).order_by(Project.created_at.asc())
        )
        .scalars()
        .first()
    )
    if project is None:
        return None
    return project.id


def _db_get_or_create_run_audit(
    *,
    project_name: str,
    user_id: str,
    run_id: str,
    status: RunAuditStatus,
    error_summary: str | None,
    thread_id: str | None = None,
) -> None:
    with SessionLocal() as session:
        project_id = _resolve_project_id_by_name(session, project_name)
        if project_id is None:
            return

        existing = (
            session.execute(
                select(RunAudit)
                .where(RunAudit.project_id == project_id)
                .where(RunAudit.run_id == run_id)
                .order_by(RunAudit.created_at.asc())
            )
            .scalars()
            .first()
        )
        if existing is None:
            session.add(
                RunAudit(
                    project_id=project_id,
                    user_id=user_id,
                    thread_id=thread_id,
                    run_id=run_id,
                    status=status,
                    error_summary=error_summary,
                )
            )
        else:
            existing.status = status
            if thread_id is not None and existing.thread_id is None:
                existing.thread_id = thread_id
            if error_summary is not None:
                existing.error_summary = error_summary
        session.commit()


def _db_update_run_audit_succeeded(
    *,
    project_name: str,
    user_id: str,
    run_id: str,
    sql_text: str | None,
    row_count: int | None,
    duration_ms: int | None,
    thread_id: str | None = None,
) -> None:
    with SessionLocal() as session:
        project_id = _resolve_project_id_by_name(session, project_name)
        if project_id is None:
            return

        audit = (
            session.execute(
                select(RunAudit)
                .where(RunAudit.project_id == project_id)
                .where(RunAudit.run_id == run_id)
                .order_by(RunAudit.created_at.asc())
            )
            .scalars()
            .first()
        )
        if audit is None:
            audit = RunAudit(
                project_id=project_id,
                user_id=user_id,
                thread_id=thread_id,
                run_id=run_id,
            )
            session.add(audit)
        elif thread_id is not None and audit.thread_id is None:
            audit.thread_id = thread_id

        audit.status = RunAuditStatus.SUCCEEDED
        if sql_text is not None:
            audit.sql_text = sql_text
        if row_count is not None:
            audit.row_count = row_count
        if duration_ms is not None:
            audit.duration_ms = duration_ms
        session.commit()


def _db_update_run_audit_failed(
    *,
    project_name: str,
    user_id: str,
    run_id: str,
    error_summary: str,
    thread_id: str | None = None,
) -> None:
    with SessionLocal() as session:
        project_id = _resolve_project_id_by_name(session, project_name)
        if project_id is None:
            return

        audit = (
            session.execute(
                select(RunAudit)
                .where(RunAudit.project_id == project_id)
                .where(RunAudit.run_id == run_id)
                .order_by(RunAudit.created_at.asc())
            )
            .scalars()
            .first()
        )
        if audit is None:
            audit = RunAudit(
                project_id=project_id,
                user_id=user_id,
                thread_id=thread_id,
                run_id=run_id,
            )
            session.add(audit)
        elif thread_id is not None and audit.thread_id is None:
            audit.thread_id = thread_id

        audit.status = RunAuditStatus.FAILED
        audit.error_summary = error_summary
        session.commit()


def _db_upsert_approval_decision_pending(
    *, project_name: str, user_id: str, run_id: str, approval_id: str, sql_proposed: str | None
) -> None:
    try:
        approval_uuid = uuid.UUID(approval_id)
    except Exception:
        return

    with SessionLocal() as session:
        project_id = _resolve_project_id_by_name(session, project_name)
        if project_id is None:
            return

        decision = session.get(ApprovalDecision, approval_uuid)
        if decision is None:
            decision = ApprovalDecision(
                id=approval_uuid,
                project_id=project_id,
                user_id=user_id,
                run_id_request=run_id,
                status=ApprovalDecisionStatus.PENDING,
                sql_proposed=sql_proposed,
            )
            session.add(decision)
        else:
            decision.project_id = project_id
            decision.user_id = user_id
            decision.run_id_request = run_id
            decision.status = ApprovalDecisionStatus.PENDING
            if sql_proposed is not None:
                decision.sql_proposed = sql_proposed
        session.commit()


async def _stream_sse_with_audit(
    *,
    resp: httpx.Response,
    client: httpx.AsyncClient,
    user_id: str,
    project_name: str,
    thread_id: str | None,
) -> AsyncIterator[bytes]:
    # Prefer thread_id derived from request path; fall back to Content-Location.
    thread_id = thread_id or _extract_thread_id_from_content_location(
        resp.headers.get("content-location") or ""
    )
    run_id: str | None = None
    run_succeeded = False
    run_audit_started_written = False
    stream_error_summary: str | None = None
    stream_error_run_id: str | None = None

    upstream_status = resp.status_code
    upstream_http_error = upstream_status >= 400

    # Parse SSE on the fly from raw bytes. Yield chunks immediately.
    buf = b""
    event_name: str | None = None
    data_lines: list[str] = []

    def handle_event(name: str | None, data: str) -> None:
        nonlocal run_id, run_succeeded, run_audit_started_written
        if name == "metadata":
            obj = _safe_json_loads(data)
            if isinstance(obj, dict):
                rid = obj.get("run_id")
                if isinstance(rid, str) and rid and run_id is None:
                    rid_str: str = rid
                    run_id = rid_str
                    if not run_audit_started_written:
                        run_audit_started_written = True
                        asyncio.create_task(
                            _run_db_best_effort(
                                lambda: _db_get_or_create_run_audit(
                                    project_name=project_name,
                                    user_id=user_id,
                                    thread_id=thread_id,
                                    run_id=rid_str,
                                    status=RunAuditStatus.STARTED,
                                    error_summary=None,
                                ),
                                op="run_audit.started",
                            )
                        )
                    if upstream_http_error:
                        asyncio.create_task(
                            _run_db_best_effort(
                                lambda: _db_update_run_audit_failed(
                                    project_name=project_name,
                                    user_id=user_id,
                                    thread_id=thread_id,
                                    run_id=rid_str,
                                    error_summary=f"upstream http {upstream_status}",
                                ),
                                op="run_audit.upstream_http_error",
                            )
                        )

        if name == "custom":
            obj = _safe_json_loads(data)
            if not isinstance(obj, dict):
                return
            if obj.get("type") != "ui":
                return
            card_name = obj.get("name")
            if not isinstance(card_name, str) or not card_name:
                return
            props_any = obj.get("props")
            props = props_any if isinstance(props_any, dict) else {}

            if card_name == "SqlApprovalCard":
                if run_id is None:
                    return
                rid = run_id
                approval_id = props.get("approval_id")
                sql_proposed = props.get("sql")
                if not isinstance(approval_id, str) or not approval_id:
                    return
                sql_text = sql_proposed if isinstance(sql_proposed, str) else None
                asyncio.create_task(
                    _run_db_best_effort(
                        lambda: _db_upsert_approval_decision_pending(
                            project_name=project_name,
                            user_id=user_id,
                            run_id=rid,
                            approval_id=approval_id,
                            sql_proposed=sql_text,
                        ),
                        op="approval_decision.pending",
                    )
                )
                return

            if card_name == "ResultTableCard":
                if run_id is None:
                    return
                rid = run_id
                sql_val = props.get("sql")
                duration_val = props.get("duration_ms")
                row_count_val = props.get("returned_row_count")
                if row_count_val is None:
                    row_count_val = props.get("row_count")

                sql_text = sql_val if isinstance(sql_val, str) else None

                duration_ms: int | None = None
                if isinstance(duration_val, (int, float)):
                    duration_ms = int(duration_val)

                row_count: int | None = None
                if isinstance(row_count_val, (int, float)):
                    row_count = int(row_count_val)

                run_succeeded = True
                asyncio.create_task(
                    _run_db_best_effort(
                        lambda: _db_update_run_audit_succeeded(
                            project_name=project_name,
                            user_id=user_id,
                            thread_id=thread_id,
                            run_id=rid,
                            sql_text=sql_text,
                            row_count=row_count,
                            duration_ms=duration_ms,
                        ),
                        op="run_audit.succeeded",
                    )
                )
                return

    try:
        async for chunk in resp.aiter_raw():
            # Keep streaming to the client regardless of parse/DB errors.
            yield chunk

            buf += chunk
            while True:
                nl = buf.find(b"\n")
                if nl < 0:
                    break
                raw_line = buf[:nl]
                buf = buf[nl + 1 :]
                line = raw_line.rstrip(b"\r").decode("utf-8", errors="replace")

                if line == "":
                    # End of event.
                    if event_name is not None or data_lines:
                        handle_event(event_name, "\n".join(data_lines))
                    event_name = None
                    data_lines = []
                    continue

                if line.startswith(":"):
                    continue

                if line.startswith("event:"):
                    event_name = line[len("event:") :].strip() or None
                    continue

                if line.startswith("data:"):
                    data_lines.append(line[len("data:") :].lstrip())
                    continue

        # EOF flush.
        if event_name is not None or data_lines:
            handle_event(event_name, "\n".join(data_lines))

    except asyncio.CancelledError:
        raise
    except Exception as err:
        if run_id is not None:
            stream_error_run_id = run_id
            stream_error_summary = str(err)
        raise
    finally:
        if stream_error_summary is not None and stream_error_run_id is not None:
            asyncio.create_task(
                _run_db_best_effort(
                    partial(
                        _db_update_run_audit_failed,
                        project_name=project_name,
                        user_id=user_id,
                        thread_id=thread_id,
                        run_id=stream_error_run_id,
                        error_summary=stream_error_summary,
                    ),
                    op="run_audit.exception",
                )
            )
        # If upstream returned an HTTP error and we never observed a success card,
        # mark the run as failed once we know the run_id.
        elif upstream_http_error and run_id is not None and not run_succeeded:
            rid = run_id
            asyncio.create_task(
                _run_db_best_effort(
                    partial(
                        _db_update_run_audit_failed,
                        project_name=project_name,
                        user_id=user_id,
                        thread_id=thread_id,
                        run_id=rid,
                        error_summary=f"upstream http {upstream_status}",
                    ),
                    op="run_audit.final_upstream_http_error",
                )
            )

        await resp.aclose()
        await client.aclose()


@router.api_route(
    "/api/lg/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def langgraph_proxy(path: str, request: Request) -> Response:
    user_id, project_id = _get_identity_headers(request)

    base_url = os.getenv("LANGGRAPH_BASE_URL", "http://127.0.0.1:2024").rstrip("/")
    upstream_url = f"{base_url}/{path}" if path else base_url

    # httpx typing for params is a bit picky; a tuple-of-tuples is accepted.
    params = tuple(request.query_params.multi_items())
    headers = _filtered_upstream_headers(request)

    json_body: Optional[Dict[str, Any]] = None
    raw_body: Optional[bytes] = None

    if _is_json_request(request):
        raw = await request.body()
        if raw:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as err:
                raise HTTPException(status_code=400, detail="Invalid JSON body") from err
        else:
            parsed = {}

        if not isinstance(parsed, dict):
            raise HTTPException(status_code=400, detail="JSON body must be an object")

        if "input" in parsed and not isinstance(parsed["input"], dict):
            raise HTTPException(status_code=400, detail="'input' must be an object")

        # Inject identity into upstream request. Create `input` if missing.
        input_obj = parsed.setdefault("input", {})
        input_obj["platform_user_id"] = user_id
        input_obj["platform_project_id"] = project_id

        json_body = parsed
    else:
        raw_body = await request.body()

    timeout = httpx.Timeout(connect=10.0, read=None, write=60.0, pool=60.0)
    client = httpx.AsyncClient(timeout=timeout, follow_redirects=False)
    try:
        upstream_request = client.build_request(
            method=request.method,
            url=upstream_url,
            params=params,
            headers=headers,
            json=json_body,
            content=raw_body,
        )
        upstream_response = await client.send(upstream_request, stream=True)
    except httpx.HTTPError as err:
        await client.aclose()
        raise HTTPException(status_code=502, detail="Upstream request failed") from err

    content_type = (upstream_response.headers.get("content-type") or "").lower()
    is_sse = content_type.startswith("text/event-stream")

    if is_sse:
        is_streaming_run = request.method.upper() == "POST" and path.endswith("runs/stream")
        thread_id = _extract_thread_id_from_request_path(path) if is_streaming_run else None
        return StreamingResponse(
            (
                _stream_sse_with_audit(
                    resp=upstream_response,
                    client=client,
                    user_id=user_id,
                    project_name=project_id,
                    thread_id=thread_id,
                )
                if is_streaming_run
                else _stream_bytes_and_close(upstream_response, client)
            ),
            status_code=upstream_response.status_code,
            media_type=upstream_response.headers.get("content-type"),
            headers=_content_location_only(upstream_response),
        )

    body_bytes = await upstream_response.aread()
    headers_out = _content_location_only(upstream_response)
    await upstream_response.aclose()
    await client.aclose()
    return Response(
        content=body_bytes,
        status_code=upstream_response.status_code,
        media_type=upstream_response.headers.get("content-type"),
        headers=headers_out,
    )


__all__ = ["router"]
