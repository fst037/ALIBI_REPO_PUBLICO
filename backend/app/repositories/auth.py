"""Postgres repository for the `auth` domain.

Real identity + sessions backed by the database:
  * register     -> INSERT an unverified user (bcrypt hash) + an email code.
  * login        -> verify the password; a verified account gets its session,
                    an unverified one a fresh code instead.
  * verify-email -> check the code (TTL, attempt cap, single use), mark the
                    email verified, issue a persisted bearer token.
  * google       -> a Google ID token already checked by
                    `app.core.google_identity`: sign in the account linked to it,
                    link an existing account by email, or create one.
  * token lookup -> resolve the session (checking expiry) to its user.

Codes are stored hashed (`app.core.security.hash_code`). This module never
sends mail: the functions that create a code return it in plain text and the
service sends it inside the same transaction, so a failed send rolls the code
(and, on register, the user) back. Everything here is local + DB.
"""

from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, func, or_, select

from app.core.google_identity import GoogleIdentity
from app.core.legal import TERMS_VERSION
from app.core.security import (
    CODE_MAX_ATTEMPTS,
    CODE_RESEND_COOLDOWN_SECONDS,
    CODE_TTL_MINUTES,
    code_matches,
    hash_code,
    hash_password,
    new_code,
    new_id,
    new_token,
    verify_password,
)
from app.db.models import AuthSessionModel, User as UserRow, VerificationCode
from app.schemas.auth import AuthSession, EmailVerificationRequired, RegisterRequest, User

_SESSION_TTL_SECONDS = 3600
_PURPOSE = "email_verify"

#: Delivers a code: `(email, code) -> None`, raising if it could not.
SendCode = Callable[[str, str], None]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    # SQLite (the repository tests) hands timestamps back naive; Postgres doesn't.
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _user(row: UserRow) -> User:
    return User(id=row.id, name=row.name, email=row.email, workspace=row.workspace)


def _user_by_email(session: Session, email: str) -> UserRow | None:
    return session.exec(select(UserRow).where(UserRow.email == email)).first()


def email_exists(session: Session, email: str) -> bool:
    return _user_by_email(session, email) is not None


def _live_code(session: Session, email: str) -> VerificationCode | None:
    """The newest unconsumed code for `email`, expired or not."""
    return session.exec(
        select(VerificationCode)
        .where(
            VerificationCode.email == email,
            VerificationCode.purpose == _PURPOSE,
            VerificationCode.consumed == False,  # noqa: E712 — SQLAlchemy needs `== False`, not `is False`
        )
        .order_by(VerificationCode.created_at.desc())
    ).first()


def _issue_code(session: Session, user_row: UserRow) -> str:
    """Burn any live code for this email and store a new one. Returns it in
    plain text — the only time it exists unhashed — for the caller to send."""
    for old in session.exec(
        select(VerificationCode).where(
            VerificationCode.email == user_row.email,
            VerificationCode.purpose == _PURPOSE,
            VerificationCode.consumed == False,  # noqa: E712
        )
    ):
        old.consumed = True
        session.add(old)
    code = new_code()
    session.add(
        VerificationCode(
            id=new_id("vcd"),
            user_id=user_row.id,
            email=user_row.email,
            purpose=_PURPOSE,
            code=hash_code(code),
            expires_at=_now() + timedelta(minutes=CODE_TTL_MINUTES),
            created_at=_now(),
        )
    )
    session.flush()
    return code


def _sent_recently(session: Session, email: str) -> bool:
    live = _live_code(session, email)
    return live is not None and _aware(live.created_at) > _now() - timedelta(
        seconds=CODE_RESEND_COOLDOWN_SECONDS
    )


def register(
    session: Session, request: RegisterRequest, send: SendCode
) -> EmailVerificationRequired:
    """Create the unverified account and email its first code.

    No session yet: it opens at `verify_email`, once the address is proven.
    The client drops any previous token before calling this, so nobody keeps
    browsing as the last user in between.
    """
    row = UserRow(
        id=new_id("usr"),
        email=request.email,
        name=request.name.strip(),
        workspace=request.workspace or "Personal",
        password_hash=hash_password(request.password),
        email_verified=False,
        # The route already refused any version other than the current one.
        terms_version=request.acceptedTermsVersion,
        terms_accepted_at=datetime.now(timezone.utc),
    )
    session.add(row)
    session.flush()
    send(row.email, _issue_code(session, row))
    return EmailVerificationRequired(email=row.email)


def _issue_session(session: Session, user_row: UserRow, remember: bool = False) -> AuthSession:
    """Open a session. `remember` means it has no expiry at all.

    "Keep me signed in" is taken literally: the row's `expires_at` is NULL and
    the only thing that ends the session is `POST /auth/logout`. A long-but-fixed
    window (the "30 days" the UI used to promise, and never implemented) would
    still sign the user out one day for no reason they could see.
    """
    token = new_token()
    expires_at = None if remember else _now() + timedelta(seconds=_SESSION_TTL_SECONDS)
    session.add(AuthSessionModel(token=token, user_id=user_row.id, expires_at=expires_at))
    session.flush()
    return AuthSession(
        accessToken=token,
        expiresIn=None if remember else _SESSION_TTL_SECONDS,
        user=_user(user_row),
    )


