"""Phase 3: mind maps and drawings as live pages, and charts over database rows."""

import pycrdt
import pytest

from tests.conftest import TEST_COLLAB_SECRET
from tests.factories import make_page, make_user, make_workspace
from ymer.content import summarize
from ymer.models import Page, PageLink, PageType, Role

pytestmark = pytest.mark.db

SECRET = {"X-Ymer-Collab-Secret": TEST_COLLAB_SECRET}


@pytest.fixture
def team(app, db, client):
    owner = make_user(app, "owner")
    editor = make_user(app, "editor")
    viewer = make_user(app, "viewer")
    outsider = make_user(app, "outsider")
    ws = make_workspace(owner, {editor: Role.EDITOR, viewer: Role.VIEWER})
    base = f"/api/workspaces/{ws.id}"
    return type("Team", (), dict(locals()))


# --- Yjs shapes the frontend writes -----------------------------------------------------


def yjs_mindmap(nodes: dict[str, dict]) -> bytes:
    doc = pycrdt.Doc()
    root = doc.get("mindmap", type=pycrdt.Map)
    for node_id, fields in nodes.items():
        root[node_id] = pycrdt.Map(fields)
    return doc.get_update()


def yjs_drawing(elements: list[dict]) -> bytes:
    doc = pycrdt.Doc()
    root = doc.get("drawing", type=pycrdt.Map)
    for element in elements:
        root[element["id"]] = element
    return doc.get_update()


def store(client, page, state: bytes):
    return client.put(f"/internal/collab/documents/{page.id}", data=state, headers=SECRET)


# --- mind maps and drawings -------------------------------------------------------------


def test_drawing_pages_can_be_created(client, team):
    resp = client.post(
        f"{team.base}/pages",
        json={"type": "drawing", "title": "Sketch"},
        headers=team.editor.headers,
    )
    assert resp.status_code == 201
    assert resp.get_json()["page"]["type"] == "drawing"


@pytest.mark.parametrize("page_type", [PageType.MINDMAP, PageType.DRAWING])
def test_mindmaps_and_drawings_open_in_the_collab_service(client, team, page_type):
    page = make_page(team.ws, "live", type=page_type)
    token = team.viewer.headers["Authorization"].split(" ", 1)[1]
    resp = client.post(
        "/internal/collab/authorize",
        json={"token": token, "document": str(page.id)},
        headers=SECRET,
    )
    assert resp.status_code == 200
    assert resp.get_json()["read_only"] is True
    assert resp.get_json()["page_type"] == page_type.value
    assert store(client, page, b"\x00\x00").status_code == 204
    fetched = client.get(f"/internal/collab/documents/{page.id}", headers=SECRET)
    assert fetched.data == b"\x00\x00"


def test_folders_still_are_not_live_pages(client, team):
    folder = make_page(team.ws, "f", type=PageType.FOLDER)
    assert store(client, folder, b"\x00\x00").status_code == 404


def test_summarize_mindmap_reads_labels_and_linked_pages():
    target = "6f2c1f4e-1c1b-4d8e-9a39-6e0f9a8b7c6d"
    state = yjs_mindmap(
        {
            "b": {"label": "Second idea", "parent": "root", "x": 200, "y": 80},
            "root": {"label": "Launch plan", "parent": None, "x": 0, "y": 0},
            "a": {"label": "First idea", "parent": "root", "x": 200, "y": -80, "pageId": target},
            "bad": {"label": "Bad link", "parent": "root", "y": 0, "pageId": "nope"},
        }
    )
    summary = summarize(state, PageType.MINDMAP)
    assert summary.text == "Launch plan First idea Bad link Second idea"
    assert {str(m) for m in summary.mentions} == {target}


def test_summarize_drawing_reads_live_text_elements():
    state = yjs_drawing(
        [
            {"id": "t2", "type": "text", "text": "below", "x": 0, "y": 100},
            {"id": "t1", "type": "text", "text": "Title", "x": 0, "y": 0},
            {"id": "r", "type": "rectangle", "x": 0, "y": 0},
            {"id": "gone", "type": "text", "text": "erased", "x": 0, "y": 50, "isDeleted": True},
        ]
    )
    assert summarize(state, PageType.DRAWING).text == "Title below"


def test_saving_a_mindmap_indexes_it_for_search_and_backlinks(client, team, db):
    target = make_page(team.ws, "Budget")
    mindmap = make_page(team.ws, "Plan", type=PageType.MINDMAP)
    state = yjs_mindmap(
        {
            "root": {"label": "Quarterly roadmap", "parent": None, "x": 0, "y": 0},
            "n1": {"label": "Hiring", "parent": "root", "x": 1, "y": 1, "pageId": str(target.id)},
        }
    )
    assert store(client, mindmap, state).status_code == 204
    db.expire_all()
    assert "Quarterly roadmap" in db.get(Page, mindmap.id).content_text
    assert db.get(PageLink, (mindmap.id, target.id)) is not None
    results = client.get(
        f"{team.base}/search", query_string={"q": "roadmap"}, headers=team.viewer.headers
    ).get_json()["results"]
    assert any(r.get("page_id") == str(mindmap.id) for r in results)


def test_mindmaps_have_versions_but_not_comments(client, team):
    mindmap = make_page(team.ws, "Plan", type=PageType.MINDMAP)
    url = f"{team.base}/pages/{mindmap.id}"
    state = yjs_mindmap({"root": {"label": "Hi", "parent": None, "x": 0, "y": 0}})
    assert store(client, mindmap, state).status_code == 204
    versions = client.get(f"{url}/versions", headers=team.viewer.headers).get_json()["versions"]
    assert len(versions) == 1  # the first save takes a snapshot
    saved = client.post(f"{url}/versions", json={"label": "Named"}, headers=team.editor.headers)
    assert saved.status_code == 201
    assert client.get(f"{url}/comments", headers=team.viewer.headers).status_code == 404


