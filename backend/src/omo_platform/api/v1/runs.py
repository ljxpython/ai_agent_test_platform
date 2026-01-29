from __future__ import annotations

# pyright: reportMissingImports=false

import datetime
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from omo_platform.api.v1.auth import V1ContextDep
from omo_platform.db.models_v1 import ProjectV1, RunV1
from omo_platform.db.session import SessionLocal


router = APIRouter(prefix="/runs")


class RunSummary(BaseModel):
    project_id: str
    thread_id: str
    run_id: str
    status: str
    created_at: datetime.datetime
    updated_at: datetime.datetime
    error: str | None


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


@router.get("")
def list_runs(
    ctx: V1ContextDep,
    limit: int = 50,
    before: datetime.datetime | None = None,
) -> dict[str, list[RunSummary]]:
    if limit <= 0 or limit > 200:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 200")

    with SessionLocal() as session:
        pid = _require_project(session, project_id=ctx.project_id)
        stmt = select(RunV1).where(RunV1.project_id == pid)
        if before is not None:
            stmt = stmt.where(RunV1.created_at < before)
        rows = session.execute(stmt.order_by(RunV1.created_at.desc()).limit(limit)).scalars().all()

        return {
            "items": [
                RunSummary(
                    project_id=str(r.project_id),
                    thread_id=str(r.thread_id),
                    run_id=str(r.run_id),
                    status=r.status,
                    created_at=r.created_at,
                    updated_at=r.updated_at,
                    error=r.error,
                )
                for r in rows
            ]
        }


@router.get("/{run_id}")
def get_run(ctx: V1ContextDep, run_id: str) -> RunSummary:
    try:
        rid = uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Not found")

    with SessionLocal() as session:
        pid = _require_project(session, project_id=ctx.project_id)
        r = (
            session.execute(
                select(RunV1)
                .where(RunV1.project_id == pid)
                .where(RunV1.run_id == rid)
            )
            .scalars()
            .first()
        )
        if r is None:
            raise HTTPException(status_code=404, detail="Not found")

        return RunSummary(
            project_id=str(r.project_id),
            thread_id=str(r.thread_id),
            run_id=str(r.run_id),
            status=r.status,
            created_at=r.created_at,
            updated_at=r.updated_at,
            error=r.error,
        )
