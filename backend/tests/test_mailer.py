"""`app.core.mailer` — the code's three routes, without a real SMTP server.

`conftest.outbox` stubs `mailer.send_code` for the whole suite; these tests
reach underneath it to `send_smtp`, with `smtplib.SMTP` replaced by a recorder.
"""

import smtplib

import pytest

from app.core import mailer
from app.core.config import get_settings

#: Taken at import, before `conftest.outbox` stubs it for each test.
_send_code = mailer.send_code


class _FakeSmtp:
    instances: list["_FakeSmtp"] = []

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.host, self.port = host, port
        self.calls: list[str] = []
        self.message = None
        _FakeSmtp.instances.append(self)

    def __enter__(self) -> "_FakeSmtp":
        return self

    def __exit__(self, *_exc) -> None:
        return None

    def starttls(self) -> None:
        self.calls.append("starttls")

    def login(self, user: str, password: str) -> None:
        self.calls.append(f"login:{user}")

    def send_message(self, message) -> None:
        self.calls.append("send")
        self.message = message


@pytest.fixture
def smtp(monkeypatch):
    _FakeSmtp.instances = []
    monkeypatch.setattr(smtplib, "SMTP", _FakeSmtp)
    return _FakeSmtp.instances


@pytest.fixture
def configured(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "smtp_user", "alibi@example.com")
    monkeypatch.setattr(settings, "smtp_password", "app-password")


def test_without_a_password_the_code_is_only_logged(smtp, caplog) -> None:
    mailer.send_smtp("ana@acme.com", "123456", 10)
    assert smtp == []
    assert "123456" in caplog.text


def test_it_sends_over_starttls_from_the_project_mailbox(smtp, configured) -> None:
    mailer.send_smtp("ana@acme.com", "123456", 10)
    (conn,) = smtp
    assert (conn.host, conn.port) == ("smtp.gmail.com", 587)
    assert conn.calls == ["starttls", "login:alibi@example.com", "send"]
    assert conn.message["To"] == "ana@acme.com"
    assert conn.message["From"] == "alibi@example.com"
    assert "123456" in conn.message.get_content()


def test_an_smtp_failure_is_mail_unavailable(monkeypatch, configured) -> None:
    def _refuse(*_args, **_kwargs):
        raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    monkeypatch.setattr(smtplib, "SMTP", _refuse)
    with pytest.raises(mailer.MailUnavailable):
        mailer.send_smtp("ana@acme.com", "123456", 10)


def test_with_a_bridge_the_send_goes_through_it(monkeypatch) -> None:
    """The VPC-attached API Lambda has no route to the SMTP server."""
    from app.core import llm_bridge_transport

    seen: dict = {}

    def _invoke(function_name, kind, config, messages, *, unavailable_cls, **extra):
        seen.update(function_name=function_name, kind=kind, **extra)
        return {}

    monkeypatch.setattr(get_settings(), "llm_bridge_function_name", "alibi-llm-bridge-dev")
    monkeypatch.setattr(llm_bridge_transport, "invoke", _invoke)
    _send_code("ana@acme.com", "123456", 10)
    assert seen == {
        "function_name": "alibi-llm-bridge-dev",
        "kind": "mail",
        "to": "ana@acme.com",
        "code": "123456",
        "minutes": 10,
    }


def test_the_bridge_sends_the_mail_kind(smtp, configured) -> None:
    from app.llm_bridge import bridge_handler

    result = bridge_handler(
        {"kind": "mail", "config": {}, "messages": [], "to": "ana@acme.com", "code": "654321", "minutes": 10}
    )
    assert result == {"ok": True, "completion": {}}
    assert "654321" in smtp[0].message.get_content()

