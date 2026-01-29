"""Database seed helpers.

This module is intentionally side-effect free at import time.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from omo_platform.db.models import Connection, ConnectionKind, Project


_DEFAULT_PROJECT_NAME = "default"
_CHINOOK_SQLITE_PATH = "/app/infra/chinook/chinook.db"


def ensure_defaults(session: Session) -> None:
    """Ensure the MVP default Project and Chinook Connection exist.

    Idempotent: calling multiple times does not create duplicates.
    """

    project = (
        session.execute(
            select(Project)
            .where(Project.name == _DEFAULT_PROJECT_NAME)
            .order_by(Project.created_at.asc())
        )
        .scalars()
        .first()
    )
    if project is None:
        project = Project(name=_DEFAULT_PROJECT_NAME)
        session.add(project)
        session.flush()  # Populate project.id

    desired_sqlite_path = _CHINOOK_SQLITE_PATH
    desired_readonly = True

    connection = None
    existing = (
        session.execute(select(Connection).where(Connection.project_id == project.id))
        .scalars()
        .all()
    )
    for candidate in existing:
        cfg = candidate.config_json if isinstance(candidate.config_json, dict) else {}
        if candidate.kind == ConnectionKind.SQLITE and cfg.get("sqlite_path") == desired_sqlite_path:
            connection = candidate
            break

    if connection is None:
        session.add(
            Connection(
                project_id=project.id,
                kind=ConnectionKind.SQLITE,
                config_json={"sqlite_path": desired_sqlite_path, "readonly": desired_readonly},
            )
        )
        session.flush()
        return

    # Make sure required config keys exist, but keep any extra keys intact.
    cfg = dict(connection.config_json or {})
    if cfg.get("sqlite_path") != desired_sqlite_path:
        cfg["sqlite_path"] = desired_sqlite_path
    if cfg.get("readonly") is not desired_readonly:
        cfg["readonly"] = desired_readonly
    if cfg != (connection.config_json or {}):
        connection.config_json = cfg


def seed_defaults() -> None:
    """Open a DB session and seed MVP defaults."""

    # Import lazily so importing this module does not require DATABASE_URL.
    from omo_platform.db.session import SessionLocal

    with SessionLocal() as session:
        try:
            ensure_defaults(session)
            session.commit()
        except Exception:
            session.rollback()
            raise


__all__ = ["ensure_defaults", "seed_defaults"]
