"""Grid shaping — header detection, preamble/footer removal, name normalisation.

These are the "your file is not a clean CSV" cases. Every fixture here is a
shape real exports actually arrive in: a report title above the data, a merged
two-row header, a TOTAL line at the bottom, a spacer column.
"""

from app.ingest.models import RawTable, TableOrigin
from app.ingest.shape import dedupe_names, normalise_name, shape_table
from app.schemas.datasets import InferenceConfidence


def _raw(rows: list[list[object]]) -> RawTable:
    return RawTable(grid=[[None if c is None else str(c) for c in r] for r in rows])


class TestHeaderDetection:
    def test_clean_table(self) -> None:
        s = shape_table(_raw([["ciudad", "monto"], ["Córdoba", "100"], ["Rosario", "250"]]))
        assert s is not None
        assert s.header == ["ciudad", "monto"]
        assert len(s.rows) == 2
        assert s.header_confidence is InferenceConfidence.HIGH

    def test_title_row_above_the_header_is_dropped(self) -> None:
        s = shape_table(
            _raw(
                [
                    ["Reporte de ventas 2024", None, None],
                    [None, None, None],
                    ["ciudad", "monto", "fecha"],
                    ["Córdoba", "100", "01/02/2024"],
                    ["Rosario", "250", "03/02/2024"],
                ]
            )
        )
        assert s is not None
        assert s.header == ["ciudad", "monto", "fecha"]
        assert len(s.rows) == 2
        assert any("anterior" in n for n in s.notes)

    def test_headerless_data_gets_generated_names_and_low_confidence(self) -> None:
        # Every row is data. Promoting the first one to a header would both
        # lose a record and invent nonsense column names.
        s = shape_table(
            _raw([["Córdoba", "100"], ["Rosario", "250"], ["Mendoza", "300"], ["Salta", "150"]])
        )
        assert s is not None
        assert s.header == ["column_1", "column_2"]
        assert s.header_synthesised is True
        assert s.header_confidence is InferenceConfidence.LOW
        assert len(s.rows) == 4  # no row was consumed as a header

    def test_merged_two_row_header_is_flattened(self) -> None:
        s = shape_table(
            _raw(
                [
                    ["2024", None, "2025", None],
                    ["Q1", "Q2", "Q1", "Q2"],
                    ["10", "20", "30", "40"],
                    ["11", "21", "31", "41"],
                ]
            )
        )
        assert s is not None
        # `col_` prefix because an identifier can't start with a digit unquoted
        # — the span label is carried across its blanks, which is the point.
        assert s.header == ["col_2024_q1", "col_2024_q2", "col_2025_q1", "col_2025_q2"]
        assert len(s.rows) == 2
        assert any("dos filas" in n for n in s.notes)


class TestJunkRemoval:
    def test_total_row_is_dropped(self) -> None:
        s = shape_table(
            _raw(
                [
                    ["ciudad", "monto"],
                    ["Córdoba", "100"],
                    ["Rosario", "250"],
                    ["TOTAL", "350"],
                ]
            )
        )
        assert s is not None
        assert len(s.rows) == 2
        assert any("totales" in n for n in s.notes)

    def test_a_customer_named_total_sa_is_not_a_footer(self) -> None:
        # Dense row → real data, even though the label starts with "Total".
        s = shape_table(
            _raw(
                [
                    ["cliente", "ciudad", "monto"],
                    ["Acme", "Córdoba", "100"],
                    ["Total S.A.", "Rosario", "250"],
                ]
            )
        )
        assert s is not None
        assert len(s.rows) == 2

    def test_blank_rows_and_spacer_columns_are_removed(self) -> None:
        s = shape_table(
            _raw(
                [
                    ["ciudad", None, "monto"],
                    ["Córdoba", None, "100"],
                    [None, None, None],
                    ["Rosario", None, "250"],
                ]
            )
        )
        assert s is not None
        assert s.header == ["ciudad", "monto"]
        assert len(s.rows) == 2
        assert any("columna vacía" in n for n in s.notes)

    def test_ragged_rows_are_padded_to_the_header(self) -> None:
        s = shape_table(_raw([["a", "b", "c"], ["1", "2"], ["3", "4", "5"]]))
        assert s is not None
        assert all(len(r) == 3 for r in s.rows)


class TestNames:
    def test_accents_spaces_and_symbols(self) -> None:
        assert normalise_name("Fecha de Vencimiento", 0) == "fecha_de_vencimiento"
        assert normalise_name("Precio ($)", 1) == "precio"
        assert normalise_name("  N° Factura ", 2) == "n_factura"

    def test_blank_and_numeric_names_get_a_fallback(self) -> None:
        assert normalise_name("", 3) == "column_4"
        assert normalise_name("2024", 0) == "column_1"

    def test_names_starting_with_a_digit_are_prefixed(self) -> None:
        assert normalise_name("1er trimestre", 0) == "col_1er_trimestre"

    def test_duplicates_are_suffixed(self) -> None:
        assert dedupe_names(["monto", "monto", "ciudad", "monto"]) == [
            "monto",
            "monto_2",
            "ciudad",
            "monto_3",
        ]

    def test_duplicate_headers_survive_shaping(self) -> None:
        s = shape_table(_raw([["monto", "monto"], ["1", "2"], ["3", "4"]]))
        assert s is not None
        assert s.header == ["monto", "monto_2"]


class TestDegenerate:
    def test_empty_grid(self) -> None:
        assert shape_table(RawTable(grid=[])) is None

    def test_header_only_no_body(self) -> None:
        assert shape_table(_raw([["ciudad", "monto"]])) is None

    def test_origin_is_preserved(self) -> None:
        raw = RawTable(
            grid=[["a", "b"], ["1", "2"]], origin=TableOrigin(sheet="Ventas", index=1)
        )
        s = shape_table(raw)
        assert s is not None
        assert s.origin.label() == "Hoja “Ventas” · tabla 2"
