"""Semantic inference: response parsing, validation, and application.

No network here. What's tested is the part that has to be right regardless of
which model is deployed — that a model response is *validated against reality*
before it touches the schema, and that a failure degrades to the deterministic
names rather than failing the upload.
"""

import pytest

from app.core.config import SchemaInferenceProvider, Settings
from app.ingest.inference import (
    InferenceProvider,
    NullProvider,
    SchemaInferenceError,
    TableSemantics,
    apply_semantics,
    column_embedding_texts,
    enrich,
    get_provider,
    parse_semantics,
)
from app.ingest.pipeline import run_pipeline
from app.schemas.datasets import VegaLiteType


@pytest.fixture
def table():
    outcome = run_pipeline(
        "ventas.csv",
        "fch_vcto,mnt,cli_id\n01/02/2024,1234,7\n15/03/2024,980,8\n".encode(),
    )
    return outcome.tables[0]


class TestParsing:
    def test_a_well_formed_response(self, table) -> None:
        payload = {
            "table_description": "Registro de ventas.",
            "columns": [
                {
                    "source_name": "fch_vcto",
                    "name": "fecha_vencimiento",
                    "description": "Fecha de vencimiento.",
                    "vl_type": "temporal",
                    "is_primary_key": False,
                }
            ],
        }
        s = parse_semantics(payload, ["fch_vcto", "mnt", "cli_id"])
        assert s.table_description == "Registro de ventas."
        assert s.columns[0].name == "fecha_vencimiento"
        assert s.columns[0].vl_type is VegaLiteType.temporal

    def test_a_hallucinated_column_is_dropped(self) -> None:
        # The single most important guard here: a model naming a column that
        # was never sent must not be able to add it to the user's schema.
        payload = {"columns": [{"source_name": "columna_inventada", "name": "x"}]}
        assert parse_semantics(payload, ["fch_vcto"]).columns == []

    def test_duplicate_suggestions_for_one_column_take_the_first(self) -> None:
        payload = {
            "columns": [
                {"source_name": "mnt", "name": "monto"},
                {"source_name": "mnt", "name": "importe"},
            ]
        }
        s = parse_semantics(payload, ["mnt"])
        assert len(s.columns) == 1 and s.columns[0].name == "monto"

    def test_an_invalid_vl_type_is_ignored_not_propagated(self) -> None:
        payload = {"columns": [{"source_name": "mnt", "name": "monto", "vl_type": "numerico"}]}
        assert parse_semantics(payload, ["mnt"]).columns[0].vl_type is None

    def test_proposed_names_are_normalised(self) -> None:
        # A model may answer with prose. It still has to end up a usable
        # identifier, so it goes through the same normaliser as a file header.
        payload = {"columns": [{"source_name": "fch_vcto", "name": "Fecha de Vencimiento"}]}
        assert parse_semantics(payload, ["fch_vcto"]).columns[0].name == "fecha_de_vencimiento"

    def test_garbage_shapes_do_not_raise(self) -> None:
        assert parse_semantics({}, ["a"]).columns == []
        assert parse_semantics({"columns": "not a list"}, ["a"]).columns == []
        assert parse_semantics({"columns": [None, 3, "x"]}, ["a"]).columns == []


class TestApplying:
    def test_renames_reach_the_dataframe(self, table) -> None:
        # The Parquet must carry the semantic names, or the query layer would
        # need a mapping table to translate every generated query.
        apply_semantics(
            table,
            parse_semantics(
                {
                    "table_description": "Ventas.",
                    "columns": [
                        {"source_name": "fch_vcto", "name": "fecha_vencimiento"},
                        {"source_name": "mnt", "name": "monto"},
                    ],
                },
                [c.name for c in table.typed.columns],
            ),
        )
        assert [c.name for c in table.typed.columns] == ["fecha_vencimiento", "monto", "cli_id"]
        assert list(table.typed.frame.columns) == ["fecha_vencimiento", "monto", "cli_id"]
        assert table.table_description == "Ventas."

    def test_two_columns_mapped_to_one_name_stay_distinct(self, table) -> None:
        s = parse_semantics(
            {
                "columns": [
                    {"source_name": "mnt", "name": "monto"},
                    {"source_name": "cli_id", "name": "monto"},
                ]
            },
            [c.name for c in table.typed.columns],
        )
        apply_semantics(table, s)
        names = [c.name for c in table.typed.columns]
        assert len(set(names)) == len(names)
        assert "monto" in names and "monto_2" in names

    def test_a_primary_key_claim_cannot_override_the_profile(self, table) -> None:
        # `mnt` is not unique in a real table; the model saying so doesn't make
        # it a key. Statistics beat assertions.
        mnt = next(c for c in table.typed.columns if c.name == "mnt")
        mnt.is_primary_key_candidate = False
        apply_semantics(
            table,
            parse_semantics(
                {"columns": [{"source_name": "mnt", "is_primary_key": True}]},
                [c.name for c in table.typed.columns],
            ),
        )
        assert mnt.is_primary_key_candidate is False

    def test_columns_the_model_ignored_keep_their_deterministic_names(self, table) -> None:
        apply_semantics(
            table,
            parse_semantics(
                {"columns": [{"source_name": "mnt", "name": "monto"}]},
                [c.name for c in table.typed.columns],
            ),
        )
        assert [c.name for c in table.typed.columns] == ["fch_vcto", "monto", "cli_id"]


class TestProviderSelection:
    def test_default_is_the_deterministic_path(self) -> None:
        provider = get_provider(Settings(_env_file=None))
        assert isinstance(provider, NullProvider)
        assert provider.enabled is False

    def test_bedrock_without_a_model_id_degrades_instead_of_failing(self) -> None:
        # A half-filled .env must not break every upload.
        settings = Settings(
            _env_file=None, schema_inference_provider=SchemaInferenceProvider.bedrock
        )
        assert isinstance(get_provider(settings), NullProvider)

    def test_enrich_survives_a_provider_that_raises(self, table) -> None:
        class Exploding(InferenceProvider):
            def infer(self, table):  # noqa: ANN001, ANN201
                raise SchemaInferenceError("model is down")

        original = [c.name for c in table.typed.columns]
        assert enrich(table, Exploding()) is False
        assert [c.name for c in table.typed.columns] == original

    def test_enrich_is_a_no_op_under_the_null_provider(self, table) -> None:
        assert enrich(table, NullProvider()) is False

    def test_enrich_applies_a_good_response(self, table) -> None:
        class Fake(InferenceProvider):
            def infer(self, table):  # noqa: ANN001, ANN201
                return TableSemantics(
                    table_description="Ventas.",
                    columns=parse_semantics(
                        {"columns": [{"source_name": "mnt", "name": "monto"}]},
                        [c.name for c in table.typed.columns],
                    ).columns,
                )

        assert enrich(table, Fake()) is True
        assert "monto" in [c.name for c in table.typed.columns]


class TestEmbeddingTexts:
    def test_one_sentence_per_column_with_no_bulk_data(self, table) -> None:
        texts = column_embedding_texts(table)
        assert len(texts) == len(table.typed.columns)
        assert texts[0].startswith("ventas.fch_vcto")
        # At most three sample values per column ever leave the pipeline.
        assert all(t.count("Examples:") == 1 for t in texts)
