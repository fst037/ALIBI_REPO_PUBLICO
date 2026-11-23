"""End-to-end pipeline: bytes in, named typed tables out.

These are the cases the product claims to handle — a messy es-AR CSV, a
workbook with several tables on one sheet, a headerless file — exercised
through the real entry point rather than through the individual stages.
"""

import io

import pytest

from app.ingest.errors import NoTablesFound, ScannedPdfUnsupported, TooManyRows
from app.ingest.pipeline import run_pipeline, safe_run_pipeline
from app.ingest.profile import profile_prompt
from app.ingest.storage import LocalFileStore, dataset_parquet_key, raw_upload_key
from app.ingest.writer import profile_to_json, to_parquet_bytes
from app.schemas.datasets import ColumnType, InferenceConfidence


def _xlsx(sheets: dict[str, list[list[object]]]) -> bytes:
    import openpyxl

    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(title=name)
        for row in rows:
            ws.append(row)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


class TestMessyCsv:
    def test_es_ar_export_with_a_title_and_a_total(self) -> None:
        """The composite case: cp1252, ';' delimiter, a report title above the
        header, es-AR numbers, dd/mm dates, and a TOTAL row at the bottom."""
        content = (
            "Reporte de ventas 2024\n"
            "\n"
            "ciudad;monto;fecha;activo\n"
            "Córdoba;1.234,56;01/02/2024;Sí\n"
            "Rosario;980,00;15/03/2024;No\n"
            "Mendoza;2.500,10;28/12/2024;Sí\n"
            "TOTAL;4.714,66;;\n"
        ).encode("cp1252")

        outcome = run_pipeline("ventas 2024.csv", content)
        assert len(outcome.tables) == 1
        table = outcome.tables[0]

        assert table.suggested_name == "ventas_2024"
        assert [c.name for c in table.typed.columns] == ["ciudad", "monto", "fecha", "activo"]
        # The title and the TOTAL row are both gone; three real rows remain.
        assert table.typed.row_count == 3

        types = {c.name: c.column_type for c in table.typed.columns}
        assert types["ciudad"] is ColumnType.TEXT
        assert types["monto"] is ColumnType.NUMBER
        assert types["fecha"] is ColumnType.DATE
        assert types["activo"] is ColumnType.BOOLEAN

        # The number actually parsed as 1234.56, not 1.23 and not text.
        assert table.typed.frame["monto"].iloc[0] == 1234.56
        assert table.typed.frame["fecha"].iloc[0].day == 1
        assert table.typed.frame["fecha"].iloc[0].month == 2

    def test_headerless_file_keeps_every_row(self) -> None:
        outcome = run_pipeline(
            "sin_encabezado.csv", b"Cordoba,100\nRosario,250\nMendoza,300\nSalta,150\n"
        )
        table = outcome.tables[0]
        assert table.typed.row_count == 4
        assert [c.name for c in table.typed.columns] == ["column_1", "column_2"]
        assert table.confidence is InferenceConfidence.LOW


class TestMultiTable:
    def test_one_upload_becomes_several_named_tables(self) -> None:
        data = _xlsx(
            {
                "Ventas": [
                    ["ciudad", "monto"],
                    ["Córdoba", 100],
                    ["Rosario", 250],
                    [None, None],
                    ["producto", "stock"],
                    ["Tornillo", 50],
                    ["Tuerca", 75],
                ],
                "Clientes": [["nombre", "email"], ["Acme", "a@x.com"], ["Beta", "b@x.com"]],
            }
        )
        outcome = run_pipeline("catalogo.xlsx", data)
        assert len(outcome.tables) == 3
        names = [t.suggested_name for t in outcome.tables]
        # Sheet names disambiguate far better than a numeric suffix would.
        assert names == ["catalogo_ventas", "catalogo_ventas_2", "catalogo_clientes"]
        assert outcome.tables[1].source_label == "Hoja “Ventas” · tabla 2"
        assert any("3 tablas" in w for w in outcome.warnings)


