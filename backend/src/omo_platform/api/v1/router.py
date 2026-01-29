from __future__ import annotations

# pyright: reportMissingImports=false

from fastapi import APIRouter

from omo_platform.api.v1.health import router as health_router


router = APIRouter(prefix="/api/v1")

router.include_router(health_router)
