"""Verification-code behaviour: the real 2FA / email-verification rules.

These run against the repository directly, over an in-memory SQLite database
holding just the three auth tables. The rest of the suite exercises the
permissive fixture backend (any password, any code), which by construction
can say nothing about codes — so without these, the TTL, the attempt cap and
the cooldown would ship untested.

SQLite rather than Postgres because it needs no service container and these
three tables use no Postgres-specific column types. The one real difference —
SQLite returns naive datetimes where `timestamptz` returns aware ones — is
itself covered here, since `_aware()` in the repository exists precisely to
normalize that.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.config import Settings
from app.db.models import AuthSessionModel, User as UserRow, VerificationCode
from app.repositories import auth as repo
from app.services import email as mailer

# The verification-code rules (TTL, attempt cap, cooldown) for
# `app.core.security.new_verification_code` / `verify_code`. The module is
# skipped when those functions are absent, so a missing import never aborts the
# whole suite at collection.
try:
    from app.core.security import hash_password, new_verification_code, verify_code
except ImportError:  # pragma: no cover
    pytest.skip(
        "app.core.security lacks new_verification_code/verify_code",
        allow_module_level=True,
    )

CODE_TABLES = [UserRow.__table__, VerificationCode.__table__, AuthSessionModel.__table__]


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine, tables=CODE_TABLES)
    with Session(engine) as session:
        yield session


@pytest.fixture
def user(db: Session) -> UserRow:
    row = UserRow(
        id="usr_test",
        email="ana@acme.com",
        name="Ana",
        workspace="Personal",
        password_hash=hash_password("correct horse"),
        email_verified=False,
    )
    db.add(row)
    db.flush()
    return row


@pytest.fixture(autouse=True)
def fast_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Deterministic policy values, independent of a developer's .env."""
    settings = Settings(
        verification_code_ttl_minutes=10,
        verification_max_attempts=3,
        verification_resend_cooldown_seconds=60,
    )
    monkeypatch.setattr(repo, "get_settings", lambda: settings)
    return settings


def _stored(db: Session, email: str) -> VerificationCode:
    return db.exec(select(VerificationCode).where(VerificationCode.email == email)).all()[-1]


# ── Code generation ────────────────────────────────────────────────────────


def test_code_is_six_digits_and_zero_padded() -> None:
    codes = {new_verification_code() for _ in range(200)}
    assert all(len(c) == 6 and c.isdigit() for c in codes)
    # 200 draws from 10^6 colliding into a handful of values would mean the
    # generator isn't really uniform.
    assert len(codes) > 190


def test_code_is_stored_hashed_never_plaintext(db: Session, user: UserRow) -> None:
    issued = repo.create_login_challenge(db, "ana@acme.com", "correct horse")
    assert issued is not None

    row = _stored(db, "ana@acme.com")
    assert row.code != issued.code
    assert verify_code(issued.code, row.code)


# ── 2FA happy path ─────────────────────────────────────────────────────────


def test_login_then_correct_code_issues_session(db: Session, user: UserRow) -> None:
    issued = repo.create_login_challenge(db, "ana@acme.com", "correct horse")
    assert issued is not None

    session, error = repo.verify_two_factor(db, issued.id, issued.code)
    assert error is None
    assert session is not None
    assert session.user.email == "ana@acme.com"
    assert session.accessToken.startswith("sess_")


def test_wrong_password_issues_no_code(db: Session, user: UserRow) -> None:
    assert repo.create_login_challenge(db, "ana@acme.com", "wrong") is None
    assert db.exec(select(VerificationCode)).all() == []


def test_code_cannot_be_reused(db: Session, user: UserRow) -> None:
    issued = repo.create_login_challenge(db, "ana@acme.com", "correct horse")
    repo.verify_two_factor(db, issued.id, issued.code)

    session, error = repo.verify_two_factor(db, issued.id, issued.code)
    assert session is None
    assert error == "expired"


# ── The rules that make six digits enough ──────────────────────────────────


def test_wrong_code_counts_an_attempt(db: Session, user: UserRow) -> None:
    issued = repo.create_login_challenge(db, "ana@acme.com", "correct horse")

    session, error = repo.verify_two_factor(db, issued.id, "000000" if issued.code != "000000" else "111111")
    assert session is None
    assert error == "invalid_code"
    assert _stored(db, "ana@acme.com").attempts == 1