# --- charts -----------------------------------------------------------------------------


@pytest.fixture
def sales(client, team):
    """A database of deals: stage (select), owner region (text), amount, won, closed date."""
    resp = client.post(
        f"{team.base}/pages",
        json={"type": "database", "title": "Deals"},
        headers=team.owner.headers,
    )
    db_id = resp.get_json()["page"]["id"]
    url = f"{team.base}/databases/{db_id}"

    def prop(**body):
        r = client.post(f"{url}/properties", json=body, headers=team.owner.headers)
        assert r.status_code == 201, r.get_json()
        return r.get_json()["property"]

    stage = prop(
        name="Stage",
        type="select",
        config={
            "options": [
                {"id": "lead", "name": "Lead", "color": "gray"},
                {"id": "won", "name": "Won", "color": "green"},
                {"id": "lost", "name": "Lost", "color": "red"},
            ]
        },
    )
    region = prop(name="Region", type="text")
    amount = prop(name="Amount", type="number")
    closed = prop(name="Closed", type="date")
    tags = prop(
        name="Tags",
        type="multi_select",
        config={"options": [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}]},
    )
    data = [
        ("won", "North", 100, "2026-01-15", ["a"]),
        ("won", "South", 50, "2026-01-20", ["a", "b"]),
        ("lost", "North", 30, "2026-02-03", []),
        ("lead", "North", 20, None, ["b"]),
        (None, "South", None, None, []),
    ]
    for s, r, a, c, t in data:
        values = {stage["id"]: s, region["id"]: r, amount["id"]: a, closed["id"]: c, tags["id"]: t}
        resp = client.post(f"{url}/rows", json={"values": values}, headers=team.owner.headers)
        assert resp.status_code == 201, resp.get_json()
    return type("Sales", (), dict(locals()))


def chart(client, sales, who=None, view_id=None, **config):
    body = {"config": config}
    if view_id:
        body["view_id"] = view_id
    return client.post(f"{sales.url}/chart", json=body, headers=(who or sales.team.owner).headers)


def test_chart_counts_rows_per_select_option_in_option_order(client, sales):
    resp = chart(client, sales, who=sales.team.viewer, x=sales.stage["id"])
    assert resp.status_code == 200, resp.get_json()
    data = resp.get_json()["chart"]
    assert data["labels"] == ["Lead", "Won", "Lost", "No value"]
    assert data["colors"] == ["gray", "green", "red", None]
    assert data["series"] == [
        {"key": "value", "name": "Count", "color": None, "data": [1, 2, 1, 1]}
    ]
    assert data["row_count"] == 5


def test_chart_sums_a_number_and_hides_empty_groups(client, sales):
    data = chart(
        client,
        sales,
        x=sales.stage["id"],
        aggregate="sum",
        measure=sales.amount["id"],
        hide_empty=True,
        sort="value_desc",
    ).get_json()["chart"]
    assert data["labels"] == ["Won", "Lost", "Lead"]
    assert data["series"][0]["data"] == [150, 30, 20]
    assert data["series"][0]["name"] == "Sum of Amount"


def test_chart_splits_into_series(client, sales):
    data = chart(
        client, sales, x=sales.region["id"], split_by=sales.stage["id"], hide_empty=True
    ).get_json()["chart"]
    assert data["labels"] == ["North", "South"]
    by_name = {s["name"]: s["data"] for s in data["series"]}
    assert by_name == {"Lead": [1, 0], "Won": [1, 1], "Lost": [1, 0]}


def test_chart_buckets_dates_and_can_run_cumulative(client, sales):
    data = chart(
        client, sales, x=sales.closed["id"], date_bucket="month", hide_empty=True, cumulative=True
    ).get_json()["chart"]
    assert data["labels"] == ["Jan 2026", "Feb 2026"]
    assert data["series"][0]["data"] == [2, 3]


def test_chart_counts_multi_select_rows_in_each_option(client, sales):
    data = chart(client, sales, x=sales.tags["id"]).get_json()["chart"]
    assert data["labels"] == ["A", "B", "No value"]
    assert data["series"][0]["data"] == [2, 2, 2]


def test_chart_applies_a_views_filters(client, sales):
    view = client.post(
        f"{sales.url}/views",
        json={
            "name": "North",
            "type": "chart",
            "config": {
                "filters": [{"property_id": sales.region["id"], "op": "equals", "value": "North"}],
                "chart": {"kind": "pie", "x": sales.stage["id"]},
            },
        },
        headers=sales.team.owner.headers,
    )
    assert view.status_code == 201, view.get_json()
    saved = view.get_json()["view"]
    assert saved["type"] == "chart"
    assert saved["config"]["chart"]["kind"] == "pie"
    data = chart(client, sales, view_id=saved["id"], x=sales.stage["id"]).get_json()["chart"]
    assert data["labels"] == ["Lead", "Won", "Lost"]
    assert data["series"][0]["data"] == [1, 1, 1]


def test_chart_rejects_aggregating_a_non_number(client, sales):
    resp = chart(client, sales, x=sales.stage["id"], aggregate="sum", measure=sales.region["id"])
    assert resp.status_code == 422
    resp = chart(client, sales, x="6f2c1f4e-1c1b-4d8e-9a39-6e0f9a8b7c6d")
    assert resp.status_code == 422


def test_chart_needs_access_to_the_database(client, sales):
    assert chart(client, sales, who=sales.team.outsider).status_code == 404
