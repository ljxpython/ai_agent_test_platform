# pyright: reportMissingImports=false

import asyncio
import json
import os
import sys
import uuid
import datetime
from collections.abc import AsyncIterator

import pytest


# Allow importing backend code when tests are invoked directly.
_BACKEND_SRC = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "backend", "src")
)
if _BACKEND_SRC not in sys.path:
    sys.path.insert(0, _BACKEND_SRC)


def _aiter_from_list(items: list[str] | list[object]) -> AsyncIterator:
    async def _gen() -> AsyncIterator:
        for item in items:
            yield item

    return _gen()


async def _collect_async(aiter: AsyncIterator) -> list:
    out = []
    async for x in aiter:
        out.append(x)
    return out


def test_canonical_seq_is_monotonic_starting_at_1() -> None:
    from omo_platform.langgraph_proxy.stream_adapter import iter_langgraph_events_to_canonical

    lines = [
        "event: metadata",
        'data: {"run_id":"run-1"}',
        "",
        "event: values",
        'data: {"data":{"messages":[{"role":"assistant","content":"hi"}]}}',
        "",
    ]

    evts = asyncio.run(
        _collect_async(iter_langgraph_events_to_canonical(_aiter_from_list(lines)))
    )
    assert [e["seq"] for e in evts] == [1, 2, 3]
    assert [e["run_id"] for e in evts] == ["run-1", "run-1", "run-1"]


def _parse_sse_data(chunk: bytes) -> dict:
    text = chunk.decode("utf-8")
    assert text.startswith("data: ")
    payload = text[len("data: ") :].strip()
    return json.loads(payload)


def test_agui_gateway_dedupes_by_run_id_and_seq() -> None:
    from omo_platform.agui_gateway.router import _iter_canonical_events_to_agui_sse
    from omo_platform.events.canonical import (
        EVENT_MESSAGE_TEXT,
        EVENT_RUN_FINISHED,
        EVENT_RUN_STARTED,
        new_event,
    )

    run_id = "run-1"
    canonical_events = [
        new_event(1, EVENT_RUN_STARTED, {}, run_id=run_id),
        new_event(2, EVENT_MESSAGE_TEXT, {"content": "hi"}, run_id=run_id),
        # Duplicate canonical event: should be ignored by the gateway.
        new_event(2, EVENT_MESSAGE_TEXT, {"content": "hi"}, run_id=run_id),
        new_event(3, EVENT_RUN_FINISHED, {}, run_id=run_id),
    ]

    chunks = asyncio.run(
        _collect_async(
            _iter_canonical_events_to_agui_sse(_aiter_from_list(canonical_events))
        )
    )
    payloads = [_parse_sse_data(c) for c in chunks]
    assert [p["type"] for p in payloads] == [
        "RUN_STARTED",
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "RUN_FINISHED",
    ]
    assert payloads[1]["messageId"] == "msg-1"
    assert payloads[2]["messageId"] == "msg-1"
    assert payloads[3]["messageId"] == "msg-1"


class _FakeScalarResult:
    def __init__(self, obj):
        self._obj = obj

    def first(self):
        return self._obj


class _FakeExecuteResult:
    def __init__(self, obj):
        self._obj = obj

    def scalars(self):
        return _FakeScalarResult(self._obj)


class _FakeSession:
    def __init__(self, results: list[object]):
        self._results = list(results)
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, stmt):
        self.executed.append(stmt)
        assert self._results, "unexpected extra session.execute()"
        return _FakeExecuteResult(self._results.pop(0))


def test_run_detail_falls_back_to_audit_status(monkeypatch: pytest.MonkeyPatch) -> None:
    from omo_platform.api import platform_router
    from omo_platform.db.models import Project, RunAudit, RunAuditStatus

    project = Project(
        id=uuid.uuid4(),
        name="proj-1",
        created_at=datetime.datetime.now(datetime.timezone.utc),
    )
    audit = RunAudit(
        id=uuid.uuid4(),
        project_id=project.id,
        user_id="u1",
        thread_id="t1",
        run_id="run-1",
        parent_run_id=None,
        status=RunAuditStatus.SUCCEEDED,
        sql_text=None,
        row_count=None,
        duration_ms=None,
        error_summary=None,
        created_at=datetime.datetime.now(datetime.timezone.utc),
    )

    fake_session = _FakeSession([project, audit])
    monkeypatch.setattr(platform_router, "SessionLocal", lambda: fake_session)

    out = platform_router.get_run(
        run_id="run-1",
        x_user_id="u1",
        x_project_id="proj-1",
    )
    assert out["run_id"] == "run-1"
    assert out["status"] == "succeeded"

    # Ensure we actually queried Project then RunAudit (audit fallback path).
    assert len(fake_session.executed) == 2
    assert fake_session.executed[0].column_descriptions[0]["entity"] is Project
    assert fake_session.executed[1].column_descriptions[0]["entity"] is RunAudit
    assert "run_audit" in str(fake_session.executed[1])
    assert "run_id" in str(fake_session.executed[1])
