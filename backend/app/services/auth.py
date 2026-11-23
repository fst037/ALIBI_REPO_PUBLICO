"""`auth` domain service — the swap point between storage backends.

When a database is configured (ALIBI_DATABASE_URL set), each call borrows a
connection and delegates to `app.repositories.auth` (real users, bcrypt
password hashes, persisted sessions and codes). Without it, it falls back to
the in-memory fixtures in `auth_memory` — so tests keep working.

Both backends receive `mailer.send_code` as their `send` callback and call it
inside the transaction: a send that fails raises `MailUnavailable`, which rolls
the new code (and, on register, the new account) back.
"""

from app.core import mailer
from app.core.google_identity import GoogleIdentity
from app.core.security import CODE_TTL_MINUTES
from app.db import pool
from app.db.engine import session_scope
from app.repositories import auth as repo
from app.schemas.auth import AuthSession, EmailVerificationRequired, RegisterRequest, User
from app.services import auth_memory as mem


def _send(email: str, code: str) -> None:
    # Looked up at call time so the test suite can stub `mailer.send_code`.
    mailer.send_code(email, code, CODE_TTL_MINUTES)


def email_exists(email: str) -> bool:
    if pool.is_configured():
        with session_scope() as session:
            return repo.email_exists(session, email)
    return mem.email_exists(email)


def register(request: RegisterRequest) -> EmailVerificationRequired:
    """Create the unverified account and email its code (see the repository)."""
    if pool.is_configured():
        with session_scope() as session:
            return repo.register(session, request, _send)
    return mem.register(request, _send)


def login(
    email: str, password: str, remember: bool = False
) -> AuthSession | EmailVerificationRequired | None:
    if pool.is_configured():
        with session_scope() as session:
            return repo.login(session, email, password, remember, _send)
    return mem.login(email, password, remember, _send)


def verify_email(email: str, code: str, remember: bool = False) -> AuthSession | str:
    """The session, or an error code: invalid_code / code_expired / too_many_attempts."""
    if pool.is_configured():
        with session_scope() as session:
            return repo.verify_email(session, email, code, remember)
    return mem.verify_email(email, code, remember)


def google_sign_in(
    identity: GoogleIdentity, remember: bool, accepted_terms_version: str | None
) -> tuple[AuthSession, bool] | str:
    """The session and whether the account is new, or an error code:
    terms_required / terms_outdated / google_account_mismatch."""
    if pool.is_configured():
        with session_scope() as session:
            return repo.google_sign_in(session, identity, remember, accepted_terms_version)
    return mem.google_sign_in(identity, remember, accepted_terms_version)


def resend_code(email: str) -> str | None:
    """None, or "resend_too_soon" inside the cooldown."""
    if pool.is_configured():
        with session_scope() as session:
            return repo.resend_code(session, email, _send)
    return mem.resend_code(email, _send)


def get_user_by_token(token: str) -> User | None:
    if pool.is_configured():
        with session_scope() as session:
            return repo.get_user_by_token(session, token)
    return mem.get_user_by_token(token)


def logout(token: str) -> None:
    if pool.is_configured():
        with session_scope() as session:
            repo.logout(session, token)
        return
    mem.logout(token)
