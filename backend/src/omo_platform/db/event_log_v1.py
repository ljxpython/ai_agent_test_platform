"""V1 canonical event log utilities.

Postgres is the fact source. Redis (if used later) is only a wake-up signal.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from omo_platform.db.models_v1 import RunEventV1, RunSeqCounterV1


def append_event(
    session: Session,
    *,
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    event_type: str,
    payload: dict,
    dedupe_key: str | None = None,
) -> int:
    """Append a canonical event and return its seq.

    Guarantees (MVP):
    - seq is monotonically increasing and gap-free per (project_id, run_id)
    - if dedupe_key is provided and already exists, return the existing seq
    """

    if dedupe_key:
        existing = (
            session.execute(
                select(RunEventV1)
                .where(RunEventV1.project_id == project_id)
                .where(RunEventV1.run_id == run_id)
                .where(RunEventV1.dedupe_key == dedupe_key)
            )
            .scalars()
            .first()
        )
        if existing is not None:
            return int(existing.seq)

    # Ensure counter row exists.
    session.execute(
        insert(RunSeqCounterV1)
        .values(project_id=project_id, run_id=run_id, next_seq=1)
        .on_conflict_do_nothing(index_elements=["project_id", "run_id"])
    )

    # Lock counter row and allocate seq.
    counter = (
        session.execute(
            select(RunSeqCounterV1)
            .where(RunSeqCounterV1.project_id == project_id)
            .where(RunSeqCounterV1.run_id == run_id)
            .with_for_update()
        )
        .scalars()
        .one()
    )
    seq = int(counter.next_seq)

    evt = RunEventV1(
        project_id=project_id,
        run_id=run_id,
        seq=seq,
        ts=datetime.datetime.now(datetime.timezone.utc),
        type=event_type,
        dedupe_key=dedupe_key,
        payload=payload or {},
    )

    try:
        session.add(evt)
        session.flush()
    except IntegrityError:
        # Concurrent insert with same dedupe_key; return the existing seq.
        session.rollback()
        if dedupe_key:
            existing = (
                session.execute(
                    select(RunEventV1)
                    .where(RunEventV1.project_id == project_id)
                    .where(RunEventV1.run_id == run_id)
                    .where(RunEventV1.dedupe_key == dedupe_key)
                )
                .scalars()
                .first()
            )
            if existing is not None:
                return int(existing.seq)
        raise

    counter.next_seq = seq + 1
    session.flush()
    return seq
