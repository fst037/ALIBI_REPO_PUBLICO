"""Helpers to emit the standard error envelope with the right status code.

Every non-2xx response in the API is `{ error: { code, message, details? } }`
(see `app/schemas/common.py`). FastAPI's default error body is `{ detail: ... }`,
which the frontend's `ApiError` does not understand, so domain routes build
their error responses through here instead of raising bare `HTTPException`.
"""

import logging

from fastapi import Request
from fastapi.responses import JSONResponse

from app.schemas.common import ErrorBody, ErrorEnvelope

logger = logging.getLogger("alibi.errors")


def error_response(status_code: int, code: str, message: str) -> JSONResponse:
    envelope = ErrorEnvelope(error=ErrorBody(code=code, message=message))
    return JSONResponse(status_code=status_code, content=envelope.model_dump(exclude_none=True))


class ApiException(Exception):
    """Raise from routes/dependencies to emit the error envelope with a status.

    Useful where returning a `JSONResponse` inline is awkward — notably inside
    FastAPI dependencies (e.g. the auth guard), which cannot return a response.
    """

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


async def api_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiException)
    return error_response(exc.status_code, exc.code, exc.message)


async def unhandled_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Last resort for a bug, not a deliberate refusal.

    Without this, an exception no route anticipated (a real crash — a bad DuckDB
    cast, an unwrapped SDK error) falls through to Starlette's default handler,
    which replies with a plain-text "Internal Server Error" body when `debug` is
    off. The frontend's generated client always does `JSON.parse(body)` on the
    response — that throws a raw `SyntaxError`, which is not an `ApiError`, so
    the chat UI shows its generic catch-all copy under the same "Query blocked"
    guard styling as an actual guardrail rejection. A real crash then looks
    identical to a deliberate refusal, which is the one distinction a developer
    debugging Module A needs. Logging the real exception here (with traceback)
    is what makes it findable in the server log instead of only "blocked" in
    the browser.
    """
    logger.exception("unhandled exception on %s %s", _request.method, _request.url.path)
    return error_response(500, "internal_error", "Something went wrong. Try again.")
