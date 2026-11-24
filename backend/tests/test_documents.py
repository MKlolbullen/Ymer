"""Version history, comments and file uploads on document pages."""

import base64
import io

import pytest

from tests.factories import grant, make_page, make_user, make_workspace
from tests.fakes import FakeS3
from ymer.documents import save_state
from ymer.models import File, PageType, Role

pytestmark = pytest.mark.db


@pytest.fixture
def s3(app):
    fake = FakeS3()
    app.extensions["s3"] = app.extensions["s3_public"] = fake
    return fake


@pytest.fixture
def team(app, db):
    owner = make_user(app, "owner")
    editor = make_user(app, "editor")
    commenter = make_user(app, "commenter")
    viewer = make_user(app, "viewer")
    outsider = make_user(app, "outsider")
    ws = make_workspace(
        owner, {editor: Role.EDITOR, commenter: Role.COMMENTER, viewer: Role.VIEWER}
    )
    doc = make_page(ws, "doc")
    child = make_page(ws, "child", parent=doc)
    folder = make_page(ws, "folder", type=PageType.FOLDER)
    return type("Team", (), dict(locals()))


def url(team, page, suffix=""):
    return f"/api/workspaces/{team.ws.id}/pages/{page.id}{suffix}"


# --- versions -------------------------------------------------------------------------


def test_save_version_needs_content_and_editor(client, team, db):
    path = url(team, team.doc, "/versions")
    assert client.post(path, json={}, headers=team.editor.headers).status_code == 409
    save_state(db, team.doc.id, b"state-1", user_id=None, version_interval=600)
    db.commit()
    assert client.post(path, json={}, headers=team.commenter.headers).status_code == 403
    resp = client.post(path, json={"label": "  Draft  "}, headers=team.editor.headers)
    assert resp.status_code == 201
    assert resp.get_json()["version"]["label"] == "Draft"
    assert resp.get_json()["version"]["created_by"]["id"] == str(team.editor.id)


def test_list_and_read_versions(client, team, db):
    save_state(db, team.doc.id, b"auto", user_id=team.owner.id, version_interval=600)
    db.commit()
    client.post(url(team, team.doc, "/versions"), json={"label": "v"}, headers=team.owner.headers)
    resp = client.get(url(team, team.doc, "/versions"), headers=team.viewer.headers)
    versions = resp.get_json()["versions"]
    assert len(versions) == 2
    assert "state" not in versions[0]
    one = client.get(
        url(team, team.doc, f"/versions/{versions[0]['id']}"), headers=team.viewer.headers
    )
    assert base64.b64decode(one.get_json()["version"]["state"]) == b"auto"


def test_versions_are_not_reachable_through_another_page(client, team, db):
    save_state(db, team.doc.id, b"s", user_id=None, version_interval=600)
    db.commit()
    vid = client.get(url(team, team.doc, "/versions"), headers=team.owner.headers).get_json()[
        "versions"
    ][0]["id"]
    resp = client.get(url(team, team.child, f"/versions/{vid}"), headers=team.owner.headers)
    assert resp.status_code == 404


def test_outsiders_and_folders(client, team):
    assert (
        client.get(url(team, team.doc, "/versions"), headers=team.outsider.headers).status_code
        == 404
    )
    assert (
        client.get(url(team, team.folder, "/comments"), headers=team.owner.headers).status_code
        == 404
    )


# --- comments -------------------------------------------------------------------------


def comment(client, team, actor, **body):
    return client.post(url(team, team.doc, "/comments"), json=body, headers=actor.headers)


def test_viewers_read_but_cannot_comment(client, team):
    assert comment(client, team, team.viewer, body="hi").status_code == 403
    assert comment(client, team, team.commenter, body="hi").status_code == 201
    resp = client.get(url(team, team.doc, "/comments"), headers=team.viewer.headers)
    assert [c["body"] for c in resp.get_json()["comments"]] == ["hi"]


def test_guest_commenter_via_page_grant(app, client, team):
    guest = make_user(app, "guest")
    grant(team.doc, guest, Role.COMMENTER)
    assert comment(client, team, guest, body="from outside").status_code == 201


def test_threads_anchors_and_replies(client, team):
    anchor = {"from": "AQID", "to": "BAUG"}
    root = comment(client, team, team.commenter, body="Typo?", anchor=anchor, quote="teh")
    assert root.status_code == 201
    root_id = root.get_json()["comment"]["id"]
    assert root.get_json()["comment"]["anchor"] == anchor

    reply = comment(client, team, team.editor, body="Fixed", thread_id=root_id)
    assert reply.status_code == 201
    reply_id = reply.get_json()["comment"]["id"]
    # No replies to replies, and replies are not anchored.
    assert comment(client, team, team.editor, body="x", thread_id=reply_id).status_code == 400
    assert (
        comment(client, team, team.editor, body="x", thread_id=root_id, anchor=anchor).status_code
        == 400
    )


