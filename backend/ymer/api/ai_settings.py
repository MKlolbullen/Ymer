"""Write-only credentials, owned exclusively by the signed-in account."""

from flask import Blueprint, current_app, jsonify
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import select
from werkzeug.exceptions import NotFound, ServiceUnavailable, UnprocessableEntity

from ymer import audit
from ymer.ai import AiError, get_ai
from ymer.ai.personal import Provider, encrypt_key
from ymer.api.validation import parse_body
from ymer.app import settings_for
from ymer.auth import current_user, login_required
from ymer.db import db_session
from ymer.models.user import User, UserAiProfile

bp = Blueprint("ai_settings", __name__, url_prefix="/settings/ai")


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    provider: Provider | None
    model: str | None = Field(default=None, min_length=1, max_length=200)
    api_key: SecretStr | None = Field(default=None, max_length=4096)


def settings_json(user: User):
    profiles = db_session.execute(
        select(UserAiProfile.provider, UserAiProfile.model).where(UserAiProfile.user_id == user.id)
    )
    default = get_ai().chat
    response = jsonify(
        active=user.ai_provider,
        profiles=dict(profiles.all()),
        server_model=default.model if default else None,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def lock_account() -> User:
    return db_session.scalars(
        select(User)
        .where(User.id == current_user().id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()


@bp.get("")
@login_required
def read_settings():
    return settings_json(current_user())


@bp.put("")
@login_required
def save_settings():
    body = parse_body(Selection)
    if body.provider is None and (body.api_key is not None or body.model is not None):
        raise UnprocessableEntity("Select a provider to save a model or key.")
    user = lock_account()
    if body.provider is not None:
        if not body.model or not body.model.isprintable():
            raise UnprocessableEntity("Enter a model ID.")
        profile = db_session.get(UserAiProfile, (user.id, body.provider))
        if body.api_key is not None:
            key = body.api_key.get_secret_value().strip()
            if (
                not key
                or not key.isascii()
                or not key.isprintable()
                or any(c.isspace() for c in key)
            ):
                raise UnprocessableEntity("Enter a valid API key.")
            try:
                encrypted = encrypt_key(settings_for(current_app), user.id, body.provider, key)
            except AiError as exc:
                raise ServiceUnavailable(str(exc)) from exc
            if profile is None:
                profile = UserAiProfile(user_id=user.id, provider=body.provider)
                db_session.add(profile)
            profile.encrypted_key = encrypted
        if profile is None:
            raise UnprocessableEntity("Enter an API key for this provider.")
        profile.model = body.model
    user.ai_provider = body.provider
    audit.record(
        "account.ai_settings",
        actor=user,
        details={
            "provider": body.provider,
            "model": body.model,
            "key_changed": body.api_key is not None,
        },
    )
    db_session.commit()
    return settings_json(user)


@bp.delete("/<provider>")
@login_required
def remove_profile(provider: str):
    user = lock_account()
    profile = db_session.get(UserAiProfile, (user.id, provider))
    if profile is None:
        raise NotFound("No saved key for this provider.")
    if user.ai_provider == provider:
        user.ai_provider = None
    audit.record("account.ai_key_removed", actor=user, details={"provider": provider})
    db_session.delete(profile)
    db_session.commit()
    return settings_json(user)
