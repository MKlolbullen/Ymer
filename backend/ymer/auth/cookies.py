"""Browser sessions in httpOnly cookies, with CSRF protection.

After sign-in the browser holds two cookies it cannot read from JavaScript:

- ``ymer_access``: the access token, sent with every ``/api`` request.
- ``ymer_refresh``: the refresh token, only sent to ``/api/auth`` and only from Ymer's
  own pages (``SameSite=Strict``).

Cookies are sent automatically, so a cookie-authenticated request that changes
something must also carry ``X-CSRF-Token``. Its value is a random string chosen at
sign-in, stored inside both tokens (the ``csrf`` claim) and handed to the app in the
sign-in response. Another site can make the browser send the cookies but cannot read
the value, so it cannot forge the header; and because the value is checked against the
signed token rather than against a second cookie, planting a cookie does not help either.

API clients that send ``Authorization: Bearer`` are not affected: a bearer token is
never sent automatically, so it needs no CSRF check.
"""

import hmac

from flask import Flask, Response, current_app, request

from ymer.app import settings_for

ACCESS_COOKIE = "ymer_access"
REFRESH_COOKIE = "ymer_refresh"
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _secure(app: Flask) -> bool:
    settings = settings_for(app)
    return (
        settings.cookie_secure
        if settings.cookie_secure is not None
        else settings.env == "production"
    )


def set_session_cookies(response: Response, tokens: dict) -> None:
    settings = settings_for(current_app)
    common = {"httponly": True, "secure": _secure(current_app), "domain": settings.cookie_domain}
    response.set_cookie(
        ACCESS_COOKIE,
        tokens["access_token"],
        max_age=settings.jwt_access_ttl,
        path="/api",
        samesite="Lax",
        **common,
    )
    response.set_cookie(
        REFRESH_COOKIE,
        tokens["refresh_token"],
        max_age=settings.jwt_refresh_ttl,
        path="/api/auth",
        samesite="Strict",
        **common,
    )


def clear_session_cookies(response: Response) -> None:
    settings = settings_for(current_app)
    for name, path in ((ACCESS_COOKIE, "/api"), (REFRESH_COOKIE, "/api/auth")):
        response.delete_cookie(
            name,
            path=path,
            domain=settings.cookie_domain,
            secure=_secure(current_app),
            httponly=True,
            samesite="Lax" if name == ACCESS_COOKIE else "Strict",
        )


def csrf_ok(expected: str | None) -> bool:
    """Whether this request may go ahead given the token's ``csrf`` claim."""
    if request.method in SAFE_METHODS:
        return True
    given = request.headers.get(CSRF_HEADER, "")
    return bool(expected) and bool(given) and hmac.compare_digest(given.encode(), expected.encode())
