"""`auth` domain routes.

Implements the `auth` tag of `contracts/auth.yaml`: register (emails a 6-digit
code, no session yet), login (a session — or, while the email is unverified, a
fresh code), verify-email (checks the code and opens the session), resend,
Google sign-in (an ID token from "Continuar con Google" — signs in, links or
creates the account, no code involved), current user, and logout. Identity lives in `app/services/auth.py`; the code is
sent by `app/core/mailer.py` over plain SMTP.

Public endpoints (register/login/verify-email/resend/google) carry `security: []` in
the contract and need no token. `/auth/me` and `/auth/logout` require the
bearer token via the `require_user` / `_bearer_token` dependencies.
"""

from fastapi import APIRouter, Depends, Response

from app.api.v1.deps import bearer_token, require_user
from app.core.errors import ApiException
from app.core.google_identity import GoogleUnavailable, InvalidGoogleToken
from app.core.google_identity import verify as verify_google_token
from app.core.legal import TERMS_VERSION
from app.core.mailer import MailUnavailable
from app.schemas.auth import (
    AuthSession,
    EmailOnlyRequest,
    EmailVerificationRequired,
    GoogleSignInRequest,
    GoogleSignInResult,
    LoginRequest,
    RegisterRequest,
    User,
    VerifyEmailRequest,
)
from app.services import auth as store

router = APIRouter(tags=["auth"])

#: Error code -> (status, message) for the code paths.
_CODE_ERRORS = {
    "invalid_code": (401, "Código incorrecto. Probá de nuevo."),
    "code_expired": (410, "El código venció. Pedí uno nuevo."),
    "too_many_attempts": (429, "Demasiados intentos. Pedí un código nuevo."),
    "resend_too_soon": (429, "Esperá un minuto antes de pedir otro código."),
}


_TERMS_OUTDATED = (
    "Los Términos y la Política de privacidad cambiaron. Recargá la página para aceptar la versión vigente."
)

#: Error code -> (status, message) for Google sign-in.
_GOOGLE_ERRORS = {
    "terms_required": (
        409,
        "Para crear tu cuenta con Google, aceptá los Términos y la Política de privacidad.",
    ),
    "terms_outdated": (409, _TERMS_OUTDATED),
    "google_account_mismatch": (
        409,
        "Ese email ya está vinculado a otra cuenta de Google. Ingresá con esa cuenta o con tu contraseña.",
    ),
}


def _code_error(code: str) -> ApiException:
    status, message = _CODE_ERRORS[code]
    return ApiException(status, code, message)


def _mail_failed() -> ApiException:
    return ApiException(
        503, "email_unavailable", "No pudimos enviar el código. Probá de nuevo en unos minutos."
    )


@router.post(
    "/auth/register",
    response_model=EmailVerificationRequired,
    status_code=201,
    summary="Create the account and email its verification code.",
)
def register(request: RegisterRequest) -> EmailVerificationRequired:
    if request.acceptedTermsVersion != TERMS_VERSION:
        raise ApiException(409, "terms_outdated", _TERMS_OUTDATED)
    if store.email_exists(request.email):
        raise ApiException(409, "email_already_registered", "Ese email ya tiene una cuenta.")
    try:
        return store.register(request)
    except MailUnavailable as err:
        raise _mail_failed() from err


@router.post(
    "/auth/login",
    response_model=None,
    summary="Authenticate: a session, or a code if the email is still unverified.",
)
def login(request: LoginRequest) -> AuthSession | EmailVerificationRequired:
    if not request.password:
        raise ApiException(
            401, "invalid_credentials", "Email o contraseña incorrectos. Probá de nuevo."
        )
    try:
        result = store.login(request.email, request.password, request.remember)
    except MailUnavailable as err:
        raise _mail_failed() from err
    if result is None:
        raise ApiException(
            401, "invalid_credentials", "Email o contraseña incorrectos. Probá de nuevo."
        )
    return result


@router.post(
    "/auth/verify-email",
    response_model=AuthSession,
    summary="Check the emailed code and open the session.",
)
def verify_email(request: VerifyEmailRequest) -> AuthSession:
    result = store.verify_email(request.email, request.code, request.remember)
    if isinstance(result, str):
        raise _code_error(result)
    return result


@router.post(
    "/auth/verify-email/resend",
    status_code=202,
    summary="Email a new verification code.",
)
def resend_verification_email(request: EmailOnlyRequest) -> Response:
    try:
        error = store.resend_code(request.email)
    except MailUnavailable as err:
        raise _mail_failed() from err
    if error:
        raise _code_error(error)
    return Response(status_code=202)


@router.post(
    "/auth/google",
    response_model=GoogleSignInResult,
    summary="Sign in or sign up with a Google ID token.",
)
def google_sign_in(request: GoogleSignInRequest) -> GoogleSignInResult:
    """Verify the token, then open the session of the account linked to that
    Google account — linking an existing account with the same email, or
    creating one when the Terms were accepted. Never asks for an emailed code:
    Google has already verified the address."""
    try:
        identity = verify_google_token(request.credential)
    except InvalidGoogleToken as err:
        raise ApiException(
            401, "invalid_google_token", "No pudimos validar tu cuenta de Google. Probá de nuevo."
        ) from err
    except GoogleUnavailable as err:
        raise ApiException(
            503,
            "google_unavailable",
            "El ingreso con Google no está disponible ahora. Probá con tu email y contraseña.",
        ) from err

    result = store.google_sign_in(identity, request.remember, request.acceptedTermsVersion)
    if isinstance(result, str):
        status, message = _GOOGLE_ERRORS[result]
        raise ApiException(status, result, message)
    session, created = result
    return GoogleSignInResult(**session.model_dump(), accountCreated=created)


@router.get("/auth/me", response_model=User, summary="Current authenticated user.")
def get_current_user(user: User = Depends(require_user)) -> User:
    return user


@router.post("/auth/logout", status_code=204, summary="Close the current session.")
def logout(token: str = Depends(bearer_token)) -> Response:
    store.logout(token)
    return Response(status_code=204)
