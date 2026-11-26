# ruff: noqa: F811  (pytest fixtures shared from test_databases are arguments here)
"""Database engine v2: formulas, filtered and two-step rollups, date ranges, files and
people by name, through the HTTP API. The full blueprints are in test_blueprints.py."""

import pytest

from tests.test_databases import add_property, add_row, db_url, projects, rows, team  # noqa: F401

pytestmark = pytest.mark.db


def patch_row(client, team, database_id, row_id, values, status=200):
    resp = client.patch(
        db_url(team, database_id, f"/rows/{row_id}"),
        json={"values": values},
        headers=team.owner.headers,
    )
    assert resp.status_code == status, resp.get_json()
    return resp.get_json()


def by_title(client, team, database_id, title_id):
    return {r["values"][title_id]: r["values"] for r in rows(client, team, database_id)}


def test_formulas_compute_rename_and_refuse_bad_expressions(client, team, projects):
    p = projects
    double = add_property(
        client,
        team,
        team.tasks,
        name="Double",
        type="formula",
        config={"expression": 'prop("Points") * 2'},
    )
    label = add_property(
        client,
        team,
        team.tasks,
        name="Label",
        type="formula",
        config={"expression": 'if(prop("Done"), "done: " + prop("Name"), prop("Double") + " pts")'},
    )
    got = by_title(client, team, team.tasks, p.t_title)
    assert got["one"][double["id"]] == 6 and got["one"][label["id"]] == "done: one"
    assert got["two"][label["id"]] == "10 pts"

    # Renaming a property keeps formulas pointing at it.
    resp = client.patch(
        db_url(team, team.tasks, f"/properties/{p.points}"),
        json={"name": "Story points"},
        headers=team.owner.headers,
    )
    assert resp.status_code == 200
    props = {
        x["id"]: x
        for x in client.get(db_url(team, team.tasks), headers=team.owner.headers).get_json()[
            "properties"
        ]
    }
    assert props[double["id"]]["config"]["expression"] == 'prop("Story points") * 2'
    assert by_title(client, team, team.tasks, p.t_title)["two"][double["id"]] == 10

    for expression, message in [
        ('prop("Nope") + 1', "no property called"),
        ("1 +", "ends too early"),
    ]:
        resp = client.post(
            db_url(team, team.tasks, "/properties"),
            json={"name": "Bad", "type": "formula", "config": {"expression": expression}},
            headers=team.owner.headers,
        )
        assert resp.status_code == 422
        assert message in str(resp.get_json())
    # Formulas are computed: they cannot be written.
    resp = client.patch(
        db_url(team, team.tasks, f"/rows/{p.t1['id']}"),
        json={"values": {double["id"]: 3}},
        headers=team.owner.headers,
    )
    assert resp.status_code == 422


def test_properties_that_depend_on_each_other_come_back_empty(client, team, projects):
    p = projects
    a = add_property(client, team, team.tasks, name="A", type="formula", config={"expression": "1"})
    b = add_property(
        client, team, team.tasks, name="B", type="formula", config={"expression": 'prop("A") + 1'}
    )
    resp = client.patch(
        db_url(team, team.tasks, f"/properties/{a['id']}"),
        json={"config": {"expression": 'prop("B") + 1'}},
        headers=team.owner.headers,
    )
    assert resp.status_code == 200
    one = by_title(client, team, team.tasks, p.t_title)["one"]
    assert one[a["id"]] is None or one[b["id"]] is None  # no endless loop, no error


def test_rollups_filter_follow_two_relations_and_build_on_formulas(client, team, projects):
    p = projects
    # Projects <- Tasks: points of tasks not done; a weighted average; a formula's sum.
    open_points = add_property(
        client,
        team,
        p.projects,
        name="Open points",
        type="rollup",
        config={
            "relation_property_id": p.reverse["id"],
            "target_property_id": p.points,
            "function": "sum",
            "filters": [{"property_id": p.done, "op": "is_empty"}],
        },
    )
    double = add_property(
        client,
        team,
        team.tasks,
        name="Double",
        type="formula",
        config={"expression": 'prop("Points") * 2'},
    )
    doubled = add_property(
        client,
        team,
        p.projects,
        name="Doubled",
        type="rollup",
        config={
            "relation_property_id": p.reverse["id"],
            "target_property_id": double["id"],
            "function": "sum",
        },
    )
    weighted = add_property(
        client,
        team,
        p.projects,
        name="Weighted",
        type="rollup",
        config={
            "relation_property_id": p.reverse["id"],
            "target_property_id": p.points,
            "weight_property_id": p.points,
            "function": "weighted_average",
        },
    )
    got = by_title(client, team, p.projects, p.p_title)
    assert got["Alpha"][open_points["id"]] == 5
    assert got["Alpha"][doubled["id"]] == 16
    assert got["Alpha"][weighted["id"]] == pytest.approx((3 * 3 + 5 * 5) / 8)
    assert got["Beta"][open_points["id"]] == 1

    # Tasks -> Project -> Tasks: the points of the other tasks in the same project.
    siblings = add_property(
        client,
        team,
        team.tasks,
        name="Project points",
        type="rollup",
        config={
            "relation_property_id": p.forward["id"],
            "through_property_id": p.reverse["id"],
            "target_property_id": p.points,
            "function": "sum",
            "through_filters": [
                {
                    "property_id": p.forward["id"],
                    "op": "contains",
                    "value": {"this": p.forward["id"]},
                }
            ],
        },
    )
    tasks = by_title(client, team, team.tasks, p.t_title)
    assert tasks["one"][siblings["id"]] == 8 and tasks["three"][siblings["id"]] == 1

    # Bad configs are refused.
    for config in [
        {**doubled["config"], "function": "weighted_average"},  # no weight
        {**doubled["config"], "through_property_id": p.points},  # not a relation
        {**doubled["config"], "filters": [{"property_id": p.p_title, "op": "equals"}]},
        {**doubled["config"], "function": "top_option"},  # not a select
    ]:
        resp = client.post(
            db_url(team, p.projects, "/properties"),
            json={"name": "Bad", "type": "rollup", "config": config},
            headers=team.owner.headers,
        )
        assert resp.status_code == 422, config


