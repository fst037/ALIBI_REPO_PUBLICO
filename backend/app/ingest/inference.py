"""Semantic schema inference — the optional, model-driven half of Module B.

The deterministic pipeline already produces a complete, queryable dataset. This
step only makes it *nicer*: it turns `fch_vcto` into `due_date`, writes a
sentence describing each column, picks Vega-Lite type hints for Module C, and
flags primary keys. If it fails, is disabled, or times out, ingestion succeeds
with the deterministic names — the model is never on the critical path.

Everything sits behind `SchemaInferenceProvider` for one specific reason: the
thesis' end state is a **fine-tuned QLoRA adapter** doing this job, evaluated
against a general-purpose model doing it by prompting. Both have to be callable
through the same interface or the comparison isn't a comparison. Today that's
`NullProvider` (rules only) and `BedrockProvider` (a chat model and an
embedding model on AWS Bedrock); a `SageMakerProvider` slots in beside them when
Module B's adapter is trained.

Two rules this module holds to:

* **Only the profile is sent.** Column names, types, counts and at most three
  sample values — never the table. That keeps the call small, cheap, and free
  of the user's actual data.
* **The model proposes, rules verify.** Anything it returns is validated
  against the columns we already know exist; unknown columns are dropped and
  type changes are ignored. A hallucinated column cannot enter the schema.
"""

from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from app.core.config import SchemaInferenceProvider, Settings, get_settings
from app.ingest.models import DetectedTable, Grid
from app.ingest.profile import profile_prompt
from app.ingest.shape import dedupe_names, normalise_name
from app.schemas.datasets import VegaLiteType

logger = logging.getLogger(__name__)

# English output is a hard product requirement, not a style preference: this is
# the step that RENAMES the user's columns before Module A's Text-to-SQL model
# ever sees them, and that model was fine-tuned on English schemas with no
# translation step. A Spanish name emitted here
# silently degrades every question asked against the dataset afterwards. The
# uploaded file's headers may still arrive in any language — the UI asks for
# English but cannot enforce it — so translating them is part of the job, and
# the examples below deliberately cover the translate-and-expand case.
_SYSTEM_PROMPT = (
    "You are an expert in data modelling. You receive the profile of a table "
    "(raw column names, types, cardinality and sample values) and return its "
    "semantic interpretation.\n"
    "Rules:\n"
    "- Reply ONLY with a valid JSON object, with no surrounding text and no ```.\n"
    "- Every name you emit MUST be in English, in snake_case, descriptive and "
    "free of abbreviations. This is mandatory even when the source name is in "
    "another language: translate it to English.\n"
    "  'fch_vcto' -> 'due_date'\n"
    "  'importe_total' -> 'total_amount'\n"
    "  'cust_nm' -> 'customer_name'\n"
    "- If a name is already clear, descriptive English snake_case, leave it as "
    "it is.\n"
    "- Do NOT invent columns: return exactly the ones you received, with the "
    "same 'source_name'.\n"
    "- 'vl_type' is one of: quantitative, nominal, ordinal, temporal.\n"
    "- Descriptions are a single line, in English.\n"
    "Format:\n"
    '{"table_description": "...", "columns": [{"source_name": "...", '
    '"name": "...", "description": "...", "vl_type": "...", '
    '"is_primary_key": false}]}'
)

_LAYOUT_PROMPT = (
    "You receive the first rows of a spreadsheet as a JSON matrix of cells "
    "(null = empty cell). The automatic rules could not determine which row is "
    "the header row.\n"
    "Your only task: decide which row holds the COLUMN NAMES.\n"
    "Rules:\n"
    "- Reply ONLY with a valid JSON object, with no surrounding text and no ```.\n"
    "- 'header_row' is the 0-based index of that row within the matrix.\n"
    "- If the table has NO header (the first row is already data), return null. "
    "That is a valid answer and preferable to inventing one.\n"
    "- Ignore titles, issue dates and notes: a stray title is not a header.\n"
    'Format: {"header_row": 2}  or  {"header_row": null}'
)


