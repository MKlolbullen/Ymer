"""Private provisioning through the real CLI and HTTP entry points."""

import pytest
from sqlalchemy import create_engine, select

from ymer.app import settings_for
from ymer.db import SessionLocal, db_session, get_engine
from ymer.models import AuditEvent, User, Workspace, WorkspaceMember


@pytest.fixture
def account_db(app):
    engine = create_engine("sqlite://")
    for model in (User, Workspace, WorkspaceMember, AuditEvent):
        model.__table__.create(engine)
    SessionLocal.configure(bind=engine)
    try:
        yield
    finally:
        db_session.remove()
        SessionLocal.configure(bind=get_engine(app))
        engine.dispose()


def test_registration_policy(app, client, account_db):
    assert client.get("/api/auth/registration").json == {"enabled": True}
    body = {"display_name": "User", "password": "test-only-password", "email": "a@example.com"}
    assert client.post("/api/auth/register", json=body).status_code == 201
    settings_for(app).registration_enabled = False
    assert client.get("/api/auth/registration").json == {"enabled": False}
    for session in ("cookie", "token"):
        response = client.post("/api/auth/register", json={**body, "session": session})
        assert response.status_code == 403
        assert "Set-Cookie" not in response.headers
    assert client.post("/api/auth/register", json={}).status_code == 403
    assert client.post("/api/auth/login", json=body).status_code == 200


def test_provision_two_equal_owners(app, client, account_db):
    settings_for(app).registration_enabled = False
    runner = app.test_cli_runner()
    actors = []
    for name in ("First", "Second"):
        email = f"{name.lower()}@example.com"
        password = f"{name}-test-only-password"
        args = ["create-user", "--email", email, "--display-name", name]
        result = runner.invoke(args=args, input=f"{password}\n{password}\n")
        assert result.exit_code == 0, result.output
        assert password not in result.output
        duplicate = runner.invoke(args=args, input="unused-secret\nunused-secret\n")
        assert duplicate.exit_code != 0 and "already exists" in duplicate.output
        login = client.post(
            "/api/auth/login",
            json={
                "email": email,
                "password": password,
                "session": "token",
            },
        )
        assert login.status_code == 200
        actors.append({"Authorization": "Bearer " + login.json["access_token"]})
    assert (
        len(
            db_session.scalars(
                select(AuditEvent).where(AuditEvent.action == "auth.provision")
            ).all()
        )
        == 2
    )

    created = client.post("/api/workspaces", headers=actors[0], json={"name": "Our workspace"})
    assert created.status_code == 201
    url = "/api/workspaces/" + created.json["workspace"]["id"]
    added = client.post(
        url + "/members", headers=actors[0], json={"email": "second@example.com", "role": "owner"}
    )
    assert added.status_code == 201
    for headers in actors:
        assert client.get(url, headers=headers).json["workspace"]["role"] == "owner"
        assert (
            client.patch(url, headers=headers, json={"name": "Shared workspace"}).status_code == 200
        )
    assert client.post("/api/auth/register", json={"email": "third@example.com"}).status_code == 403


def test_cli_hides_invalid_password(app):
    result = app.test_cli_runner().invoke(
        args=["create-user", "--email", "person@example.com", "--display-name", "Person"],
        input="short\nshort\n",
    )
    assert result.exit_code != 0 and "password" in result.output.lower()
    assert "short" not in result.output
