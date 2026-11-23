"""Format detection and extraction.

The Excel cases build real .xlsx files with openpyxl rather than checking in
binary fixtures — the fixture is then readable in the test, and a reviewer can
see exactly which pathological layout is being exercised.
"""

import io
import json

import pytest

from app.ingest.detect import decode_text, detect_file_type, sniff_delimiter
from app.ingest.errors import UnsupportedFileType
from app.ingest.extract import extract
from app.ingest.extract.delimited import extract_delimited, extract_json
from app.ingest.extract.document import stitch_pages
from app.ingest.extract.excel import find_islands
from app.schemas.datasets import DatasetFileType


def _xlsx(sheets: dict[str, list[list[object]]], merges: dict[str, list[str]] | None = None) -> bytes:
    import openpyxl

    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(title=name)
        for row in rows:
            ws.append(row)
        for ref in (merges or {}).get(name, []):
            ws.merge_cells(ref)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


class TestFileTypeDetection:
    def test_content_beats_extension(self) -> None:
        # A real xlsx that someone renamed to .csv — trusting the extension
        # would hand a zip container to the CSV parser.
        data = _xlsx({"Hoja1": [["a", "b"], [1, 2]]})
        assert detect_file_type("ventas.csv", data) is DatasetFileType.xlsx

    def test_pdf_magic(self) -> None:
        assert detect_file_type("x.bin", b"%PDF-1.7\n...") is DatasetFileType.pdf

    def test_legacy_xls_is_rejected_with_a_reason(self) -> None:
        with pytest.raises(UnsupportedFileType):
            detect_file_type("old.xls", b"\xd0\xcf\x11\xe0ancient")

    def test_extension_only_for_text_formats(self) -> None:
        assert detect_file_type("data.csv", b"a,b\n1,2") is DatasetFileType.csv
        assert detect_file_type("data.json", b'[{"a":1}]') is DatasetFileType.json

    def test_claimed_xlsx_without_the_signature_is_rejected(self) -> None:
        with pytest.raises(UnsupportedFileType):
            detect_file_type("fake.xlsx", b"this is just text, not a zip")


class TestEncoding:
    def test_utf8(self) -> None:
        assert decode_text("ciudad\nCórdoba".encode()).text.endswith("Córdoba")

    def test_latin1_export_is_not_rejected(self) -> None:
        # Excel on a Spanish-locale Windows machine writes cp1252, and this is
        # by far the most common real-world encoding for these uploads.
        result = decode_text("ciudad\nCórdoba".encode("cp1252"))
        assert "Córdoba" in result.text

    def test_a_column_of_accented_names_keeps_every_accent(self) -> None:
        # The regression that hid behind the single-accent case above. On a
        # short, mostly-ASCII sample the detector picks a codepage essentially
        # at random among the Latin family, and the wrong one still decodes
        # without raising: charset-normalizer 3.5 read this as cp775 and
        # produced "C¾rdoba" and "RĒo". Nothing downstream can notice that, so
        # the assertion has to be on the accents themselves.
        cities = "Córdoba;Tucumán;Río Negro;Neuquén;Entre Ríos"
        result = decode_text(cities.encode("cp1252"))
        assert result.text == cities

    def test_cp1252_punctuation_and_currency_survive(self) -> None:
        # Excel emits smart quotes, en dashes and € in the 0x80-0x9F range that
        # Latin-1 leaves undefined — the part of cp1252 that is genuinely not
        # Latin-1.
        line = "nombre\n“Café” €1.234 — ok"
        assert decode_text(line.encode("cp1252")).text == line

    def test_utf16_without_a_bom_is_still_detected(self) -> None:
        # The other half of the guard: a wide encoding is only accepted when
        # the NUL bytes back it up. This file has them, so it must NOT be
        # forced down the cp1252 path — that would turn it into mojibake.
        result = decode_text("ciudad\nCórdoba".encode("utf-16-le"))
        assert "Córdoba" in result.text

    def test_utf8_bom_is_stripped(self) -> None:
        result = decode_text(b"\xef\xbb\xbfciudad,monto")
        assert result.text.startswith("ciudad")


