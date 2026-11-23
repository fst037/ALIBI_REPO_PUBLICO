"""Password hashing and opaque token/id generation for the auth domain.

Passwords are stored only as bcrypt hashes (never plaintext). Tokens and ids
are URL-safe random strings — opaque, server-issued, and looked up in the
database, so there's no signing key to manage (the trade-off vs stateless JWTs
is a DB read per authenticated request, fine at this scale).
"""

import hashlib
import hmac
import secrets

import bcrypt


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def new_id(prefix: str) -> str:
    """A readable, unique id, e.g. new_id('usr') -> 'usr_x8Kf...'."""
    return f"{prefix}_{secrets.token_urlsafe(8)}"


def new_token() -> str:
    """An opaque session bearer token."""
    return "sess_" + secrets.token_urlsafe(24)


# ── Email verification codes ───────────────────────────────────────────────
# Shared by both auth backends (app/repositories/auth.py, app/services/auth_memory.py).

#: How long a code is valid.
CODE_TTL_MINUTES = 10
#: Wrong guesses before a code is burnt. A 6-digit code is 10^6 values, all
#: walkable inside the TTL without a cap (see migration 0003).
CODE_MAX_ATTEMPTS = 5
#: Minimum gap between two codes for the same email, so "Reenviar" can't be
#: used to flood a mailbox (or burn through the sending account's daily quota).
CODE_RESEND_COOLDOWN_SECONDS = 60


def new_code() -> str:
    """A uniformly random 6-digit code, e.g. '048213'."""
    return f"{secrets.randbelow(10**6):06d}"


def hash_code(code: str) -> str:
    """What is stored: a leaked row doesn't hand out a live code."""
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def code_matches(code: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_code(code), stored_hash)
