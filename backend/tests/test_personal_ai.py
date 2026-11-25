"""Exercise authenticated settings and provider routing without network or a provider account."""

import pytest
from sqlalchemy import create_engine

from tests.factories import make_user
from ymer.ai import AiError, ChatRequest
from ymer.ai.personal import ENDPOINTS, Provider, profile_chat
from ymer.app import settings_for
from ymer.db import SessionLocal, db_session, get_engine
from ymer.models.audit import AuditEvent
from ymer.models.user import User, UserAiProfile

PATH = "/api/settings/ai"


@pytest.fixture
def accounts(app):
    # These endpoints only need the account tables, so they can run without pgvector.
    engine = create_engine("sqlite://")
    User.__table__.create(engine)
    UserAiProfile.__table__.create(engine)
    AuditEvent.__table__.create(engine)
    SessionLocal.configure(bind=engine)
    try:
        yield make_user(app, "alice"), make_user(app, "bob")
    finally:
        db_session.remove()
        SessionLocal.configure(bind=get_engine(app))
        engine.dispose()


@pytest.mark.parametrize("provider", list(Provider))
def test_personal_route_and_isolation(app, client, accounts, provider, monkeypatch):
    alice, bob = accounts
    model = f"{provider}-model"
    key = f"test-only-{provider}-credential"
    response = client.put(
        PATH,
        headers=alice.headers,
        json={
            "provider": provider,
            "model": model,
            "api_key": key,
        },
    )
    assert response.status_code == 200, response.json
    assert key not in response.text
    assert response.json["profiles"] == {provider: model}
    assert response.headers["Cache-Control"] == "no-store"
    assert client.get(PATH, headers=bob.headers).json["profiles"] == {}
    assert client.get("/api/ai/status", headers=bob.headers).json["demo"]
    status = client.get("/api/ai/status", headers=alice.headers).json
    assert (status["provider"], status["model"], status["demo"]) == (provider, model, False)
    assert status["embedding_model"] == "hash-256"
    assert client.delete(f"{PATH}/{provider}", headers=bob.headers).status_code == 404

    profile = db_session.get(UserAiProfile, (alice.id, provider))
    assert key.encode() not in profile.encrypted_key
    settings = settings_for(app)
    chat = profile_chat(settings, profile, model)
    if provider == Provider.ANTHROPIC:
        assert str(chat.client.base_url).rstrip("/") == ENDPOINTS[provider]
        assert chat.client.api_key == key
    else:

        def send(url, body, api_key, timeout):
            assert url == ENDPOINTS[provider] + "/chat/completions"
            assert body["model"] == model and api_key == key
            return {"choices": [{"message": {"content": "ok"}}]}

        monkeypatch.setattr("ymer.ai.providers._post_json", send)
        assert chat.complete(ChatRequest(task="test", system="test", messages=[])) == "ok"

    copied = UserAiProfile(user_id=bob.id, provider=provider, encrypted_key=profile.encrypted_key)
    with pytest.raises(AiError, match="decrypt"):
        profile_chat(settings, copied, model)


def test_switch_rotate_remove_and_disable(app, client, accounts):
    alice, _ = accounts

    def save(**body):
        return client.put(PATH, headers=alice.headers, json=body)

    assert client.get(PATH).status_code == 401
    assert save(provider="openai", model="one").status_code == 422
    assert save(provider="openai", model="one", api_key="one-key").status_code == 200
    assert save(provider="gemini", model="two", api_key="two-key").status_code == 200
    assert save(provider="openai", model="three").json["profiles"] == {
        "openai": "three",
        "gemini": "two",
    }

    def saved_key():
        profile = db_session.get(UserAiProfile, (alice.id, "openai"))
        return profile_chat(settings_for(app), profile, profile.model).api_key

    assert saved_key() == "one-key"
    assert save(provider="openai", model="three", api_key="new-key").status_code == 200
    assert saved_key() == "new-key"
    assert save(provider=None).json["active"] is None
    assert client.get("/api/ai/status", headers=alice.headers).json["demo"]
    assert save(provider="gemini", model="two").json["active"] == "gemini"
    assert client.delete(PATH + "/openai", headers=alice.headers).json["active"] == "gemini"
    app.extensions["ymer_ai"].chat = None
    assert client.get("/api/ai/status", headers=alice.headers).json["enabled"] is False
    deleted = client.delete(PATH + "/gemini", headers=alice.headers).json
    assert deleted["active"] is None and deleted["profiles"] == {}
