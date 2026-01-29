from __future__ import annotations

# pyright: reportMissingImports=false

import datetime
import os
import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from omo_platform.api.v1.auth import V1ContextDep
from typing import Annotated
from fastapi import Header

from omo_platform.db.models_v1 import ApprovalV1, ProjectV1, RunV1, ThreadV1
from omo_platform.db.session import SessionLocal
from omo_platform.execution.sql_agent_exec import execute_run_a, execute_run_b
import omo_platform.sql_safety as sql_safety


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


class ApprovalActionRequest(BaseModel):
    action: str  # approve | reject
    sql: str | None = None


class ApprovalActionResponse(BaseModel):
    status: str
    run_id_execute: str | None = None


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
    background_tasks: BackgroundTasks,
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

        # Start Run-A in the background.
        background_tasks.add_task(
            execute_run_a,
            project_id=pid,
            thread_id=tid,
            run_id=r.run_id,
        )
        return CreateRunResponse(run_id=str(r.run_id), status=r.status)


def _resolve_chinook_path() -> str:
    # Container path (compose) first, then repo-relative fallback.
    candidates = [
        "/app/infra/chinook/chinook.db",
        "infra/chinook/chinook.db",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    # Default to repo-relative.
    return "infra/chinook/chinook.db"


@router.post("/{thread_id}/approvals/{approval_id}")
def decide_approval(
    ctx: V1ContextDep,
    thread_id: str,
    approval_id: str,
    req: ApprovalActionRequest,
    background_tasks: BackgroundTasks,
) -> ApprovalActionResponse:
    try:
        tid = uuid.UUID(thread_id)
        aid = uuid.UUID(approval_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Not found")

    action = (req.action or "").strip().lower()
    if action not in {"approve", "reject"}:
        raise HTTPException(status_code=400, detail="Invalid action")

    with SessionLocal() as session:
        pid = _require_project(session, project_id=ctx.project_id)
        approval = (
            session.execute(
                select(ApprovalV1)
                .where(ApprovalV1.project_id == pid)
                .where(ApprovalV1.approval_id == aid)
            )
            .scalars()
            .first()
        )
        if approval is None or approval.thread_id != tid:
            raise HTTPException(status_code=404, detail="Not found")

        if action == "reject":
            if approval.status != "rejected":
                approval.status = "rejected"
                approval.decided_at = datetime.datetime.now(datetime.timezone.utc)
            session.commit()
            return ApprovalActionResponse(status="rejected", run_id_execute=None)

        # approve
        if not req.sql or not req.sql.strip():
            raise HTTPException(status_code=400, detail="Missing sql")

        sql_approved = sql_safety.validate_and_rewrite_sql(req.sql, max_rows=200)

        if approval.status == "approved" and approval.run_id_execute is not None:
            return ApprovalActionResponse(
                status="approved",
                run_id_execute=str(approval.run_id_execute),
            )

        # Create (or reuse) Run-B.
        existing_run_b = (
            session.execute(
                select(RunV1)
                .where(RunV1.project_id == pid)
                .where(RunV1.approval_id == aid)
            )
            .scalars()
            .first()
        )
        if existing_run_b is not None:
            approval.status = "approved"
            approval.sql_approved = sql_approved
            approval.decided_at = datetime.datetime.now(datetime.timezone.utc)
            approval.run_id_execute = existing_run_b.run_id
            session.commit()
            return ApprovalActionResponse(
                status="approved",
                run_id_execute=str(existing_run_b.run_id),
            )

        now = datetime.datetime.now(datetime.timezone.utc)
        run_b = RunV1(
            project_id=pid,
            thread_id=tid,
            parent_run_id=approval.run_id_request,
            approval_id=aid,
            status="executing",
            assistant_id="sql_agent",
            idempotency_key=None,
            input_json=None,
            config_json=None,
            created_at=now,
            updated_at=now,
        )
        session.add(run_b)
        session.flush()

        approval.status = "approved"
        approval.sql_approved = sql_approved
        approval.decided_at = now
        approval.run_id_execute = run_b.run_id
        session.commit()

        background_tasks.add_task(
            execute_run_b,
            project_id=pid,
            run_id=run_b.run_id,
            chinook_path=_resolve_chinook_path(),
        )

        return ApprovalActionResponse(status="approved", run_id_execute=str(run_b.run_id))
