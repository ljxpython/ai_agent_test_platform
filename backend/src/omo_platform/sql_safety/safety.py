# ruff: noqa
# NOTE: This module is intentionally strict and SQLite-only.
"""SQL safety helpers for SQLite read-only querying.

Constraints (intentionally strict for early safety hardening):
- Accept exactly one statement.
- Allow only SELECT or WITH ... SELECT.
- Reject any SQL containing forbidden keywords (case-insensitive token check).
- Enforce a LIMIT (add if missing; clamp if larger).

This module is pure library code: no side effects at import time.
"""

from __future__ import annotations

import importlib
import re
from typing import Optional


class SqlSafetyError(Exception):
    """Raised when an input SQL query violates safety rules."""


# SQLite-specific denylist for early safety.
# Note: We intentionally include transaction/control statements and extension loading.
_FORBIDDEN_KEYWORDS = (
    "PRAGMA",
    "ATTACH",
    "DETACH",
    "INSERT",
    "UPDATE",
    "DELETE",
    "CREATE",
    "ALTER",
    "DROP",
    "REPLACE",
    "VACUUM",
    "ANALYZE",
    "BEGIN",
    "COMMIT",
    "ROLLBACK",
    "TRANSACTION",
    "SAVEPOINT",
    "RELEASE",
    "LOAD_EXTENSION",
)

# Word-boundary match; avoid matching substrings like "DROPLET".
_FORBIDDEN_RE = re.compile(r"(?i)\b(" + "|".join(_FORBIDDEN_KEYWORDS) + r")\b")


def _strip_quoted_regions(sql: str) -> str:
    """Remove quoted regions so keyword scan ignores literals/quoted identifiers.

    This is NOT a full SQL lexer; it is a small, conservative helper to reduce
    false positives when scanning for forbidden keywords.
    """

    out: list[str] = []
    i = 0
    n = len(sql)

    in_single = False
    in_double = False
    in_backtick = False
    in_bracket = False

    while i < n:
        ch = sql[i]

        if in_single:
            # SQLite escapes single quotes by doubling them: ''
            if ch == "'":
                if i + 1 < n and sql[i + 1] == "'":
                    i += 2
                    continue
                in_single = False
            i += 1
            continue

        if in_double:
            # SQLite escapes double quotes by doubling them: ""
            if ch == '"':
                if i + 1 < n and sql[i + 1] == '"':
                    i += 2
                    continue
                in_double = False
            i += 1
            continue

        if in_backtick:
            if ch == "`":
                in_backtick = False
            i += 1
            continue

        if in_bracket:
            if ch == "]":
                in_bracket = False
            i += 1
            continue

        if ch == "'":
            in_single = True
            out.append(" ")
            i += 1
            continue
        if ch == '"':
            in_double = True
            out.append(" ")
            i += 1
            continue
        if ch == "`":
            in_backtick = True
            out.append(" ")
            i += 1
            continue
        if ch == "[":
            in_bracket = True
            out.append(" ")
            i += 1
            continue

        out.append(ch)
        i += 1

    return "".join(out)


def _find_forbidden_keyword(sql: str) -> Optional[str]:
    stripped = _strip_quoted_regions(sql)
    match = _FORBIDDEN_RE.search(stripped)
    return match.group(1).upper() if match else None


def validate_and_rewrite_sql(sql: str, max_rows: int = 200) -> str:
    """Validate and rewrite an input SQL query for safe read-only execution.

    Returns a rewritten SQL string (SQLite dialect) with LIMIT enforced.

    Raises:
        SqlSafetyError: when the SQL is rejected or cannot be safely rewritten.
    """

    if not isinstance(sql, str) or not sql.strip():
        raise SqlSafetyError("SQL must be a non-empty string")
    if not isinstance(max_rows, int) or max_rows <= 0:
        raise SqlSafetyError("max_rows must be a positive int")

    forbidden = _find_forbidden_keyword(sql)
    if forbidden:
        raise SqlSafetyError(f"Forbidden keyword detected: {forbidden}")

    # Keep these names defined for linters/type-checkers even though we
    # fail fast when sqlglot isn't available.
    sqlglot = None
    exp = None
    ParseError = Exception

    try:
        sqlglot = importlib.import_module("sqlglot")
        # sqlglot v25 exposes expressions as `sqlglot.expressions` and also
        # re-exports it as `sqlglot.exp`. Older code may try `sqlglot.exp` as a module.
        # Import in a version-tolerant way.
        try:
            exp = importlib.import_module("sqlglot.expressions")
        except Exception:
            exp = getattr(sqlglot, "exp")
        errors_mod = importlib.import_module("sqlglot.errors")
        ParseError = getattr(errors_mod, "ParseError", Exception)
    except Exception as e:  # pragma: no cover
        # Dependencies are wired in a later atomic task.
        raise SqlSafetyError(f"sqlglot is required for SQL validation: {e}") from e

    try:
        statements = sqlglot.parse(sql, read="sqlite")
    except ParseError as e:
        raise SqlSafetyError(f"Invalid SQL (parse failed): {e}") from e

    if len(statements) != 1:
        raise SqlSafetyError("Only a single SQL statement is allowed")

    root = statements[0]

    # Allow only SELECT or WITH ... SELECT.
    select = None
    if isinstance(root, exp.Select):
        select = root
    elif isinstance(root, exp.With) and isinstance(root.this, exp.Select):
        select = root.this

    if select is None:
        raise SqlSafetyError("Only SELECT or WITH ... SELECT statements are allowed")

    # Enforce LIMIT.
    limit_expr = select.args.get("limit")
    if limit_expr is None:
        # sqlglot v25 uses `Limit(expression=...)` for the row count.
        select.set("limit", exp.Limit(expression=exp.Literal.number(max_rows)))
    else:
        # If LIMIT is not a simple integer literal, force it to max_rows.
        limit_value: Optional[int] = None
        try:
            limit_count = getattr(limit_expr, "expression", None)
            # `exp` is imported dynamically; keep this check duck-typed so
            # static analyzers don't get confused.
            if limit_count is not None and getattr(limit_count, "is_int", False):
                raw = getattr(limit_count, "this", None)
                if raw is not None:
                    limit_value = int(raw)
        except Exception:
            limit_value = None

        if limit_value is None or limit_value > max_rows:
            limit_expr.set("expression", exp.Literal.number(max_rows))

    return root.sql(dialect="sqlite")
