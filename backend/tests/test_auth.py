from datetime import timedelta

from fastapi.testclient import TestClient

from app.core import mailer
from app.core.legal import TERMS_VERSION
from app.main import app
from app.services import auth_memory

client = TestClient(app)

_NEW = {"email": "new@user.com", "password": "abcd1234", "name": "New User", "acceptedTermsVersion": TERMS_VERSION}


def _register(outbox: dict[str, str], body: dict | None = None) -> str:
    res = client.post("/api/v1/auth/register", json=body or _NEW)
    assert res.status_code == 201
    return outbox[(body or _NEW)["email"]]


def _wrong(code: str) -> str:
    return f"{(int(code) + 1) % 10**6:06d}"


# ---- login ----


def test_login_of_a_verified_account_opens_the_session() -> None:
    res = client.post("/api/v1/auth/login", json={"email": "ana@acme.com", "password": "pw"})
    assert res.status_code == 200
    session = res.json()
    assert session["status"] == "authenticated"
    assert session["tokenType"] == "Bearer"
    assert session["user"]["email"] == "ana@acme.com"

    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {session['accessToken']}"})
    assert me.status_code == 200
    assert me.json()["email"] == "ana@acme.com"


def test_login_empty_password_invalid() -> None:
    res = client.post("/api/v1/auth/login", json={"email": "x@y.com", "password": ""})
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "invalid_credentials"


def test_me_without_token_unauthorized() -> None:
    res = client.get("/api/v1/auth/me")
    assert res.status_code == 401
    assert res.json() == {"error": {"code": "unauthorized", "message": "Iniciá sesión para continuar."}}


def test_logout() -> None:
    login = client.post("/api/v1/auth/login", json={"email": "z@z.com", "password": "pw"})
    headers = {"Authorization": f"Bearer {login.json()['accessToken']}"}

    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 204
    # Token is now invalid.
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401


# ---- register ----


def test_register_emails_a_code_and_opens_no_session(outbox: dict[str, str]) -> None:
    res = client.post("/api/v1/auth/register", json=_NEW)
    assert res.status_code == 201
    assert res.json() == {"status": "email_verification_required", "email": "new@user.com"}
    assert "accessToken" not in res.json()
    assert len(outbox["new@user.com"]) == 6


def test_register_then_conflict(outbox: dict[str, str]) -> None:
    _register(outbox)
    dup = client.post("/api/v1/auth/register", json=_NEW)
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "email_already_registered"


def test_register_short_password_422() -> None:
    res = client.post(
        "/api/v1/auth/register",
        json={"email": "a@b.com", "password": "short", "name": "Someone", "acceptedTermsVersion": TERMS_VERSION},
    )
    assert res.status_code == 422


def test_register_without_a_name_422() -> None:
    """The greeting and the account screen have nothing to show without it."""
    res = client.post(
        "/api/v1/auth/register", json={"email": "nameless@user.com", "password": "abcd1234", "acceptedTermsVersion": TERMS_VERSION}
    )
    assert res.status_code == 422


def test_a_failed_send_leaves_no_account(monkeypatch) -> None:
    """Otherwise the address would be taken (409) by an account nobody can verify."""

    def _down(*_args) -> None:
        raise mailer.MailUnavailable("smtp down")

    monkeypatch.setattr(mailer, "send_code", _down)
    res = client.post("/api/v1/auth/register", json=_NEW)
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "email_unavailable"
    assert not auth_memory.email_exists(_NEW["email"])


def test_register_requires_accepting_the_current_terms() -> None:
    """No account without consent to the Terms and the Privacy Policy in force
    (Ley 25.326) — and not to an older text than the one in force."""
    missing = client.post(
        "/api/v1/auth/register",
        json={"email": "noterms@user.com", "password": "abcd1234", "name": "No Terms"},
    )
    assert missing.status_code == 422

    outdated = client.post(
        "/api/v1/auth/register",
        json={"email": "old@user.com", "password": "abcd1234", "name": "Old Terms", "acceptedTermsVersion": "2000-01-01"},
    )
    assert outdated.status_code == 409
    assert outdated.json()["error"]["code"] == "terms_outdated"


# ---- verify-email ----


