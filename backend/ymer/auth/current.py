"""Resolve the signed-in user from the request.

The access token comes from ``Authorization: Bearer`` (API clients) or from the
``ymer_access`` cookie (the browser app). Cookie requests that change something must
also pass the CSRF check (``ymer.auth.cookies``).
"""

from collections.abc import Callable
from functools import wraps

from flask import g, request
from werkzeug.exceptions import Forbidden, Unauthorized

from ymer.auth.cookies import ACCESS_COOKIE, csrf_ok
from ymer.auth.tokens import TokenError, TokenType, decode_token_claims
from ymer.db import db_session
from ymer.models import User


def bearer_token() -> str | None:
    header = request.headers.get("Authorization", "")
    if not header:
        return None
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise Unauthorized("Missing bearer token.")
    return token.strip()


def load_user_from_token(token: str, expected_type: TokenType) -> User:
    """Return the active user a token belongs to, or raise 401."""
    user, _ = load_user_and_csrf(token, expected_type)
    return user


def load_user_and_csrf(token: str, expected_type: TokenType) -> tuple[User, str | None]:
    try:
        user_id, version, csrf = decode_token_claims(token, expected_type)
    except TokenError as exc:
        raise Unauthorized("Invalid or expired token.") from exc
    user = db_session.get(User, user_id)
    if user is None or not user.is_active or user.token_version != version:
        raise Unauthorized("Invalid or expired token.")
    return user, csrf


def authenticate() -> User:
    """The user behind this request's credentials, or 401 (403 on a failed CSRF check)."""
    token = bearer_token()
    if token is not None:
        user, _ = load_user_and_csrf(token, "access")
        g.auth_method = "bearer"
        return user
    cookie = request.cookies.get(ACCESS_COOKIE)
    if not cookie:
        raise Unauthorized("Sign in first.")
    user, csrf = load_user_and_csrf(cookie, "access")
    if not csrf_ok(csrf):
        raise Forbidden("Missing or invalid CSRF token. Reload the page and try again.")
    g.auth_method = "cookie"
    g.csrf_token = csrf
    return user


def login_required[**P, R](view: Callable[P, R]) -> Callable[P, R]:
    @wraps(view)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        g.current_user = authenticate()
        return view(*args, **kwargs)

    return wrapper


def current_user() -> User:
    """The user set by :func:`login_required`. Only valid inside such a view."""
    return g.current_user
