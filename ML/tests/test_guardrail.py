"""Module A's guardrail: reads pass (including every set operation), anything else does not.

    cd ML/src && python -m pytest ../tests -q
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from model_a.guardrail import validate_sql  # noqa: E402


@pytest.mark.parametrize("sql", [
    "SELECT a FROM t",
    "WITH x AS (SELECT a FROM t) SELECT a FROM x",
    "SELECT a FROM t UNION SELECT a FROM u",
    "SELECT a FROM t UNION ALL SELECT a FROM u",
    "SELECT a FROM t INTERSECT SELECT a FROM u",
    "SELECT a FROM t EXCEPT SELECT a FROM u",
])
def test_reads_pass(sql):
    assert validate_sql(sql) == (True, None)


@pytest.mark.parametrize("sql", [
    "",
    "DROP TABLE t",
    "DELETE FROM t",
    "SELECT 1; DROP TABLE t",
    "INSERT INTO t VALUES (1)",
    "SELECT a FROM t INTERSECT DELETE FROM u",
    "SELECT a FROM t UNION SELECT a FROM u; DROP TABLE t",
    "PRAGMA table_info(t)",
])
def test_everything_else_is_rejected(sql):
    ok, reason = validate_sql(sql)
    assert not ok and reason
