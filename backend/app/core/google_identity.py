"""Checks a "Sign in with Google" ID token — the credential Google's button hands the browser.

No password and no OAuth code reach the backend: Google Identity Services gives
the browser a signed JWT (the *ID token*) for the account the person picked, and
the frontend posts it to `POST /auth/google`. This module decides whether to
believe it, following Google's server-side checklist
(developers.google.com/identity/gsi/web/guides/verify-google-id-token):

* the RS256 signature verifies against one of Google's published keys;
* `aud` is this app's client id (`ALIBI_GOOGLE_CLIENT_ID`), so a token minted
  for another site is refused;
* `iss` is accounts.google.com and `exp` is in the future;
* `email_verified` is true — the address is what links the Google account to an
  ALIBI one, so an unproven address is no identity at all.

There is no client secret: an ID token is checked with public keys, so there is
nothing to keep in Secrets Manager.

Two routes, the same split as `mailer.py`: the API Lambda sits in the VPC with
no route to googleapis.com, so in the deployed topology the bridge Lambda
(`app/llm_bridge.py`, kind `google`) fetches the keys and runs the check; local
dev and the tests run it in-process. `PyJWKClient` caches the keys for the life
of the warm function, so only a cold start pays the fetch, and a key Google
rotated in since is fetched on its first unknown `kid`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Any

import jwt

from app.core.config import get_settings

GOOGLE_CERTS_URL = "https://www.googleapis.com/oauth2/v3/certs"
#: Both spellings are valid `iss` values for a Google ID token.
_ISSUERS = ("accounts.google.com", "https://accounts.google.com")
_TIMEOUT_SECONDS = 10.0
#: Google rotates its signing keys every few days and serves them with a
#: multi-hour max-age; an hour keeps a warm function well inside that.
_KEYS_LIFESPAN_SECONDS = 3600
#: Clock skew allowed on `exp`/`iat` between Google and this function.
_LEEWAY_SECONDS = 60


class GoogleUnavailable(Exception):
    """Google sign-in is not configured here, or Google's keys could not be fetched."""


class InvalidGoogleToken(Exception):
    """The credential is not a valid ID token for this app."""


@dataclass(frozen=True)
class GoogleIdentity:
    #: Google's stable id for the account — survives the person changing address.
    sub: str
    email: str
    name: str


@lru_cache(maxsize=1)
def _keys() -> jwt.PyJWKClient:
    return jwt.PyJWKClient(
        GOOGLE_CERTS_URL,
        cache_keys=True,
        lifespan=_KEYS_LIFESPAN_SECONDS,
        timeout=_TIMEOUT_SECONDS,
    )


def _display_name(claims: dict[str, Any], email: str) -> str:
    name = str(claims.get("name") or "").strip()
    if name:
        return name
    parts = (str(claims.get("given_name") or "").strip(), str(claims.get("family_name") or "").strip())
    # A Google account always has a name; the address is only the last resort.
    return " ".join(p for p in parts if p) or email.split("@")[0]


def check(credential: str, audience: str) -> dict[str, str]:
    """Verify `credential` in this process and return the identity's fields.

    A plain dict rather than `GoogleIdentity`, because the bridge sends it back
    as JSON. Raises `InvalidGoogleToken` for anything wrong with the token and
    `GoogleUnavailable` only when Google's keys could not be reached.
    """
    try:
        signing_key = _keys().get_signing_key_from_jwt(credential)
    except jwt.PyJWKClientConnectionError as err:
        raise GoogleUnavailable(f"could not fetch Google's keys: {err}") from err
    except (jwt.PyJWKClientError, jwt.InvalidTokenError) as err:
        # Malformed token, or a `kid` that is not one of Google's keys.
        raise InvalidGoogleToken(str(err)) from err

    try:
        claims = jwt.decode(
            credential,
            signing_key.key,
            algorithms=["RS256"],
            audience=audience,
            leeway=_LEEWAY_SECONDS,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except jwt.InvalidTokenError as err:
        raise InvalidGoogleToken(str(err)) from err

    if claims.get("iss") not in _ISSUERS:
        raise InvalidGoogleToken(f"unexpected issuer {claims.get('iss')!r}")
    email = str(claims.get("email") or "").strip().lower()
    if not email:
        raise InvalidGoogleToken("the token carries no email")
    if claims.get("email_verified") not in (True, "true"):
        raise InvalidGoogleToken("Google has not verified this email")

    return asdict(GoogleIdentity(sub=str(claims["sub"]), email=email, name=_display_name(claims, email)))


def verify(credential: str) -> GoogleIdentity:
    """The Google account behind `credential`, by whichever route this deploy has."""
    settings = get_settings()
    if not settings.google_client_id:
        raise GoogleUnavailable("ALIBI_GOOGLE_CLIENT_ID is not set")

    bridge = settings.llm_bridge_function_name
    if not bridge:
        return GoogleIdentity(**check(credential, settings.google_client_id))

    from app.core.llm_bridge_transport import call

    body = call(
        bridge,
        {"kind": "google", "credential": credential, "audience": settings.google_client_id},
        read_timeout=_TIMEOUT_SECONDS,
        unavailable_cls=GoogleUnavailable,
    )
    if body.get("ok"):
        return GoogleIdentity(**body["completion"])
    # A bad token is the user's problem (401); anything else is ours (503).
    if body.get("code") == "invalid_google_token":
        raise InvalidGoogleToken(body.get("error") or "")
    raise GoogleUnavailable(body.get("error") or "bridge returned an error with no detail")


__all__ = ["GoogleIdentity", "GoogleUnavailable", "InvalidGoogleToken", "check", "verify"]
