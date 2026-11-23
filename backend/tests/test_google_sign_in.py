""""Continuar con Google": the ID token is really verified, and the account rules hold.

The tokens here are real RS256 JWTs signed with a key made for the test, and
`PyJWKClient.fetch_data` serves that key's JWKS in place of Google's endpoint —
so signature, audience, issuer and expiry are checked by the same code path a
deploy runs, not by a stub that says yes.
"""

import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel

from app.core import google_identity, llm_bridge_transport
from app.core.config import get_settings
from app.core.google_identity import GoogleIdentity, GoogleUnavailable, InvalidGoogleToken
from app.core.legal import TERMS_VERSION
from app.core.security import hash_password, verify_password
from app.db.models import AuthSessionModel, User as UserRow, VerificationCode
from app.llm_bridge import bridge_handler
from app.main import app
from app.repositories import auth as repo
from app.services import auth_memory

client = TestClient(app)

_CLIENT_ID = "1234-test.apps.googleusercontent.com"
_KID = "test-key-1"
_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwks() -> dict[str, Any]:
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(_KEY.public_key(), as_dict=True)
    return {"keys": [{**jwk, "kid": _KID, "use": "sig", "alg": "RS256"}]}


def _token(key: Any = _KEY, kid: str = _KID, **overrides: Any) -> str:
    now = int(time.time())
    claims = {
        "iss": "https://accounts.google.com",
        "aud": _CLIENT_ID,
        "sub": "google-sub-1",
        "email": "ana.gomez@gmail.com",
        "email_verified": True,
        "name": "Ana Gómez",
        "iat": now,
        "exp": now + 3600,
        **overrides,
    }
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})


@pytest.fixture(autouse=True)
def _google(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "google_client_id", _CLIENT_ID)
    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", lambda _self: _jwks())
    google_identity._keys.cache_clear()
    yield
    google_identity._keys.cache_clear()


def _sign_in(credential: str, **body: Any):  # noqa: ANN202
    return client.post("/api/v1/auth/google", json={"credential": credential, **body})


# ---- the token ----


def test_a_valid_token_yields_its_identity() -> None:
    identity = google_identity.verify(_token())
    assert identity == GoogleIdentity(sub="google-sub-1", email="ana.gomez@gmail.com", name="Ana Gómez")


@pytest.mark.parametrize(
    "credential",
    [
        pytest.param(lambda: _token(aud="someone-else.apps.googleusercontent.com"), id="minted for another site"),
        pytest.param(lambda: _token(iss="https://evil.example"), id="wrong issuer"),
        pytest.param(lambda: _token(exp=int(time.time()) - 3600, iat=int(time.time()) - 7200), id="expired"),
        pytest.param(lambda: _token(email_verified=False), id="unverified email"),
        pytest.param(lambda: _token(email=None), id="no email"),
        pytest.param(lambda: _token(key=_OTHER_KEY), id="signed by another key"),
        pytest.param(lambda: _token(kid="not-a-google-key"), id="unknown key id"),
        pytest.param(lambda: "not.a.jwt", id="garbage"),
    ],
)
def test_a_bad_token_is_refused(credential) -> None:  # noqa: ANN001
    with pytest.raises(InvalidGoogleToken):
        google_identity.verify(credential())
    res = _sign_in(credential(), acceptedTermsVersion=TERMS_VERSION)
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "invalid_google_token"


def test_an_hs256_token_cannot_pass_as_google() -> None:
    """The classic JWT confusion: a token "signed" with the public key as an HMAC secret."""
    forged = jwt.encode(
        {"iss": "accounts.google.com", "aud": _CLIENT_ID, "sub": "x", "email": "a@b.com",
         "email_verified": True, "iat": int(time.time()), "exp": int(time.time()) + 60},
        "a-shared-secret-long-enough-for-hs256",
        algorithm="HS256",
        headers={"kid": _KID},
    )
    with pytest.raises(InvalidGoogleToken):
        google_identity.verify(forged)


def test_not_configured_is_a_503_not_a_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "google_client_id", None)
    res = _sign_in(_token(), acceptedTermsVersion=TERMS_VERSION)
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "google_unavailable"


def test_googles_keys_unreachable_is_a_503(monkeypatch: pytest.MonkeyPatch) -> None:
    def _down(_self):  # noqa: ANN001, ANN202
        raise jwt.PyJWKClientConnectionError("connection refused")

    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", _down)
    with pytest.raises(GoogleUnavailable):
        google_identity.verify(_token())
    assert _sign_in(_token()).status_code == 503


# ---- the deployed route: the bridge runs the check ----


