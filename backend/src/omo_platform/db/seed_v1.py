"""V1 database seed helpers (projects table).

This module is intentionally side-effect free at import time.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from omo_platform.db.models_v1 import ProjectV1


DEFAULT_PROJECT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
DEFAULT_PROJECT_NAME = "default"


def ensure_v1_defaults(session: Session) -> None:
    project = (
        session.execute(
            select(ProjectV1).where(ProjectV1.project_id == DEFAULT_PROJECT_ID)
        )
        .scalars()
        .first()
    )
    if project is None:
        session.add(ProjectV1(project_id=DEFAULT_PROJECT_ID, name=DEFAULT_PROJECT_NAME))
        session.flush()


def seed_v1_defaults() -> None:
    from omo_platform.db.session import SessionLocal

    with SessionLocal() as session:
        try:
            ensure_v1_defaults(session)
            session.commit()
        except Exception:
            session.rollback()
            raise


__all__ = ["DEFAULT_PROJECT_ID", "DEFAULT_PROJECT_NAME", "ensure_v1_defaults", "seed_v1_defaults"]
