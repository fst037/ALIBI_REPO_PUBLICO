"""`app/db/models.py`'s enum columns bind what Postgres actually stores.

No live database needed: `bind_processor` is the same conversion SQLAlchemy
runs before it ever opens a connection, so this catches a name/value mismatch
in `_pg_enum` without the Postgres-path setup the rest of the suite avoids
(see `conftest.py`).

Regression coverage for a real bug: `_pg_enum` bound a Python enum member's
*name* (`NUMERO_ENTERO`) instead of its `.value` (`"Número entero"`) until
Module B's ingestion commit became the first code ever to write a `ColumnType`
or `InferenceConfidence` value to Postgres and hit the mismatch. Every other
enum in the app has name == value, which is exactly why nothing caught it
sooner.
"""

from enum import Enum

from sqlalchemy.dialects import postgresql

from app.db.models import (
    ChartType,
    ConversationSourceType,
    DatasetFileType,
    DatasetStatus,
    InvoiceStatus,
    LedgerAction,
    MessageRole,
    NotificationTone,
    UploadPhase,
    UploadStatus,
    VegaLiteType,
    _pg_enum,
)
from app.schemas.datasets import ColumnType, InferenceConfidence
from app.schemas.plans import BillingCycle, PlanId

_DIALECT = postgresql.dialect()

# Every enum `_pg_enum` is asked to bind anywhere in `app/db/models.py`. If a
# new enum column is added, add it here too — that's the only way this test
# keeps meaning anything.
_ALL_ENUMS = [
    PlanId,
    BillingCycle,
    InvoiceStatus,
    DatasetFileType,
    UploadStatus,
    UploadPhase,
    DatasetStatus,
    InferenceConfidence,
    ColumnType,
    VegaLiteType,
    ConversationSourceType,
    MessageRole,
    ChartType,
    NotificationTone,
    LedgerAction,
]


def _bound(enum_cls: type[Enum], member: Enum) -> str:
    column_type = _pg_enum(enum_cls, "irrelevant_for_this_check")
    processor = column_type.bind_processor(_DIALECT)
    return processor(member) if processor else member


def test_every_member_of_every_db_enum_binds_to_its_value() -> None:
    for enum_cls in _ALL_ENUMS:
        for member in enum_cls:
            assert _bound(enum_cls, member) == member.value, (
                f"{enum_cls.__name__}.{member.name} bound "
                f"{_bound(enum_cls, member)!r}, not its value {member.value!r} — "
                "Postgres will reject it as an unknown enum label"
            )


def test_column_type_and_inference_confidence_are_the_ones_that_would_have_caught_this() -> None:
    """The two enums whose name != value for at least one member.

    If this list ever shrinks to empty, the regression above stops proving
    anything — `_pg_enum` could revert to using `.name` and every existing
    enum would still round-trip by coincidence, same as before this bug was
    found.
    """
    mismatched = {
        enum_cls.__name__
        for enum_cls in _ALL_ENUMS
        if any(member.name != member.value for member in enum_cls)
    }
    assert mismatched == {"ColumnType", "InferenceConfidence"}
