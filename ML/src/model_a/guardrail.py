"""
SQL security guardrail for Module A (Text-to-SQL).

Rejects any statement that is not a pure, single SELECT.
Called by inference.py before any SQL reaches the ALIBI orchestrator.

References:
  - P2SQL injection attacks: Pedro et al. (2025)
  - sqlglot AST-based validation (dialect-agnostic)
"""

from typing import Optional, Tuple

import sqlglot
from sqlglot import exp

# AST node types that indicate non-SELECT operations
_FORBIDDEN_NODE_TYPES = (
    exp.Drop, exp.Delete, exp.Insert, exp.Update, exp.Create,
    exp.Alter, exp.TruncateTable, exp.Command, exp.Transaction,
    exp.Commit, exp.Rollback, exp.Grant, exp.Revoke,
)

# A read is a SELECT or a set operation over SELECTs. INTERSECT and EXCEPT are siblings of UNION in
# sqlglot (not subclasses), and Spider-style questions need all three — measured 2026-10-11, rejecting
# them blocked ~5% of correct answers. Writes inside an operand are still caught by the node walk below.
_READ_STATEMENTS = (exp.Select, exp.Union, exp.Intersect, exp.Except)

# Keyword-level pre-filter (belt-and-suspenders before AST parsing)
_FORBIDDEN_KEYWORDS = frozenset([
    "drop", "delete", "insert", "update", "alter", "truncate",
    "exec", "execute", "create", "grant", "revoke",
    "attach", "detach", "pragma",  # SQLite-specific DDL
])


def validate_sql(sql: str) -> Tuple[bool, Optional[str]]:
    """
    Validate that sql is a single safe SELECT statement.

    Returns:
        (True, None) if the SQL is safe.
        (False, reason) if it should be rejected.
    """
    if not sql or not sql.strip():
        return False, "empty SQL"

    # Pre-filter: keyword scan before AST parsing
    tokens = sql.lower().split()
    for token in tokens:
        clean = token.strip(";,()")
        if clean in _FORBIDDEN_KEYWORDS:
            return False, f"forbidden keyword: {clean!r}"

    # Reject multiple statements (semicolon splitting)
    try:
        statements = sqlglot.parse(sql, dialect="sqlite")
    except Exception as exc:
        return False, f"parse error: {exc}"

    if len(statements) != 1:
        return False, f"expected 1 statement, got {len(statements)}"

    stmt = statements[0]
    if not isinstance(stmt, _READ_STATEMENTS):
        return False, f"not a SELECT: {type(stmt).__name__}"

    # Walk the AST for any forbidden node types
    for node in stmt.walk():
        if isinstance(node, _FORBIDDEN_NODE_TYPES):
            return False, f"forbidden AST node: {type(node).__name__}"

    return True, None
