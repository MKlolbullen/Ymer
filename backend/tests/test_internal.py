"""The internal API the collab service uses: auth checks and document persistence."""

from datetime import UTC, datetime, timedelta

import pytest

from tests.conftest import TEST_COLLAB_SECRET
from tests.factories import grant, make_page, make_user, make_workspace
from ymer.models import DocumentContent, DocumentVersion, PageType, Role

pytestmark = pytest.mark.db

SECRET = {"X-Ymer-Collab-Secret": TEST_COLLAB_SECRET}


def token_of(actor) -> str:
    return actor.headers["Authorization"].split(" ", 1)[1]


@pytest.fixture
def setup(app, db):
    owner = make_user(app, "owner")
    viewer = make_user(app, "viewer")
    commenter = make_user(app, "commenter")
    guest = make_user(app, "guest")
    outsider = make_user(app, "outsider")
    ws = make_workspace(owner, {viewer: Role.VIEWER, commenter: Role.COMMENTER})
    doc = make_page(ws, "doc")
    folder = make_page(ws, "folder", type=PageType.FOLDER)
    grant(doc, guest, Role.EDITOR)
    return type("Setup", (), dict(locals()))


def authorize(client, actor, document, headers=SECRET):
    return client.post(
        "/internal/collab/authorize",
        json={"token": token_of(actor), "document": str(document)},
        headers=headers,
    )


def test_requires_the_shared_secret(client, setup):
    assert authorize(client, setup.owner, setup.doc.id, headers={}).status_code == 401
    wrong = {"X-Ymer-Collab-Secret": "nope"}
    assert authorize(client, setup.owner, setup.doc.id, headers=wrong).status_code == 401
    assert client.get(f"/internal/collab/documents/{setup.doc.id}").status_code == 401


def test_internal_routes_are_not_under_the_public_api(client, setup):
    resp = client.post("/api/internal/collab/authorize", json={}, headers=SECRET)
    assert resp.status_code == 404


@pytest.mark.parametrize(
    ("who", "role", "read_only"),
    [
        ("owner", "owner", False),
        ("guest", "editor", False),
        ("commenter", "commenter", True),
        ("viewer", "viewer", True),
    ],
)
def test_authorize_reports_role_and_write_access(client, setup, who, role, read_only):
    resp = authorize(client, getattr(setup, who), setup.doc.id)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["role"] == role
    assert body["read_only"] is read_only
    assert body["user"]["id"] == str(getattr(setup, who).id)


def test_authorize_rejects_outsiders_bad_tokens_and_non_documents(client, setup):
    assert authorize(client, setup.outsider, setup.doc.id).status_code == 404
    assert authorize(client, setup.owner, setup.folder.id).status_code == 404
    assert authorize(client, setup.owner, "not-a-uuid").status_code == 404
    resp = client.post(
        "/internal/collab/authorize",
        json={"token": "garbage", "document": str(setup.doc.id)},
        headers=SECRET,
    )
    assert resp.status_code == 401


def test_fetch_is_empty_until_stored_then_round_trips(client, setup, db):
    url = f"/internal/collab/documents/{setup.doc.id}"
    assert client.get(url, headers=SECRET).status_code == 204
    resp = client.put(
        url, data=b"\x01\x02state", headers={**SECRET, "X-Ymer-User-Id": str(setup.owner.id)}
    )
    assert resp.status_code == 204
    resp = client.get(url, headers=SECRET)
    assert resp.status_code == 200
    assert resp.data == b"\x01\x02state"
    assert db.get(DocumentContent, setup.doc.id).updated_by_id == setup.owner.id


def test_first_save_takes_a_snapshot_and_later_ones_wait(client, setup, db):
    url = f"/internal/collab/documents/{setup.doc.id}"
    client.put(url, data=b"v1", headers=SECRET)
    client.put(url, data=b"v2", headers=SECRET)
    versions = db.query(DocumentVersion).filter_by(page_id=setup.doc.id).all()
    assert [v.state for v in versions] == [b"v1"]

    # Once the newest snapshot is old enough, the next changed save takes another.
    versions[0].created_at = datetime.now(UTC) - timedelta(hours=1)
    db.commit()
    client.put(url, data=b"v3", headers=SECRET)
    states = sorted(v.state for v in db.query(DocumentVersion).filter_by(page_id=setup.doc.id))
    assert states == [b"v1", b"v3"]


def test_store_rejects_empty_bodies_and_folders(client, setup):
    assert (
        client.put(f"/internal/collab/documents/{setup.doc.id}", headers=SECRET).status_code == 400
    )
    resp = client.put(f"/internal/collab/documents/{setup.folder.id}", data=b"x", headers=SECRET)
    assert resp.status_code == 404
