""""Keep me signed in" means the session has no expiry at all.

The checkbox used to promise 30 days and implement nothing: every session got
the same one-hour token whether or not it was ticked. Now a remembered session
is stored with `expires_at = NULL` and ends only at logout.

The database repository is exercised directly against SQLite here — the rest of
the suite runs the in-memory backend, which tracks no expiry at all, so it
cannot tell a remembered session from a plain one. Only the three tables this
touches are created, since most of the schema uses Postgres-only types.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel, select

from app.core.legal import TERMS_VERSION
from app.core.security import hash_password
from app.db.models import AuthSessionModel, User as UserRow, VerificationCode
from app.repositories import auth as repo
from app.schemas.auth import RegisterRequest


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(
        engine,
        tables=[UserRow.__table__, AuthSessionModel.__table__, VerificationCode.__table__],
    )
    with Session(engine) as session:
        yield session


def _user(db: Session, email: str = "someone@alibi.app") -> UserRow:
    row = UserRow(
        id="usr_test",
        email=email,
        name="Some One",
        workspace="Personal",
        password_hash=hash_password("abcd1234"),
        email_verified=False,
    )
    db.add(row)
    db.flush()
    return row


class TestRememberedSession:
    def test_it_has_no_expiry(self, db: Session) -> None:
        session = repo._issue_session(db, _user(db), remember=True)
        row = db.get(AuthSessionModel, session.accessToken)
        assert row.expires_at is None
        assert session.expiresIn is None

    def test_it_still_resolves_to_its_user(self, db: Session) -> None:
        """`NULL > now()` is NULL, not true — a plain comparison would have
        signed every remembered user out on their next request."""
        session = repo._issue_session(db, _user(db), remember=True)
        resolved = repo.get_user_by_token(db, session.accessToken)
        assert resolved is not None
        assert resolved.email == "someone@alibi.app"

    def test_logout_is_what_ends_it(self, db: Session) -> None:
        session = repo._issue_session(db, _user(db), remember=True)
        repo.logout(db, session.accessToken)
        assert repo.get_user_by_token(db, session.accessToken) is None


class TestPlainSession:
    def test_it_expires(self, db: Session) -> None:
        session = repo._issue_session(db, _user(db), remember=False)
        row = db.get(AuthSessionModel, session.accessToken)
        assert row.expires_at is not None
        assert session.expiresIn == 3600

    def test_a_lapsed_token_resolves_to_nobody(self, db: Session) -> None:
        session = repo._issue_session(db, _user(db), remember=False)
        row = db.get(AuthSessionModel, session.accessToken)
        row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.add(row)
        db.flush()
        assert repo.get_user_by_token(db, session.accessToken) is None




# ---- the emailed code, on the real repository ----


class _Outbox:
    def __init__(self) -> None:
        self.sent: dict[str, str] = {}

    def __call__(self, email: str, code: str) -> None:
        self.sent[email] = code


def test_registration_opens_no_session_until_the_code(db: Session) -> None:
    outbox = _Outbox()
    repo.register(
        db, RegisterRequest(email="new@alibi.app", password="abcd1234", name="New Person", acceptedTermsVersion=TERMS_VERSION), outbox
    )
    assert db.exec(select(AuthSessionModel)).first() is None

    session = repo.verify_email(db, "new@alibi.app", outbox.sent["new@alibi.app"], remember=True)
    assert db.get(AuthSessionModel, session.accessToken).expires_at is None
    assert db.exec(select(UserRow)).one().email_verified is True


def test_the_code_is_stored_hashed(db: Session) -> None:
    outbox = _Outbox()
    repo.register(
        db, RegisterRequest(email="new@alibi.app", password="abcd1234", name="New Person", acceptedTermsVersion=TERMS_VERSION), outbox
    )
    row = db.exec(select(VerificationCode)).one()
    assert row.code != outbox.sent["new@alibi.app"]
    assert len(row.code) == 64


def test_wrong_guesses_are_counted_and_burn_the_code(db: Session) -> None:
    outbox = _Outbox()
    repo.register(
        db, RegisterRequest(email="new@alibi.app", password="abcd1234", name="New Person", acceptedTermsVersion=TERMS_VERSION), outbox
    )
    code = outbox.sent["new@alibi.app"]
    wrong = f"{(int(code) + 1) % 10**6:06d}"
    results = [repo.verify_email(db, "new@alibi.app", wrong, False) for _ in range(5)]
    assert results == ["invalid_code"] * 4 + ["too_many_attempts"]
    assert repo.verify_email(db, "new@alibi.app", code, False) == "code_expired"


def test_login_of_an_unverified_account_returns_no_session(db: Session) -> None:
    user = _user(db)
    outbox = _Outbox()
    result = repo.login(db, user.email, "abcd1234", remember=False, send=outbox)
    assert result.status == "email_verification_required"
    assert user.email in outbox.sent


def test_a_wrong_password_sends_nothing(db: Session) -> None:
    user = _user(db)
    outbox = _Outbox()
    assert repo.login(db, user.email, "nope-nope", remember=False, send=outbox) is None
    assert outbox.sent == {}


def test_login_of_a_verified_account_carries_the_flag(db: Session) -> None:
    user = _user(db)
    user.email_verified = True
    db.add(user)
    session = repo.login(db, user.email, "abcd1234", remember=True, send=_Outbox())
    assert db.get(AuthSessionModel, session.accessToken).expires_at is None


def test_the_api_reports_no_expiry_for_a_remembered_login(
    client: TestClient
) -> None:
    """End to end through the app, on whichever backend is configured."""
    login = client.post(
        "/api/v1/auth/login", json={"email": "ana@acme.com", "password": "pw", "remember": True}
    )
    assert login.status_code == 200
    assert login.json()["expiresIn"] is None

    me = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {login.json()['accessToken']}"},
    )
    assert me.status_code == 200


def test_the_api_reports_an_expiry_when_not_remembered(client: TestClient) -> None:
    login = client.post("/api/v1/auth/login", json={"email": "ana@acme.com", "password": "pw"})
    assert login.json()["expiresIn"] == 3600
