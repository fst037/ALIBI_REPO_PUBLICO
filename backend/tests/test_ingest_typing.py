"""Type-inference rules — the deterministic half of Module B's schema inference.

The es-AR cases are the point of most of this file: a Spanish-locale export is
the *normal* input for this product, and every off-the-shelf parser defaults to
the US convention. Getting `1.234,56` wrong doesn't raise — it silently yields
a text column the user can't sum, or a number 1000× too small.
"""

from app.ingest.typing_rules import (
    ColumnTyping,
    NumberStyle,
    infer_column_typing,
    parse_bool,
    parse_date,
    parse_number,
)
from app.schemas.datasets import ColumnType, InferenceConfidence, VegaLiteType


def _type_of(values: list[str | None]) -> ColumnType:
    return infer_column_typing(values).column_type


class TestNumbers:
    def test_es_ar_thousands_and_decimal(self) -> None:
        t = infer_column_typing(["1.234,56", "980,00", "12.500,75"])
        assert t.column_type is ColumnType.NUMBER
        assert t.number_style == NumberStyle.ES
        assert t.coerce("1.234,56") == 1234.56
        assert t.coerce("12.500,75") == 12500.75

    def test_en_us_thousands_and_decimal(self) -> None:
        t = infer_column_typing(["1,234.56", "980.00", "12,500.75"])
        assert t.number_style == NumberStyle.EN
        assert t.coerce("1,234.56") == 1234.56

    def test_ambiguous_column_resolved_by_its_other_rows(self) -> None:
        # "1.234" alone is unknowable. A sibling row with a comma decimal mark
        # settles the whole column as es-AR — which is the entire reason style
        # detection is per column and not per value.
        t = infer_column_typing(["1.234", "2.500", "980,50"])
        assert t.number_style == NumberStyle.ES
        assert t.coerce("1.234") == 1234.0

    def test_currency_and_nbsp_are_stripped(self) -> None:
        # Excel emits a non-breaking space as the es-AR thousands separator.
        t = infer_column_typing(["$ 1.234,56", "$ 980,00"])
        assert t.column_type is ColumnType.NUMBER
        assert t.coerce("$ 1.234,56") == 1234.56
        # parse_number returns an exact Decimal; float() to compare with a literal.
        assert float(parse_number("1 234,56", NumberStyle.ES)) == 1234.56

    def test_accounting_parentheses_are_negative(self) -> None:
        assert float(parse_number("(1.234,56)", NumberStyle.ES)) == -1234.56

    def test_integers_stay_integers(self) -> None:
        t = infer_column_typing(["1", "42", "1500"])
        assert t.column_type is ColumnType.WHOLE_NUMBER
        assert t.sql_type == "BIGINT"
        assert t.coerce("1500") == 1500

    def test_written_decimals_stay_float_even_when_whole(self) -> None:
        # "1,00" is a price, not a count — rounding it to an int loses the
        # fact that this column can hold cents.
        t = infer_column_typing(["1,00", "2,00", "3,00"])
        assert t.column_type is ColumnType.NUMBER

    def test_percentages(self) -> None:
        t = infer_column_typing(["45%", "3,5%", "100%"])
        assert t.column_type is ColumnType.PERCENTAGE
        assert t.vl_type is VegaLiteType.quantitative
        assert t.coerce("45%") == 45.0

    def test_mostly_numeric_with_junk_still_numeric_but_less_certain(self) -> None:
        t = infer_column_typing(["10", "20", "30", "40", "s/d"])
        assert t.column_type is ColumnType.WHOLE_NUMBER
        # 's/d' is a null token, not a parse failure, so certainty stays high.
        assert t.confidence is InferenceConfidence.HIGH

    def test_text_with_a_few_numbers_is_text(self) -> None:
        assert _type_of(["10", "20", "pendiente", "en revisión", "cancelado"]) is ColumnType.TEXT


class TestDates:
    def test_day_first_is_the_default(self) -> None:
        # 03/04/2024 is 3 April to this product's users, not 4 March.
        assert parse_date("03/04/2024") == __import__("datetime").date(2024, 4, 3)

    def test_evidence_overrides_the_default(self) -> None:
        # A row with 13 in the second position proves the column is month-first.
        t = infer_column_typing(["01/13/2024", "02/28/2024", "03/15/2024"])
        assert t.column_type is ColumnType.DATE
        assert t.day_first is False
        assert t.coerce("01/13/2024").month == 1

    def test_day_evidence_confirms_day_first(self) -> None:
        t = infer_column_typing(["25/12/2024", "01/02/2024"])
        assert t.day_first is True
        assert t.coerce("25/12/2024").day == 25

    def test_iso_and_datetime(self) -> None:
        d = __import__("datetime").date(2024, 1, 3)
        assert parse_date("2024-01-03") == d
        assert parse_date("2024-01-03 14:30:00") == d
        assert parse_date("2024-01-03T14:30:00Z") == d

    def test_spanish_month_names(self) -> None:
        assert parse_date("12-ene-2024") == __import__("datetime").date(2024, 1, 12)
        assert parse_date("5 dic 2023") == __import__("datetime").date(2023, 12, 5)

    def test_impossible_dates_are_not_dates(self) -> None:
        assert parse_date("31/02/2024") is None
        assert _type_of(["31/02/2024", "45/13/2024"]) is ColumnType.TEXT

    def test_two_digit_years(self) -> None:
        assert parse_date("01/02/99").year == 1999
        assert parse_date("01/02/24").year == 2024


class TestBooleans:
    def test_spanish_and_english_tokens(self) -> None:
        assert parse_bool("Sí") is True
        assert parse_bool("si") is True
        assert parse_bool("NO") is False
        assert parse_bool("verdadero") is True
        assert _type_of(["Sí", "No", "Sí", "No"]) is ColumnType.BOOLEAN

    def test_one_zero_flag_column(self) -> None:
        assert _type_of(["1", "0", "1", "1", "0"]) is ColumnType.BOOLEAN

    def test_constant_column_of_ones_is_not_a_flag(self) -> None:
        # Needs two distinct values; otherwise every all-1 id column becomes a
        # boolean.
        assert _type_of(["1", "1", "1", "1"]) is ColumnType.WHOLE_NUMBER

    def test_id_column_is_not_a_flag(self) -> None:
        assert _type_of(["101", "102", "103"]) is ColumnType.WHOLE_NUMBER


class TestNulls:
    def test_common_null_tokens_are_null_not_text(self) -> None:
        t = infer_column_typing(["10", "20", "-", "N/A", "", "s/d", "#N/A"])
        assert t.column_type is ColumnType.WHOLE_NUMBER
        assert t.coerce("-") is None
        assert t.coerce("N/A") is None
        assert t.coerce("") is None

    def test_all_empty_column_is_low_confidence_text(self) -> None:
        t = infer_column_typing([None, "", "  "])
        assert t.column_type is ColumnType.TEXT
        assert t.confidence is InferenceConfidence.LOW


class TestCoercionRoundTrip:
    def test_typing_is_applied_consistently(self) -> None:
        t: ColumnTyping = infer_column_typing(["1.234,56", "-", "980,00"])
        assert [t.coerce(v) for v in ["1.234,56", "-", "980,00"]] == [1234.56, None, 980.0]
