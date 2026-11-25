"""Authentication endpoints.

- ``POST /api/auth/register``      {email, password, display_name, session?} -> user
- ``POST /api/auth/login``         {email, password, session?} -> user
- ``POST /api/auth/refresh``       new tokens (from the refresh cookie, or {refresh_token})
- ``POST /api/auth/logout``        revokes every token the user holds, clears the cookies
- ``GET  /api/auth/me``            the signed-in user (and the CSRF token, for the app)
- ``POST /api/auth/collab-token``  a two-minute token for opening a live document
- ``GET  /api/auth/activity``      the signed-in user's own security events

``session`` picks how the tokens are handed over:

- ``"cookie"`` (the default, used by the app): httpOnly cookies (``ymer.auth.cookies``);
  the body carries ``csrf_token`` for the ``X-CSRF-Token`` header, and no tokens.
- ``"token"`` (scripts and other API clients): ``access_token`` and ``refresh_token`` in
  the body, to send as ``Authorization: Bearer``. No cookies are set.

Sign-in and sign-up are rate limited per address, and failed sign-ins per email
(``ymer.ratelimit``). Each outcome is written to the audit log.
"""

from typing import Literal

from flask import Blueprint, current_app, g, jsonify, request
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from werkzeug.exceptions import Conflict, Forbidden, HTTPException, Unauthorized

from ymer import audit, ratelimit
from ymer.api.validation import parse_body
from ymer.app import settings_for
from ymer.auth import current_user, hash_password, issue_token_pair, login_required
from ymer.auth.cookies import (
    REFRESH_COOKIE,
    clear_session_cookies,
    csrf_ok,
    set_session_cookies,
)
from ymer.auth.current import authenticate, load_user_and_csrf
from ymer.auth.passwords import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH, verify_password
from ymer.auth.tokens import issue_collab_token, new_csrf_token
from ymer.db import db_session
from ymer.models import AuditEvent, User

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


def _normalise_email(value: str) -> str:
    return value.strip().lower()


class RegisterBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)
    display_name: str = Field(min_length=1, max_length=120)
    session: Literal["cookie", "token"] = "cookie"

    _email = field_validator("email")(_normalise_email)


class LoginBody(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=MAX_PASSWORD_LENGTH)
    session: Literal["cookie", "token"] = "cookie"


class RefreshBody(BaseModel):
    refresh_token: str | None = None


def user_json(user: User) -> dict:
    return {
        "id": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


def _signed_in(user: User, mode: str, status: int = 200):
    """The sign-in response: cookies + CSRF token, or bearer tokens in the body."""
    if mode == "token":
        return jsonify(
            user=user_json(user), **issue_token_pair(user.id, user.token_version)
        ), status
    csrf = new_csrf_token()
    tokens = issue_token_pair(user.id, user.token_version, csrf)
    response = jsonify(user=user_json(user), csrf_token=csrf, expires_in=tokens["expires_in"])
    response.status_code = status
    set_session_cookies(response, tokens)
    return response


@auth_bp.post("/register")
def register():
    settings = settings_for(current_app)
    ratelimit.hit(
        "register",
        ratelimit.client_ip(),
        settings.rate_limit_register,
        3600,
        "Too many new accounts from this address. Try again later.",
    )
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
        db_session.flush()
        audit.record("auth.register", actor=user)
        db_session.commit()
    except IntegrityError as exc:  # lost a race with a concurrent sign-up
        db_session.rollback()
        raise Conflict("An account with this email already exists.") from exc
    return _signed_in(user, body.session, 201)


LOGIN_FAILURE_WINDOW = 15 * 60


@auth_bp.post("/login")
def login():
    settings = settings_for(current_app)
    ratelimit.hit(
        "login",
        ratelimit.client_ip(),
        settings.rate_limit_login,
        60,
        "Too many sign-in attempts. Wait a minute and try again.",
    )
    body = parse_body(LoginBody)
    email = _normalise_email(body.email)
    try:
        ratelimit.check(
            "login-failures",
            email,
            settings.rate_limit_login_failures,
            LOGIN_FAILURE_WINDOW,
            "Too many failed sign-ins for this account. Try again in 15 minutes.",
        )
    except HTTPException:
        audit.record("auth.login_blocked", details={"email": email[:320]}, commit=True)
        raise
    user = db_session.scalar(select(User).where(User.email == email))
    ok = verify_password(user.password_hash if user else None, body.password)
    if not ok or user is None or not user.is_active:
        ratelimit.record("login-failures", email, LOGIN_FAILURE_WINDOW)
        audit.record(
            "auth.login_failed",
            actor=user,
            details={"email": email[:320], "reason": "inactive" if ok and user else "password"},
            commit=True,
        )
        raise Unauthorized("Incorrect email or password.")
    audit.record("auth.login", actor=user, details={"session": body.session}, commit=True)
    return _signed_in(user, body.session)


@auth_bp.post("/refresh")
def refresh():
    settings = settings_for(current_app)
    ratelimit.hit("refresh", ratelimit.client_ip(), settings.rate_limit_login * 3, 60)
    body = parse_body(RefreshBody) if request.get_data() else RefreshBody()
    if body.refresh_token:  # an API client
        user, _ = load_user_and_csrf(body.refresh_token, "refresh")
        return jsonify(**issue_token_pair(user.id, user.token_version))
    cookie = request.cookies.get(REFRESH_COOKIE)
    if not cookie:
        raise Unauthorized("Sign in first.")
    try:
        user, csrf = load_user_and_csrf(cookie, "refresh")
    except Unauthorized:
        response = jsonify(error="Unauthorized", message="Your session has ended. Sign in again.")
        response.status_code = 401
        clear_session_cookies(response)
        return response
    if not csrf_ok(csrf):
        raise Forbidden("Missing or invalid CSRF token. Reload the page and try again.")
    # Keep the same CSRF value, so other open tabs keep working.
    tokens = issue_token_pair(user.id, user.token_version, csrf)
    response = jsonify(csrf_token=csrf, expires_in=tokens["expires_in"])
    set_session_cookies(response, tokens)
    return response


@auth_bp.post("/logout")
def logout():
    """Revoke every token the user holds. Always clears the cookies, even when the
    session has already expired, so the browser is left signed out either way."""
    try:
        user = authenticate()
    except Unauthorized:
        user = None
    if user is not None:
        user.token_version = User.token_version + 1  # atomic in SQL
        audit.record("auth.logout", actor=user)
        db_session.commit()
    response = current_app.response_class(status=204)
    clear_session_cookies(response)
    return response


@auth_bp.get("/me")
@login_required
def me():
    body: dict = {"user": user_json(current_user())}
    if getattr(g, "csrf_token", None):
        body["csrf_token"] = g.csrf_token
    return jsonify(body)


@auth_bp.post("/collab-token")
@login_required
def collab_token():
    """A short-lived token for the collab service. The browser cannot read its cookies,
    and the collab WebSocket may live on another host, so it asks for one of these each
    time it (re)connects."""
    user = current_user()
    return jsonify(token=issue_collab_token(user.id, user.token_version))


@auth_bp.get("/activity")
@login_required
def activity():
    """The signed-in user's recent sign-ins, failed sign-ins and sign-outs."""
    user = current_user()
    events = db_session.scalars(
        select(AuditEvent)
        .where(AuditEvent.actor_id == user.id, AuditEvent.action.like("auth.%"))
        .order_by(AuditEvent.created_at.desc())
        .limit(50)
    ).all()
    return jsonify(events=[audit.event_json(e, user) for e in events])
