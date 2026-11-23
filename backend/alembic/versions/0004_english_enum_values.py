"""english enum values

Revision ID: 0004_english_enum_values
Revises: 0003_verification_code_attempts
Create Date: 2026-07-30

Renames the values of the three Spanish-valued Postgres enum types to English,
as part of the platform-wide English refactor. The Python-side enums in `app/schemas/charts.py` and
`app/schemas/datasets.py` and the contracts in `contracts/*.yaml` were changed in
the same commit; this revision brings the database into agreement.

  column_type          Texto → Text, Número → Number, Número entero → Whole number,
                       Fecha → Date, Porcentaje → Percentage, Booleano → Boolean
  inference_confidence alta → high, media → medium, baja → low
  chart_type           barras → bar, lineas → line, dispersion → scatter,
                       torta → pie, histograma → histogram, calor → heatmap,
                       tabla → table

**Why `ALTER TYPE ... RENAME VALUE` and not a new type + cast.** Renaming a label
rewrites nothing: existing rows keep pointing at the same internal enum member, so
`dataset_columns.type`, `dataset_schemas.confidence`, `dataset_columns.confidence`,
`saved_charts.chart_type` and `dashboard_charts.chart_type` all keep their data with
no table rewrite and no downtime. Creating a parallel type would mean an
`ALTER COLUMN ... TYPE ... USING` on every dependent column — a rewrite, plus five
places to get the mapping right. Requires PostgreSQL 10+; this instance is 16.14.

**Column defaults.** Two columns default to `'media'::inference_confidence`.
Postgres stores that default as a reference to the enum member, not as text, so a
rename carries it over — but the defaults are re-declared explicitly at the end
anyway. It costs nothing and removes the need to trust that behaviour, since this
migration cannot be rehearsed against the real database from here (private RDS
behind the Zscaler proxy).

**Idempotence.** Each rename is guarded on the old label still existing, so
re-running against a partially-migrated database is safe.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0004_english_enum_values"
down_revision = "0003_verification_code_attempts"
branch_labels = None
depends_on = None


# (type name, [(old label, new label), ...])
_RENAMES: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "column_type",
        [
            ("Texto", "Text"),
            ("Número", "Number"),
            ("Número entero", "Whole number"),
            ("Fecha", "Date"),
            ("Porcentaje", "Percentage"),
            ("Booleano", "Boolean"),
        ],
    ),
    (
        "inference_confidence",
        [("alta", "high"), ("media", "medium"), ("baja", "low")],
    ),
    (
        "chart_type",
        [
            ("barras", "bar"),
            ("lineas", "line"),
            ("dispersion", "scatter"),
            ("torta", "pie"),
            ("histograma", "histogram"),
            ("calor", "heatmap"),
            ("tabla", "table"),
        ],
    ),
]

# Columns whose default references `inference_confidence`, re-declared after the
# rename. (table, column, new default label)
_DEFAULTS = [
    ("dataset_schemas", "confidence", "medium"),
    ("dataset_columns", "confidence", "medium"),
]


def _rename(type_name: str, old: str, new: str) -> None:
    """Rename one enum label, skipping it if `old` is already gone.

    The DO block makes the migration re-runnable: a half-applied state (possible
    if an earlier run was interrupted) resumes instead of failing on the labels
    that already moved.
    """
    op.execute(
        f"""
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_enum e
            JOIN pg_type t ON t.oid = e.enumtypid
            WHERE t.typname = '{type_name}' AND e.enumlabel = '{old}'
          ) THEN
            ALTER TYPE {type_name} RENAME VALUE '{old}' TO '{new}';
          END IF;
        END $$;
        """
    )


def upgrade() -> None:
    for type_name, pairs in _RENAMES:
        for old, new in pairs:
            _rename(type_name, old, new)

    for table, column, label in _DEFAULTS:
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} "
            f"SET DEFAULT '{label}'::inference_confidence"
        )


def downgrade() -> None:
    for type_name, pairs in _RENAMES:
        for old, new in pairs:
            _rename(type_name, new, old)

    for table, column, _ in _DEFAULTS:
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} "
            f"SET DEFAULT 'media'::inference_confidence"
        )
