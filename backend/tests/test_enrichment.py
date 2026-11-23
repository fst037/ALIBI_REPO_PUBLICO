"""Semantic enrichment, driven through the real orchestrator.

`test_ingest_inference.py` tests the parsing and validation in isolation. This
file tests the thing that actually matters in production: with a provider
configured, does an upload come out of the *whole* pipeline with semantic names,
descriptions and embeddings — and, more importantly, does it still succeed when
the model misbehaves?

The provider here is a fake rather than a mocked HTTP client. What is being
tested is the contract between ALIBI and *any* provider, and that contract is
the interface, not Azure's wire format — a test built on recorded Foundry
responses would pass for the wrong reason the day the adapter replaces it.
"""

import pytest
from fastapi.testclient import TestClient

from app.ingest import inference
from app.ingest.inference import (
    ColumnSemantics,
    InferenceProvider,
    TableSemantics,
    column_embeddings,
    layout_arbiter,
    parse_header_row,
)
from app.ingest.models import RawTable
from app.ingest.pipeline import run_pipeline
from app.ingest.shape import MAX_ARBITRATION_ROWS, arbitration_excerpt, shape_table
from app.schemas.datasets import InferenceConfidence, VegaLiteType
from tests.test_uploads import _upload

CRYPTIC = b"cod_cli;fch_vcto;imp_tot\n1;01/02/2024;1.234,56\n2;15/03/2024;980,00\n"

# A grid the rules genuinely cannot settle, and a very common one: the column
# headers are YEARS. The header heuristic works by type contrast — a header is
# labels above values — and "2023" parses as a number, so row 0 looks exactly as
# value-like as the data beneath it. Every row scores 0 and the deterministic
# answer is `columna_1..3` with confidence `baja`.
#
# This is the motivating case for arbitration existing at all: a human reads
# this layout instantly, and no amount of type-signature scoring will.
AMBIGUOUS = [
    ["Sucursal", "2023", "2024"],
    ["Centro", "12", "15"],
    ["Norte", "8", "9"],
    ["Sur", "21", "24"],
]
AMBIGUOUS_CSV = b"Sucursal;2023;2024\nCentro;12;15\nNorte;8;9\nSur;21;24\n"


class FakeProvider(InferenceProvider):
    """A provider that behaves exactly as well or badly as a test needs."""

    def __init__(
        self,
        *,
        semantics: TableSemantics | None = None,
        vectors: list[list[float]] | None = None,
        header_row: int | None = None,
        raises: bool = False,
    ) -> None:
        self._semantics = semantics or TableSemantics()
        self._vectors = vectors
        self._header_row = header_row
        self._raises = raises
        self.embed_calls = 0
        self.arbitrate_calls = 0

    def infer(self, table):  # noqa: ANN001, ANN201
        if self._raises:
            raise RuntimeError("429 rate limited")
        return self._semantics

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.embed_calls += 1
        if self._raises:
            raise RuntimeError("timeout")
        if self._vectors is not None:
            return self._vectors
        return [[0.01] * 512 for _ in texts]

    def arbitrate_layout(self, excerpt):  # noqa: ANN001, ANN201
        self.arbitrate_calls += 1
        self.last_excerpt = excerpt
        if self._raises:
            raise RuntimeError("503")
        return self._header_row

    @property
    def model_id(self) -> str:
        return "fake:v1"


@pytest.fixture
def use_provider(monkeypatch):
    """Install a provider for the duration of a test.

    Patched at `inference.get_provider`, which is the single place everything
    else resolves a provider through — so the orchestrator, the arbiter factory
    and the embedding path all pick it up without any of them knowing.
    """

    def _install(provider: InferenceProvider) -> InferenceProvider:
        monkeypatch.setattr(inference, "get_provider", lambda settings=None: provider)
        return provider

    return _install


# ---------------------------------------------------------------------------
# Enrichment through the API
# ---------------------------------------------------------------------------


