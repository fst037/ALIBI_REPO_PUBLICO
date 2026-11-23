"""Sends the 6-digit verification code by email — plain SMTP, standard library.

No mail service and no AWS: the code leaves
from the project's own mailbox over SMTP with STARTTLS (`smtplib` +
`email.message`, nothing to install). With Gmail that is smtp.gmail.com:587 and
an *app password*, configured through the `ALIBI_SMTP_*` settings.

Three routes, picked from the settings:

* **Dev mode** — no `smtp_password`: the code is logged instead of sent, so
  local runs and the test suite need no mailbox (the tests stub `send_code`).
* **Direct** — this process opens the SMTP connection itself (local dev with a
  real mailbox, or a deploy that is not VPC-attached).
* **Through the bridge** — `llm_bridge_function_name` set: the API Lambda sits
  in the VPC with no NAT, so it has no route to smtp.gmail.com either; the
  non-VPC bridge Lambda (`app/llm_bridge.py`, kind `mail`) sends it instead,
  with the SMTP settings in *its* environment. Same reason, same split as the
  model calls.

Any failure raises `MailUnavailable`; the caller turns it into a 503 and rolls
back whatever it was about to commit, so a code nobody received never exists.
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_SUBJECT = "Tu código de verificación de ALIBI"
_SMTP_TIMEOUT_SECONDS = 10.0


class MailUnavailable(Exception):
    """The code could not be sent."""


def _body(code: str, minutes: int) -> str:
    return (
        f"Tu código de verificación de ALIBI es: {code}\n\n"
        f"Vence en {minutes} minutos. Si no fuiste vos, ignorá este mensaje.\n"
    )


def send_smtp(to: str, code: str, minutes: int) -> None:
    """Open the SMTP connection and send. Used directly and by the bridge."""
    settings = get_settings()
    if not settings.smtp_password:
        # Dev mode: nothing to send with, so the code goes to the log.
        logger.warning("SMTP not configured — verification code for %s: %s", to, code)
        return

    message = EmailMessage()
    message["Subject"] = _SUBJECT
    message["From"] = settings.mail_from or settings.smtp_user
    message["To"] = to
    message.set_content(_body(code, minutes))

    try:
        with smtplib.SMTP(
            settings.smtp_host, settings.smtp_port, timeout=_SMTP_TIMEOUT_SECONDS
        ) as smtp:
            smtp.starttls()
            smtp.login(settings.smtp_user or "", settings.smtp_password)
            smtp.send_message(message)
    except (smtplib.SMTPException, OSError) as err:
        raise MailUnavailable(f"{type(err).__name__}: {err}") from err


def send_code(to: str, code: str, minutes: int) -> None:
    """Deliver `code` to `to` by whichever route this deploy has (see module doc)."""
    bridge = get_settings().llm_bridge_function_name
    if not bridge:
        send_smtp(to, code, minutes)
        return

    from app.core.llm_bridge_transport import invoke

    invoke(
        bridge,
        "mail",
        {"timeout_seconds": _SMTP_TIMEOUT_SECONDS},
        [],
        unavailable_cls=MailUnavailable,
        to=to,
        code=code,
        minutes=minutes,
    )


__all__ = ["MailUnavailable", "send_code", "send_smtp"]
