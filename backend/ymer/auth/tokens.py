"""Issue and verify JWTs.

Two token types share one signing key and are told apart by the ``typ`` claim:
``access`` (short-lived, sent as ``Authorization: Bearer``) and ``refresh`` (long-lived,
only accepted by ``POST /api/auth/refresh``). Both carry ``ver``, the user's
``token_version``; bumping it (logout, password change) revokes every token issued before.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

import jwt
from flask import current_app

from ymer.app import settings_for

ALGORITHM = "HS256"
ISSUER = "ymer"

TokenType = Literal["access", "refresh"]


class TokenError(Exception):
    """The token is missing, malformed, expired, or of the wrong type."""


def _encode(user_id: uuid.UUID, token_version: int, typ: TokenType, ttl: int) -> str:
    now = datetime.now(UTC)
    payload = {
        "iss": ISSUER,
        "sub": str(user_id),
        "typ": typ,
        "ver": token_version,
        "iat": now,
        "exp": now + timedelta(seconds=ttl),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, current_app.config["SECRET_KEY"], algorithm=ALGORITHM)


def issue_token_pair(user_id: uuid.UUID, token_version: int) -> dict:
    settings = settings_for(current_app)
    return {
        "access_token": _encode(user_id, token_version, "access", settings.jwt_access_ttl),
        "refresh_token": _encode(user_id, token_version, "refresh", settings.jwt_refresh_ttl),
        "token_type": "Bearer",
        "expires_in": settings.jwt_access_ttl,
    }


def decode_token(token: str, expected_type: TokenType) -> tuple[uuid.UUID, int]:
    """Return ``(user_id, token_version)`` or raise :class:`TokenError`."""
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
        return uuid.UUID(payload["sub"]), int(payload["ver"])
    except (ValueError, TypeError) as exc:
        raise TokenError("malformed claims") from exc
