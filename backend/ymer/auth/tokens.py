"""Issue and verify JWTs.

Three token types share one signing key and are told apart by the ``typ`` claim:

- ``access``: short-lived. Browsers get it in an httpOnly cookie; API clients send it
  as ``Authorization: Bearer``.
- ``refresh``: long-lived, only accepted by ``POST /api/auth/refresh``.
- ``collab``: lives two minutes, only accepted by the collab service when a page opens
  a live connection (the browser cannot read its cookies, so it asks for one of these).

All carry ``ver``, the user's ``token_version``; bumping it (logout, password change)
revokes every token issued before. Access and refresh tokens issued to a browser also
carry ``csrf``, the value the browser must echo in ``X-CSRF-Token`` (see
``ymer.auth.cookies``). It is chosen at sign-in and kept across refreshes.
"""

import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

import jwt
from flask import current_app

from ymer.app import settings_for

ALGORITHM = "HS256"
ISSUER = "ymer"

TokenType = Literal["access", "refresh", "collab"]

COLLAB_TTL = 120


class TokenError(Exception):
    """The token is missing, malformed, expired, or of the wrong type."""


def _encode(
    user_id: uuid.UUID, token_version: int, typ: TokenType, ttl: int, csrf: str | None = None
) -> str:
    now = datetime.now(UTC)
    payload: dict = {
        "iss": ISSUER,
        "sub": str(user_id),
        "typ": typ,
        "ver": token_version,
        "iat": now,
        "exp": now + timedelta(seconds=ttl),
        "jti": uuid.uuid4().hex,
    }
    if csrf:
        payload["csrf"] = csrf
    return jwt.encode(payload, current_app.config["SECRET_KEY"], algorithm=ALGORITHM)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def issue_token_pair(user_id: uuid.UUID, token_version: int, csrf: str | None = None) -> dict:
    """Access + refresh tokens. Pass ``csrf`` for a browser session (cookies)."""
    settings = settings_for(current_app)
    return {
        "access_token": _encode(user_id, token_version, "access", settings.jwt_access_ttl, csrf),
        "refresh_token": _encode(user_id, token_version, "refresh", settings.jwt_refresh_ttl, csrf),
        "token_type": "Bearer",
        "expires_in": settings.jwt_access_ttl,
    }


def issue_collab_token(user_id: uuid.UUID, token_version: int) -> str:
    return _encode(user_id, token_version, "collab", COLLAB_TTL)


def decode_token(token: str, expected_type: TokenType) -> tuple[uuid.UUID, int]:
    """Return ``(user_id, token_version)`` or raise :class:`TokenError`."""
    user_id, version, _ = decode_token_claims(token, expected_type)
    return user_id, version


def decode_token_claims(token: str, expected_type: TokenType) -> tuple[uuid.UUID, int, str | None]:
    """Return ``(user_id, token_version, csrf)`` or raise :class:`TokenError`."""
    try:
        payload = jwt.decode(
            token,
            current_app.config["SECRET_KEY"],
            algorithms=[ALGORITHM],
            issuer=ISSUER,
            options={"require": ["exp", "iat", "sub", "typ", "ver", "iss"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
    if payload["typ"] != expected_type:
        raise TokenError(f"expected a {expected_type} token")
    try:
        csrf = payload.get("csrf")
        return (
            uuid.UUID(payload["sub"]),
            int(payload["ver"]),
            csrf if isinstance(csrf, str) else None,
        )
    except (ValueError, TypeError) as exc:
        raise TokenError("malformed claims") from exc
