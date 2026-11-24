"""Who can see and change what: workspace roles, page grants and workspace isolation."""

import uuid

import pytest

from tests.factories import Actor, grant, make_page, make_user, make_workspace
from ymer.models import Page, PagePermission, Role

pytestmark = pytest.mark.db


@pytest.fixture
def team(app, db):
    """A workspace with one member per role, an outsider, and a small page tree.

    root
    ├── child
    │   └── grandchild
    └── sibling
    """
    owner = make_user(app, "owner")
    editor = make_user(app, "editor")
    commenter = make_user(app, "commenter")
    viewer = make_user(app, "viewer")
    outsider = make_user(app, "outsider")
    ws = make_workspace(
        owner, {editor: Role.EDITOR, commenter: Role.COMMENTER, viewer: Role.VIEWER}
    )
    root = make_page(ws, "root")
    child = make_page(ws, "child", parent=root)
    grandchild = make_page(ws, "grandchild", parent=child)
    sibling = make_page(ws, "sibling", parent=root)
    return type("Team", (), dict(locals()))


def ws_url(team, suffix=""):
    return f"/api/workspaces/{team.ws.id}{suffix}"


def page_url(team, page, suffix=""):
    return ws_url(team, f"/pages/{page.id}{suffix}")


def titles(resp):
    return sorted(p["title"] for p in resp.get_json()["pages"])


def actor(team, name) -> Actor:
    return getattr(team, name)


# --- workspace isolation -------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["", "/members", "/pages"],
)
def test_outsider_gets_404_for_everything_in_a_workspace(client, team, path):
    resp = client.get(ws_url(team, path), headers=team.outsider.headers)
    assert resp.status_code == 404


def test_outsider_cannot_read_a_page_by_id(client, team):
    resp = client.get(page_url(team, team.root), headers=team.outsider.headers)
    assert resp.status_code == 404


def test_missing_and_forbidden_workspaces_look_the_same(client, team):
    missing = client.get(f"/api/workspaces/{uuid.uuid4()}", headers=team.outsider.headers)
    forbidden = client.get(ws_url(team), headers=team.outsider.headers)
    assert missing.status_code == forbidden.status_code == 404
    assert missing.get_json() == forbidden.get_json()


def test_page_from_another_workspace_is_not_reachable_through_mine(app, client, team):
    # The outsider owns a workspace of their own; the team's page must not be
    # reachable through it even with a correct page id.
    mine = make_workspace(team.outsider)
    resp = client.get(
        f"/api/workspaces/{mine.id}/pages/{team.root.id}", headers=team.outsider.headers
    )
    assert resp.status_code == 404
    # Nor can the team owner, who can see the page in its own workspace.
    resp = client.get(f"/api/workspaces/{mine.id}/pages/{team.root.id}", headers=team.owner.headers)
    assert resp.status_code == 404


def test_cannot_create_a_page_under_a_parent_from_another_workspace(client, team):
    mine = make_workspace(team.outsider)
    resp = client.post(
        f"/api/workspaces/{mine.id}/pages",
        json={"type": "document", "title": "x", "parent_id": str(team.root.id)},
        headers=team.outsider.headers,
    )
    assert resp.status_code == 404


def test_workspace_list_only_shows_my_workspaces(client, team):
    make_workspace(team.outsider)
    resp = client.get("/api/workspaces", headers=team.outsider.headers)
    ids = [w["id"] for w in resp.get_json()["workspaces"]]
    assert str(team.ws.id) not in ids
    assert len(ids) == 1


# --- page access by workspace role ---------------------------------------------------

ROLE_MATRIX = [
    # name, can view, can edit
    ("owner", True, True),
    ("editor", True, True),
    ("commenter", True, False),
    ("viewer", True, False),
]


