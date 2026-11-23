"""Pydantic models for the `account` tag of `contracts/auth.yaml`.
The account entity is `User` (defined in `app.schemas.auth`).
"""

from pydantic import BaseModel, model_validator


class UpdateProfileRequest(BaseModel):
    """Partial patch — saved per section (never "save all"). At least one field."""

    name: str | None = None
    email: str | None = None
    workspace: str | None = None

    @model_validator(mode="after")
    def _at_least_one(self) -> "UpdateProfileRequest":
        if self.name is None and self.email is None and self.workspace is None:
            raise ValueError("provide at least one field to update")
        return self
