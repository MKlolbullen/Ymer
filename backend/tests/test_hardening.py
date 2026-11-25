"""Cookie sessions with CSRF protection, rate limits and the audit log."""

import pytest
from sqlalchemy import select

from tests.conftest import TEST_COLLAB_SECRET
from tests.factories import PASSWORD, make_user, make_workspace
from ymer.extensions import get_redis
from ymer.models import AuditEvent, Role
from ymer.ratelimit import PREFIX

pytestmark = pytest.mark.db


def set_settings(app, **changes):
    app.extensions["ymer_settings"] = app.extensions["ymer_settings"].model_copy(update=changes)


@pytest.fixture(autouse=True)
def _clean_counters(app):
    with app.app_context():
        redis = get_redis()
        for key in redis.scan_iter(f"{PREFIX}:*"):
            redis.delete(key)
    yield


def sign_up(client, email="ada@example.com"):
    return client.post(
        "/api/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": "Ada"},
    )


def set_cookies(resp) -> dict[str, str]:
    return {h.split("=", 1)[0]: h for h in resp.headers.getlist("Set-Cookie")}


# --- cookie sessions -----------------------------------------------------------------------


def test_sign_in_sets_httponly_cookies_and_no_tokens_in_the_body(client, db):
    resp = sign_up(client)
    assert resp.status_code == 201
    body = resp.get_json()
    assert "access_token" not in body and "refresh_token" not in body
    assert body["csrf_token"]
    cookies = set_cookies(resp)
    access, refresh = cookies["ymer_access"], cookies["ymer_refresh"]
    assert "HttpOnly" in access and "Path=/api" in access and "SameSite=Lax" in access
    assert "HttpOnly" in refresh and "Path=/api/auth" in refresh
    assert "SameSite=Strict" in refresh
    assert "Secure" not in access  # development: plain http on localhost

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.get_json()["user"]["email"] == "ada@example.com"
    assert me.get_json()["csrf_token"] == body["csrf_token"]


def test_cookies_are_secure_in_production(app, client, db):
    set_settings(app, cookie_secure=True)
    cookies = set_cookies(sign_up(client))
    assert "Secure" in cookies["ymer_access"] and "Secure" in cookies["ymer_refresh"]


def test_cookie_requests_that_change_things_need_the_csrf_header(client, db):
    csrf = sign_up(client).get_json()["csrf_token"]
    resp = client.post("/api/workspaces", json={"name": "Team"})
    assert resp.status_code == 403
    assert "CSRF" in resp.get_json()["message"]
    resp = client.post("/api/workspaces", json={"name": "Team"}, headers={"X-CSRF-Token": "x"})
    assert resp.status_code == 403
    resp = client.post("/api/workspaces", json={"name": "Team"}, headers={"X-CSRF-Token": csrf})
    assert resp.status_code == 201
    assert client.get("/api/workspaces").status_code == 200  # reads need no header


def test_bearer_clients_need_no_csrf_header(app, client, db):
    actor = make_user(app, "script")
    resp = client.post("/api/workspaces", json={"name": "Team"}, headers=actor.headers)
    assert resp.status_code == 201


def test_refresh_from_cookie_keeps_the_csrf_value(client, db):
    csrf = sign_up(client).get_json()["csrf_token"]
    assert client.post("/api/auth/refresh").status_code == 403
    resp = client.post("/api/auth/refresh", headers={"X-CSRF-Token": csrf})
    assert resp.status_code == 200
    assert resp.get_json()["csrf_token"] == csrf
    assert "access_token" not in resp.get_json()
    assert "ymer_access" in set_cookies(resp)


def test_logout_clears_cookies_and_revokes_the_session(client, db):
    csrf = sign_up(client).get_json()["csrf_token"]
    old_refresh = client.get_cookie("ymer_refresh", path="/api/auth").value
    resp = client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})
    assert resp.status_code == 204
    assert "ymer_access=;" in set_cookies(resp)["ymer_access"]
    assert client.get("/api/auth/me").status_code == 401
    # The old refresh token stops working even if someone kept a copy.
    resp = client.post("/api/auth/refresh", json={"refresh_token": old_refresh})
    assert resp.status_code == 401


def test_logout_without_a_session_still_clears_cookies(client, db):
    resp = client.post("/api/auth/logout")
    assert resp.status_code == 204
    assert "ymer_access" in set_cookies(resp)


def test_collab_token_opens_live_documents_but_not_the_api(client, db):
    csrf = sign_up(client).get_json()["csrf_token"]
    ws = client.post("/api/workspaces", json={"name": "T"}, headers={"X-CSRF-Token": csrf})
    ws_id = ws.get_json()["workspace"]["id"]
    page = client.post(
        f"/api/workspaces/{ws_id}/pages",
        json={"title": "Doc", "type": "document"},
        headers={"X-CSRF-Token": csrf},
    ).get_json()["page"]
    token = client.post("/api/auth/collab-token", headers={"X-CSRF-Token": csrf}).get_json()[
        "token"
    ]
    resp = client.post(
        "/internal/collab/authorize",
        json={"token": token, "document": page["id"]},
        headers={"X-Ymer-Collab-Secret": TEST_COLLAB_SECRET},
    )
    assert resp.status_code == 200
    assert resp.get_json()["role"] == "owner"
    other = client.application.test_client()
    resp = other.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


