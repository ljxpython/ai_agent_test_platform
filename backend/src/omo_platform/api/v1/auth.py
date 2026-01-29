from __future__ import annotations

# pyright: reportMissingImports=false

import os
from dataclasses import dataclass
from typing import Annotated

import jwt
from fastapi import Depends, Header, HTTPException


@dataclass(frozen=True)
class V1Context:
    user_id: str
    project_id: str


def _get_jwt_secret() -> str:
    secret = os.getenv("PLATFORM_JWT_SECRET")
    if not secret:
        raise RuntimeError("PLATFORM_JWT_SECRET environment variable is required")
    return secret


def _decode_bearer_token(authz: str) -> str:
    parts = (authz or "").split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = parts[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return token


def _verify_jwt(token: str) -> V1Context:
    try:
        payload = jwt.decode(
            token,
            _get_jwt_secret(),
            algorithms=["HS256"],
            options={"require": ["sub", "project_id"]},
            leeway=60,
        )
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Unauthorized")

    sub = payload.get("sub")
    project_id = payload.get("project_id")
    if not isinstance(sub, str) or not sub:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if not isinstance(project_id, str) or not project_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return V1Context(user_id=sub, project_id=project_id)


def get_v1_context(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> V1Context:
    if not authorization:
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = _decode_bearer_token(authorization)
    return _verify_jwt(token)


V1ContextDep = Annotated[V1Context, Depends(get_v1_context)]
