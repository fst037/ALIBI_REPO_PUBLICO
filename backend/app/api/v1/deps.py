"""Shared route dependencies (auth guard)."""

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.errors import ApiException
from app.schemas.auth import User
from app.services import auth as auth_store

# A real HTTP Bearer security scheme (vs a manual Header param): it documents
# the auth in OpenAPI and, crucially, makes Swagger UI's "Authorize" button
# actually send the `Authorization: Bearer <token>` header. auto_error=False so
# a missing/invalid header yields our error envelope (401) instead of
# HTTPBearer's default 403.
_bearer_scheme = HTTPBearer(auto_error=False)


def bearer_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> str:
    if credentials is None or not credentials.credentials:
        raise ApiException(401, "unauthorized", "Iniciá sesión para continuar.")
    return credentials.credentials


def require_user(token: str = Depends(bearer_token)) -> User:
    user = auth_store.get_user_by_token(token)
    if user is None:
        raise ApiException(401, "unauthorized", "Iniciá sesión para continuar.")
    return user
