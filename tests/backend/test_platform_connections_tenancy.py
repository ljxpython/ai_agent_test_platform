# pyright: reportMissingImports=false

import datetime
import os
import sys
import uuid

import pytest


# Allow importing backend code when tests are invoked directly.
_BACKEND_SRC = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "backend", "src")
)
if _BACKEND_SRC not in sys.path:
    sys.path.insert(0, _BACKEND_SRC)


class _FakeScalarResult:
    def __init__(self, obj):
        self._obj = obj

    def first(self):
        return self._obj

    def all(self):
        return list(self._obj)


class _FakeExecuteResult:
    def __init__(self, obj):
        self._obj = obj

    def scalars(self):
        return _FakeScalarResult(self._obj)


class _FakeSession:
    def __init__(self, *, project_by_name: dict[str, object], connections: list[object]):
        self._project_by_name = dict(project_by_name)
        self._connections = list(connections)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, stmt):
        # Return a Project for auth, then a project-scoped Connection list.
        entity = stmt.column_descriptions[0]["entity"]

        from omo_platform.db.models import Connection, Project

        if entity is Project:
            # _require_auth uses Project.name == <header-value>.
            name = None
            for crit in getattr(stmt, "_where_criteria", ()):
                left = getattr(crit, "left", None)
                if left is not None and getattr(left, "name", None) == "name" and str(left) == "project.name":
                    name = crit.right.value
                    break
            if name is None:
                return _FakeExecuteResult(None)
            return _FakeExecuteResult(self._project_by_name.get(str(name)))

        if entity is Connection:
            project_id = None
            for crit in getattr(stmt, "_where_criteria", ()):
                left = getattr(crit, "left", None)
                if (
                    left is not None
                    and getattr(left, "name", None) == "project_id"
                    and str(left) == "connection.project_id"
                ):
                    project_id = crit.right.value
                    break

            conns = (
                [c for c in self._connections if getattr(c, "project_id") == project_id]
                if project_id is not None
                else list(self._connections)
            )
            return _FakeExecuteResult(conns)

        raise AssertionError(f"unexpected entity in execute(): {entity!r}")


def test_platform_connections_are_project_scoped(monkeypatch: pytest.MonkeyPatch) -> None:
    from omo_platform.api import platform_router
    from omo_platform.db.models import Connection, ConnectionKind, Project

    now = datetime.datetime.now(datetime.timezone.utc)

    p1 = Project(id=uuid.uuid4(), name="proj-1", created_at=now)
    p2 = Project(id=uuid.uuid4(), name="proj-2", created_at=now)

    c1 = Connection(
        id=uuid.uuid4(),
        project_id=p1.id,
        kind=ConnectionKind.SQLITE,
        config_json={"sqlite_path": "/tmp/a.db", "readonly": True},
        created_at=now,
    )
    c2 = Connection(
        id=uuid.uuid4(),
        project_id=p2.id,
        kind=ConnectionKind.SQLITE,
        config_json={"sqlite_path": "/tmp/b.db", "readonly": True},
        created_at=now,
    )

    def _session_local():
        return _FakeSession(
            project_by_name={p1.name: p1, p2.name: p2},
            connections=[c1, c2],
        )

    monkeypatch.setattr(platform_router, "SessionLocal", _session_local)

    out1 = platform_router.list_connections(x_user_id="u1", x_project_id=p1.name)
    assert [o["id"] for o in out1] == [str(c1.id)]
    assert out1[0]["kind"] == "sqlite"
    assert out1[0]["config_json"]["sqlite_path"] == "/tmp/a.db"

    out2 = platform_router.list_connections(x_user_id="u1", x_project_id=p2.name)
    assert [o["id"] for o in out2] == [str(c2.id)]
