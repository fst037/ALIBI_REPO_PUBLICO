"""Export requests (CSV/PNG/PDF) with plan gating.

Validates the source and the plan, and returns a short-lived download URL that
no stored file backs. Which formats a plan may export is `plans.limits.exportFormats` (Free: CSV;
Pro: CSV/PNG/PDF) — anything else is a 402.
"""

import secrets
from datetime import datetime, timedelta, timezone

from app.core.errors import ApiException
from app.schemas.plans import ExportFormat
from app.schemas.exports import ExportFromMessage, ExportRequest, ExportResult
from app.services import billing as billing_store
from app.services import conversations as chat_store
from app.services import dashboards as dashboards_store

_IMAGE_FORMATS = {"png", "pdf"}
_URL_TTL = timedelta(minutes=15)


def create_export(owner_id: str, request: ExportRequest) -> ExportResult:
    fmt = request.format.value

    billing_store.require_export_format(owner_id, ExportFormat(fmt))
    if fmt in _IMAGE_FORMATS:
        if request.chartType is None:
            raise ApiException(
                422, "validation_error", "Falta el tipo de gráfico para exportar como imagen o PDF."
            )

    if isinstance(request.source, ExportFromMessage):
        if chat_store.get_assistant_message(owner_id, request.source.messageId) is None:
            raise ApiException(404, "not_found", "No se encontró el resultado de origen.")
    else:
        if dashboards_store.get_saved_chart(owner_id, request.source.savedChartId) is None:
            raise ApiException(404, "not_found", "No se encontró el gráfico de origen.")

    expires_at = datetime.now(timezone.utc) + _URL_TTL
    return ExportResult(
        downloadUrl=f"https://exports.alibi.local/{secrets.token_urlsafe(12)}.{fmt}",
        format=request.format,
        expiresAt=expires_at.isoformat(),
    )
