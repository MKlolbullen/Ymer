"""Backlinks, full-text search, moving pages and sharing by email."""

import pycrdt
import pytest

from tests.conftest import TEST_COLLAB_SECRET
from tests.factories import grant, make_page, make_user, make_workspace
from ymer.content import summarize
from ymer.models import Page, PageLink, PagePermission, PageType, Role

pytestmark = pytest.mark.db


def yjs_document(*paragraphs: list) -> bytes:
    """A TipTap-shaped Yjs document. Each paragraph is a list of str or ("mention", id, label)."""
    doc = pycrdt.Doc()
    fragment = doc.get("default", type=pycrdt.XmlFragment)
    for parts in paragraphs:
        children = []
        for part in parts:
            if isinstance(part, str):
                children.append(pycrdt.XmlText(part))
            else:
                _, page_id, label = part
                children.append(
                    pycrdt.XmlElement("pageMention", {"id": str(page_id), "label": label})
                )
        fragment.children.append(pycrdt.XmlElement("paragraph", {}, children))
    return doc.get_update()


def store(client, page, state: bytes):
    resp = client.put(
        f"/internal/collab/documents/{page.id}",
        data=state,
        headers={"X-Ymer-Collab-Secret": TEST_COLLAB_SECRET},
    )
    assert resp.status_code == 204


@pytest.fixture
def team(app, db):
    owner = make_user(app, "owner")
    editor = make_user(app, "editor")
    viewer = make_user(app, "viewer")
    outsider = make_user(app, "outsider")
    ws = make_workspace(owner, {editor: Role.EDITOR, viewer: Role.VIEWER})
    base = f"/api/workspaces/{ws.id}"
    return type("Team", (), dict(locals()))


# --- reading documents ----------------------------------------------------------------


def test_summarize_reads_text_and_mentions():
    target = "6f2c1f4e-1c1b-4d8e-9a39-6e0f9a8b7c6d"
    summary = summarize(yjs_document(["Hello ", "world"], ["see ", ("mention", target, "Plan")]))
    assert summary.text == "Hello world see Plan"
    assert {str(m) for m in summary.mentions} == {target}
    assert summarize(b"not yjs") is None


def test_formatted_text_has_no_markup():
    doc = pycrdt.Doc()
    fragment = doc.get("default", type=pycrdt.XmlFragment)
    text = pycrdt.XmlText()
    fragment.children.append(pycrdt.XmlElement("paragraph", {}, [text]))
    text.insert(0, "plain ")
    text.insert(6, "bold", {"bold": {}})
    assert summarize(doc.get_update()).text == "plain bold"


# --- backlinks ------------------------------------------------------------------------


def test_saving_a_document_records_its_links(client, team, db):
    target = make_page(team.ws, "Target")
    other_ws = make_workspace(team.owner)
    foreign = make_page(other_ws, "Foreign")
    source = make_page(team.ws, "Source")
    store(
        client,
        source,
        yjs_document([("mention", target.id, "Target"), ("mention", foreign.id, "Foreign")]),
    )
    links = db.query(PageLink).filter_by(source_page_id=source.id).all()
    assert [link.target_page_id for link in links] == [target.id]  # other workspace dropped

    # A later save without the mention removes the link.
    store(client, source, yjs_document(["no links now"]))
    assert db.query(PageLink).filter_by(source_page_id=source.id).count() == 0


def test_backlinks_only_list_pages_the_reader_can_see(client, team, db):
    target = make_page(team.ws, "Target")
    public = make_page(team.ws, "Public notes")
    private = make_page(team.ws, "Private notes")
    store(client, public, yjs_document([("mention", target.id, "Target")]))
    store(client, private, yjs_document([("mention", target.id, "Target")]))
    url = f"{team.base}/pages/{target.id}/backlinks"
    titles = {p["title"] for p in client.get(url, headers=team.viewer.headers).get_json()["pages"]}
    assert titles == {"Public notes", "Private notes"}

    # A guest who can see only the target sees no backlinks from pages hidden to them.
    grant(target, team.outsider, Role.VIEWER)
    resp = client.get(url, headers=team.outsider.headers)
    assert resp.status_code == 200
    assert resp.get_json()["pages"] == []


# --- search ---------------------------------------------------------------------------