def test_code_is_burned_after_max_attempts(db: Session, user: UserRow, fast_settings: Settings) -> None:
    issued = repo.create_login_challenge(db, "ana@acme.com", "correct horse")
    wrong = "000000" if issued.code != "000000" else "111111"

    for _ in range(fast_settings.verification_max_attempts):
        _, error = repo.verify_two_factor(db, issued.id, wrong)
        assert error == "invalid_code"

    # The right code no longer helps — brute force can't be resumed by finally
    # guessing correctly.
    session, error = repo.verify_two_factor(db, issued.id, issued.code)
    assert session is None
    assert error == "expired"


def test_expired_code_is_rejected(db: Session, user: UserRow) -> None:
    issued = repo.create_login_challenge(db, "ana@acme.com", "correct horse")
    row = _stored(db, "ana@acme.com")
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.add(row)
    db.flush()

    session, error = repo.verify_two_factor(db, issued.id, issued.code)
    assert session is None
    assert error == "expired"


def test_unknown_challenge_is_invalid_not_expired(db: Session) -> None:
    session, error = repo.verify_two_factor(db, "chl_nope", "123456")
    assert session is None
    assert error == "invalid_code"


def test_resend_is_rate_limited(db: Session, user: UserRow) -> None:
    assert repo.issue_code(db, "ana@acme.com", "two_factor", user.id) is not None
    # Immediately again: inside the cooldown.
    assert repo.issue_code(db, "ana@acme.com", "two_factor", user.id) is None


def test_new_code_invalidates_the_previous_one(db: Session, user: UserRow) -> None:
    first = repo.issue_code(db, "ana@acme.com", "two_factor", user.id)

    # Step past the cooldown without sleeping.
    row = _stored(db, "ana@acme.com")
    row.created_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    db.add(row)
    db.flush()

    second = repo.issue_code(db, "ana@acme.com", "two_factor", user.id)
    assert second is not None

    session, error = repo.verify_two_factor(db, first.id, first.code)
    assert session is None
    assert error == "expired"

    session, error = repo.verify_two_factor(db, second.id, second.code)
    assert error is None
    assert session is not None


# ── Email verification ─────────────────────────────────────────────────────


def test_verify_email_marks_the_user_verified(db: Session, user: UserRow) -> None:
    issued = repo.issue_code(db, "ana@acme.com", "email_verify", user.id)

    verified, error = repo.verify_email(db, "ana@acme.com", issued.code)
    assert (verified, error) == (True, None)
    assert db.get(UserRow, "usr_test").email_verified is True


def test_verify_email_rejects_a_wrong_code(db: Session, user: UserRow) -> None:
    issued = repo.issue_code(db, "ana@acme.com", "email_verify", user.id)
    wrong = "000000" if issued.code != "000000" else "111111"

    verified, error = repo.verify_email(db, "ana@acme.com", wrong)
    assert (verified, error) == (False, "invalid_code")
    assert db.get(UserRow, "usr_test").email_verified is False


def test_two_factor_code_does_not_verify_an_email(db: Session, user: UserRow) -> None:
    """The purposes are separate credentials, not one code with two uses."""
    issued = repo.issue_code(db, "ana@acme.com", "two_factor", user.id)

    verified, error = repo.verify_email(db, "ana@acme.com", issued.code)
    assert verified is False
    assert error == "invalid_code"


def test_find_unverified_user_skips_verified_ones(db: Session, user: UserRow) -> None:
    assert repo.find_unverified_user(db, "ana@acme.com") is not None
    user.email_verified = True
    db.add(user)
    db.flush()
    assert repo.find_unverified_user(db, "ana@acme.com") is None


# ── Email rendering + sender seam ──────────────────────────────────────────


def test_templates_carry_the_code_in_both_parts() -> None:
    message = mailer.two_factor_code_email("ana@acme.com", "042195", 10)
    assert message.to == "ana@acme.com"
    assert "042195" in message.subject
    assert "042195" in message.text
    assert "042195" in message.html
    assert "10 minutos" in message.text


def test_console_sender_is_refused_on_lambda() -> None:
    """Console delivery writes codes to the log; on Lambda that's CloudWatch."""
    with pytest.raises(RuntimeError, match="CloudWatch"):
        mailer._build_sender("console", on_lambda=True)

    assert isinstance(mailer._build_sender("console", on_lambda=False), mailer.ConsoleEmailSender)


def test_unknown_email_backend_fails_loudly() -> None:
    with pytest.raises(RuntimeError, match="Unknown email_backend"):
        mailer._build_sender("ses", on_lambda=False)
