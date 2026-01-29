# pyright: reportMissingImports=false

import os
import re
import sys
import importlib


import pytest


# Allow importing backend code when tests are invoked directly.
_BACKEND_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backend", "src"))  # noqa: E402
if _BACKEND_SRC not in sys.path:
    sys.path.insert(0, _BACKEND_SRC)  # noqa: E402


def _sql_safety_module():
    # Import after sys.path adjustment so `omo_platform` resolves when pytest is
    # invoked without PYTHONPATH.
    return importlib.import_module("omo_platform.sql_safety")


_SQL_SAFETY = _sql_safety_module()
SqlSafetyError = _SQL_SAFETY.SqlSafetyError
validate_and_rewrite_sql = _SQL_SAFETY.validate_and_rewrite_sql


def _assert_limit(sql: str, expected: int) -> None:
    assert re.search(rf"(?i)\bLIMIT\s+{expected}\b", sql), sql


def test_validate_and_rewrite_sql_accepts_select_and_enforces_limit() -> None:
    rewritten = validate_and_rewrite_sql("SELECT 1")
    assert "SELECT" in rewritten.upper()
    _assert_limit(rewritten, 200)


def test_validate_and_rewrite_sql_clamps_limit_to_max_rows() -> None:
    rewritten = validate_and_rewrite_sql("SELECT 1 LIMIT 999999")
    _assert_limit(rewritten, 200)


@pytest.mark.parametrize(
    "sql",
    [
        # SQLite control / metadata
        "PRAGMA user_version",
        "ATTACH DATABASE 'file.db' AS ext",
        "DETACH DATABASE ext",
        # DML
        "INSERT INTO t(a) VALUES (1)",
        "UPDATE t SET a = 1",
        "DELETE FROM t",
        "REPLACE INTO t(a) VALUES (1)",
        # DDL / maintenance
        "CREATE TABLE t(a INTEGER)",
        "ALTER TABLE t ADD COLUMN b TEXT",
        "DROP TABLE t",
        "VACUUM",
        "ANALYZE",
        # Transactions
        "BEGIN TRANSACTION",
        "COMMIT",
        "ROLLBACK",
        "TRANSACTION",
        "SAVEPOINT s1",
        "RELEASE s1",
        # Extension loading
        "SELECT load_extension('x')",
    ],
)
def test_validate_and_rewrite_sql_rejects_forbidden_keywords(sql: str) -> None:
    with pytest.raises(SqlSafetyError):
        validate_and_rewrite_sql(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "select load_extension('x')",
        "ATTACH DATABASE 'file.db' AS ext",
        "pRaGmA table_info('t')",
    ],
)
def test_validate_and_rewrite_sql_rejects_common_bypass_attempts(sql: str) -> None:
    with pytest.raises(SqlSafetyError):
        validate_and_rewrite_sql(sql)