def test_date_ranges_files_and_people_by_name(client, team, projects):
    p = projects
    dates = add_property(client, team, team.tasks, name="When", type="date_range")
    files = add_property(client, team, team.tasks, name="Files", type="file")
    people = add_property(client, team, team.tasks, name="People", type="person")
    row = patch_row(
        client,
        team,
        team.tasks,
        p.t1["id"],
        {
            dates["id"]: {"start": "2026-11-02", "end": "2026-12-04"},
            files["id"]: [{"url": "s3://bucket/roe/ENG-001.pdf"}, "https://example.com/a.pdf"],
            people["id"]: [{"name": "Editor"}, {"name": "Ada Outsider"}, str(team.viewer.id)],
        },
    )["row"]["values"]
    assert row[dates["id"]] == {"start": "2026-11-02", "end": "2026-12-04"}
    assert row[files["id"]] == [
        {"name": "ENG-001.pdf", "url": "s3://bucket/roe/ENG-001.pdf"},
        {"name": "a.pdf", "url": "https://example.com/a.pdf"},
    ]
    # "Editor" is a member's name: stored as that member. The other name stays a name.
    assert row[people["id"]] == [
        {"id": str(team.editor.id), "display_name": "Editor"},
        {"id": None, "display_name": "Ada Outsider"},
        {"id": str(team.viewer.id), "display_name": "Viewer"},
    ]
    for bad in [
        {dates["id"]: {"start": "2026-12-04", "end": "2026-11-02"}},
        {files["id"]: [{"url": "javascript:alert(1)"}]},
        {files["id"]: [{"file_id": "00000000-0000-0000-0000-000000000000"}]},
        {people["id"]: ["not-a-member"]},
    ]:
        patch_row(client, team, team.tasks, p.t1["id"], bad, status=422)

    # Views filter and sort the new types; "is any of" works on selects and text.
    view = client.post(
        db_url(team, team.tasks, "/views"),
        json={
            "name": "In range",
            "type": "table",
            "config": {
                "filters": [
                    {"property_id": dates["id"], "op": "equals", "value": "2026-11-20"},
                    {"property_id": people["id"], "op": "contains", "value": "Ada Outsider"},
                    {"property_id": p.t_title, "op": "in", "value": ["one", "two"]},
                ],
                "sorts": [{"property_id": dates["id"], "direction": "desc"}],
            },
        },
        headers=team.owner.headers,
    ).get_json()["view"]
    shown = rows(client, team, team.tasks, view_id=view["id"])
    assert [r["values"][p.t_title] for r in shown] == ["one"]

    # Text becomes people (matched by name) and date ranges become dates.
    note = add_property(client, team, team.tasks, name="Who", type="text")
    patch_row(client, team, team.tasks, p.t2["id"], {note["id"]: "Owner, Someone Else"})
    resp = client.patch(
        db_url(team, team.tasks, f"/properties/{note['id']}"),
        json={"type": "person"},
        headers=team.owner.headers,
    )
    assert resp.status_code == 200
    two = by_title(client, team, team.tasks, p.t_title)["two"]
    assert two[note["id"]] == [
        {"id": str(team.owner.id), "display_name": "Owner"},
        {"id": None, "display_name": "Someone Else"},
    ]
    resp = client.patch(
        db_url(team, team.tasks, f"/properties/{dates['id']}"),
        json={"type": "date"},
        headers=team.owner.headers,
    )
    assert resp.status_code == 200
    assert by_title(client, team, team.tasks, p.t_title)["one"][dates["id"]] == "2026-11-02"
