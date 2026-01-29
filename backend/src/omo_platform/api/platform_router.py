"""MVP platform API router.

Endpoints:
- GET  /api/platform/runs
- GET  /api/platform/runs/{run_id}
- GET  /api/platform/connections
- POST /api/platform/threads/{thread_id}/approvals/{approval_id}

All endpoints require X-User-Id and X-Project-Id headers.
X-Project-Id is interpreted as Project.name (MVP).
"""

# pyright: reportMissingImports=false

from __future__ import annotations

import datetime
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from omo_platform.db.models import (
    ApprovalDecision,
    ApprovalDecisionStatus,
    Connection,
    Project,
    RunAudit,
)
from omo_platform.db.session import SessionLocal


router = APIRouter(prefix="/api/platform")


def _now_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _require_auth(
    session: Session, *, user_id: str | None, project_name: str | None
) -> tuple[str, uuid.UUID, str]:
    if not user_id or not project_name:
        raise HTTPException(status_code=401, detail="Unauthorized")

    project = (
        session.execute(
            select(Project)
            .where(Project.name == project_name)
            .order_by(Project.created_at.asc())
        )
        .scalars()
        .first()
    )
    if project is None:
        raise HTTPException(status_code=401, detail="Unauthorized")

    return user_id, project.id, project.name


def _run_audit_to_dict(audit: RunAudit) -> dict[str, Any]:
    return {
        "id": str(audit.id),
        "project_id": str(audit.project_id),
        "user_id": audit.user_id,
        "thread_id": audit.thread_id,
        "run_id": audit.run_id,
        "parent_run_id": audit.parent_run_id,
        "status": str(audit.status.value),
        "sql_text": audit.sql_text,
        "row_count": audit.row_count,
        "duration_ms": audit.duration_ms,
        "error_summary": audit.error_summary,
        "created_at": audit.created_at,
    }


def _connection_to_dict(conn: Connection) -> dict[str, Any]:
    return {
        "id": str(conn.id),
        "kind": str(conn.kind.value),
        "config_json": conn.config_json,
        "created_at": conn.created_at,
    }


class ApprovalActionRequest(BaseModel):
    action: Literal["approve", "reject"]
    sql: str | None = None


@router.get("/runs")
def list_runs(
    *,
    limit: int = 50,
    before: datetime.datetime | None = None,
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
    x_project_id: str | None = Header(default=None, alias="X-Project-Id"),
) -> list[dict[str, Any]]:
    if limit <= 0:
        raise HTTPException(status_code=400, detail="limit must be positive")
    if limit > 200:
        raise HTTPException(status_code=400, detail="limit must be <= 200")

    with SessionLocal() as session:
        _, project_id, _ = _require_auth(
            session, user_id=x_user_id, project_name=x_project_id
        )

        stmt = select(RunAudit).where(RunAudit.project_id == project_id)
        if before is not None:
            stmt = stmt.where(RunAudit.created_at < before)

        runs = (
            session.execute(
                stmt.order_by(RunAudit.created_at.desc()).limit(limit)
            )
            .scalars()
            .all()
        )
        return [_run_audit_to_dict(r) for r in runs]


@router.get("/runs/{run_id}")
def get_run(
    *,
    run_id: str,
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
    x_project_id: str | None = Header(default=None, alias="X-Project-Id"),
) -> dict[str, Any]:
    with SessionLocal() as session:
        _, project_id, _ = _require_auth(
            session, user_id=x_user_id, project_name=x_project_id
        )

        audit = (
            session.execute(
                select(RunAudit)
                .where(RunAudit.project_id == project_id)
                .where(RunAudit.run_id == run_id)
                .order_by(RunAudit.created_at.desc())
            )
            .scalars()
            .first()
        )
        if audit is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return _run_audit_to_dict(audit)


@router.get("/connections")
def list_connections(
    *,
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
    x_project_id: str | None = Header(default=None, alias="X-Project-Id"),
) -> list[dict[str, Any]]:
    with SessionLocal() as session:
        _, project_id, _ = _require_auth(
            session, user_id=x_user_id, project_name=x_project_id
        )

        conns = (
            session.execute(
                select(Connection)
                .where(Connection.project_id == project_id)
                .order_by(Connection.created_at.desc())
            )
            .scalars()
            .all()
        )
        return [_connection_to_dict(c) for c in conns]


@router.post("/threads/{thread_id}/approvals/{approval_id}")
def decide_approval(
    *,
    thread_id: str,
    approval_id: uuid.UUID,
    body: ApprovalActionRequest,
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
    x_project_id: str | None = Header(default=None, alias="X-Project-Id"),
) -> dict[str, str]:
    with SessionLocal() as session:
        user_id, project_id, _ = _require_auth(
            session, user_id=x_user_id, project_name=x_project_id
        )

        decision = session.get(ApprovalDecision, approval_id)
        if decision is not None and decision.project_id != project_id:
            # Enforce project isolation; do not allow cross-project updates.
            raise HTTPException(status_code=404, detail="Approval not found")

        status = (
            ApprovalDecisionStatus.APPROVED
            if body.action == "approve"
            else ApprovalDecisionStatus.REJECTED
        )
        decided_at = _now_utc()

        if decision is None:
            # run_id_request is required by schema; best-effort derive it from
            # the newest RunAudit row for this (project, thread_id).
            run_id_request = (
                session.execute(
                    select(RunAudit.run_id)
                    .where(RunAudit.project_id == project_id)
                    .where(RunAudit.thread_id == thread_id)
                    .order_by(RunAudit.created_at.desc())
                )
                .scalars()
                .first()
            )
            if not run_id_request:
                raise HTTPException(status_code=404, detail="Approval not found")

            decision = ApprovalDecision(
                id=approval_id,
                project_id=project_id,
                user_id=user_id,
                thread_id=thread_id,
                run_id_request=run_id_request,
                status=status,
                sql_approved=body.sql,
                decided_at=decided_at,
            )
            session.add(decision)
        else:
            decision.thread_id = thread_id
            decision.user_id = user_id
            decision.status = status
            if body.sql is not None:
                decision.sql_approved = body.sql
            decision.decided_at = decided_at

        session.commit()

    return {"status": "accepted"}
