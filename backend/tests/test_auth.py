"""Email/password sign-up, login and JWT handling."""

from datetime import UTC, datetime, timedelta

import jwt
import pytest

from tests.conftest import TEST_SECRET_KEY
from tests.factories import PASSWORD, make_user

pytestmark = pytest.mark.db


def register(client, email="ada@example.com", password=PASSWORD, name="Ada"):
    return client.post(
        "/api/auth/register", json={"email": email, "password": password, "display_name": name}
    )


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_register_returns_user_and_tokens(client, db):
    resp = register(client, email="  Ada@Example.COM ")
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["user"]["email"] == "ada@example.com"
    assert "password_hash" not in body["user"]
    assert body["token_type"] == "Bearer"
    me = client.get("/api/auth/me", headers=bearer(body["access_token"]))
    assert me.status_code == 200
    assert me.get_json()["user"]["email"] == "ada@example.com"


def test_register_rejects_duplicate_email_case_insensitively(client, db):
    assert register(client).status_code == 201
    resp = register(client, email="ADA@example.com")
    assert resp.status_code == 409


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "not-an-email", "password": PASSWORD, "display_name": "A"},
        {"email": "a@example.com", "password": "short", "display_name": "A"},
        {"email": "a@example.com", "password": PASSWORD, "display_name": ""},
        {},
    ],
)
def test_register_validates_input(client, db, payload):
    resp = client.post("/api/auth/register", json=payload)
    assert resp.status_code == 422
    assert resp.get_json()["errors"]


def test_login_with_correct_password(client, db):
    register(client)
    resp = client.post("/api/auth/login", json={"email": "ADA@example.com", "password": PASSWORD})
    assert resp.status_code == 200
    assert resp.get_json()["access_token"]


@pytest.mark.parametrize(
    "email,password",
    [("ada@example.com", "wrong password"), ("nobody@example.com", PASSWORD)],
)
def test_login_failures_look_the_same(client, db, email, password):
    register(client)
    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 401
    assert resp.get_json()["message"] == "Incorrect email or password."


def test_inactive_user_cannot_log_in_or_use_tokens(app, client, db):
    actor = make_user(app, "ada")
    actor.user.is_active = False
    db.commit()
    resp = client.post("/api/auth/login", json={"email": actor.user.email, "password": PASSWORD})
    assert resp.status_code == 401
    assert client.get("/api/auth/me", headers=actor.headers).status_code == 401


def test_protected_route_requires_token(client, db):
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": "Token abc"}).status_code == 401
    assert client.get("/api/auth/me", headers=bearer("not.a.jwt")).status_code == 401


def test_refresh_token_is_not_an_access_token(client, db):
    tokens = register(client).get_json()
    assert client.get("/api/auth/me", headers=bearer(tokens["refresh_token"])).status_code == 401


def test_access_token_cannot_be_used_to_refresh(client, db):
    tokens = register(client).get_json()
    resp = client.post("/api/auth/refresh", json={"refresh_token": tokens["access_token"]})
    assert resp.status_code == 401


def test_refresh_issues_new_tokens(client, db):
    tokens = register(client).get_json()
    resp = client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 200
    new = resp.get_json()
    assert client.get("/api/auth/me", headers=bearer(new["access_token"])).status_code == 200


def test_logout_revokes_access_and_refresh_tokens(client, db):
    tokens = register(client).get_json()
    assert (
        client.post("/api/auth/logout", headers=bearer(tokens["access_token"])).status_code == 204
    )
    assert client.get("/api/auth/me", headers=bearer(tokens["access_token"])).status_code == 401
    resp = client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 401


def _forge(claims, key=TEST_SECRET_KEY, algorithm="HS256"):
    now = datetime.now(UTC)
    payload = {
        "iss": "ymer",
        "typ": "access",
        "ver": 0,
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    payload.update(claims)
    return jwt.encode(payload, key, algorithm=algorithm)


def test_expired_token_is_rejected(app, client, db):
    actor = make_user(app, "ada")
    token = _forge({"sub": str(actor.id), "exp": datetime.now(UTC) - timedelta(seconds=1)})
    assert client.get("/api/auth/me", headers=bearer(token)).status_code == 401


def test_token_signed_with_another_key_is_rejected(app, client, db):
    actor = make_user(app, "ada")
    token = _forge({"sub": str(actor.id)}, key="some-other-key-that-is-at-least-32-bytes")
    assert client.get("/api/auth/me", headers=bearer(token)).status_code == 401


def test_unsigned_token_is_rejected(app, client, db):
    actor = make_user(app, "ada")
    token = _forge({"sub": str(actor.id)}, key=None, algorithm="none")
    assert client.get("/api/auth/me", headers=bearer(token)).status_code == 401


def test_valid_forged_shape_works_so_the_rejections_above_are_meaningful(app, client, db):
    actor = make_user(app, "ada")
    token = _forge({"sub": str(actor.id)})
    assert client.get("/api/auth/me", headers=bearer(token)).status_code == 200