@pytest.mark.parametrize("name,can_view,can_edit", ROLE_MATRIX)
def test_page_access_follows_workspace_role(client, team, name, can_view, can_edit):
    headers = actor(team, name).headers
    assert client.get(page_url(team, team.child), headers=headers).status_code == 200
    assert titles(client.get(ws_url(team, "/pages"), headers=headers)) == [
        "child",
        "grandchild",
        "root",
        "sibling",
    ]

    edit = client.patch(page_url(team, team.child), json={"title": "renamed"}, headers=headers)
    create_root = client.post(
        ws_url(team, "/pages"), json={"type": "folder", "title": "new"}, headers=headers
    )
    create_child = client.post(
        ws_url(team, "/pages"),
        json={"type": "document", "title": "new", "parent_id": str(team.child.id)},
        headers=headers,
    )
    delete = client.delete(page_url(team, team.grandchild), headers=headers)
    expected = 200 if can_edit else 403
    assert edit.status_code == expected
    assert create_root.status_code == (201 if can_edit else 403)
    assert create_child.status_code == (201 if can_edit else 403)
    assert delete.status_code == (204 if can_edit else 403)


@pytest.mark.parametrize(
    "name,role", [("owner", "owner"), ("editor", "editor"), ("viewer", "viewer")]
)
def test_page_reports_my_role(client, team, name, role):
    resp = client.get(page_url(team, team.root), headers=actor(team, name).headers)
    assert resp.get_json()["page"]["my_role"] == role


# --- page grants ---------------------------------------------------------------------


def test_guest_with_a_grant_sees_only_that_subtree(client, team):
    grant(team.child, team.outsider, Role.VIEWER)
    headers = team.outsider.headers

    assert titles(client.get(ws_url(team, "/pages"), headers=headers)) == ["child", "grandchild"]
    assert client.get(page_url(team, team.child), headers=headers).status_code == 200
    assert client.get(page_url(team, team.grandchild), headers=headers).status_code == 200
    assert client.get(page_url(team, team.root), headers=headers).status_code == 404
    assert client.get(page_url(team, team.sibling), headers=headers).status_code == 404


def test_guest_sees_the_workspace_but_not_its_members(client, team):
    grant(team.child, team.outsider, Role.VIEWER)
    headers = team.outsider.headers
    workspaces = client.get("/api/workspaces", headers=headers).get_json()["workspaces"]
    assert [(w["id"], w["role"]) for w in workspaces] == [(str(team.ws.id), None)]
    assert client.get(ws_url(team), headers=headers).status_code == 200
    assert client.get(ws_url(team, "/members"), headers=headers).status_code == 403


def test_viewer_guest_cannot_edit_and_editor_guest_can(client, team):
    grant(team.child, team.outsider, Role.VIEWER)
    headers = team.outsider.headers
    resp = client.patch(page_url(team, team.child), json={"title": "x"}, headers=headers)
    assert resp.status_code == 403

    grant(team.grandchild, team.outsider, Role.EDITOR)
    resp = client.patch(page_url(team, team.grandchild), json={"title": "x"}, headers=headers)
    assert resp.status_code == 200
    # ...but still not the page above, and not top-level pages.
    resp = client.patch(page_url(team, team.child), json={"title": "x"}, headers=headers)
    assert resp.status_code == 403
    resp = client.post(ws_url(team, "/pages"), json={"type": "document"}, headers=headers)
    assert resp.status_code == 403


def test_nearest_grant_wins(client, team):
    grant(team.root, team.outsider, Role.EDITOR)
    grant(team.child, team.outsider, Role.VIEWER)
    pages = client.get(ws_url(team, "/pages"), headers=team.outsider.headers).get_json()["pages"]
    roles = {p["title"]: p["my_role"] for p in pages}
    assert roles == {
        "root": "editor",
        "sibling": "editor",
        "child": "viewer",
        "grandchild": "viewer",
    }


def test_grant_can_narrow_a_members_access(client, team):
    grant(team.child, team.editor, Role.VIEWER)
    headers = team.editor.headers
    resp = client.patch(page_url(team, team.grandchild), json={"title": "x"}, headers=headers)
    assert resp.status_code == 403
    resp = client.patch(page_url(team, team.sibling), json={"title": "x"}, headers=headers)
    assert resp.status_code == 200


