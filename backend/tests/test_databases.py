"""Databases: schema, values, relations, rollups, views and permissions."""

import pytest

from tests.factories import grant, make_page, make_user, make_workspace
from ymer.models import DatabaseRow, Page, PageType, Role

pytestmark = pytest.mark.db


@pytest.fixture
def team(app, db, client):
    owner = make_user(app, "owner")
    editor = make_user(app, "editor")
    viewer = make_user(app, "viewer")
    outsider = make_user(app, "outsider")
    ws = make_workspace(owner, {editor: Role.EDITOR, viewer: Role.VIEWER})
    base = f"/api/workspaces/{ws.id}"

    def new_database(title: str, who=owner, parent=None) -> str:
        resp = client.post(
            f"{base}/pages",
            json={"type": "database", "title": title, "parent_id": parent},
            headers=who.headers,
        )
        assert resp.status_code == 201, resp.get_json()
        return resp.get_json()["page"]["id"]

    tasks = new_database("Tasks")
    return type("Team", (), dict(locals()))


def db_url(team, database_id, suffix=""):
    return f"{team.base}/databases/{database_id}{suffix}"


def schema(client, team, database_id, who=None):
    resp = client.get(db_url(team, database_id), headers=(who or team.owner).headers)
    assert resp.status_code == 200
    return resp.get_json()


def add_property(client, team, database_id, who=None, **body):
    resp = client.post(
        db_url(team, database_id, "/properties"), json=body, headers=(who or team.owner).headers
    )
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()["property"]


def add_row(client, team, database_id, values=None, who=None):
    resp = client.post(
        db_url(team, database_id, "/rows"),
        json={"values": values or {}},
        headers=(who or team.owner).headers,
    )
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()["row"]


def rows(client, team, database_id, who=None, **params):
    resp = client.get(
        db_url(team, database_id, "/rows"), query_string=params, headers=(who or team.owner).headers
    )
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["rows"]


def title_id(client, team, database_id):
    return next(
        p["id"] for p in schema(client, team, database_id)["properties"] if p["type"] == "title"
    )


# --- schema ---------------------------------------------------------------------------


def test_new_database_has_title_property_and_table_view(client, team):
    data = schema(client, team, team.tasks, team.viewer)
    assert [(p["name"], p["type"]) for p in data["properties"]] == [("Name", "title")]
    assert [(v["name"], v["type"]) for v in data["views"]] == [("Table", "table")]


def test_database_routes_only_for_database_pages(client, team):
    doc = make_page(team.ws, "doc")
    assert client.get(db_url(team, doc.id), headers=team.owner.headers).status_code == 404


def test_title_property_is_unique_and_permanent(client, team):
    resp = client.post(
        db_url(team, team.tasks, "/properties"),
        json={"name": "Other", "type": "title"},
        headers=team.owner.headers,
    )
    assert resp.status_code == 422
    tid = title_id(client, team, team.tasks)
    resp = client.delete(db_url(team, team.tasks, f"/properties/{tid}"), headers=team.owner.headers)
    assert resp.status_code == 400
    resp = client.patch(
        db_url(team, team.tasks, f"/properties/{tid}"),
        json={"type": "text"},
        headers=team.owner.headers,
    )
    assert resp.status_code == 422


def test_permissions(client, team, db):
    # Viewers read; editors write; outsiders see nothing.
    assert client.get(db_url(team, team.tasks), headers=team.outsider.headers).status_code == 404
    resp = client.post(db_url(team, team.tasks, "/rows"), json={}, headers=team.viewer.headers)
    assert resp.status_code == 403
    add_row(client, team, team.tasks, who=team.editor)
    assert len(rows(client, team, team.tasks, who=team.viewer)) == 1
    # A page grant opens one database to a guest.
    grant(db.get(Page, team.tasks), team.outsider, Role.VIEWER)
    assert len(rows(client, team, team.tasks, who=team.outsider)) == 1


# --- values ---------------------------------------------------------------------------


