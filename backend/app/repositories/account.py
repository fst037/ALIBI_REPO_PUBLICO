"""Postgres repository for the `account` domain (profile updates).

The account entity is `User`. Only the fields present in the patch are updated
(saved per section, never "save all"); the request model guarantees at least
one is set.
"""

from sqlmodel import Session

from app.db.models import User as UserRow
from app.schemas.account import UpdateProfileRequest
from app.schemas.auth import User


def update_profile(session: Session, user_id: str, request: UpdateProfileRequest) -> User:
    row = session.get(UserRow, user_id)
    if request.name is not None:
        row.name = request.name
    if request.email is not None:
        row.email = request.email
    if request.workspace is not None:
        row.workspace = request.workspace
    session.add(row)
    session.flush()
    return User(id=row.id, name=row.name, email=row.email, workspace=row.workspace)
