"""Ingestion failures, phrased for the person who uploaded the file.

The ingestion flow is explicit that an error must be
a concrete, non-technical sentence ("We couldn't read the file. Check that it
isn't damaged or password-protected.") — never a stack trace and never a
library name. So every failure the pipeline can produce is enumerated here
with the exact message the user sees, and the pipeline raises one of these
rather than letting a `BadZipFile` or a `UnicodeDecodeError` escape.

`code` is the machine-readable half that goes into the error envelope; the
frontend can key off it (e.g. to offer "upload another file" vs "retry").

Only *pipeline* failures live here. A request rejected before any byte is read
— an unsupported extension, a file over the plan's limit — is raised as
`services.uploads.UploadRejected`, which also carries the HTTP status.
"""

from __future__ import annotations


class IngestError(Exception):
    """Base class — carries a user-facing message and a stable code."""

    code = "ingest_failed"
    message = "No pudimos procesar el archivo. Probá de nuevo o subí otro."

    def __init__(self, detail: str | None = None) -> None:
        # `detail` is the technical cause; it goes to the logs, never to the user.
        super().__init__(detail or self.message)
        self.detail = detail


class UnsupportedFileType(IngestError):
    code = "unsupported_file_type"
    message = "Formato no admitido. Usá CSV, XLSX, PDF, DOCX o JSON."


class FileUnreadable(IngestError):
    code = "file_unreadable"
    message = (
        "No pudimos leer el archivo. Verificá que no esté dañado ni protegido con contraseña."
    )


class NoTablesFound(IngestError):
    code = "no_tables_found"
    message = (
        "No encontramos ninguna tabla en el archivo. Verificá que tenga filas y columnas "
        "con datos, y que la primera fila tenga los nombres de las columnas."
    )


class ScannedPdfUnsupported(IngestError):
    """A PDF with no text layer.

    v1 does not OCR — the ML spec defers LayoutLMv3/Donut to future work — so
    this is an honest "we can't", not a transient failure.
    """

    code = "scanned_pdf_unsupported"
    message = (
        "Este PDF parece un escaneo: no tiene texto seleccionable, así que no podemos extraer sus "
        "tablas. Probá con el archivo original (Excel o CSV) si lo tenés."
    )


class TooManyRows(IngestError):
    code = "too_many_rows"
    message = "La tabla tiene más filas de las que podemos procesar en tu plan."


class EmptyFile(IngestError):
    code = "empty_file"
    message = "El archivo está vacío."