@dataclass(slots=True)
class ColumnSemantics:
    source_name: str
    name: str | None = None
    description: str | None = None
    vl_type: VegaLiteType | None = None
    is_primary_key: bool | None = None


@dataclass(slots=True)
class TableSemantics:
    table_description: str | None = None
    columns: list[ColumnSemantics] = field(default_factory=list)


class SchemaInferenceError(RuntimeError):
    """Raised internally; callers treat it as "no enrichment available"."""


class InferenceProvider(ABC):
    """Semantic naming + embeddings. Both are optional and independently so."""

    @abstractmethod
    def infer(self, table: DetectedTable) -> TableSemantics: ...

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Column embeddings for Module A's schema linking. Empty = unavailable."""
        return []

    def arbitrate_layout(self, excerpt: Grid) -> int | None:
        """Which row of `excerpt` is the header? None = no header / don't know.

        The one place a model sees real cell values rather than a profile, and
        only for a bounded excerpt (`shape.arbitration_excerpt`). Consulted
        solely when the deterministic rules already failed.
        """
        return None

    @property
    def enabled(self) -> bool:
        return True

    @property
    def model_id(self) -> str:
        """Recorded alongside every stored vector.

        Vectors from two different models are not comparable, so a stored one
        is meaningless without knowing what produced it — that's why the column
        is NOT NULL and why this is on the interface rather than an afterthought
        at the call site.
        """
        return "none"


class NullProvider(InferenceProvider):
    """No model. The deterministic names stand and no vectors are produced."""

    def infer(self, table: DetectedTable) -> TableSemantics:
        return TableSemantics()

    @property
    def enabled(self) -> bool:
        return False


# ---------------------------------------------------------------------------
# Response handling
# ---------------------------------------------------------------------------


def _extract_json(text: str) -> dict:
    """Pull a JSON object out of a model response.

    Models wrap JSON in ``` fences or a sentence of preamble often enough that
    parsing the raw string is not good enough — and this has to work across
    whichever model is deployed, not just one that honours a strict JSON mode.
    """
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            text = text[start : end + 1]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SchemaInferenceError(f"response was not JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SchemaInferenceError("response JSON was not an object")
    return parsed


def parse_semantics(payload: dict, known_columns: list[str]) -> TableSemantics:
    """Validate a model response against the columns that actually exist.

    The model proposes; this verifies. A response naming a column we never sent
    is dropped rather than trusted — that is the difference between an
    assistant and an oracle, and it is the only thing standing between a
    hallucination and the user's schema.
    """
    known = set(known_columns)
    seen: set[str] = set()
    columns: list[ColumnSemantics] = []

    for raw in payload.get("columns") or []:
        if not isinstance(raw, dict):
            continue
        source = str(raw.get("source_name") or raw.get("name") or "").strip()
        if source not in known or source in seen:
            continue
        seen.add(source)

        vl_type: VegaLiteType | None = None
        raw_vl = str(raw.get("vl_type") or "").strip().lower()
        if raw_vl in {v.value for v in VegaLiteType}:
            vl_type = VegaLiteType(raw_vl)

        proposed = str(raw.get("name") or "").strip()
        # Run the model's name through the same normaliser as a file header, so
        # "Due Date" can't arrive as an identifier needing quotes.
        clean = normalise_name(proposed, len(columns)) if proposed else None

        description = str(raw.get("description") or "").strip() or None
        is_pk = raw.get("is_primary_key")

        columns.append(
            ColumnSemantics(
                source_name=source,
                name=clean,
                description=description[:500] if description else None,
                vl_type=vl_type,
                is_primary_key=bool(is_pk) if isinstance(is_pk, bool) else None,
            )
        )

    table_description = str(payload.get("table_description") or "").strip() or None
    return TableSemantics(table_description=table_description[:500] if table_description else None, columns=columns)


def parse_header_row(payload: dict) -> int | None:
    """Read `header_row` out of an arbitration response.

    Tolerant of the shapes a model actually returns — a bare integer, a string
    digit, `"null"` — and refuses everything else. A negative index is rejected
    rather than wrapped: Python would happily read `-1` as the last row, which
    is emphatically not what the model meant.
    """
    if not isinstance(payload, dict):
        return None
    value = payload.get("header_row", payload.get("headerRow"))
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"", "null", "none", "no"}:
            return None
        try:
            value = int(text)
        except ValueError:
            return None
    if not isinstance(value, int):
        return None
    return value if value >= 0 else None