class TestDelimiterSniffing:
    def test_semicolon_wins_for_an_es_ar_export(self) -> None:
        # The killer case: commas are decimal marks here, so guessing ','
        # would split every price in half.
        text = "ciudad;monto;fecha\nCórdoba;1.234,56;01/02/2024\nRosario;980,00;02/02/2024"
        guess = sniff_delimiter(text)
        assert guess.delimiter == ";"
        assert guess.columns == 3

    def test_plain_comma(self) -> None:
        assert sniff_delimiter("a,b,c\n1,2,3\n4,5,6").delimiter == ","

    def test_tab(self) -> None:
        assert sniff_delimiter("a\tb\tc\n1\t2\t3").delimiter == "\t"

    def test_pipe(self) -> None:
        assert sniff_delimiter("a|b|c\n1|2|3").delimiter == "|"


class TestDelimitedExtraction:
    def test_end_to_end_es_ar_csv(self) -> None:
        raw = "ciudad;monto\nCórdoba;1.234,56\nRosario;980,00".encode("cp1252")
        tables = extract(DatasetFileType.csv, raw)
        assert len(tables) == 1
        assert tables[0].grid[0] == ["ciudad", "monto"]
        assert tables[0].grid[1] == ["Córdoba", "1.234,56"]

    def test_quoted_fields_with_embedded_delimiters(self) -> None:
        tables = extract_delimited(b'a,b\n"Rosario, Santa Fe",100\n"x",200')
        assert tables[0].grid[1] == ["Rosario, Santa Fe", "100"]

    def test_empty_cells_become_none(self) -> None:
        tables = extract_delimited(b"a,b,c\n1,,3")
        assert tables[0].grid[1] == ["1", None, "3"]


class TestJsonExtraction:
    def test_array_of_objects(self) -> None:
        data = json.dumps([{"ciudad": "Córdoba", "monto": 100}]).encode()
        grid = extract_json(data)[0].grid
        assert grid[0] == ["ciudad", "monto"]
        assert grid[1] == ["Córdoba", "100"]

    def test_nested_objects_are_flattened(self) -> None:
        data = json.dumps([{"cliente": {"nombre": "Acme", "id": 7}}]).encode()
        assert extract_json(data)[0].grid[0] == ["cliente_nombre", "cliente_id"]

    def test_api_envelope_picks_the_records_list(self) -> None:
        data = json.dumps({"status": "ok", "data": [{"a": 1}, {"a": 2}]}).encode()
        grid = extract_json(data)[0].grid
        assert grid[0] == ["a"]
        assert len(grid) == 3

    def test_jsonl(self) -> None:
        data = b'{"a": 1}\n{"a": 2}\n'
        assert len(extract_json(data)[0].grid) == 3

    def test_ragged_records_union_their_keys(self) -> None:
        data = b'[{"a": 1}, {"b": 2}]'
        grid = extract_json(data)[0].grid
        assert grid[0] == ["a", "b"]
        assert grid[2] == [None, "2"]


