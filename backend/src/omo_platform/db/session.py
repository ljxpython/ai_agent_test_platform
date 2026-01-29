"""SQLAlchemy engine/session helpers.

Requires:
- DATABASE_URL: SQLAlchemy database URL
"""

# pyright: reportMissingImports=false

from __future__ import annotations

import os
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


def get_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL environment variable is required")
    return url


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    # pool_pre_ping helps avoid stale connections after network blips.
    return create_engine(get_database_url(), pool_pre_ping=True)


@lru_cache(maxsize=1)
def _get_sessionmaker() -> sessionmaker[Session]:
    # 延迟绑定 engine，避免 import 时就要求 DATABASE_URL。
    return sessionmaker(
        bind=get_engine(),
        autoflush=False,
        expire_on_commit=False,
    )


def SessionLocal() -> Session:
    """Create a SQLAlchemy ORM Session.

    Note: this is intentionally a callable (not a module-level sessionmaker)
    so importing this module does not require DATABASE_URL.
    """

    return _get_sessionmaker()()
