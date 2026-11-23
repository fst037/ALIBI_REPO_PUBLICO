"""Deterministic type inference — rules, no model.

Per the Module B design,
basic typing is a *stable, well-defined* task and stays rule-based; only the
semantic layer (what a column MEANS) is learned. This module is that rule half.

The one non-obvious decision here: **ambiguity is resolved per column, never
per value.** `"1.234"` is 1234 to an Argentine accountant and 1.234 to an
American one, and no amount of staring at that single cell settles it. Looking
at the whole column does: if any value in it has a `,` as the decimal mark, or
a group of exactly three digits after a `.` in more than one place, the column
is es-AR and every value is read that way. The same trick disambiguates
`03/04/2024` — if *any* row in the column has a first component above 12, the
column is unambiguously day-first.

Getting this wrong is not cosmetic. Reading `1.234,56` as a string means the
user cannot sum their own revenue column; reading `1.500` as 1.5 silently
divides their numbers by a thousand.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation

from app.schemas.datasets import ColumnType, InferenceConfidence, VegaLiteType

# A value has to parse for this share of non-null cells for the type to stick.
_STRONG = 0.95
_WEAK = 0.80

_NULL_TOKENS = {
    "",
    "-",
    "--",
    "n/a",
    "na",
    "n.a.",
    "null",
    "none",
    "nan",
    "s/d",
    "sin dato",
    "sin datos",
    "#n/a",
    "#value!",
    "#¡valor!",
    "#div/0!",
    "#¡div/0!",
}

_TRUE_TOKENS = {"true", "verdadero", "sí", "si", "s", "yes", "y", "1", "x", "✓"}
_FALSE_TOKENS = {"false", "falso", "no", "n", "0", ""}

# Currency symbols and thousand-space variants seen in exported spreadsheets.
_CURRENCY = re.compile(r"[$€£¥]|\bARS\b|\bUSD\b|\bEUR\b", re.IGNORECASE)
_SPACES = re.compile(r"[\s  ]")

_DATE_SEP = re.compile(r"^(\d{1,4})[/\-.](\d{1,2})[/\-.](\d{1,4})$")
_ISO_DATETIME = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[T ]\d{2}:\d{2}")

_MONTHS_ES = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12,
}


def is_null(value: str | None) -> bool:
    return value is None or value.strip().lower() in _NULL_TOKENS


def _clean(value: str) -> str:
    """Strip currency symbols and all flavours of space (incl. non-breaking,
    which is what Excel emits for thousands separators in es-AR locales)."""
    return _SPACES.sub("", _CURRENCY.sub("", value)).strip()


# ---------------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------------


class NumberStyle:
    """Which decimal convention a column uses."""

    ES = "es"  # 1.234,56
    EN = "en"  # 1,234.56
    PLAIN = "plain"  # 1234.56 — no grouping, unambiguous


def _detect_number_style(values: list[str]) -> str:
    """Decide the column's convention from the evidence available.

    Only a value containing BOTH separators, or a comma used as a decimal
    mark, is real evidence. `1.234` alone is not — it votes for neither, and
    counting it would let the first row of a column decide for all the others.
    """
    es_votes = en_votes = 0
    for raw in values:
        v = _clean(raw)
        has_dot, has_comma = "." in v, "," in v
        if has_dot and has_comma:
            # Whichever separator comes last is the decimal mark.
            if v.rfind(",") > v.rfind("."):
                es_votes += 1
            else:
                en_votes += 1
        elif has_comma and re.fullmatch(r"-?\d+,\d{1,8}", v):
            # A comma with something other than 3 trailing digits can only be
            # a decimal mark ("12,5"). With exactly 3 it is ambiguous
            # ("12,500") and deliberately not counted.
            if len(v.split(",")[1]) != 3:
                es_votes += 1
        elif has_dot and re.fullmatch(r"-?\d+\.\d{1,8}", v):
            if len(v.split(".")[1]) != 3:
                en_votes += 1
    if es_votes > en_votes:
        return NumberStyle.ES
    if en_votes > es_votes:
        return NumberStyle.EN
    return NumberStyle.PLAIN


def parse_number(value: str, style: str = NumberStyle.PLAIN) -> Decimal | None:
    """Parse one cell under a known column convention. None if it isn't a number."""
    v = _clean(value)
    if not v:
        return None
    negative = False
    # Accounting notation: (1.234,56) means negative.
    if v.startswith("(") and v.endswith(")"):
        negative, v = True, v[1:-1]
    v = v.rstrip("%")
    if not v:
        return None

    if style == NumberStyle.ES:
        v = v.replace(".", "").replace(",", ".")
    elif style == NumberStyle.EN:
        v = v.replace(",", "")
    else:
        # No grouping evidence: a lone comma is still far more likely to be an
        # es-AR decimal mark than a thousands separator in this product's
        # audience, and a lone dot is already valid.
        if "," in v and "." not in v:
            v = v.replace(",", ".")
        else:
            v = v.replace(",", "")

    if not re.fullmatch(r"[+-]?\d*\.?\d+([eE][+-]?\d+)?", v):
        return None
    try:
        parsed = Decimal(v)
    except InvalidOperation:
        return None
    return -parsed if negative else parsed