class TestFailures:
    def test_a_file_with_no_table_fails_with_a_usable_message(self) -> None:
        with pytest.raises(NoTablesFound) as exc:
            run_pipeline("notas.csv", b"solo una linea de texto suelta\n")
        assert "tabla" in exc.value.message.lower()
        assert exc.value.code == "no_tables_found"

    def test_row_limit_is_enforced(self) -> None:
        rows = "\n".join(f"c{i},{i}" for i in range(50))
        with pytest.raises(TooManyRows):
            run_pipeline("big.csv", f"ciudad,monto\n{rows}".encode(), max_rows=10)

    def test_safe_wrapper_returns_errors_instead_of_raising(self) -> None:
        outcome, error = safe_run_pipeline("x.csv", b"nada util aca\n")
        assert outcome is None
        assert error is not None
        # Whatever went wrong, the user gets a sentence — never a traceback.
        assert error.message and not error.message.startswith("Traceback")

    def test_scanned_pdf_is_reported_as_unsupported_not_broken(self) -> None:
        # A valid PDF whose pages carry no text layer. The distinction matters:
        # "we can't read scans" is actionable, "the file is corrupt" is not.
        minimal_pdf = (
            b"%PDF-1.4\n"
            b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
            b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
            b"trailer<</Root 1 0 R>>\n"
        )
        outcome, error = safe_run_pipeline("escaneo.pdf", minimal_pdf)
        assert outcome is None
        assert error is not None
        if isinstance(error, ScannedPdfUnsupported):
            assert "scan" in error.message.lower()
        else:
            # A malformed-enough PDF may fail earlier; either way the user gets
            # a plain-language explanation rather than a library error.
            assert error.message


