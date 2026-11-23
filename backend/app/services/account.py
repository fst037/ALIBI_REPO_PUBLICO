"""`account` domain service — swap point for profile updates.

`get_account` is just the authenticated user (resolved by `require_user`), so
only the update path needs a backend: it writes to the database when configured,
otherwise it mutates the in-memory fixture user (the object the fixture session
hands back), preserving the prior no-database behaviour for tests.
"""

from app.db import pool
from app.db.engine import session_scope
from app.repositories import account as repo
from app.schemas.account import UpdateProfileRequest
from app.schemas.auth import User


def update_profile(user: User, request: UpdateProfileRequest) -> User:
    if pool.is_configured():
        with session_scope() as session:
            return repo.update_profile(session, user.id, request)

    # Fixture backend: mutate the in-memory user object in place.
    if request.name is not None:
        user.name = request.name
    if request.email is not None:
        user.email = request.email
    if request.workspace is not None:
        user.workspace = request.workspace
    return user