def _looks_percent(value: str) -> bool:
    return _clean(value).endswith("%")


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------


def _detect_day_first(values: list[str]) -> bool:
    """True when the column is day-first.

    Evidence beats default: a single row with a first component above 12 proves
    day-first, and a single row with a *second* component above 12 proves
    month-first. With no evidence either way we assume day-first, which is the
    correct default for the Spanish-speaking users this product targets — and
    the opposite of what every US-defaulting parser would do.
    """
    for raw in values:
        m = _DATE_SEP.match(raw.strip())
        if not m:
            continue
        a, b, _ = m.groups()
        if len(a) == 4:  # ISO, tells us nothing about d/m order
            continue
        if int(a) > 12:
            return True
        if int(b) > 12:
            return False
    return True


def parse_date(value: str, day_first: bool = True) -> date | None:
    v = value.strip()
    if not v:
        return None

    iso = _ISO_DATETIME.match(v)
    if iso:
        y, m, d = (int(x) for x in iso.groups())
        return _safe_date(y, m, d)

    m = _DATE_SEP.match(v)
    if m:
        a, b, c = (int(x) for x in m.groups())
        if len(m.group(1)) == 4:  # yyyy-mm-dd
            return _safe_date(a, b, c)
        if len(m.group(3)) == 4 or c > 31:  # dd/mm/yyyy or mm/dd/yyyy
            day, month = (a, b) if day_first else (b, a)
            return _safe_date(_expand_year(c), month, day)
        # Two-digit year: dd/mm/yy
        day, month = (a, b) if day_first else (b, a)
        return _safe_date(_expand_year(c), month, day)

    # "12-ene-2024" / "12 ene 2024"
    m = re.fullmatch(r"(\d{1,2})[\s\-/]?([a-záéíóú]{3,10})\.?[\s\-/]?(\d{2,4})", v, re.IGNORECASE)
    if m:
        day, month_name, year = m.groups()
        month = _MONTHS_ES.get(month_name[:3].lower())
        if month:
            return _safe_date(_expand_year(int(year)), month, int(day))
    return None


def _expand_year(year: int) -> int:
    if year >= 100:
        return year
    # Two-digit years: 00-69 → 2000s, 70-99 → 1900s (the POSIX convention).
    return 2000 + year if year < 70 else 1900 + year


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Booleans
# ---------------------------------------------------------------------------


def parse_bool(value: str) -> bool | None:
    v = value.strip().lower()
    if v in _TRUE_TOKENS:
        return True
    if v in _FALSE_TOKENS and v != "":
        return False
    return None


# ---------------------------------------------------------------------------
# Column-level inference
# ---------------------------------------------------------------------------


class ColumnTyping:
    """The decision made for one column, plus how to apply it to a value."""

    __slots__ = ("column_type", "sql_type", "vl_type", "confidence", "number_style", "day_first")

    def __init__(
        self,
        column_type: ColumnType,
        sql_type: str,
        vl_type: VegaLiteType,
        confidence: InferenceConfidence,
        number_style: str = NumberStyle.PLAIN,
        day_first: bool = True,
    ) -> None:
        self.column_type = column_type
        self.sql_type = sql_type
        self.vl_type = vl_type
        self.confidence = confidence
        self.number_style = number_style
        self.day_first = day_first

    def coerce(self, value: str | None):  # noqa: ANN201 - heterogeneous by design
        """Convert one raw cell to its typed value, or None."""
        if is_null(value):
            return None
        assert value is not None
        if self.column_type is ColumnType.BOOLEAN:
            return parse_bool(value)
        if self.column_type is ColumnType.DATE:
            return parse_date(value, self.day_first)
        if self.column_type in (ColumnType.NUMBER, ColumnType.WHOLE_NUMBER, ColumnType.PERCENTAGE):
            parsed = parse_number(value, self.number_style)
            if parsed is None:
                return None
            if self.column_type is ColumnType.WHOLE_NUMBER:
                return int(parsed)
            return float(parsed)
        return value.strip()