def test_search_finds_titles_text_and_rows(client, team, db):
    plan = make_page(team.ws, "Quarterly planning")
    store(client, plan, yjs_document(["We ship the roadmap in December."]))
    make_page(team.ws, "Unrelated")
    database = client.post(
        f"{team.base}/pages",
        json={"type": "database", "title": "Tasks"},
        headers=team.owner.headers,
    ).get_json()["page"]
    title = client.get(
        f"{team.base}/databases/{database['id']}", headers=team.owner.headers
    ).get_json()["properties"][0]["id"]
    client.post(
        f"{team.base}/databases/{database['id']}/rows",
        json={"values": {title: "Draft the roadmap"}},
        headers=team.owner.headers,
    )

    def search(q, who=team.viewer):
        resp = client.get(f"{team.base}/search", query_string={"q": q}, headers=who.headers)
        assert resp.status_code == 200
        return resp.get_json()["results"]

    by_title = search("quarter")
    assert [r["title"] for r in by_title] == ["Quarterly planning"]

    roadmap = search("roadmap")
    kinds = {(r["kind"], r["title"]) for r in roadmap}
    assert kinds == {("page", "Quarterly planning"), ("row", "Draft the roadmap")}
    page_hit = next(r for r in roadmap if r["kind"] == "page")
    assert "\u0002roadmap\u0003" in page_hit["snippet"]

    assert search("Quartrly planing")[0]["title"] == "Quarterly planning"  # typo, trigram
    assert search("") == []
    assert search("&|!:*") == []


def test_search_respects_permissions(client, team, db):
    secret = make_page(team.ws, "Secret plans")
    shared = make_page(team.ws, "Shared plans")
    grant(shared, team.outsider, Role.VIEWER)
    store(client, secret, yjs_document(["hidden words"]))
    resp = client.get(f"{team.base}/search?q=plans", headers=team.outsider.headers)
    assert [r["title"] for r in resp.get_json()["results"]] == ["Shared plans"]
    # Outsiders with no grant cannot search the workspace at all.
    stranger = make_user(client.application, "stranger")
    assert client.get(f"{team.base}/search?q=plans", headers=stranger.headers).status_code == 404


# --- moving pages ---------------------------------------------------------------------


def _children(client, team, parent=None):
    pages = client.get(f"{team.base}/pages", headers=team.owner.headers).get_json()["pages"]
    level = [p for p in pages if p["parent_id"] == (str(parent.id) if parent else None)]
    return [p["title"] for p in sorted(level, key=lambda p: p["position"])]


def test_move_reorders_and_reparents(client, team, db):
    a, b, c = (make_page(team.ws, t) for t in "abc")
    folder = make_page(team.ws, "folder", type=PageType.FOLDER)
    url = f"{team.base}/pages/{c.id}/move"
    assert (
        client.post(url, json={"before_id": str(a.id)}, headers=team.editor.headers).status_code
        == 200
    )
    assert _children(client, team) == ["c", "a", "b", "folder"]

    resp = client.post(url, json={"parent_id": str(folder.id)}, headers=team.editor.headers)
    assert resp.get_json()["page"]["parent_id"] == str(folder.id)
    assert _children(client, team, folder) == ["c"]
    assert _children(client, team) == ["a", "b", "folder"]

    # Into its own subtree, or before a page with another parent: refused.
    resp = client.post(
        f"{team.base}/pages/{folder.id}/move",
        json={"parent_id": str(c.id)},
        headers=team.editor.headers,
    )
    assert resp.status_code == 400
    resp = client.post(
        url, json={"parent_id": None, "before_id": str(c.id)}, headers=team.editor.headers
    )
    assert resp.status_code == 400
    # Viewers cannot move.
    resp = client.post(url, json={"parent_id": None}, headers=team.viewer.headers)
    assert resp.status_code == 403


# --- sharing by email -----------------------------------------------------------------


def test_share_by_email(client, team, db):
    page = make_page(team.ws, "Doc")
    url = f"{team.base}/pages/{page.id}/permissions"
    resp = client.post(
        url,
        json={"email": team.outsider.user.email.upper(), "role": "commenter"},
        headers=team.editor.headers,
    )
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["permission"]["display_name"] == "Outsider"
    assert db.query(PagePermission).filter_by(page_id=page.id).one().role is Role.COMMENTER

    nobody = client.post(
        url, json={"email": "nobody@example.com", "role": "viewer"}, headers=team.editor.headers
    )
    assert nobody.status_code == 404
    too_high = client.post(
        url, json={"email": team.outsider.user.email, "role": "owner"}, headers=team.editor.headers
    )
    assert too_high.status_code == 400
    assert (
        client.post(
            url,
            json={"email": team.outsider.user.email, "role": "viewer"},
            headers=team.viewer.headers,
        ).status_code
        == 403
    )


def test_database_pages_are_not_collab_documents(client, team, db):
    database = make_page(team.ws, "db", type=PageType.DATABASE)
    resp = client.put(
        f"/internal/collab/documents/{database.id}",
        data=b"x",
        headers={"X-Ymer-Collab-Secret": TEST_COLLAB_SECRET},
    )
    assert resp.status_code == 404
    assert db.get(Page, database.id).content_text == ""