def apply_semantics(table: DetectedTable, semantics: TableSemantics) -> None:
    """Write accepted suggestions onto the table, in place.

    Renames are applied to the pandas frame too, so the Parquet that gets
    written carries the semantic names and the query layer needs no mapping
    table. Uniqueness is re-established afterwards: two cryptic columns can
    perfectly well map onto the same clean name.
    """
    if semantics.table_description:
        table.table_description = semantics.table_description

    by_source = {c.source_name: c for c in semantics.columns}
    proposed: list[str] = []
    for column in table.typed.columns:
        suggestion = by_source.get(column.name)
        proposed.append(
            suggestion.name if suggestion and suggestion.name else column.name
        )
    final_names = dedupe_names(proposed)

    renames: dict[str, str] = {}
    for column, new_name in zip(table.typed.columns, final_names):
        suggestion = by_source.get(column.name)
        if suggestion:
            if suggestion.description:
                column.description = suggestion.description
            if suggestion.vl_type is not None:
                column.vl_type = suggestion.vl_type
            if suggestion.is_primary_key is not None:
                # A model flag alone doesn't make a key: the profile already
                # knows whether the column is unique and non-null, and a
                # non-unique column is not a primary key no matter what the
                # model says.
                column.is_primary_key_candidate = (
                    suggestion.is_primary_key and column.is_primary_key_candidate
                )
        if new_name != column.name:
            renames[column.name] = new_name
            column.name = new_name

    if renames:
        table.typed.frame = table.typed.frame.rename(columns=renames)


# ---------------------------------------------------------------------------
# AWS Bedrock
# ---------------------------------------------------------------------------


