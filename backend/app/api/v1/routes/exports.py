"""`exports` domain routes (visualization): CSV/PNG/PDF with plan gating."""

from fastapi import APIRouter, Depends

from app.api.v1.deps import require_user
from app.schemas.auth import User
from app.schemas.exports import ExportRequest, ExportResult
from app.services import exports as store

router = APIRouter(tags=["exports"])


@router.post(
    "/exports", response_model=ExportResult, status_code=201, summary="Export to CSV / PNG / PDF."
)
def create_export(request: ExportRequest, user: User = Depends(require_user)) -> ExportResult:
    return store.create_export(user.id, request)
