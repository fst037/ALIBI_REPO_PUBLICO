"""Shared response shapes — the standard error envelope used across domains.

Mirrors the contracts' `Error` schema: `{ error: { code, message, details? } }`.
The frontend's `lib/api/http.ts` (`ApiError`) unwraps exactly this shape.
"""

from pydantic import BaseModel


class ErrorDetail(BaseModel):
    field: str | None = None
    message: str | None = None


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[ErrorDetail] | None = None


class ErrorEnvelope(BaseModel):
    error: ErrorBody
