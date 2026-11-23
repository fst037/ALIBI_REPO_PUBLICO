"""Work out what a file actually is, before trying to parse it.

Three questions, none of which the user should ever be asked:

1. **What format is it?** The extension is a hint, not evidence — people rename
   files, and a `.csv` exported from Excel is sometimes a real `.xlsx`. Magic
   bytes decide; the extension only breaks ties.
2. **What encoding?** A CSV exported from Excel on a Spanish-locale Windows
   machine is Latin-1 or Windows-1252 far more often than UTF-8. Decoding it as
   UTF-8 either raises or silently mangles every accented city name.
3. **What delimiter?** In es-AR locales Excel writes `;` (because `,` is the
   decimal mark). Assuming a comma turns the entire file into one column.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

from app.ingest.errors import EmptyFile, UnsupportedFileType
from app.schemas.datasets import DatasetFileType

# Signatures, checked in order. ZIP covers both xlsx and docx — they're both
# zip containers, so the extension breaks that tie.
_ZIP_MAGIC = b"PK\x03\x04"
_PDF_MAGIC = b"%PDF"
_OLE_MAGIC = b"\xd0\xcf\x11\xe0"  # legacy .xls

_EXTENSION_MAP = {
    "csv": DatasetFileType.csv,
    "tsv": DatasetFileType.csv,
    "txt": DatasetFileType.csv,
    "xlsx": DatasetFileType.xlsx,
    "xlsm": DatasetFileType.xlsx,
    "xls": DatasetFileType.xlsx,
    "pdf": DatasetFileType.pdf,
    "docx": DatasetFileType.docx,
    "json": DatasetFileType.json,
    "jsonl": DatasetFileType.json,
    "ndjson": DatasetFileType.json,
}

DELIMITER_CANDIDATES = [",", ";", "\t", "|"]


def extension_of(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def file_type_from_extension(filename: str) -> DatasetFileType:
    """Extension only — the check the API can make without the bytes.

    `POST /uploads` hands out a presigned URL and never sees the file, so this
    is all it has to reject the obviously-unsupported early. It is a filter,
    not the decision: `detect_file_type` re-checks the actual content once the
    object lands, and a `.csv` full of PDF is caught there.
    """
    ext = extension_of(filename)
    if ext not in _EXTENSION_MAP:
        raise UnsupportedFileType(f"unsupported extension .{ext or 'none'}")
    return _EXTENSION_MAP[ext]


def detect_file_type(filename: str, data: bytes) -> DatasetFileType:
    """Content first, extension second."""
    if not data:
        raise EmptyFile("zero-length upload")

    head = data[:8]
    if head.startswith(_PDF_MAGIC):
        return DatasetFileType.pdf
    if head.startswith(_OLE_MAGIC):
        # Legacy .xls — a different container that openpyxl cannot read.
        raise UnsupportedFileType(
            "legacy .xls (OLE2) is not supported; ask the user to re-save as .xlsx"
        )
    if head.startswith(_ZIP_MAGIC):
        ext = extension_of(filename)
        if ext == "docx":
            return DatasetFileType.docx
        # An OOXML workbook always contains this part; checking it beats
        # trusting a possibly-wrong extension.
        if b"xl/workbook.xml" in data[:8192] or ext in {"xlsx", "xlsm"}:
            return DatasetFileType.xlsx
        if b"word/document.xml" in data[:8192]:
            return DatasetFileType.docx
        return DatasetFileType.xlsx

    ext = extension_of(filename)
    if ext in _EXTENSION_MAP:
        mapped = _EXTENSION_MAP[ext]
        # A file claiming to be xlsx/pdf/docx but lacking the magic bytes is
        # corrupt or misnamed; only the text formats can be trusted this way.
        if mapped in (DatasetFileType.csv, DatasetFileType.json):
            return mapped
        raise UnsupportedFileType(f"extension .{ext} but content is not a {mapped.value}")

    # No extension and no signature: if it decodes as text, treat it as
    # delimited — that covers the "exported without an extension" case.
    try:
        data[:4096].decode("utf-8")
    except UnicodeDecodeError:
        raise UnsupportedFileType(f"unrecognised binary content (.{ext or 'none'})") from None
    return DatasetFileType.csv


# ---------------------------------------------------------------------------
# Text decoding
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class DecodedText:
    text: str
    encoding: str
    # True when we fell back to a lossy decode; the caller warns the user that
    # some characters may look wrong rather than failing the whole upload.
    lossy: bool = False


# Share of non-ASCII bytes above which the file really is written in another
# script — Cyrillic in cp1251, Japanese in Shift-JIS, where nearly every letter
# is a high byte. Below it the file is a Western-locale export whose handful of
# high bytes are accents, and there is not enough signal for a statistical
# guess to beat the cp1252 prior. Even a column made entirely of accented
# Spanish city names sits near 10%.
_NON_LATIN_BYTE_SHARE = 0.30


def _plausible(encoding: str, data: bytes) -> bool:
    """Sanity-check a statistically guessed encoding.

    Statistical detectors are unreliable on short inputs, and the damage is
    silent: the bytes decode without raising into perfectly valid — and
    completely wrong — text, so nothing downstream flags it. The column just
    fills with gibberish.

    Two different wrong answers, from the same 14-byte fixture:

    * A **wide** guess. `"ciudad\\nCórdoba"` in cp1252 reads as UTF-16LE into
      CJK. The tell is NUL bytes: genuine UTF-16/32 text in a Latin script is
      at least half NULs, a single-byte encoding has essentially none. This one
      is worth keeping, because a BOM-less UTF-16 file is real and the NUL
      count identifies it honestly.
    * A **single-byte** guess of the wrong codepage. charset-normalizer 3.4
      called that fixture UTF-16LE and 3.5 calls it cp1006, rendering ó as an
      Arabic presentation form. Naming the bad encodings does not work — the
      answer changes with the detector's version — and neither does checking
      that the output looks Latin, because cp775 decodes a longer sample of the
      same data into Ē and ¾ and would pass. So for mostly-ASCII data it is not
      consulted: cp1252 is right far more often than whatever the detector
      lands on, which is what the module docstring already assumes.
    """
    sample = data[:4096]
    if not sample:
        return False

    normalised = encoding.lower().replace("-", "_")
    if normalised.startswith(("utf_16", "utf_32")):
        return sample.count(0) / len(sample) > 0.1

    high_bytes = sum(1 for byte in sample if byte > 0x7F)
    return high_bytes / len(sample) > _NON_LATIN_BYTE_SHARE


def decode_text(data: bytes) -> DecodedText:
    """Decode bytes to text, trying hardest to succeed rather than to be strict.

    A mangled accent is annoying; a rejected upload is worse. So the last
    resort is a lossy Latin-1 decode (which cannot fail) with a flag set,
    rather than an exception.
    """
    # A BOM is definitive — no need to guess.
    for bom, enc in (
        (b"\xef\xbb\xbf", "utf-8-sig"),
        (b"\xff\xfe\x00\x00", "utf-32-le"),
        (b"\x00\x00\xfe\xff", "utf-32-be"),
        (b"\xff\xfe", "utf-16-le"),
        (b"\xfe\xff", "utf-16-be"),
    ):
        if data.startswith(bom):
            return DecodedText(data.decode(enc, errors="replace"), enc)

    try:
        return DecodedText(data.decode("utf-8"), "utf-8")
    except UnicodeDecodeError:
        pass

    try:
        from charset_normalizer import from_bytes

        best = from_bytes(data).best()
        if best is not None and best.encoding and _plausible(best.encoding, data):
            return DecodedText(str(best), best.encoding)
    except ImportError:  # pragma: no cover - the dependency is declared
        pass

    # cp1252 is the single most likely source for a Spanish-locale Excel
    # export, and Latin-1 decodes any byte sequence without raising.
    for enc in ("cp1252", "latin-1"):
        try:
            return DecodedText(data.decode(enc), enc, lossy=True)
        except UnicodeDecodeError:
            continue
    return DecodedText(data.decode("latin-1", errors="replace"), "latin-1", lossy=True)


# ---------------------------------------------------------------------------
# Delimiter sniffing
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class DelimiterGuess:
    delimiter: str
    columns: int
    consistency: float


def sniff_delimiter(text: str, sample_lines: int = 60) -> DelimiterGuess:
    """Pick the delimiter that yields the most *consistent* row width.

    `csv.Sniffer` is deliberately not used: it raises on files it can't figure
    out, and it has no notion of "this one gives 7 columns on every row while
    that one gives 1" — which is exactly the evidence that settles `,` vs `;`
    on a Spanish-locale export full of `1.234,56`.
    """
    sample = "\n".join(text.splitlines()[:sample_lines])
    if not sample.strip():
        raise EmptyFile("no text content to sniff")

    best = DelimiterGuess(",", 1, 0.0)
    for candidate in DELIMITER_CANDIDATES:
        try:
            rows = [
                r
                for r in csv.reader(io.StringIO(sample), delimiter=candidate)
                if any(c.strip() for c in r)
            ]
        except csv.Error:
            continue
        if not rows:
            continue

        widths: dict[int, int] = {}
        for row in rows:
            widths[len(row)] = widths.get(len(row), 0) + 1
        modal_width, modal_count = max(widths.items(), key=lambda kv: (kv[1], kv[0]))
        if modal_width < 2:
            continue
        consistency = modal_count / len(rows)
        # Prefer consistency first, then more columns: a delimiter that splits
        # every row into 7 fields beats one that splits half of them into 2.
        if (consistency, modal_width) > (best.consistency, best.columns):
            best = DelimiterGuess(candidate, modal_width, consistency)

    return best