def test_values_are_validated_and_normalised(client, team):
    tid = title_id(client, team, team.tasks)
    num = add_property(client, team, team.tasks, name="Points", type="number")["id"]
    done = add_property(client, team, team.tasks, name="Done", type="checkbox")["id"]
    due = add_property(client, team, team.tasks, name="Due", type="date")["id"]
    link = add_property(client, team, team.tasks, name="Link", type="url")["id"]
    status = add_property(
        client,
        team,
        team.tasks,
        name="Status",
        type="select",
        config={"options": [{"name": "Todo"}, {"name": "Done", "color": "green"}]},
    )
    todo_id = status["config"]["options"][0]["id"]
    who = add_property(client, team, team.tasks, name="Owner", type="person")["id"]

    row = add_row(
        client,
        team,
        team.tasks,
        {
            tid: "  Write spec ",
            num: "3",
            done: True,
            due: "2026-12-01",
            link: "example.com",
            status["id"]: todo_id,
            who: [str(team.editor.id)],
        },
    )
    v = row["values"]
    assert v[tid] == "Write spec"
    assert v[num] == 3
    assert v[done] is True
    assert v[due] == "2026-12-01"
    assert v[link] == "https://example.com"
    assert v[status["id"]] == todo_id
    assert v[who] == [{"id": str(team.editor.id), "display_name": "Editor"}]

    bad = [
        {num: "many"},
        {done: "yes"},
        {due: "12/01/2026"},
        {link: "javascript:alert(1)"},
        {status["id"]: "nope"},
        {who: [str(team.outsider.id)]},  # not a workspace member
        {"00000000-0000-0000-0000-000000000000": "x"},
    ]
    for values in bad:
        resp = client.patch(
            db_url(team, team.tasks, f"/rows/{row['id']}"),
            json={"values": values},
            headers=team.owner.headers,
        )
        assert resp.status_code == 422, values

    # Partial update merges; null clears.
    resp = client.patch(
        db_url(team, team.tasks, f"/rows/{row['id']}"),
        json={"values": {num: None, done: False}},
        headers=team.owner.headers,
    )
    v = resp.get_json()["row"]["values"]
    assert v[num] is None and v[done] is None and v[tid] == "Write spec"


def test_deleted_option_disappears_from_values(client, team):
    status = add_property(
        client,
        team,
        team.tasks,
        name="Tags",
        type="multi_select",
        config={"options": [{"name": "a"}, {"name": "b"}]},
    )
    a, b = (o["id"] for o in status["config"]["options"])
    add_row(client, team, team.tasks, {status["id"]: [a, b]})
    client.patch(
        db_url(team, team.tasks, f"/properties/{status['id']}"),
        json={"config": {"options": [status["config"]["options"][1]]}},
        headers=team.owner.headers,
    )
    assert rows(client, team, team.tasks)[0]["values"][status["id"]] == [b]


def test_changing_type_converts_values(client, team):
    prop = add_property(client, team, team.tasks, name="Stage", type="text")["id"]
    for value in ["Draft", "Review", "Draft", None]:
        add_row(client, team, team.tasks, {prop: value} if value else {})
    resp = client.patch(
        db_url(team, team.tasks, f"/properties/{prop}"),
        json={"type": "select"},
        headers=team.owner.headers,
    )
    assert resp.status_code == 200
    options = resp.get_json()["property"]["config"]["options"]
    assert [o["name"] for o in options] == ["Draft", "Review"]
    names = {o["id"]: o["name"] for o in options}
    got = [names.get(r["values"][prop]) for r in rows(client, team, team.tasks)]
    assert got == ["Draft", "Review", "Draft", None]

    resp = client.patch(
        db_url(team, team.tasks, f"/properties/{prop}"),
        json={"type": "number"},
        headers=team.owner.headers,
    )
    assert all(r["values"][prop] is None for r in rows(client, team, team.tasks))


def test_deleting_a_property_removes_its_values(client, team, db):
    prop = add_property(client, team, team.tasks, name="Notes", type="text")["id"]
    row = add_row(client, team, team.tasks, {prop: "hello"})
    client.delete(db_url(team, team.tasks, f"/properties/{prop}"), headers=team.owner.headers)
    assert prop not in db.get(DatabaseRow, row["id"]).values


# --- relations and rollups ------------------------------------------------------------


@pytest.fixture
def projects(client, team):
    """Projects <-> Tasks two-way relation, tasks with points and done flags."""
    projects = team.new_database("Projects")
    rel = client.post(
        db_url(team, team.tasks, "/properties"),
        json={
            "name": "Project",
            "type": "relation",
            "config": {"database_id": projects},
            "two_way": True,
            "reverse_name": "Tasks",
        },
        headers=team.owner.headers,
    )
    assert rel.status_code == 201, rel.get_json()
    forward, reverse = rel.get_json()["property"], rel.get_json()["reverse_property"]
    t_title = title_id(client, team, team.tasks)
    p_title = title_id(client, team, projects)
    points = add_property(client, team, team.tasks, name="Points", type="number")["id"]
    done = add_property(client, team, team.tasks, name="Done", type="checkbox")["id"]
    alpha = add_row(client, team, projects, {p_title: "Alpha"})
    beta = add_row(client, team, projects, {p_title: "Beta"})
    t1 = add_row(client, team, team.tasks, {t_title: "one", points: 3, done: True})
    t2 = add_row(client, team, team.tasks, {t_title: "two", points: 5})
    t3 = add_row(client, team, team.tasks, {t_title: "three", points: 1})
    for t, p in [(t1, alpha), (t2, alpha), (t3, beta)]:
        resp = client.patch(
            db_url(team, team.tasks, f"/rows/{t['id']}"),
            json={"values": {forward["id"]: [p["id"]]}},
            headers=team.owner.headers,
        )
        assert resp.status_code == 200, resp.get_json()
    return type("Projects", (), dict(locals()))


