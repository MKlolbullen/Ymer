"""Account creation shared by self-registration and the local administrator CLI."""

from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from werkzeug.exceptions import Conflict

from ymer import audit
from ymer.auth.passwords import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH, hash_password
from ymer.db import db_session
from ymer.models import User


class AccountDetails(BaseModel):
    email: EmailStr
    password: str = Field(
        min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH, repr=False
    )
    display_name: str = Field(min_length=1, max_length=120)

    @field_validator("email")
    @classmethod
    def normalise_email(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("display_name")
    @classmethod
    def trim_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Enter a display name.")
        return value.strip()


def create_account(details: AccountDetails, *, provisioned: bool = False) -> User:
    duplicate = "An account with this email already exists."
    if db_session.scalar(select(User.id).where(User.email == details.email)):
        raise Conflict(duplicate)
    user = User(
        email=details.email,
        password_hash=hash_password(details.password),
        display_name=details.display_name,
    )
    db_session.add(user)
    try:
        db_session.flush()
        if provisioned:
            audit.record("auth.provision", target=("user", user.id))
        else:
            audit.record("auth.register", actor=user)
        db_session.commit()
    except IntegrityError as exc:
        db_session.rollback()
        if db_session.scalar(select(User.id).where(User.email == details.email)):
            raise Conflict(duplicate) from exc
        raise
    return user
