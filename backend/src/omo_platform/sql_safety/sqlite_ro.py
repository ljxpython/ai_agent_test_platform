"""SQLite read-only execution helpers.

This module provides small, dependency-free helpers for executing SELECT-only
queries against a SQLite database in read-only mode.

Requirements enforced here:
- Open via SQLite URI with mode=ro.
- Force connection-level read-only behavior via PRAGMA query_only=ON.
- Validate/rewrite user SQL via validate_and_rewrite_sql before execution.
- Return columns, rows (truncated), and duration_ms.

No side effects at import time.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from typing import Optional, Sequence
from urllib.parse import quote

from .safety import validate_and_rewrite_sql


@dataclass(frozen=True)
class SqliteQueryResult:
    columns: list[str]
    rows: list[list[object]]
    duration_ms: int


def _to_sqlite_ro_uri(db_path: str) -> str:
    """Convert a filesystem path into a SQLite URI opened in read-only mode."""

    if not isinstance(db_path, str) or not db_path:
        raise ValueError("db_path must be a non-empty string")

    # If the caller already provided a URI, keep it but ensure mode=ro exists.
    # We keep this conservative: if a URI is passed without mode=ro, reject it.
    if db_path.startswith("file:"):
        if "mode=ro" not in db_path:
            raise ValueError("SQLite URI must include mode=ro")
        return db_path

    # Quote path for URI usage while keeping slashes readable.
    quoted_path = quote(db_path, safe="/\\")
    return f"file:{quoted_path}?mode=ro"


def connect_readonly(db_path: str, *, timeout_s: float = 5.0) -> sqlite3.Connection:
    """Open a SQLite connection in read-only mode and enable query_only."""

    uri = _to_sqlite_ro_uri(db_path)
    conn = sqlite3.connect(uri, uri=True, timeout=timeout_s)
    try:
        # Enforce read-only behavior at the SQLite engine level.
        conn.execute("PRAGMA query_only=ON")
    except Exception:
        conn.close()
        raise
    return conn


def execute_readonly_query(
    db_path: str,
    sql: str,
    *,
    params: Optional[Sequence[object]] = None,
    max_rows: int = 200,
    timeout_s: float = 5.0,
) -> SqliteQueryResult:
    """Validate + execute a read-only SQL query against a SQLite DB.

    Args:
        db_path: Path to the sqlite file.
        sql: User-provided SQL query (will be validated/rewritten).
        params: Optional sqlite parameter sequence.
        max_rows: Maximum number of rows returned.
        timeout_s: Connection timeout.
    """

    rewritten_sql = validate_and_rewrite_sql(sql, max_rows=max_rows)

    start = time.monotonic()
    conn = connect_readonly(db_path, timeout_s=timeout_s)
    try:
        cur = conn.cursor()
        try:
            if params is None:
                cur.execute(rewritten_sql)
            else:
                cur.execute(rewritten_sql, params)

            columns = [desc[0] for desc in (cur.description or [])]

            # Clamp to max_rows even if validation/rewrite is bypassed upstream.
            fetched = cur.fetchmany(max_rows)
            rows: list[list[object]] = [list(row) for row in fetched]
        finally:
            cur.close()
    finally:
        conn.close()

    duration_ms = int((time.monotonic() - start) * 1000)
    return SqliteQueryResult(columns=columns, rows=rows, duration_ms=duration_ms)


@dataclass(frozen=True)
class SqliteTableSchema:
    name: str
    columns: list[str]


def introspect_schema(
    db_path: str,
    *,
    include_sqlite_internal: bool = False,
    timeout_s: float = 5.0,
) -> list[SqliteTableSchema]:
    """List tables and their columns using sqlite_master and PRAGMA table_info."""

    def quote_ident(identifier: str) -> str:
        # Defensive quoting for identifiers coming from sqlite_master.
        return '"' + identifier.replace('"', '""') + '"'

    conn = connect_readonly(db_path, timeout_s=timeout_s)
    try:
        cur = conn.cursor()
        try:
            table_sql = "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            cur.execute(table_sql)
            table_names = [row[0] for row in cur.fetchall()]

            if not include_sqlite_internal:
                table_names = [n for n in table_names if not n.startswith("sqlite_")]

            schemas: list[SqliteTableSchema] = []
            for table_name in table_names:
                # Note: SQLite does not reliably support bound parameters for PRAGMA.
                cur.execute(f"PRAGMA table_info({quote_ident(table_name)})")
                # table_info columns: cid, name, type, notnull, dflt_value, pk
                cols = [row[1] for row in cur.fetchall()]
                schemas.append(SqliteTableSchema(name=table_name, columns=cols))
            return schemas
        finally:
            cur.close()
    finally:
        conn.close()
