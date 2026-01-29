from __future__ import annotations

# pyright: reportMissingImports=false

from fastapi import APIRouter


router = APIRouter()


@router.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}