def test_grant_never_narrows_an_owner(client, team, db):
    db.add(PagePermission(page_id=team.child.id, user_id=team.owner.id, role=Role.VIEWER))
    db.commit()
    resp = client.patch(page_url(team, team.child), json={"title": "x"}, headers=team.owner.headers)
    assert resp.status_code == 200
    page = client.get(page_url(team, team.child), headers=team.owner.headers).get_json()["page"]
    assert page["my_role"] == "owner"


# --- sharing pages -------------------------------------------------------------------


def share(client, team, page, by, target, role):
    return client.put(
        page_url(team, page, f"/permissions/{target.id}"), json={"role": role}, headers=by.headers
    )


@pytest.mark.parametrize("name", ["viewer", "commenter"])
def test_viewers_and_commenters_cannot_share(client, team, name):
    resp = share(client, team, team.child, actor(team, name), team.outsider, "viewer")
    assert resp.status_code == 403


def test_editor_can_share_up_to_their_own_role(client, team):
    assert share(client, team, team.child, team.editor, team.outsider, "viewer").status_code == 200
    assert share(client, team, team.child, team.editor, team.outsider, "editor").status_code == 200
    assert share(client, team, team.child, team.editor, team.outsider, "owner").status_code == 400
    listed = client.get(page_url(team, team.child, "/permissions"), headers=team.editor.headers)
    assert [(p["user_id"], p["role"]) for p in listed.get_json()["permissions"]] == [
        (str(team.outsider.id), "editor")
    ]


def test_editor_guest_cannot_grant_more_than_editor(client, team, app):
    friend = make_user(app, "friend")
    grant(team.child, team.outsider, Role.COMMENTER)
    resp = share(client, team, team.child, team.outsider, friend, "viewer")
    assert resp.status_code == 403  # commenters cannot share at all
    grant(team.grandchild, team.outsider, Role.EDITOR)
    assert share(client, team, team.grandchild, team.outsider, friend, "editor").status_code == 200


def test_removing_a_grant_removes_access(client, team):
    grant(team.child, team.outsider, Role.VIEWER)
    resp = client.delete(
        page_url(team, team.child, f"/permissions/{team.outsider.id}"), headers=team.editor.headers
    )
    assert resp.status_code == 204
    assert client.get(page_url(team, team.child), headers=team.outsider.headers).status_code == 404


# --- moving pages --------------------------------------------------------------------


def test_cannot_move_a_page_under_its_own_descendant(client, team):
    resp = client.patch(
        page_url(team, team.root),
        json={"parent_id": str(team.grandchild.id)},
        headers=team.owner.headers,
    )
    assert resp.status_code == 400


def test_moving_needs_editor_on_the_destination(client, team):
    grant(team.sibling, team.editor, Role.VIEWER)
    resp = client.patch(
        page_url(team, team.grandchild),
        json={"parent_id": str(team.sibling.id)},
        headers=team.editor.headers,
    )
    assert resp.status_code == 403


def test_moving_a_page_out_of_a_shared_subtree_revokes_inherited_access(client, team):
    grant(team.child, team.outsider, Role.VIEWER)
    resp = client.patch(
        page_url(team, team.grandchild),
        json={"parent_id": str(team.sibling.id)},
        headers=team.owner.headers,
    )
    assert resp.status_code == 200
    resp = client.get(page_url(team, team.grandchild), headers=team.outsider.headers)
    assert resp.status_code == 404


def test_move_to_top_level_needs_workspace_editor(client, team):
    grant(team.child, team.outsider, Role.EDITOR)
    resp = client.patch(
        page_url(team, team.grandchild), json={"parent_id": None}, headers=team.outsider.headers
    )
    assert resp.status_code == 403
    resp = client.patch(
        page_url(team, team.grandchild), json={"parent_id": None}, headers=team.editor.headers
    )
    assert resp.status_code == 200
    assert resp.get_json()["page"]["parent_id"] is None