class TestExcelIslands:
    def test_two_tables_separated_by_a_blank_row(self) -> None:
        grid = [
            ["ciudad", "monto"],
            ["Córdoba", "100"],
            [None, None],
            ["producto", "stock"],
            ["Tornillo", "50"],
        ]
        assert len(find_islands(grid)) == 2

    def test_two_tables_side_by_side(self) -> None:
        grid = [
            ["ciudad", "monto", None, "producto", "stock"],
            ["Córdoba", "100", None, "Tornillo", "50"],
        ]
        assert len(find_islands(grid)) == 2

    def test_a_stray_note_is_not_a_table(self) -> None:
        # One cell floating below the data is a comment, not a dataset.
        grid = [["a", "b"], ["1", "2"], [None, None], ["nota al pie", None]]
        assert len(find_islands(grid)) == 1

    def test_a_short_table_stacked_above_another_beside_a_taller_neighbour(self) -> None:
        """Regression: two short tables stacked in the same narrow columns,
        next to one table tall enough to span both of their combined height.

        `_row_bands` alone treats a row as blank only when EVERY column is
        empty — so the blank row between the two stacked tables never read
        as blank at all, because the taller neighbour still had data there.
        The whole left-hand stack collapsed into one island, and the second
        table's header row survived only as a garbage data row underneath
        the first table's header.
        """
        grid = [
            ["ciudad", "monto", None, "id", "cant"],
            ["Córdoba", "100", None, "1", "10"],
            [None, None, None, "2", "20"],  # blank only in columns 0-1
            ["producto", "stock", None, "3", "30"],
            ["Tornillo", "50", None, "4", "40"],
            [None, None, None, "5", "50"],
        ]
        islands = find_islands(grid)
        assert len(islands) == 3
        left = sorted((r0, r1) for r0, r1, c0, c1 in islands if c0 == 0)
        # Two short islands, not one tall one spanning rows 0-6.
        assert left == [(0, 2), (3, 5)]

    def test_multi_table_workbook_end_to_end(self) -> None:
        data = _xlsx(
            {
                "Ventas": [
                    ["Reporte 2024", None],
                    [None, None],
                    ["ciudad", "monto"],
                    ["Córdoba", 100],
                    ["Rosario", 250],
                    [None, None],
                    ["producto", "stock"],
                    ["Tornillo", 50],
                    ["Tuerca", 75],
                ],
                "Vacía": [],
            }
        )
        tables = extract(DatasetFileType.xlsx, data)
        # The title block is too small to be an island; the two real tables are.
        assert len(tables) == 2
        assert tables[0].origin.sheet == "Ventas"
        assert tables[0].origin.label() == "Hoja “Ventas”"
        assert tables[1].origin.label() == "Hoja “Ventas” · tabla 2"

    def test_stacked_tables_beside_a_taller_one_end_to_end(self) -> None:
        """The real-world shape this was reported from: two small invoice
        tables stacked in the same columns, next to one ingredient table
        tall enough to keep the blank row between them from ever reading as
        blank sheet-wide. Before the fix this produced one dataset — the
        second table's header ("NroFactura", "IdPlato") swallowed as a
        garbage row under the first table's header ("NroFactura", "IdMesa").
        """
        data = _xlsx(
            {
                "Datos": [
                    ["NroFactura", "IdMesa", None, "IdIngrediente", "descripcion"],
                    [1, 1, None, 100, "Gancia"],
                    [2, 3, None, 101, "Colon"],
                    [None, None, None, 102, "Suter"],
                    ["NroFactura", "IdPlato", None, 103, "Mont"],
                    [1, 100, None, 104, "Chateau"],
                    [1, 102, None, 105, "San Felipe"],
                    [2, 200, None, 106, "Bianchi"],
                ],
            }
        )
        tables = extract(DatasetFileType.xlsx, data)
        assert len(tables) == 3
        headers = sorted(t.grid[0] for t in tables)
        assert headers == [
            ["IdIngrediente", "descripcion"],
            ["NroFactura", "IdMesa"],
            ["NroFactura", "IdPlato"],
        ]

    def test_merged_cells_are_expanded_across_their_span(self) -> None:
        # openpyxl reports a merged value only at the top-left; without
        # un-merging, a grouped header is indistinguishable from a title.
        data = _xlsx(
            {"H": [["2024", None, "2025", None], ["Q1", "Q2", "Q1", "Q2"], [1, 2, 3, 4]]},
            merges={"H": ["A1:B1", "C1:D1"]},
        )
        grid = extract(DatasetFileType.xlsx, data)[0].grid
        assert grid[0] == ["2024", "2024", "2025", "2025"]

    def test_dates_come_through_as_iso(self) -> None:
        import datetime

        data = _xlsx({"H": [["fecha", "monto"], [datetime.date(2024, 3, 1), 10]]})
        assert extract(DatasetFileType.xlsx, data)[0].grid[1][0] == "2024-03-01"


class TestPdfStitching:
    def test_a_table_continuing_across_pages_is_one_table(self) -> None:
        page1 = [["ciudad", "monto"], ["Córdoba", "100"]]
        page2 = [["ciudad", "monto"], ["Rosario", "250"]]  # header repeated
        out = stitch_pages([(1, page1), (2, page2)])
        assert len(out) == 1
        # The repeated header must not survive as a data row.
        assert out[0].grid == [["ciudad", "monto"], ["Córdoba", "100"], ["Rosario", "250"]]

    def test_continuation_without_a_repeated_header(self) -> None:
        out = stitch_pages([(1, [["a", "b"], ["1", "2"]]), (2, [["3", "4"], ["5", "6"]])])
        assert len(out) == 1
        assert len(out[0].grid) == 4

    def test_different_widths_stay_separate(self) -> None:
        out = stitch_pages([(1, [["a", "b"], ["1", "2"]]), (2, [["x", "y", "z"], ["1", "2", "3"]])])
        assert len(out) == 2
        assert out[1].origin.page == 2
