"""Outbound email: the message templates and the send seam.

Everything here is transport-agnostic on purpose: a new transport is a new
`EmailSender` implementation plus one line in `_build_sender`, and nothing
else in the codebase moves.

Same shape as the storage seam in the domain services: a protocol, a local
implementation that needs no AWS, and a single factory the callers go through.

`ConsoleEmailSender` logs the message (including the code) at INFO. That is
fine — and useful — for local dev, where the alternative is not being able to
log in at all. It must never be the backend in a deployed environment: the
codes would land in CloudWatch in plaintext. `_build_sender` refuses to
silently do that.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

from app.core.config import get_settings

logger = logging.getLogger("alibi.email")


@dataclass(frozen=True)
class EmailMessage:
    to: str
    subject: str
    text: str
    html: str


# ── Templates ──────────────────────────────────────────────────────────────
# Plain text is the source of truth and always sent alongside the HTML: some
# clients (and most spam filters) prefer it, and a text-only fallback means the
# code is always readable even if the HTML is stripped.

_HTML_SHELL = """\
<!doctype html>
<html lang="es">
  <body style="margin:0;padding:32px 16px;background:#0a0a0c;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;">
    <table role="presentation" style="max-width:480px;margin:0 auto;background:#141417;border-radius:16px;padding:32px;">
      <tr><td>
        <p style="margin:0 0 8px;color:#27F5A9;font-size:13px;letter-spacing:.08em;text-transform:uppercase;">ALIBI</p>
        <h1 style="margin:0 0 16px;color:#f4f4f5;font-size:20px;font-weight:600;">{heading}</h1>
        <p style="margin:0 0 24px;color:#a1a1aa;font-size:15px;line-height:1.6;">{intro}</p>
        <p style="margin:0 0 24px;padding:16px;background:#0a0a0c;border-radius:12px;text-align:center;
                  color:#f4f4f5;font-family:'JetBrains Mono',ui-monospace,SFMono-Regular,Menlo,monospace;
                  font-size:32px;font-weight:600;letter-spacing:.24em;">{code}</p>
        <p style="margin:0;color:#71717a;font-size:13px;line-height:1.6;">
          The code expires in {ttl} minutes. If this wasn't you, ignore this email.
        </p>
      </td></tr>
    </table>
  </body>
</html>
"""


def _render(to: str, subject: str, heading: str, intro: str, code: str, ttl: int) -> EmailMessage:
    text = (
        f"{heading}\n\n"
        f"{intro}\n\n"
        f"    {code}\n\n"
        f"El código vence en {ttl} minutos. Si no fuiste vos, ignorá este correo.\n"
    )
    html = _HTML_SHELL.format(heading=heading, intro=intro, code=code, ttl=ttl)
    return EmailMessage(to=to, subject=subject, text=text, html=html)


def two_factor_code_email(to: str, code: str, ttl_minutes: int) -> EmailMessage:
    return _render(
        to=to,
        subject=f"{code} es tu código de ingreso a ALIBI",
        heading="Tu código de ingreso",
        intro="Usá este código para terminar de ingresar a ALIBI.",
        code=code,
        ttl=ttl_minutes,
    )


def email_verification_email(to: str, code: str, ttl_minutes: int) -> EmailMessage:
    return _render(
        to=to,
        subject=f"{code} es tu código de verificación de ALIBI",
        heading="Verificá tu correo",
        intro="Ingresá este código en ALIBI para confirmar tu correo electrónico.",
        code=code,
        ttl=ttl_minutes,
    )


# ── Senders ────────────────────────────────────────────────────────────────


class EmailSender(Protocol):
    def send(self, message: EmailMessage) -> None: ...


class ConsoleEmailSender:
    """Local-dev sender: logs the message instead of delivering it."""

    def send(self, message: EmailMessage) -> None:
        logger.info(
            "[email:console] to=%s subject=%s\n%s", message.to, message.subject, message.text
        )


@lru_cache
def get_sender() -> EmailSender:
    return _build_sender(get_settings().email_backend, on_lambda=_on_lambda())


def _on_lambda() -> bool:
    """True when running inside the deployed Lambda.

    `ALIBI_ENVIRONMENT` can't answer this — the deployed environment is itself
    named "dev", so it reads identically to a laptop. `AWS_LAMBDA_FUNCTION_NAME`
    is set by the Lambda runtime and by nothing else.
    """
    return bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))


def _build_sender(backend: str, on_lambda: bool) -> EmailSender:
    if backend == "console":
        # Logging 2FA codes to CloudWatch would be a real credential leak, not
        # a nuisance — fail loudly rather than deliver them there quietly.
        if on_lambda:
            raise RuntimeError(
                "email_backend='console' is not allowed in the deployed Lambda: "
                "verification codes would be written to CloudWatch in plaintext."
            )
        return ConsoleEmailSender()
    raise RuntimeError(
        f"Unknown email_backend '{backend}'. Only 'console' exists."
    )