def test_resolve_edit_and_delete_rules(client, team):
    root_id = comment(client, team, team.commenter, body="Q").get_json()["comment"]["id"]
    path = url(team, team.doc, f"/comments/{root_id}")

    # Anyone who can comment can resolve; only the author can change the text.
    resp = client.patch(path, json={"resolved": True}, headers=team.editor.headers)
    assert resp.get_json()["comment"]["resolved_by"]["id"] == str(team.editor.id)
    assert (
        client.patch(path, json={"body": "Hijack"}, headers=team.editor.headers).status_code == 403
    )
    resp = client.patch(
        path, json={"body": "Q2", "resolved": False}, headers=team.commenter.headers
    )
    assert resp.get_json()["comment"]["body"] == "Q2"
    assert resp.get_json()["comment"]["resolved_at"] is None

    other_id = comment(client, team, team.editor, body="mine").get_json()["comment"]["id"]
    other = url(team, team.doc, f"/comments/{other_id}")
    assert client.delete(other, headers=team.commenter.headers).status_code == 403
    # Editors can delete anyone's comment; a thread goes with its replies.
    comment(client, team, team.editor, body="reply", thread_id=root_id)
    assert client.delete(path, headers=team.editor.headers).status_code == 204
    left = client.get(url(team, team.doc, "/comments"), headers=team.owner.headers)
    assert [c["body"] for c in left.get_json()["comments"]] == ["mine"]


# --- files ----------------------------------------------------------------------------


def upload(client, team, actor, data=b"PNGDATA", name="pic.png", mimetype="image/png", page=None):
    return client.post(
        url(team, page or team.doc, "/files"),
        data={"file": (io.BytesIO(data), name, mimetype)},
        headers=actor.headers,
        content_type="multipart/form-data",
    )


def test_upload_requires_editor_and_stores_the_bytes(client, team, s3):
    assert upload(client, team, team.commenter).status_code == 403
    resp = upload(client, team, team.editor, name="../../etc/pic.png")
    assert resp.status_code == 201
    body = resp.get_json()["file"]
    assert body["filename"] == "pic.png"
    assert body["size_bytes"] == 7
    assert "inline" in body["url"]
    [(key, (data, extra))] = s3.objects.items()
    assert data == b"PNGDATA"
    assert extra == {"ContentType": "image/png"}
    assert key[1] == f"workspaces/{team.ws.id}/files/{body['id']}"


def test_risky_types_download_instead_of_rendering(client, team, s3):
    resp = upload(client, team, team.editor, data=b"<svg/>", name="x.svg", mimetype="image/svg+xml")
    assert "attachment" in resp.get_json()["file"]["url"]
    resp = upload(client, team, team.editor, data=b"x", name="a", mimetype="bad type!")
    assert resp.get_json()["file"]["content_type"] == "application/octet-stream"


def test_rejects_empty_and_oversized_files(app, client, team, s3):
    assert upload(client, team, team.editor, data=b"").status_code == 400
    app.extensions["ymer_settings"] = app.extensions["ymer_settings"].model_copy(
        update={"max_upload_bytes": 3}
    )
    assert upload(client, team, team.editor, data=b"four").status_code == 413


def test_viewers_get_urls_and_files_stay_on_their_page(client, team, s3):
    file_id = upload(client, team, team.editor).get_json()["file"]["id"]
    resp = client.get(url(team, team.doc, f"/files/{file_id}"), headers=team.viewer.headers)
    assert resp.status_code == 200
    assert resp.get_json()["file"]["url"].startswith("https://s3.test/")
    assert (
        client.get(
            url(team, team.child, f"/files/{file_id}"), headers=team.owner.headers
        ).status_code
        == 404
    )
    assert (
        client.get(
            url(team, team.doc, f"/files/{file_id}"), headers=team.outsider.headers
        ).status_code
        == 404
    )
    listing = client.get(url(team, team.doc, "/files"), headers=team.viewer.headers)
    assert [f["id"] for f in listing.get_json()["files"]] == [file_id]


def test_delete_file_and_page_deletion_cleans_storage(client, team, s3, db):
    file_id = upload(client, team, team.editor).get_json()["file"]["id"]
    upload(client, team, team.editor, page=team.child)
    assert len(s3.objects) == 2
    assert (
        client.delete(
            url(team, team.doc, f"/files/{file_id}"), headers=team.viewer.headers
        ).status_code
        == 403
    )
    assert (
        client.delete(
            url(team, team.doc, f"/files/{file_id}"), headers=team.editor.headers
        ).status_code
        == 204
    )
    assert len(s3.objects) == 1
    # Deleting a page removes the stored objects of files anywhere in its subtree.
    assert client.delete(url(team, team.doc), headers=team.editor.headers).status_code == 204
    assert s3.objects == {}
    assert db.query(File).count() == 0