# --- members -------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["editor", "commenter", "viewer"])
def test_only_owners_manage_members_and_the_workspace(client, team, name):
    headers = actor(team, name).headers
    add = client.post(
        ws_url(team, "/members"), json={"email": team.outsider.user.email}, headers=headers
    )
    change = client.patch(
        ws_url(team, f"/members/{team.viewer.id}"), json={"role": "owner"}, headers=headers
    )
    rename = client.patch(ws_url(team), json={"name": "mine now"}, headers=headers)
    delete = client.delete(ws_url(team), headers=headers)
    for resp in (add, change, rename, delete):
        assert resp.status_code == 403
    # Last, as the commenter's attempt succeeds: removing yourself is leaving.
    remove = client.delete(ws_url(team, f"/members/{team.commenter.id}"), headers=headers)
    if name == "commenter":  # removing yourself is leaving, which is allowed
        assert remove.status_code == 204
    else:
        assert remove.status_code == 403


def test_owner_adds_changes_and_removes_members(client, team):
    headers = team.owner.headers
    resp = client.post(
        ws_url(team, "/members"),
        json={"email": team.outsider.user.email.upper(), "role": "viewer"},
        headers=headers,
    )
    assert resp.status_code == 201
    assert client.get(page_url(team, team.root), headers=team.outsider.headers).status_code == 200

    resp = client.patch(
        ws_url(team, f"/members/{team.outsider.id}"), json={"role": "editor"}, headers=headers
    )
    assert resp.get_json()["member"]["role"] == "editor"

    resp = client.delete(ws_url(team, f"/members/{team.outsider.id}"), headers=headers)
    assert resp.status_code == 204
    assert client.get(page_url(team, team.root), headers=team.outsider.headers).status_code == 404


def test_adding_an_existing_member_conflicts(client, team):
    resp = client.post(
        ws_url(team, "/members"), json={"email": team.viewer.user.email}, headers=team.owner.headers
    )
    assert resp.status_code == 409


def test_last_owner_cannot_leave_or_be_demoted(client, team):
    headers = team.owner.headers
    resp = client.patch(
        ws_url(team, f"/members/{team.owner.id}"), json={"role": "editor"}, headers=headers
    )
    assert resp.status_code == 400
    assert (
        client.delete(ws_url(team, f"/members/{team.owner.id}"), headers=headers).status_code == 400
    )

    # With a second owner, the first may step down.
    client.patch(
        ws_url(team, f"/members/{team.editor.id}"), json={"role": "owner"}, headers=headers
    )
    resp = client.patch(
        ws_url(team, f"/members/{team.owner.id}"), json={"role": "editor"}, headers=headers
    )
    assert resp.status_code == 200


def test_removing_a_member_also_removes_their_page_grants(client, team, db):
    grant(team.child, team.viewer, Role.EDITOR)
    resp = client.delete(ws_url(team, f"/members/{team.viewer.id}"), headers=team.owner.headers)
    assert resp.status_code == 204
    assert client.get(page_url(team, team.child), headers=team.viewer.headers).status_code == 404
    assert db.query(PagePermission).filter_by(user_id=team.viewer.id).count() == 0


def test_creator_becomes_owner_and_deleting_removes_pages(client, team, db):
    resp = client.post("/api/workspaces", json={"name": "New"}, headers=team.outsider.headers)
    assert resp.status_code == 201
    ws_id = resp.get_json()["workspace"]["id"]
    assert resp.get_json()["workspace"]["role"] == "owner"
    page = client.post(
        f"/api/workspaces/{ws_id}/pages", json={"type": "mindmap"}, headers=team.outsider.headers
    ).get_json()["page"]
    assert (
        client.delete(f"/api/workspaces/{ws_id}", headers=team.outsider.headers).status_code == 204
    )
    db.expunge_all()
    assert db.get(Page, uuid.UUID(page["id"])) is None
