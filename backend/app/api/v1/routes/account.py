"""`account` domain routes: current user's account/workspace data."""

from fastapi import APIRouter, Depends

from app.api.v1.deps import require_user
from app.schemas.account import UpdateProfileRequest
from app.schemas.auth import User
from app.services import account as store

router = APIRouter(tags=["account"])


@router.get("/account", response_model=User, summary="Account and workspace details.")
def get_account(user: User = Depends(require_user)) -> User:
    return user


@router.patch("/account", response_model=User, summary="Save profile/workspace changes.")
def update_profile(request: UpdateProfileRequest, user: User = Depends(require_user)) -> User:
    return store.update_profile(user, request)