class TestEnrichedUpload:
    def test_semantic_names_reach_the_stored_table(
        self, client: TestClient, auth_headers: dict, use_provider
    ) -> None:
        use_provider(
            FakeProvider(
                semantics=TableSemantics(
                    table_description="Facturas pendientes de cobro.",
                    columns=[
                        ColumnSemantics(
                            source_name="cod_cli",
                            name="codigo_cliente",
                            description="Identificador del cliente.",
                            vl_type=VegaLiteType.nominal,
                        ),
                        ColumnSemantics(
                            source_name="fch_vcto", name="fecha_vencimiento"
                        ),
                        ColumnSemantics(source_name="imp_tot", name="importe_total"),
                    ],
                )
            )
        )
        upload = _upload(client, auth_headers, "facturas.csv", CRYPTIC)
        dataset_id = upload["datasets"][0]["id"]
        schema = client.get(f"/api/v1/datasets/{dataset_id}/schema", headers=auth_headers).json()

        assert [c["name"] for c in schema["columns"]] == [
            "codigo_cliente",
            "fecha_vencimiento",
            "importe_total",
        ]
        assert schema["tableDescription"] == "Facturas pendientes de cobro."
        # The raw header is kept so the user still recognises the column.
        assert schema["columns"][0]["sourceName"] == "cod_cli"
        assert schema["columns"][0]["description"] == "Identificador del cliente."

    def test_a_semantic_rename_is_queryable(
        self, client: TestClient, auth_headers: dict, use_provider
    ) -> None:
        use_provider(
            FakeProvider(
                semantics=TableSemantics(
                    columns=[ColumnSemantics(source_name="cod_cli", name="codigo_cliente")]
                )
            )
        )
        upload = _upload(client, auth_headers, "facturas.csv", CRYPTIC)
        dataset_id = upload["datasets"][0]["id"]
        preview = client.get(
            f"/api/v1/datasets/{dataset_id}/preview", headers=auth_headers
        ).json()
        # The rename reached the Parquet, so the preview reads it under the new
        # name with no projection involved.
        assert preview["rows"][0]["codigo_cliente"] == 1

    def test_a_provider_that_fails_still_produces_a_dataset(
        self, client: TestClient, auth_headers: dict, use_provider
    ) -> None:
        use_provider(FakeProvider(raises=True))
        upload = _upload(client, auth_headers, "facturas.csv", CRYPTIC)

        # The whole point of the deterministic-first design: a rate-limited or
        # broken model costs nice names, never the upload.
        assert upload["status"] == "ready"
        dataset_id = upload["datasets"][0]["id"]
        schema = client.get(f"/api/v1/datasets/{dataset_id}/schema", headers=auth_headers).json()
        assert [c["name"] for c in schema["columns"]] == ["cod_cli", "fch_vcto", "imp_tot"]

    def test_no_provider_means_no_model_call_at_all(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        # The default configuration. Asserted because "offline by default" is a
        # property of the test suite itself, not just a nice idea.
        assert layout_arbiter() is None
        upload = _upload(client, auth_headers, "facturas.csv", CRYPTIC)
        assert upload["status"] == "ready"


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------


class TestEmbeddings:
    def _table(self):  # noqa: ANN202
        return run_pipeline("facturas.csv", CRYPTIC).tables[0]

    def test_vectors_are_returned_with_the_model_that_made_them(self, use_provider) -> None:
        provider = use_provider(FakeProvider())
        vectors, model_id = column_embeddings(self._table())
        assert len(vectors) == 3
        assert all(len(v) == 512 for v in vectors)
        # Vectors from two models aren't comparable, so the id is not optional.
        assert model_id == "fake:v1"
        assert provider.embed_calls == 1

    def test_the_wrong_width_is_discarded_not_stored(self, use_provider) -> None:
        # A model that ignores the `dimensions` request returns its native
        # width. Caught here it costs embeddings; reaching pgvector it would
        # fail the whole commit — after the Parquet was already written.
        use_provider(FakeProvider(vectors=[[0.1] * 1536] * 3))
        vectors, model_id = column_embeddings(self._table())
        assert vectors == []
        assert model_id is None

    def test_the_wrong_count_is_discarded(self, use_provider) -> None:
        # Fewer vectors than columns would silently mis-assign every embedding
        # from the missing one onwards, since they are matched by ordinal.
        use_provider(FakeProvider(vectors=[[0.1] * 512]))
        assert column_embeddings(self._table()) == ([], None)

    def test_a_failing_embedder_is_not_an_upload_failure(self, use_provider) -> None:
        use_provider(FakeProvider(raises=True))
        assert column_embeddings(self._table()) == ([], None)

    def test_no_provider_means_no_vectors(self) -> None:
        assert column_embeddings(self._table()) == ([], None)


# ---------------------------------------------------------------------------
# Layout arbitration
# ---------------------------------------------------------------------------


class TestLayoutArbitration:
    def test_the_rules_win_when_they_are_confident(self, use_provider) -> None:
        provider = use_provider(FakeProvider(header_row=0))
        clean = RawTable(grid=[["ciudad", "monto"], ["Córdoba", "100"], ["Rosario", "250"]])
        shaped = shape_table(clean, arbiter=provider.arbitrate_layout)

        assert shaped is not None
        assert shaped.header == ["ciudad", "monto"]
        # Not consulted at all: a model is never asked to second-guess a
        # confident deterministic answer, which is what keeps the cost bounded.
        assert provider.arbitrate_calls == 0

    def test_the_rules_alone_get_this_layout_wrong(self) -> None:
        # Stated explicitly so the next two tests are measured against a real
        # baseline rather than an assumed one.
        shaped = shape_table(RawTable(grid=AMBIGUOUS))
        assert shaped is not None
        assert shaped.header_synthesised is True
        assert shaped.header == ["column_1", "column_2", "column_3"]
        # And the first data row is lost to nothing — it is kept as data, which
        # is why the deterministic fallback is safe even though it is wrong.
        assert len(shaped.rows) == 4

    def test_an_ambiguous_layout_is_settled_by_the_model(self, use_provider) -> None:
        provider = use_provider(FakeProvider(header_row=0))
        shaped = shape_table(RawTable(grid=AMBIGUOUS), arbiter=provider.arbitrate_layout)

        assert shaped is not None
        assert provider.arbitrate_calls == 1
        # `Sucursal` is recovered. The year columns still normalise to
        # `columna_N` — a bare `2023` is not a usable SQL identifier — but the
        # row is correctly treated as a header, so it stops being a data row.
        assert shaped.header[0] == "sucursal"
        assert len(shaped.rows) == 3
        # Never `alta`: the rules found nothing, so the user gets the last look.
        assert shaped.header_confidence is InferenceConfidence.MEDIUM
        assert any("ambigua" in note for note in shaped.notes)

    def test_declining_to_answer_falls_back_to_generated_names(self, use_provider) -> None:
        provider = use_provider(FakeProvider(header_row=None))
        shaped = shape_table(RawTable(grid=AMBIGUOUS), arbiter=provider.arbitrate_layout)

        assert shaped is not None
        assert provider.arbitrate_calls == 1
        assert shaped.header_synthesised is True
        assert shaped.header_confidence is InferenceConfidence.LOW

    @pytest.mark.parametrize("verdict", [99, -1, "dos", 3])
    def test_an_unusable_verdict_is_refused(self, use_provider, verdict) -> None:
        # Out of range, negative (Python would read -1 as the last row, which is
        # not what the model meant), non-integer, or the last row (leaving no
        # data). Each must degrade to generated names, never to a broken table.
        provider = use_provider(FakeProvider(header_row=verdict))
        shaped = shape_table(RawTable(grid=AMBIGUOUS), arbiter=provider.arbitrate_layout)
        assert shaped is not None
        assert shaped.header_synthesised is True

    def test_an_arbiter_that_raises_is_not_a_failure(self, use_provider) -> None:
        provider = use_provider(FakeProvider(raises=True))
        shaped = shape_table(RawTable(grid=AMBIGUOUS), arbiter=provider.arbitrate_layout)
        assert shaped is not None
        assert shaped.header_synthesised is True

    def test_arbitration_reaches_the_pipeline(self, use_provider) -> None:
        provider = use_provider(FakeProvider(header_row=0))
        without = run_pipeline("reporte.csv", AMBIGUOUS_CSV)
        with_model = run_pipeline(
            "reporte.csv", AMBIGUOUS_CSV, arbiter=provider.arbitrate_layout
        )
        # Same bytes, same pipeline; the arbiter is the only difference.
        assert without.tables[0].typed.columns[0].name == "column_1"
        assert with_model.tables[0].typed.columns[0].name == "sucursal"
        assert with_model.tables[0].typed.row_count == 3


class TestArbitrationExcerpt:
    def test_the_excerpt_is_bounded(self) -> None:
        # This is the ONE place real cell values leave the process, so the size
        # of what leaves is a property worth pinning down.
        grid = [[f"celda-{r}-{c}" for c in range(40)] for r in range(60)]
        excerpt = arbitration_excerpt(grid)
        assert len(excerpt) == MAX_ARBITRATION_ROWS
        assert all(len(row) == 20 for row in excerpt)

    def test_long_cells_are_truncated(self) -> None:
        excerpt = arbitration_excerpt([["x" * 500, "y"], ["a", "b"]])
        assert len(excerpt[0][0]) == 60

    def test_blank_cells_stay_null(self) -> None:
        # `null` is meaningful to the model — it is how a title row's span reads.
        assert arbitration_excerpt([["a", "  ", None]])[0] == ["a", None, None]


class TestParseHeaderRow:
    @pytest.mark.parametrize(
        ("payload", "expected"),
        [
            ({"header_row": 2}, 2),
            ({"headerRow": 2}, 2),
            ({"header_row": "3"}, 3),
            ({"header_row": 0}, 0),
            ({"header_row": None}, None),
            ({"header_row": "null"}, None),
            ({"header_row": "none"}, None),
            ({"header_row": ""}, None),
            ({"header_row": "no sé"}, None),
            ({"header_row": -1}, None),
            ({"header_row": True}, None),
            ({"header_row": 1.5}, None),
            ({}, None),
            ({"otra_cosa": 1}, None),
        ],
    )
    def test_tolerant_of_shapes_a_model_actually_returns(self, payload, expected) -> None:
        assert parse_header_row(payload) == expected