class BedrockProvider(InferenceProvider):
    """A chat model + an embedding model on AWS Bedrock.

    Called with the processor Lambda's own IAM role through the shared transport
    (`app/core/bedrock.py`): no key, no endpoint URL. The stage-1 processor runs
    outside the VPC, so it reaches Bedrock with no NAT gateway and no standing
    cost. Converse has no JSON mode; the prompts ask for JSON and `_extract_json`
    tolerates a fenced or prefaced object.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        if not self.settings.schema_inference_configured:
            raise SchemaInferenceError(
                "ALIBI_SCHEMA_INFERENCE_PROVIDER=bedrock but ALIBI_SCHEMA_INFERENCE_MODEL_ID is unset"
            )

    def _chat(self, system: str, user: str) -> dict:
        from app.core import bedrock

        reply = bedrock.converse(
            self.settings.schema_inference_model_id or "",
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            region=self.settings.aws_region,
            timeout_seconds=self.settings.schema_inference_timeout_seconds,
            max_tokens=2000,
            temperature=0.0,
        )
        if not reply.text.strip():
            raise SchemaInferenceError("empty response")
        return _extract_json(reply.text)

    def infer(self, table: DetectedTable) -> TableSemantics:
        payload = self._chat(_SYSTEM_PROMPT, profile_prompt(table.typed, table.suggested_name))
        return parse_semantics(payload, [c.name for c in table.typed.columns])

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts or not self.settings.embedding_model_id:
            return []
        from app.core import bedrock

        return bedrock.embed(
            self.settings.embedding_model_id,
            texts,
            region=self.settings.aws_region,
            timeout_seconds=self.settings.schema_inference_timeout_seconds,
            # Titan Text Embeddings V2 emits this width natively, which is how the
            # pgvector column stays at 512 without a migration. A model that
            # ignores it returns its own width and `column_embeddings` rejects it.
            dimensions=self.settings.embedding_dimensions,
        )

    def arbitrate_layout(self, excerpt: Grid) -> int | None:
        return parse_header_row(self._chat(_LAYOUT_PROMPT, json.dumps(excerpt, ensure_ascii=False)))

    @property
    def model_id(self) -> str:
        # The embedding model, because that is what makes two stored vectors
        # comparable (or not).
        return f"bedrock:{self.settings.embedding_model_id}"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def get_provider(settings: Settings | None = None) -> InferenceProvider:
    settings = settings or get_settings()
    if settings.schema_inference_provider is SchemaInferenceProvider.bedrock:
        try:
            return BedrockProvider(settings)
        except SchemaInferenceError as exc:
            # Misconfiguration degrades to the deterministic path rather than
            # failing every upload — loudly, so it gets noticed.
            logger.warning("semantic inference disabled: %s", exc)
    return NullProvider()


def enrich(table: DetectedTable, provider: InferenceProvider | None = None) -> bool:
    """Best-effort enrichment. Returns whether anything was applied.

    Never raises: an ingestion that produced a valid table must not fail
    because a model was slow, rate-limited, or talking nonsense.
    """
    provider = provider or get_provider()
    if not provider.enabled:
        return False
    try:
        semantics = provider.infer(table)
    except Exception as exc:  # noqa: BLE001 - deliberate boundary
        logger.warning("semantic inference failed for %s: %s", table.suggested_name, exc)
        return False
    if not semantics.columns and not semantics.table_description:
        return False
    apply_semantics(table, semantics)
    return True


def layout_arbiter(provider: InferenceProvider | None = None):  # noqa: ANN201
    """A `shape.LayoutArbiter` for the configured provider, or None.

    Returning None rather than a no-op callable is deliberate: `shape_table`
    then never enters the arbitration branch at all, so with no provider the
    shaping code path is byte-for-byte the one the unit tests cover.
    """
    provider = provider or get_provider()
    if not provider.enabled:
        return None
    return provider.arbitrate_layout


def column_embeddings(
    table: DetectedTable, provider: InferenceProvider | None = None
) -> tuple[list[list[float]], str | None]:
    """Validated column vectors, or nothing.

    The width check is not paranoia: Titan Text Embeddings V2 honours the
    `dimensions` request but another model silently ignores it and returns its
    native width. Caught here, that is a log line and a dataset without
    embeddings; caught by Postgres, it is an opaque `expected 512 dimensions`
    error that fails the whole commit — after the Parquet was already written.
    """
    provider = provider or get_provider()
    if not provider.enabled:
        return [], None
    expected_columns = len(table.typed.columns)
    try:
        vectors = provider.embed(column_embedding_texts(table))
    except Exception as exc:  # noqa: BLE001 - deliberate boundary
        logger.warning("embedding failed for %s: %s", table.suggested_name, exc)
        return [], None
    if not vectors:
        return [], None
    width = get_settings().embedding_dimensions
    if len(vectors) != expected_columns or any(len(v) != width for v in vectors):
        logger.warning(
            "discarding embeddings for %s: expected %d vectors of %d dims, got %d of %s",
            table.suggested_name,
            expected_columns,
            width,
            len(vectors),
            sorted({len(v) for v in vectors}),
        )
        return [], None
    return vectors, provider.model_id


def column_embedding_texts(table: DetectedTable) -> list[str]:
    """One descriptive sentence per column, as the ML spec's Step 5 defines it."""
    return [
        f"{table.suggested_name}.{c.name} ({c.sql_type}): "
        f"{c.description or c.source_name}. Examples: {', '.join(c.samples[:3])}"
        for c in table.typed.columns
    ]


__all__ = [
    "ColumnSemantics",
    "BedrockProvider",
    "InferenceProvider",
    "NullProvider",
    "SchemaInferenceError",
    "TableSemantics",
    "apply_semantics",
    "column_embedding_texts",
    "column_embeddings",
    "enrich",
    "get_provider",
    "layout_arbiter",
    "parse_header_row",
    "parse_semantics",
]
