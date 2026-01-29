"""FastAPI application entrypoint.

This module is intentionally minimal and side-effect free beyond constructing
the FastAPI app and registering routes.
"""

# pyright: reportMissingImports=false

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi import Request
from starlette.responses import PlainTextResponse

from omo_platform.agui_gateway.router import router as agui_gateway_router
from omo_platform.langgraph_proxy.router import router as langgraph_proxy_router
from omo_platform.api.platform_router import router as platform_router
from omo_platform.api.v1.router import router as v1_router


app = FastAPI()


def _is_truthy_env(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes"}


@app.middleware("http")
async def _require_internal_proxy_header(request: Request, call_next):
    # When enabled, enforce that all control-plane API routes are only reachable
    # via the internal reverse proxy that injects the boundary header.
    if _is_truthy_env(os.getenv("REQUIRE_INTERNAL_PROXY")):
        path = request.url.path

        # Local health checks must stay unauthenticated.
        # `/api/v1/*` is intended to be a public API surface, so never gate it.
        if path != "/healthz" and path.startswith("/api/") and not path.startswith("/api/v1/"):
            if request.headers.get("X-Internal-Proxy") != "1":
                return PlainTextResponse("Forbidden", status_code=403)

    return await call_next(request)


app.include_router(agui_gateway_router)
app.include_router(langgraph_proxy_router)
app.include_router(platform_router)
app.include_router(v1_router)


@app.get("/healthz")
def healthz() -> dict[str, bool]:
    return {"ok": True}
