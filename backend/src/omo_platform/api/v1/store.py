from __future__ import annotations

# pyright: reportMissingImports=false

import datetime
import json
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from omo_platform.api.v1.auth import V1ContextDep
from omo_platform.db.models_v1 import ProjectV1, StoreItemV1
from omo_platform.db.session import SessionLocal


router = APIRouter(prefix="/store")


class StorePutRequest(BaseModel):
    value: dict


class StoreGetResponse(BaseModel):
    namespace: str
    key: str
    value: dict
    updated_at: datetime.datetime


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


def _validate_key(namespace: str, key: str) -> None:
    if not namespace or len(namespace) > 64:
        raise HTTPException(status_code=400, detail="Invalid namespace")
    if not key or len(key) > 128:
        raise HTTPException(status_code=400, detail="Invalid key")


def _validate_value_size(value: dict) -> None:
    # Locked: 16KB JSON size (canonical compact encoding).
    raw = json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    if len(raw) > 16 * 1024:
        raise HTTPException(status_code=413, detail="value too large")


@router.get("/{namespace}/{key}")
def get_item(ctx: V1ContextDep, namespace: str, key: str) -> StoreGetResponse:
    _validate_key(namespace, key)
    with SessionLocal() as session:
        pid = _require_project(session, project_id=ctx.project_id)
        row = (
            session.execute(
                select(StoreItemV1)
                .where(StoreItemV1.project_id == pid)
                .where(StoreItemV1.namespace == namespace)
                .where(StoreItemV1.key == key)
            )
            .scalars()
            .first()
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Not found")
        return StoreGetResponse(
            namespace=row.namespace,
            key=row.key,
            value=row.value or {},
            updated_at=row.updated_at,
        )


@router.put("/{namespace}/{key}")
def put_item(ctx: V1ContextDep, namespace: str, key: str, req: StorePutRequest) -> StoreGetResponse:
    _validate_key(namespace, key)
    _validate_value_size(req.value)
    now = datetime.datetime.now(datetime.timezone.utc)
    with SessionLocal() as session:
        pid = _require_project(session, project_id=ctx.project_id)
        row = (
            session.execute(
                select(StoreItemV1)
                .where(StoreItemV1.project_id == pid)
                .where(StoreItemV1.namespace == namespace)
                .where(StoreItemV1.key == key)
            )
            .scalars()
            .first()
        )
        if row is None:
            row = StoreItemV1(
                project_id=pid,
                namespace=namespace,
                key=key,
                value=req.value,
                updated_at=now,
            )
            session.add(row)
        else:
            row.value = req.value
            row.updated_at = now
        session.commit()
        return StoreGetResponse(namespace=row.namespace, key=row.key, value=row.value or {}, updated_at=row.updated_at)


@router.delete("/{namespace}/{key}")
def delete_item(ctx: V1ContextDep, namespace: str, key: str) -> dict[str, bool]:
    _validate_key(namespace, key)
    with SessionLocal() as session:
        pid = _require_project(session, project_id=ctx.project_id)
        row = (
            session.execute(
                select(StoreItemV1)
                .where(StoreItemV1.project_id == pid)
                .where(StoreItemV1.namespace == namespace)
                .where(StoreItemV1.key == key)
            )
            .scalars()
            .first()
        )
        if row is None:
            return {"ok": True}
        session.delete(row)
        session.commit()
        return {"ok": True}
