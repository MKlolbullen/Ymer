"""Authentication endpoints.

- ``POST /api/auth/register``  {email, password, display_name} -> user + tokens
- ``POST /api/auth/login``     {email, password} -> user + tokens
- ``POST /api/auth/refresh``   {refresh_token} -> new tokens
- ``POST /api/auth/logout``    revokes every token the user holds
- ``GET  /api/auth/me``        the signed-in user
"""

from flask import Blueprint, jsonify
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from werkzeug.exceptions import Conflict, Unauthorized

from ymer.api.validation import parse_body
from ymer.auth import current_user, hash_password, issue_token_pair, login_required
from ymer.auth.current import load_user_from_token
from ymer.auth.passwords import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH, verify_password
from ymer.db import db_session
from ymer.models import User

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


def _normalise_email(value: str) -> str:
    return value.strip().lower()


class RegisterBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)
    display_name: str = Field(min_length=1, max_length=120)

    _email = field_validator("email")(_normalise_email)


class LoginBody(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=MAX_PASSWORD_LENGTH)


class RefreshBody(BaseModel):
    refresh_token: str


def user_json(user: User) -> dict:
    return {
        "id": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


@auth_bp.post("/register")
def register():
    body = parse_body(RegisterBody)
    if db_session.scalar(select(User.id).where(User.email == body.email)):
        raise Conflict("An account with this email already exists.")
    user = User(
        email=body.email,
        password_hash=hash_password(body.password),
        display_name=body.display_name.strip(),
    )
    db_session.add(user)
    try:
        db_session.commit()
    except IntegrityError as exc:  # lost a race with a concurrent sign-up
        db_session.rollback()
        raise Conflict("An account with this email already exists.") from exc
    return jsonify(user=user_json(user), **issue_token_pair(user.id, user.token_version)), 201


@auth_bp.post("/login")
def login():
    body = parse_body(LoginBody)
    user = db_session.scalar(select(User).where(User.email == _normalise_email(body.email)))
    ok = verify_password(user.password_hash if user else None, body.password)
    if not ok or user is None or not user.is_active:
        raise Unauthorized("Incorrect email or password.")
    return jsonify(user=user_json(user), **issue_token_pair(user.id, user.token_version))


@auth_bp.post("/refresh")
def refresh():
    body = parse_body(RefreshBody)
    user = load_user_from_token(body.refresh_token, "refresh")
    return jsonify(**issue_token_pair(user.id, user.token_version))


@auth_bp.post("/logout")
@login_required
def logout():
    user = current_user()
    user.token_version = User.token_version + 1  # atomic in SQL
    db_session.commit()
    return "", 204


@auth_bp.get("/me")
@login_required
def me():
    return jsonify(user=user_json(current_user()))