def infer_column_typing(values: list[str | None]) -> ColumnTyping:
    """Infer the type of a column from its values.

    Order matters: boolean before number, because `1`/`0` parses as both and a
    two-valued 1/0 column is much more useful as a flag; and percentage before
    plain number, because `45%` parses as 45 once the sign is stripped.
    """
    present = [v for v in values if not is_null(v)]
    if not present:
        # An all-empty column is text — guessing anything else from no
        # evidence would just be noise the user has to correct.
        return ColumnTyping(
            ColumnType.TEXT, "TEXT", VegaLiteType.nominal, InferenceConfidence.LOW
        )

    total = len(present)
    stripped = [v.strip() for v in present]  # type: ignore[union-attr]

    # --- boolean --------------------------------------------------------
    distinct = {s.lower() for s in stripped}
    bool_hits = sum(1 for s in stripped if parse_bool(s) is not None)
    if bool_hits / total >= _STRONG and len(distinct) <= 3:
        # A column of all-"1" is not a boolean, it's a constant; requiring two
        # distinct values keeps id-like integer columns from becoming flags.
        if len(distinct) >= 2 or not distinct <= {"0", "1"}:
            return ColumnTyping(
                ColumnType.BOOLEAN, "BOOLEAN", VegaLiteType.nominal, _confidence(bool_hits / total)
            )

    # --- percentage -----------------------------------------------------
    pct_hits = sum(1 for s in stripped if _looks_percent(s))
    if pct_hits / total >= _STRONG:
        style = _detect_number_style(stripped)
        parsed_ok = sum(1 for s in stripped if parse_number(s, style) is not None)
        if parsed_ok / total >= _STRONG:
            return ColumnTyping(
                ColumnType.PERCENTAGE,
                "DOUBLE",
                VegaLiteType.quantitative,
                _confidence(parsed_ok / total),
                number_style=style,
            )

    # --- number ---------------------------------------------------------
    style = _detect_number_style(stripped)
    numbers = [parse_number(s, style) for s in stripped]
    num_hits = sum(1 for n in numbers if n is not None)
    num_rate = num_hits / total
    if num_rate >= _WEAK:
        all_integral = all(n == n.to_integral_value() for n in numbers if n is not None)
        # A number written with a decimal mark stays a float even if this
        # sample happens to be whole — "1,00" is a price, not a count. Read off
        # `numbers` rather than re-parsing: this is a whole extra pass over the
        # column, and typing is already the slowest step in ingestion.
        wrote_decimals = any(
            "," in _clean(s) or "." in _clean(s)
            for s, n in zip(stripped, numbers)
            if n is not None
        )
        if all_integral and not wrote_decimals:
            return ColumnTyping(
                ColumnType.WHOLE_NUMBER,
                "BIGINT",
                VegaLiteType.quantitative,
                _confidence(num_rate),
                number_style=style,
            )
        return ColumnTyping(
            ColumnType.NUMBER,
            "DOUBLE",
            VegaLiteType.quantitative,
            _confidence(num_rate),
            number_style=style,
        )

    # --- date -----------------------------------------------------------
    day_first = _detect_day_first(stripped)
    date_hits = sum(1 for s in stripped if parse_date(s, day_first) is not None)
    if date_hits / total >= _WEAK:
        return ColumnTyping(
            ColumnType.DATE,
            "DATE",
            VegaLiteType.temporal,
            _confidence(date_hits / total),
            day_first=day_first,
        )

    # --- text -----------------------------------------------------------
    # Text is `nominal` to Vega-Lite whether it's a 5-value category or free
    # prose; the distinction that matters downstream is cardinality, and
    # Module C reads that off the profile rather than the type hint.
    return ColumnTyping(ColumnType.TEXT, "TEXT", VegaLiteType.nominal, InferenceConfidence.HIGH)


def _confidence(rate: float) -> InferenceConfidence:
    if rate >= _STRONG:
        return InferenceConfidence.HIGH
    if rate >= _WEAK:
        return InferenceConfidence.MEDIUM
    return InferenceConfidence.LOW