# --- rate limits ---------------------------------------------------------------------------


def test_failed_sign_ins_lock_the_email_for_a_while(app, client, db):
    set_settings(app, rate_limit_enabled=True, rate_limit_login_failures=3)
    sign_up(client.application.test_client(), "bob@example.com")
    wrong = {"email": "bob@example.com", "password": "wrong password", "session": "token"}
    codes = [client.post("/api/auth/login", json=wrong).status_code for _ in range(3)]
    assert codes == [401, 401, 401]
    right = {**wrong, "password": PASSWORD}
    resp = client.post("/api/auth/login", json=right)
    assert resp.status_code == 429
    assert int(resp.headers["Retry-After"]) > 0
    # Other accounts are not affected.
    sign_up(client.application.test_client(), "cy@example.com")
    resp = client.post("/api/auth/login", json={**right, "email": "cy@example.com"})
    assert resp.status_code == 200

    actions = db.scalars(select(AuditEvent.action).order_by(AuditEvent.created_at)).all()
    assert actions.count("auth.login_failed") == 3
    assert "auth.login_blocked" in actions


def test_sign_in_attempts_are_limited_per_address(app, client, db):
    set_settings(app, rate_limit_enabled=True, rate_limit_login=2)
    body = {"email": "nobody@example.com", "password": "x"}
    codes = [client.post("/api/auth/login", json=body).status_code for _ in range(3)]
    assert codes == [401, 401, 429]


def test_sign_ups_are_limited_per_address(app, client, db):
    set_settings(app, rate_limit_enabled=True, rate_limit_register=1)
    assert sign_up(client, "a1@example.com").status_code == 201
    assert sign_up(client.application.test_client(), "a2@example.com").status_code == 429


def test_api_requests_are_limited_per_user(app, client, db):
    set_settings(app, rate_limit_enabled=True, rate_limit_api=3)
    alice, bob = make_user(app, "alice"), make_user(app, "bob")
    codes = [client.get("/api/auth/me", headers=alice.headers).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    assert client.get("/api/auth/me", headers=bob.headers).status_code == 200
    assert client.get("/api/health").status_code == 200  # never limited


def test_limits_fail_open_when_redis_is_down(app, client, db, monkeypatch):
    class Down:
        def __getattr__(self, name):
            raise ConnectionError("redis is down")

    set_settings(app, rate_limit_enabled=True, rate_limit_api=1, rate_limit_login=1)
    monkeypatch.setitem(app.extensions, "redis", Down())
    actor = make_user(app, "alice")
    for _ in range(3):
        assert client.get("/api/auth/me", headers=actor.headers).status_code == 200


# --- audit log -----------------------------------------------------------------------------


def test_workspace_owners_read_the_audit_log(app, client, db):
    owner, editor, newcomer = (make_user(app, n) for n in ("owner", "editor", "newcomer"))
    ws = make_workspace(owner, {editor: Role.EDITOR})
    base = f"/api/workspaces/{ws.id}"
    resp = client.post(
        f"{base}/members",
        json={"email": newcomer.user.email, "role": "viewer"},
        headers=owner.headers,
    )
    assert resp.status_code == 201
    client.patch(f"{base}/members/{newcomer.id}", json={"role": "editor"}, headers=owner.headers)
    page = client.post(
        f"{base}/pages", json={"title": "Plan", "type": "document"}, headers=owner.headers
    )
    page_id = page.get_json()["page"]["id"]
    client.delete(f"{base}/pages/{page_id}", headers=owner.headers)

    assert client.get(f"{base}/audit", headers=editor.headers).status_code == 403
    resp = client.get(f"{base}/audit", headers=owner.headers)
    assert resp.status_code == 200
    events = resp.get_json()["events"]
    assert [e["action"] for e in events] == ["page.delete", "member.role", "member.add"]
    assert events[1]["details"] == {"from": "viewer", "to": "editor"}
    assert events[2]["actor"]["id"] == str(owner.id)
    assert events[0]["details"]["title"] == "Plan"

    resp = client.get(f"{base}/audit?action=member.&limit=1", headers=owner.headers)
    body = resp.get_json()
    assert [e["action"] for e in body["events"]] == ["member.role"] and body["more"] is True


def test_my_activity_lists_my_sign_ins(client, db):
    sign_up(client)
    client.post("/api/auth/login", json={"email": "ada@example.com", "password": "nope"})
    client.post("/api/auth/login", json={"email": "ada@example.com", "password": PASSWORD})
    actions = [e["action"] for e in client.get("/api/auth/activity").get_json()["events"]]
    assert actions == ["auth.login", "auth.login_failed", "auth.register"]


def test_audit_details_never_hold_secrets(app, client, db):
    actor = make_user(app, "alice")
    resp = client.put(
        "/api/settings/ai",
        json={"provider": "anthropic", "model": "claude-opus-5-5", "api_key": "sk-ant-secret"},
        headers=actor.headers,
    )
    assert resp.status_code in (200, 503)  # 503 when no key-encryption secret is configured
    sign_up(client)
    client.post("/api/auth/login", json={"email": "ada@example.com", "password": "hunter22!"})
    for event in db.scalars(select(AuditEvent)).all():
        text = str(event.details)
        assert "sk-ant-secret" not in text and "hunter22" not in text and PASSWORD not in text