def test_through_the_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    """The API Lambda has no route to googleapis.com, so it hands the token to
    the bridge — here the real `bridge_handler`, minus the Lambda invoke."""
    sent: list[dict[str, Any]] = []

    def _invoke(_fn: str, payload: dict[str, Any], **_kw: Any) -> dict[str, Any]:
        sent.append(payload)
        return bridge_handler(payload)

    monkeypatch.setattr(get_settings(), "llm_bridge_function_name", "alibi-llm-bridge-dev")
    monkeypatch.setattr(llm_bridge_transport, "call", _invoke)

    assert google_identity.verify(_token()).sub == "google-sub-1"
    assert sent[0]["kind"] == "google"
    assert sent[0]["audience"] == _CLIENT_ID
    # A bad token comes back as a code: a 401 for the user, not an outage.
    with pytest.raises(InvalidGoogleToken):
        google_identity.verify(_token(aud="other"))


# ---- the account (in-memory backend, through the API) ----


def test_a_new_google_account_needs_the_terms_first() -> None:
    res = _sign_in(_token())
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "terms_required"
    assert not auth_memory.email_exists("ana.gomez@gmail.com")


def test_with_the_terms_it_creates_the_account_and_opens_the_session() -> None:
    res = _sign_in(_token(), acceptedTermsVersion=TERMS_VERSION, remember=True)
    assert res.status_code == 200
    body = res.json()
    assert body["accountCreated"] is True
    assert body["expiresIn"] is None  # remembered
    assert body["user"]["name"] == "Ana Gómez"

    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {body['accessToken']}"})
    assert me.status_code == 200
    assert me.json()["email"] == "ana.gomez@gmail.com"


def test_signing_in_again_is_the_same_account_and_needs_no_terms() -> None:
    first = _sign_in(_token(), acceptedTermsVersion=TERMS_VERSION).json()
    again = _sign_in(_token())
    assert again.status_code == 200
    assert again.json()["accountCreated"] is False
    assert again.json()["user"]["id"] == first["user"]["id"]


def test_outdated_terms_are_refused() -> None:
    res = _sign_in(_token(), acceptedTermsVersion="1999-01-01")
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "terms_outdated"


def test_an_unverified_email_signup_is_finished_by_google(outbox: dict[str, str]) -> None:
    """Registered with a password, never typed the code: Google proves the
    mailbox, so the account signs in — no code, and no duplicate account."""
    client.post(
        "/api/v1/auth/register",
        json={"email": "ana.gomez@gmail.com", "password": "abcd1234", "name": "Ana",
              "acceptedTermsVersion": TERMS_VERSION},
    )
    res = _sign_in(_token())
    assert res.status_code == 200
    assert res.json()["accountCreated"] is False


# ---- the account rules, on the real repository ----


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(
        engine,
        tables=[UserRow.__table__, AuthSessionModel.__table__, VerificationCode.__table__],
    )
    with Session(engine) as session:
        yield session


_ANA = GoogleIdentity(sub="google-sub-1", email="ana.gomez@gmail.com", name="Ana Gómez")


def _password_account(db: Session, *, verified: bool, email: str = "Ana.Gomez@gmail.com") -> UserRow:
    row = UserRow(
        id="usr_pw", email=email, name="Ana", workspace="Personal",
        password_hash=hash_password("abcd1234"), email_verified=verified,
    )
    db.add(row)
    db.flush()
    return row


def test_repo_creates_a_verified_passwordless_account_with_the_consent(db: Session) -> None:
    session, created = repo.google_sign_in(db, _ANA, False, TERMS_VERSION)
    assert created
    row = db.get(UserRow, session.user.id)
    assert row.google_sub == "google-sub-1"
    assert row.email_verified
    assert row.password_hash is None
    assert row.terms_version == TERMS_VERSION
    assert row.terms_accepted_at is not None


def test_repo_links_a_verified_account_and_keeps_its_password(db: Session) -> None:
    """Matched case-insensitively: the signup form kept the capitals, Google does not."""
    existing = _password_account(db, verified=True)
    session, created = repo.google_sign_in(db, _ANA, False, None)
    assert not created
    assert session.user.id == existing.id
    assert existing.google_sub == "google-sub-1"
    assert verify_password("abcd1234", existing.password_hash)


def test_repo_drops_the_password_of_an_account_nobody_verified(db: Session) -> None:
    """Pre-account takeover: whoever typed the address set that password, and
    they need not own the mailbox Google has just proven is Ana's."""
    existing = _password_account(db, verified=False)
    repo.google_sign_in(db, _ANA, False, None)
    assert existing.email_verified
    assert existing.password_hash is None


def test_repo_refuses_an_address_linked_to_another_google_account(db: Session) -> None:
    existing = _password_account(db, verified=True)
    existing.google_sub = "a-different-google-account"
    db.add(existing)
    db.flush()
    assert repo.google_sign_in(db, _ANA, False, TERMS_VERSION) == "google_account_mismatch"


def test_repo_needs_current_terms_only_to_create(db: Session) -> None:
    assert repo.google_sign_in(db, _ANA, False, None) == "terms_required"
    assert repo.google_sign_in(db, _ANA, False, "1999-01-01") == "terms_outdated"
