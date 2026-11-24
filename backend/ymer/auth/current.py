"""Resolve the signed-in user from the request's bearer token."""

from collections.abc import Callable
from functools import wraps

from flask import g, request
from werkzeug.exceptions import Unauthorized

from ymer.auth.tokens import TokenError, decode_token
from ymer.db import db_session
from ymer.models import User


def _bearer_token() -> str:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise Unauthorized("Missing bearer token.")
    return token.strip()


def load_user_from_token(token: str, expected_type: str) -> User:
    """Return the active user a token belongs to, or raise 401."""
    try:
        user_id, version = decode_token(token, expected_type)  # type: ignore[arg-type]
    except TokenError as exc:
        raise Unauthorized("Invalid or expired token.") from exc
    user = db_session.get(User, user_id)
    if user is None or not user.is_active or user.token_version != version:
        raise Unauthorized("Invalid or expired token.")
    return user


def login_required[**P, R](view: Callable[P, R]) -> Callable[P, R]:
    @wraps(view)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        g.current_user = load_user_from_token(_bearer_token(), "access")
        return view(*args, **kwargs)

    return wrapper


def current_user() -> User:
    """The user set by :func:`login_required`. Only valid inside such a view."""
    return g.current_user
