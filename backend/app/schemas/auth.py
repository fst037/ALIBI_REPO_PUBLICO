"""Pydantic models for the `auth` domain.

Conform to the `auth` tag of `contracts/auth.yaml`. camelCase field names keep
the wire shape aligned with the generated TS client (see the datasets domain for
the rationale). Only the identity/session models the seam actually uses are here;
account/plans/billing/notifications (other tags of the same contract file) are
separate domains.
"""

from typing import Literal

from pydantic import BaseModel, Field


class User(BaseModel):
    id: str
    name: str
    email: str
    workspace: str


class RegisterRequest(BaseModel):
    email: str
    password: str = Field(min_length=8)
    #: First and last name. Required: it is what the onboarding greeting and the
    #: account screen show, and deriving one from the email address produced
    #: things like "Hi, m.gonzalez42".
    name: str = Field(min_length=1)
    workspace: str | None = None
    #: The version of the Terms and Privacy Policy the user read and accepted
    #: (`app/core/legal.py`). Required: no account exists without that consent.
    acceptedTermsVersion: str = Field(min_length=1)


class LoginRequest(BaseModel):
    email: str
    password: str
    #: "Keep me signed in": the session gets no expiry at all. If the email still
    #: needs verifying, the session is issued at `verify-email` instead, and the
    #: flag that counts is the one sent there.
    remember: bool = False


class AuthSession(BaseModel):
    status: Literal["authenticated"] = "authenticated"
    accessToken: str
    tokenType: Literal["Bearer"] = "Bearer"
    #: None = the session has no expiry; only logout ends it.
    expiresIn: int | None = None
    user: User


class EmailVerificationRequired(BaseModel):
    """A 6-digit code was emailed; `POST /auth/verify-email` opens the session.

    Returned by register, and by login while the account's email is unverified.
    """

    status: Literal["email_verification_required"] = "email_verification_required"
    email: str


# LoginResponse is a discriminated union on `status` (see the contract's oneOf).
LoginResponse = AuthSession | EmailVerificationRequired


class VerifyEmailRequest(BaseModel):
    email: str
    code: str = Field(pattern=r"^[0-9]{6}$")
    #: "Keep me signed in" — the session is issued here, so this is the flag
    #: that decides whether it gets an expiry at all.
    remember: bool = False


class EmailOnlyRequest(BaseModel):
    email: str


class GoogleSignInRequest(BaseModel):
    #: The ID token Google Identity Services gave the browser (a signed JWT).
    credential: str = Field(min_length=1)
    #: "Keep me signed in", as on the email login.
    remember: bool = False
    #: Only needed when the Google account has no ALIBI account yet: creating
    #: one requires consent to the Terms in force, same as `register`. Without
    #: it that case answers 409 `terms_required`, and the client asks for the
    #: consent and sends the same credential again.
    acceptedTermsVersion: str | None = None


class GoogleSignInResult(AuthSession):
    """The session, plus whether this sign-in created the account — a new
    account goes on to choose its plan, like one created by `register`."""

    accountCreated: bool = False
