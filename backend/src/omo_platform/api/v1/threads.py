from __future__ import annotations

# pyright: reportMissingImports=false

import datetime
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from omo_platform.api.v1.auth import V1ContextDep
from typing import Annotated
from fastapi import Header

from omo_platform.db.models_v1 import ProjectV1, ThreadV1, RunV1
from omo_platform.db.session import SessionLocal


router = APIRouter(prefix="/threads")


class CreateThreadRequest(BaseModel):
    metadata: dict[str, Any] | None = None


class CreateThreadResponse(BaseModel):
    project_id: str
    thread_id: str
    created_at: datetime.datetime


class CreateRunRequest(BaseModel):
    assistant_id: str = "sql_agent"
    input: dict[str, Any] | None = None
    config: dict[str, Any] | None = None


class CreateRunResponse(BaseModel):
    run_id: str
    status: str


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


@router.post("")
def create_thread(ctx: V1ContextDep, req: CreateThreadRequest) -> CreateThreadResponse:
    with SessionLocal() as session:
        pid = _require_project(session, project_id=ctx.project_id)

        t = ThreadV1(project_id=pid, metadata_json=req.metadata or {})
        session.add(t)
        session.commit()

        return CreateThreadResponse(
            project_id=str(t.project_id),
            thread_id=str(t.thread_id),
            created_at=t.created_at,
        )


@router.post("/{thread_id}/runs")
def create_run(
    ctx: V1ContextDep,
    thread_id: str,
    req: CreateRunRequest,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> CreateRunResponse:
    try:
        tid = uuid.UUID(thread_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Not found")

    if idempotency_key is not None:
        idempotency_key = idempotency_key.strip() or None

    with SessionLocal() as session:
        pid = _require_project(session, project_id=ctx.project_id)

        t = (
            session.execute(
                select(ThreadV1)
                .where(ThreadV1.project_id == pid)
                .where(ThreadV1.thread_id == tid)
            )
            .scalars()
            .first()
        )
        if t is None:
            raise HTTPException(status_code=404, detail="Not found")

        if idempotency_key:
            existing = (
                session.execute(
                    select(RunV1)
                    .where(RunV1.project_id == pid)
                    .where(RunV1.thread_id == tid)
                    .where(RunV1.idempotency_key == idempotency_key)
                )
                .scalars()
                .first()
            )
            if existing is not None:
                return CreateRunResponse(run_id=str(existing.run_id), status=existing.status)

        now = datetime.datetime.now(datetime.timezone.utc)
        r = RunV1(
            project_id=pid,
            thread_id=tid,
            status="running",
            assistant_id=req.assistant_id,
            idempotency_key=idempotency_key,
            input_json=req.input,
            config_json=req.config,
            created_at=now,
            updated_at=now,
        )
        session.add(r)
        session.commit()
        return CreateRunResponse(run_id=str(r.run_id), status=r.status)