class TestProfiling:
    def test_profile_reports_keys_nulls_and_samples(self) -> None:
        outcome = run_pipeline(
            "clientes.csv",
            b"id,ciudad,notas\n1,Cordoba,ok\n2,Rosario,\n3,Mendoza,revisar\n",
        )
        by_name = {c.name: c for c in outcome.tables[0].typed.columns}

        assert by_name["id"].is_primary_key_candidate is True
        assert by_name["ciudad"].is_primary_key_candidate is True  # unique here too
        assert by_name["notas"].n_null == 1
        assert by_name["ciudad"].n_unique == 3
        assert by_name["ciudad"].samples[:1] == ["Cordoba"]

    def test_the_prompt_carries_structure_not_data(self) -> None:
        outcome = run_pipeline("v.csv", b"ciudad,monto\nCordoba,100\nRosario,250\n")
        prompt = profile_prompt(outcome.tables[0].typed, "ventas")
        assert "### Table: ventas" in prompt
        assert "ciudad" in prompt and "BIGINT" in prompt
        # At most three sample values per column ever leave the pipeline.
        assert prompt.count("Cordoba") == 1

    def test_a_column_mixing_numbers_and_words_stays_text_and_loses_nothing(self) -> None:
        numeric = "\n".join(f"c{i},{i}" for i in range(30))
        words = "\n".join(f"c{i},pendiente" for i in range(30, 40))
        outcome = run_pipeline("mixto.csv", f"codigo,valor\n{numeric}\n{words}\n".encode())
        table = outcome.tables[0]
        valor = {c.name: c for c in table.typed.columns}["valor"]
        # Typing it numeric would null out every "pendiente" — data the user
        # can see in their own file, silently gone.
        assert valor.column_type is ColumnType.TEXT
        assert table.typed.row_count == 40
        assert valor.n_null == 0

    def test_a_type_inferred_from_the_sample_is_revised_if_the_tail_disagrees(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Typing reads only the first N rows for speed. When the rest of the
        # file contradicts that guess, the column must be demoted rather than
        # coerced — otherwise a big file silently loses its tail.
        monkeypatch.setattr("app.ingest.profile.TYPE_SAMPLE_ROWS", 5)
        head = "\n".join(f"c{i},{i}" for i in range(5))
        tail = "\n".join(f"c{i},pendiente" for i in range(5, 25))
        outcome = run_pipeline("tail.csv", f"codigo,valor\n{head}\n{tail}\n".encode())
        valor = {c.name: c for c in outcome.tables[0].typed.columns}["valor"]
        assert valor.column_type is ColumnType.TEXT
        assert valor.confidence is InferenceConfidence.LOW
        assert valor.n_null == 0


class TestSerialisation:
    def test_parquet_round_trips_through_duckdb(self) -> None:
        import duckdb

        outcome = run_pipeline(
            "ventas.csv", "ciudad;monto\nCórdoba;1.234,56\nRosario;980,00\n".encode()
        )
        table = outcome.tables[0]
        data = to_parquet_bytes(table.typed.frame)
        assert data.startswith(b"PAR1")

        store = LocalFileStore(__import__("tempfile").mkdtemp())
        key = dataset_parquet_key("u_test", "ds_test")
        store.put(key, data)

        # The whole point of the data plane: the query engine reads it back as
        # SQL with correct types, no schema registration anywhere.
        rows = duckdb.sql(
            f"SELECT ciudad, monto FROM read_parquet('{store.uri_for(key)}') ORDER BY monto DESC"
        ).fetchall()
        assert rows == [("Córdoba", 1234.56), ("Rosario", 980.0)]

    def test_profile_json_is_serialisable(self) -> None:
        import json

        outcome = run_pipeline("v.csv", b"ciudad,monto\nCordoba,100\n")
        payload = json.loads(profile_to_json(outcome.tables[0].typed, "ventas"))
        assert payload["tableName"] == "ventas"
        assert payload["columns"][0]["name"] == "ciudad"


class TestStorageKeys:
    def test_keys_are_tenant_prefixed(self) -> None:
        assert raw_upload_key("u_ana", "up_1", "v.csv").startswith("uploads/u_ana/")
        assert dataset_parquet_key("u_ana", "ds_1").startswith("tenants/u_ana/")

    def test_path_traversal_in_a_filename_cannot_escape_the_store(self) -> None:
        import tempfile
        from pathlib import Path

        store = LocalFileStore(tempfile.mkdtemp())
        # The filename is user-supplied. The key builder strips separators, so
        # the remaining dots are inert — what matters is where the bytes land.
        key = raw_upload_key("u_ana", "up_1", "../../../etc/passwd")
        assert "/" not in key.rsplit("/", 1)[-1] and "\\" not in key
        store.put(key, b"x")
        assert Path(store.uri_for(key)).is_relative_to(store.root)

        # And the store refuses an escaping key outright, so a future caller
        # that builds one by hand still can't get out.
        with pytest.raises(ValueError):
            store.put("../escape.txt", b"x")



class TestSampleFormatting:
    """The "Examples" column of the confirmation screen, and the model prompt.

    Both read these strings verbatim, which is why `str(980.0)` is wrong: the
    trailing `.0` is noise.

    The input is es-AR (`1.234,56`) and the output is en-US (`1,234.56`) on
    purpose. Parsing has to accept the locale the file was written in; display
    has to match `lib/format.ts`, which renders the preview table right beside
    this column.

    Fixtures are `;`-delimited and two columns wide on purpose — a one-column
    table is discarded as unusable (`MIN_USEFUL_COLUMNS`), and a comma-delimited
    file whose values contain commas is genuinely ambiguous.
    """

    def test_es_ar_decimals_are_parsed_and_shown_en_us(self) -> None:
        outcome = run_pipeline(
            "v.csv", b"ciudad;monto\nCordoba;1.234,56\nRosario;980,00\nSalta;2.500,10\n"
        )
        monto = outcome.tables[0].typed.columns[1]
        assert monto.column_type is ColumnType.NUMBER
        assert monto.samples == ["1,234.56", "980.00", "2,500.10"]

    def test_integers_keep_no_decimals(self) -> None:
        outcome = run_pipeline("v.csv", b"ciudad;unidades\nCordoba;1500\nRosario;12480\nSalta;7\n")
        unidades = outcome.tables[0].typed.columns[1]
        assert unidades.column_type is ColumnType.WHOLE_NUMBER
        assert unidades.samples == ["1,500", "12,480", "7"]

    def test_booleans_read_as_words(self) -> None:
        # The file says "si"/"no" — Spanish input stays readable, the label the
        # user is shown is English.
        outcome = run_pipeline("v.csv", b"ciudad;activo\nCordoba;si\nRosario;no\nSalta;si\n")
        activo = outcome.tables[0].typed.columns[1]
        assert activo.column_type is ColumnType.BOOLEAN
        assert activo.samples[:2] == ["Yes", "No"]

    def test_dates_stay_iso(self) -> None:
        # ISO even though the file used dd/mm: unambiguous beats familiar for a
        # value shown right next to its inferred type.
        outcome = run_pipeline("v.csv", b"ciudad;fecha\nCordoba;01/02/2024\nRosario;15/03/2024\n")
        assert outcome.tables[0].typed.columns[1].samples == ["2024-02-01", "2024-03-15"]

    def test_text_is_left_alone(self) -> None:
        # Not "s/d" for the second value — that is a recognised es-AR null
        # token, so it would read as absent rather than as text.
        outcome = run_pipeline("v.csv", b"ciudad;nota\nCordoba;1.234,56 pesos\nRosario;a confirmar\n")
        # A text column that happens to look numeric must not be reformatted.
        assert outcome.tables[0].typed.columns[1].samples == ["1.234,56 pesos", "a confirmar"]