def login(
    session: Session, email: str, password: str, remember: bool, send: SendCode
) -> AuthSession | EmailVerificationRequired | None:
    """None = bad credentials. A verified account gets its session; an
    unverified one gets a fresh code instead (unless one went out under a
    minute ago — then the user is just sent back to the code they have)."""
    user_row = _user_by_email(session, email)
    if user_row is None or not verify_password(password, user_row.password_hash):
        return None
    if user_row.email_verified:
        return _issue_session(session, user_row, remember)
    if not _sent_recently(session, email):
        send(email, _issue_code(session, user_row))
    return EmailVerificationRequired(email=email)


def verify_email(
    session: Session, email: str, code: str, remember: bool
) -> AuthSession | str:
    """Redeem the code and open the session, or return the error code.

    Returns instead of raising on purpose: a wrong guess must still be counted,
    and an exception would roll that increment back with the transaction.
    """
    live = _live_code(session, email)
    if live is None or _aware(live.expires_at) <= _now():
        return "code_expired"
    if not code_matches(code, live.code):
        live.attempts += 1
        if live.attempts >= CODE_MAX_ATTEMPTS:
            live.consumed = True
        session.add(live)
        return "too_many_attempts" if live.consumed else "invalid_code"

    live.consumed = True
    session.add(live)
    user_row = session.get(UserRow, live.user_id)
    user_row.email_verified = True
    session.add(user_row)
    return _issue_session(session, user_row, remember)


def google_sign_in(
    session: Session,
    identity: GoogleIdentity,
    remember: bool,
    accepted_terms_version: str | None,
) -> tuple[AuthSession, bool] | str:
    """The session for a verified Google identity, and whether its account is new.

    Or an error code, returned like `verify_email`'s: `terms_required` (no
    account yet, and no consent to the Terms sent — the client asks for it and
    retries with the same credential), `terms_outdated`, or
    `google_account_mismatch` (the address belongs to an account already linked
    to a *different* Google account).

    An existing account with the same address is **linked**, not duplicated:
    Google has just proven the person owns that mailbox. If that account had
    never verified its email, its password is dropped on linking — it was set by
    whoever typed the address, who need not be its owner, and keeping it would
    hand them an account the real owner now uses (pre-account takeover). A
    verified account keeps its password: its owner set it.
    """
    row = session.exec(select(UserRow).where(UserRow.google_sub == identity.sub)).first()
    if row is None:
        # Case-insensitive: Google lowercases the address, a signup form may not have.
        row = session.exec(
            select(UserRow).where(func.lower(UserRow.email) == identity.email.lower())
        ).first()
        if row is not None:
            if row.google_sub is not None:
                return "google_account_mismatch"
            if not row.email_verified:
                row.password_hash = None
            row.email_verified = True
            row.google_sub = identity.sub
            row.updated_at = datetime.utcnow()
            session.add(row)

    if row is not None:
        return _issue_session(session, row, remember), False

    if accepted_terms_version is None:
        return "terms_required"
    if accepted_terms_version != TERMS_VERSION:
        return "terms_outdated"
    row = UserRow(
        id=new_id("usr"),
        email=identity.email,
        name=identity.name,
        workspace="Personal",
        password_hash=None,
        email_verified=True,
        google_sub=identity.sub,
        terms_version=accepted_terms_version,
        terms_accepted_at=_now(),
    )
    session.add(row)
    session.flush()
    return _issue_session(session, row, remember), True


def resend_code(session: Session, email: str, send: SendCode) -> str | None:
    """Send a new code to an unverified account. Silent for unknown or already
    verified emails (the 202 says nothing either way, so accounts don't leak);
    "resend_too_soon" inside the cooldown."""
    user_row = _user_by_email(session, email)
    if user_row is None or user_row.email_verified:
        return None
    if _sent_recently(session, email):
        return "resend_too_soon"
    send(email, _issue_code(session, user_row))
    return None


def get_user_by_token(session: Session, token: str) -> User | None:
    # A NULL `expires_at` is a session that never lapses ("Keep me signed in"),
    # so it has to be admitted explicitly — `NULL > now()` is NULL, not true,
    # and a plain comparison would have signed those users out immediately.
    auth_session = session.exec(
        select(AuthSessionModel).where(
            AuthSessionModel.token == token,
            or_(
                AuthSessionModel.expires_at.is_(None),
                AuthSessionModel.expires_at > datetime.now(timezone.utc),
            ),
        )
    ).first()
    if auth_session is None:
        return None
    user_row = session.get(UserRow, auth_session.user_id)
    return _user(user_row) if user_row else None


def logout(session: Session, token: str) -> None:
    auth_session = session.get(AuthSessionModel, token)
    if auth_session is not None:
        session.delete(auth_session)