def test_two_way_relation_reads_from_both_sides(client, team, projects):
    p = projects
    task_rows = {r["values"][p.t_title]: r for r in rows(client, team, team.tasks)}
    assert task_rows["one"]["values"][p.forward["id"]] == [{"id": p.alpha["id"], "title": "Alpha"}]
    project_rows = {r["values"][p.p_title]: r for r in rows(client, team, p.projects)}
    linked = project_rows["Alpha"]["values"][p.reverse["id"]]
    assert [x["title"] for x in linked] == ["one", "two"]

    # Writing from the reverse side changes the same links.
    resp = client.patch(
        db_url(team, p.projects, f"/rows/{p.beta['id']}"),
        json={"values": {p.reverse["id"]: [p.t1["id"], p.t3["id"]]}},
        headers=team.owner.headers,
    )
    assert resp.status_code == 200
    task_rows = {r["values"][p.t_title]: r for r in rows(client, team, team.tasks)}
    assert [x["title"] for x in task_rows["one"]["values"][p.forward["id"]]] == ["Alpha", "Beta"]


def test_relation_rejects_rows_from_other_databases(client, team, projects):
    p = projects
    resp = client.patch(
        db_url(team, team.tasks, f"/rows/{p.t1['id']}"),
        json={"values": {p.forward["id"]: [p.t2["id"]]}},
        headers=team.owner.headers,
    )
    assert resp.status_code == 422


def test_rollups(client, team, projects):
    p = projects

    def rollup(function, target):
        prop = add_property(
            client,
            team,
            p.projects,
            name=function,
            type="rollup",
            config={
                "relation_property_id": p.reverse["id"],
                "target_property_id": target,
                "function": function,
            },
        )
        return prop["id"]

    ids = {
        "count": rollup("count", p.t_title),
        "sum": rollup("sum", p.points),
        "average": rollup("average", p.points),
        "max": rollup("max", p.points),
        "percent_checked": rollup("percent_checked", p.done),
        "show_original": rollup("show_original", p.t_title),
    }
    by_name = {r["values"][p.p_title]: r["values"] for r in rows(client, team, p.projects)}
    alpha = {k: by_name["Alpha"][v] for k, v in ids.items()}
    assert alpha == {
        "count": 2,
        "sum": 8,
        "average": 4,
        "max": 5,
        "percent_checked": 50,
        "show_original": ["one", "two"],
    }
    assert by_name["Beta"][ids["sum"]] == 1


def test_rollup_config_is_checked(client, team, projects):
    p = projects
    resp = client.post(
        db_url(team, p.projects, "/properties"),
        json={
            "name": "bad",
            "type": "rollup",
            "config": {
                "relation_property_id": p.reverse["id"],
                "target_property_id": p.p_title,  # not in the related database
                "function": "sum",
            },
        },
        headers=team.owner.headers,
    )
    assert resp.status_code == 422


def test_hidden_related_database_hides_titles_and_rollups(client, team, projects, db):
    """A guest with access to Tasks only sees that links exist, not what they point at."""
    p = projects
    add_property(
        client,
        team,
        team.tasks,
        name="Project count",
        type="rollup",
        config={
            "relation_property_id": p.forward["id"],
            "target_property_id": p.p_title,
            "function": "show_original",
        },
    )
    grant(db.get(Page, team.tasks), team.outsider, Role.EDITOR)
    got = rows(client, team, team.tasks, who=team.outsider)
    one = next(r for r in got if r["values"][p.t_title] == "one")
    assert one["values"][p.forward["id"]] == [{"id": p.alpha["id"], "title": None}]
    rollup_values = [v for k, v in one["values"].items() if k not in (p.t_title,)]
    assert ["Alpha"] not in rollup_values
    # ...and cannot add links into it.
    resp = client.patch(
        db_url(team, team.tasks, f"/rows/{p.t2['id']}"),
        json={"values": {p.forward["id"]: [p.beta["id"]]}},
        headers=team.outsider.headers,
    )
    assert resp.status_code == 404


