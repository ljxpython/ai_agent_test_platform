from __future__ import annotations

# pyright: reportMissingImports=false

from fastapi import APIRouter

from omo_platform.api.v1.health import router as health_router
from omo_platform.api.v1.threads import router as threads_router
from omo_platform.api.v1.runs import router as runs_router


router = APIRouter(prefix="/api/v1")

router.include_router(health_router)
router.include_router(threads_router)
router.include_router(runs_router)
