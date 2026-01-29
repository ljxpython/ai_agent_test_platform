"""MVP canonical event schema.

This module defines the minimal, stable event envelope used across the platform.
It is intentionally dependency-free (stdlib only) and has no import-time effects.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Final, TypedDict


# ==================== Canonical event type names (MVP) ====================

EVENT_RUN_STARTED: Final[str] = "run.started"
EVENT_RUN_FINISHED: Final[str] = "run.finished"
EVENT_RUN_ERROR: Final[str] = "run.error"
EVENT_MESSAGE_TEXT: Final[str] = "message.text"
EVENT_UI_CARD: Final[str] = "ui.card"


class CanonicalEvent(TypedDict):
    """A minimal, JSON-serializable event envelope.

    - ts is an ISO8601 UTC timestamp (e.g. "2026-01-26T12:34:56.789Z").
    - payload is arbitrary JSON-like content; keep it object-only in MVP.
    """

    seq: int
    ts: str
    thread_id: str | None
    run_id: str | None
    type: str
    payload: dict[str, object]


def _utc_now_iso8601() -> str:
    """Return current UTC time as ISO8601 with a 'Z' suffix."""

    # datetime.isoformat() uses '+00:00'; normalize to 'Z' for canonical UTC.
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def new_event(
    seq: int,
    type: str,
    payload: dict[str, object],
    *,
    thread_id: str | None = None,
    run_id: str | None = None,
) -> CanonicalEvent:
    """Create a canonical event with an auto-filled UTC timestamp.

    Enforces seq >= 1 to avoid ambiguous 'zero' events.
    """

    if seq < 1:
        raise ValueError(f"seq must be >= 1, got {seq}")

    return {
        "seq": seq,
        "ts": _utc_now_iso8601(),
        "thread_id": thread_id,
        "run_id": run_id,
        "type": type,
        "payload": payload,
    }