def test_two_way_needs_edit_rights_on_the_other_database(client, team, db):
    other = team.new_database("Other")
    mine = team.new_database("Mine")
    grant(db.get(Page, other), team.editor, Role.VIEWER)
    body = {"name": "Rel", "type": "relation", "config": {"database_id": other}}
    url = db_url(team, mine, "/properties")
    assert (
        client.post(url, json={**body, "two_way": True}, headers=team.editor.headers).status_code
        == 422
    )
    assert client.post(url, json=body, headers=team.editor.headers).status_code == 201


def test_deleting_one_half_removes_the_two_way_relation(client, team, projects):
    p = projects
    resp = client.delete(
        db_url(team, p.projects, f"/properties/{p.reverse['id']}"), headers=team.owner.headers
    )
    assert resp.status_code == 204
    names = [x["name"] for x in schema(client, team, team.tasks)["properties"]]
    assert "Project" not in names


def test_deleting_a_row_removes_its_links(client, team, projects):
    p = projects
    client.delete(db_url(team, p.projects, f"/rows/{p.alpha['id']}"), headers=team.owner.headers)
    task_rows = {r["values"][p.t_title]: r for r in rows(client, team, team.tasks)}
    assert task_rows["one"]["values"][p.forward["id"]] is None


# --- views ----------------------------------------------------------------------------


def test_view_filters_and_sorts(client, team, projects):
    p = projects
    view = client.post(
        db_url(team, team.tasks, "/views"),
        json={
            "name": "Open, biggest first",
            "type": "table",
            "config": {
                "filters": [{"property_id": p.done, "op": "equals", "value": False}],
                "sorts": [{"property_id": p.points, "direction": "desc"}],
            },
        },
        headers=team.owner.headers,
    )
    assert view.status_code == 201, view.get_json()
    got = rows(client, team, team.tasks, view_id=view.get_json()["view"]["id"])
    assert [r["values"][p.t_title] for r in got] == ["two", "three"]

    # Relation "contains" filters by a related row.
    client.patch(
        db_url(team, team.tasks, f"/views/{view.get_json()['view']['id']}"),
        json={
            "config": {
                "filters": [
                    {"property_id": p.forward["id"], "op": "contains", "value": p.beta["id"]}
                ]
            }
        },
        headers=team.owner.headers,
    )
    got = rows(client, team, team.tasks, view_id=view.get_json()["view"]["id"])
    assert [r["values"][p.t_title] for r in got] == ["three"]


def test_sorting_puts_empty_values_last(client, team):
    tid = title_id(client, team, team.tasks)
    num = add_property(client, team, team.tasks, name="N", type="number")["id"]
    for title, n in [("a", 2), ("b", None), ("c", 1)]:
        add_row(client, team, team.tasks, {tid: title, num: n})
    view = client.post(
        db_url(team, team.tasks, "/views"),
        json={
            "name": "v",
            "type": "board",
            "config": {"sorts": [{"property_id": num, "direction": "desc"}]},
        },
        headers=team.owner.headers,
    ).get_json()["view"]
    got = rows(client, team, team.tasks, view_id=view["id"])
    assert [r["values"][tid] for r in got] == ["a", "c", "b"]


def test_view_config_is_validated_and_last_view_kept(client, team):
    url = db_url(team, team.tasks, "/views")
    bad = {"name": "v", "type": "table", "config": {"filters": [{"property_id": "x", "op": "?"}]}}
    assert client.post(url, json=bad, headers=team.owner.headers).status_code == 422
    only = schema(client, team, team.tasks)["views"][0]["id"]
    assert client.delete(f"{url}/{only}", headers=team.owner.headers).status_code == 400


def test_row_search_query(client, team):
    tid = title_id(client, team, team.tasks)
    add_row(client, team, team.tasks, {tid: "Buy milk"})
    add_row(client, team, team.tasks, {tid: "Write 100% report"})
    assert [r["values"][tid] for r in rows(client, team, team.tasks, q="milk")] == ["Buy milk"]
    assert len(rows(client, team, team.tasks, q="100%")) == 1


def test_database_page_type_in_tree(client, team):
    pages = client.get(f"{team.base}/pages", headers=team.owner.headers).get_json()["pages"]
    assert any(p["type"] == PageType.DATABASE.value for p in pages)
