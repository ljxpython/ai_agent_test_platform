from __future__ import annotations

# pyright: reportMissingImports=false

import asyncio
import datetime
import json
import uuid

from fastapi import APIRouter, HTTPException, Request
from starlette.responses import StreamingResponse
from sqlalchemy import func, select

from omo_platform.api.v1.auth import V1ContextDep
from omo_platform.db.models_v1 import ProjectV1, RunEventV1, RunV1
from omo_platform.db.session import SessionLocal


router = APIRouter()


def _iso_z(dt: datetime.datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def _require_project(session, *, project_id: str) -> uuid.UUID:
    try:
        pid = uuid.UUID(project_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="Unauthorized")
    exists = (
        session.execute(select(ProjectV1).where(ProjectV1.project_id == pid)).scalars().first()
    )
    if exists is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return pid


@router.get("/runs/{run_id}/events")
async def stream_run_events(
    request: Request,
    ctx: V1ContextDep,
    run_id: str,
) -> StreamingResponse:
    try:
        rid = uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Not found")

    last_event_id = request.headers.get("Last-Event-ID")
    last = 0
    if last_event_id:
        try:
            last = int(last_event_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Last-Event-ID")
        if last < 0:
            raise HTTPException(status_code=400, detail="Invalid Last-Event-ID")

    # Validate tenant and compute thread_id once.
    with SessionLocal() as session:
        pid = _require_project(session, project_id=ctx.project_id)
        run = (
            session.execute(
                select(RunV1)
                .where(RunV1.project_id == pid)
                .where(RunV1.run_id == rid)
            )
            .scalars()
            .first()
        )
        if run is None:
            raise HTTPException(status_code=404, detail="Not found")
        thread_id = str(run.thread_id)

        min_seq = (
            session.execute(
                select(func.min(RunEventV1.seq))
                .where(RunEventV1.project_id == pid)
                .where(RunEventV1.run_id == rid)
            )
            .scalar()
        )
        if last > 0:
            # If events are expired (gap cannot be satisfied), return 410.
            if min_seq is None or last < int(min_seq) - 1:
                raise HTTPException(status_code=410, detail="events expired")

    async def gen():
        nonlocal last
        # Replay + tail loop (Postgres polling). Heartbeat via SSE comment.
        while True:
            if await request.is_disconnected():
                return

            def _fetch():
                with SessionLocal() as session:
                    pid = _require_project(session, project_id=ctx.project_id)
                    rows = (
                        session.execute(
                            select(RunEventV1)
                            .where(RunEventV1.project_id == pid)
                            .where(RunEventV1.run_id == rid)
                            .where(RunEventV1.seq > last)
                            .order_by(RunEventV1.seq.asc())
                        )
                        .scalars()
                        .all()
                    )
                    return rows

            rows = await asyncio.to_thread(_fetch)
            if rows:
                for row in rows:
                    evt = {
                        "seq": int(row.seq),
                        "ts": _iso_z(row.ts),
                        "thread_id": thread_id,
                        "run_id": str(rid),
                        "type": row.type,
                        "payload": row.payload or {},
                    }
                    data = json.dumps(evt, separators=(",", ":"), ensure_ascii=True)
                    # Update cursor only after we have a concrete event.
                    last = int(row.seq)
                    yield f"id: {evt['seq']}\n".encode("utf-8")
                    yield f"data: {data}\n\n".encode("utf-8")
                continue

            # Idle heartbeat.
            yield b": ping\n\n"
            await asyncio.sleep(1.0)

    return StreamingResponse(gen(), media_type="text/event-stream")