def test_the_right_code_opens_the_session_for_the_new_account(outbox: dict[str, str]) -> None:
    code = _register(outbox)
    res = client.post(
        "/api/v1/auth/verify-email", json={"email": "new@user.com", "code": code, "remember": True}
    )
    assert res.status_code == 200
    session = res.json()
    assert session["expiresIn"] is None

    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {session['accessToken']}"})
    assert me.json()["email"] == "new@user.com"
    assert me.json()["name"] == "New User"


def test_a_wrong_code_is_refused(outbox: dict[str, str]) -> None:
    code = _register(outbox)
    res = client.post("/api/v1/auth/verify-email", json={"email": "new@user.com", "code": _wrong(code)})
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "invalid_code"


def test_a_code_works_once(outbox: dict[str, str]) -> None:
    code = _register(outbox)
    body = {"email": "new@user.com", "code": code}
    assert client.post("/api/v1/auth/verify-email", json=body).status_code == 200
    again = client.post("/api/v1/auth/verify-email", json=body)
    assert again.status_code == 410
    assert again.json()["error"]["code"] == "code_expired"


def test_the_fifth_wrong_guess_burns_the_code(outbox: dict[str, str]) -> None:
    code = _register(outbox)
    wrong = {"email": "new@user.com", "code": _wrong(code)}
    for _ in range(4):
        assert client.post("/api/v1/auth/verify-email", json=wrong).json()["error"]["code"] == (
            "invalid_code"
        )
    fifth = client.post("/api/v1/auth/verify-email", json=wrong)
    assert fifth.status_code == 429
    assert fifth.json()["error"]["code"] == "too_many_attempts"
    # Even the right code is dead now.
    right = client.post("/api/v1/auth/verify-email", json={"email": "new@user.com", "code": code})
    assert right.status_code == 410


def test_an_expired_code_is_refused(outbox: dict[str, str]) -> None:
    code = _register(outbox)
    auth_memory._codes["new@user.com"].expires_at -= timedelta(minutes=11)
    res = client.post("/api/v1/auth/verify-email", json={"email": "new@user.com", "code": code})
    assert res.status_code == 410


# ---- an unverified account logging in ----


def test_login_before_verifying_sends_the_user_back_to_the_code(outbox: dict[str, str]) -> None:
    code = _register(outbox)
    res = client.post("/api/v1/auth/login", json={"email": "new@user.com", "password": "abcd1234"})
    assert res.status_code == 200
    assert res.json() == {"status": "email_verification_required", "email": "new@user.com"}
    # Within the cooldown no new code goes out: the one already sent still works.
    assert outbox["new@user.com"] == code


def test_once_verified_login_opens_the_session(outbox: dict[str, str]) -> None:
    code = _register(outbox)
    client.post("/api/v1/auth/verify-email", json={"email": "new@user.com", "code": code})
    res = client.post("/api/v1/auth/login", json={"email": "new@user.com", "password": "abcd1234"})
    assert res.json()["status"] == "authenticated"


# ---- resend ----


def test_resend_inside_the_cooldown_is_refused(outbox: dict[str, str]) -> None:
    _register(outbox)
    res = client.post("/api/v1/auth/verify-email/resend", json={"email": "new@user.com"})
    assert res.status_code == 429
    assert res.json()["error"]["code"] == "resend_too_soon"


def test_resend_replaces_the_code(outbox: dict[str, str]) -> None:
    first = _register(outbox)
    auth_memory._codes["new@user.com"].created_at -= timedelta(minutes=2)
    res = client.post("/api/v1/auth/verify-email/resend", json={"email": "new@user.com"})
    assert res.status_code == 202
    second = outbox["new@user.com"]
    if second != first:  # 1-in-10^6 they collide
        old = client.post("/api/v1/auth/verify-email", json={"email": "new@user.com", "code": first})
        assert old.status_code == 401
    ok = client.post("/api/v1/auth/verify-email", json={"email": "new@user.com", "code": second})
    assert ok.status_code == 200


def test_resend_says_nothing_about_unknown_emails(outbox: dict[str, str]) -> None:
    res = client.post("/api/v1/auth/verify-email/resend", json={"email": "nobody@nowhere.com"})
    assert res.status_code == 202
    assert outbox == {}
