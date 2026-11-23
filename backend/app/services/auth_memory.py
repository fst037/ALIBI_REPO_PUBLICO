"""In-memory auth store: the fallback when no database is configured.

Canned identity/session, seeded from the user fixture. State is in-process and
resets on restart. Login stays permissive (any password, any email — the
fixture user's identity is borrowed for unknown ones) to preserve the prior
no-database behaviour used by tests. Accounts *registered* here are real
enough to exercise the code flow: they start unverified and need the emailed
code, with the same TTL, attempt cap and cooldown as the DB path. When a DB is
configured, the auth service routes to `app.repositories.auth` instead.
"""

import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core.google_identity import GoogleIdentity
from app.core.legal import TERMS_VERSION
from app.core.security import (
    CODE_MAX_ATTEMPTS,
    CODE_RESEND_COOLDOWN_SECONDS,
    CODE_TTL_MINUTES,
    code_matches,
    hash_code,
    new_code,
)
from app.repositories.auth import SendCode
from app.schemas.auth import AuthSession, EmailVerificationRequired, RegisterRequest, User

_SESSION_TTL_SECONDS = 3600
_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

_fixture_user = User(**json.loads((_FIXTURES / "user.json").read_text(encoding="utf-8")))


@dataclass
class _Code:
    hash: str
    created_at: datetime
    expires_at: datetime
    attempts: int = 0


# Module-level mutable state.
_users: dict[str, User] = {_fixture_user.email: _fixture_user}
_unverified: set[str] = set()  # emails registered here and not yet verified
_codes: dict[str, _Code] = {}  # email -> its one live code
_sessions: dict[str, User] = {}  # accessToken -> user
_google: dict[str, str] = {}  # Google `sub` -> the email of the account it is linked to


def _now() -> datetime:
    return datetime.now(timezone.utc)


def reset() -> None:
    """Forget registrations and codes. Sessions survive: some test modules
    open one at import time and reuse it across their tests."""
    _users.clear()
    _users[_fixture_user.email] = _fixture_user
    _unverified.clear()
    _codes.clear()
    _google.clear()


def email_exists(email: str) -> bool:
    return email in _users


def _issue_code(email: str, send: SendCode) -> None:
    code = new_code()
    _codes[email] = _Code(
        hash=hash_code(code),
        created_at=_now(),
        expires_at=_now() + timedelta(minutes=CODE_TTL_MINUTES),
    )
    send(email, code)


def _sent_recently(email: str) -> bool:
    live = _codes.get(email)
    return live is not None and live.created_at > _now() - timedelta(
        seconds=CODE_RESEND_COOLDOWN_SECONDS
    )


def register(request: RegisterRequest, send: SendCode) -> EmailVerificationRequired:
    """Create the unverified account and email its first code — no session yet."""
    _issue_code(request.email, send)  # first: a failed send leaves no account
    _users[request.email] = User(
        id="usr_" + secrets.token_urlsafe(6),
        name=request.name.strip(),
        email=request.email,
        workspace=request.workspace or _fixture_user.workspace,
    )
    _unverified.add(request.email)
    return EmailVerificationRequired(email=request.email)


def login(
    email: str, _password: str, remember: bool, send: SendCode
) -> AuthSession | EmailVerificationRequired:
    # Permissive: the fixture backend doesn't verify the password.
    if email not in _unverified:
        return _issue_session(email, remember)
    if not _sent_recently(email):
        _issue_code(email, send)
    return EmailVerificationRequired(email=email)


def verify_email(email: str, code: str, remember: bool) -> AuthSession | str:
    live = _codes.get(email)
    if live is None or live.expires_at <= _now():
        return "code_expired"
    if not code_matches(code, live.hash):
        live.attempts += 1
        if live.attempts >= CODE_MAX_ATTEMPTS:
            del _codes[email]
            return "too_many_attempts"
        return "invalid_code"
    del _codes[email]
    _unverified.discard(email)
    return _issue_session(email, remember)


def google_sign_in(
    identity: GoogleIdentity, remember: bool, accepted_terms_version: str | None
) -> tuple[AuthSession, bool] | str:
    """Same outcomes as the repository's `google_sign_in`, over the dicts above."""
    email = _google.get(identity.sub)
    if email is None:
        email = next((e for e in _users if e.lower() == identity.email.lower()), None)
        if email is not None:
            if email in _google.values():
                return "google_account_mismatch"
            _google[identity.sub] = email
            _unverified.discard(email)
            _codes.pop(email, None)
    if email is not None:
        return _issue_session(email, remember), False

    if accepted_terms_version is None:
        return "terms_required"
    if accepted_terms_version != TERMS_VERSION:
        return "terms_outdated"
    _users[identity.email] = User(
        id="usr_" + secrets.token_urlsafe(6),
        name=identity.name,
        email=identity.email,
        workspace="Personal",
    )
    _google[identity.sub] = identity.email
    return _issue_session(identity.email, remember), True


def resend_code(email: str, send: SendCode) -> str | None:
    if email not in _unverified:
        return None
    if _sent_recently(email):
        return "resend_too_soon"
    _issue_code(email, send)
    return None


def _issue_session(email: str, remember: bool = False) -> AuthSession:
    # Synthesize a user for unregistered emails (mirrors the old mock's login).
    user = _users.get(email) or User(
        id=_fixture_user.id,
        name=_fixture_user.name,
        email=email,
        workspace=_fixture_user.workspace,
    )
    token = "sess_" + secrets.token_urlsafe(24)
    # No expiry is tracked in this backend at all — a token lives until the
    # process restarts — so `remember` only shapes what the client is told.
    _sessions[token] = user
    return AuthSession(
        accessToken=token,
        expiresIn=None if remember else _SESSION_TTL_SECONDS,
        user=user,
    )


def get_user_by_token(token: str) -> User | None:
    return _sessions.get(token)


def logout(token: str) -> None:
    _sessions.pop(token, None)
