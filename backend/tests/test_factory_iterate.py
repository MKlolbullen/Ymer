# ruff: noqa: F811  (the team fixture is imported from test_factory)
"""Factory stage F5: change requests, spec changes, change plans, stacks and budgets."""

import io
import json
import uuid
import zipfile
from datetime import UTC, datetime

import pytest

from tests.test_ai import el, yjs_blocks
from tests.test_factory import SECRET, SPEC, _approved_brief, team  # noqa: F401 (fixture)
from ymer.db import db_session
from ymer.factory.budget import month_start
from ymer.factory.changes import clean_changes, clean_plan, spec_diff
from ymer.factory.fake import fake_change_plan, fake_spec_changes
from ymer.factory.fake_agent import task_files
from ymer.factory.package import package_files
from ymer.factory.runner import work_once
from ymer.factory.stacks import STACKS
from ymer.models import FactoryRun, Workspace

EDITED_SPEC = yjs_blocks(
    el("heading", "Expense tracker", level=1),
    el("paragraph", "A small web app to log expenses and see totals per month."),
    el("heading", "Features", level=2),
    el(
        "bulletList",
        el("listItem", el("paragraph", "Add an expense: amount, date and category")),
        el("listItem", el("paragraph", "Monthly totals per category")),
        el("listItem", el("paragraph", "Export to CSV")),
        el("listItem", el("paragraph", "Dark mode: follows the system setting")),
    ),
    el("heading", "Out of scope", level=2),
    el("bulletList", el("listItem", el("paragraph", "Receipt scanning"))),
)

# --- pure helpers -------------------------------------------------------------------------


def test_spec_diff_shows_only_what_changed():
    diff = spec_diff("a\nb\nc\nd\ne", "a\nb\nC\nd\ne")
    assert "-c" in diff and "+C" in diff
    assert "---" not in diff and "+++" not in diff
    assert spec_diff("x", "x") == ""
    assert spec_diff("", "y" * 50, limit=10).endswith("cut ...]")


def test_fake_spec_changes_turns_added_and_removed_lines_into_requests():
    diff = "@@ -1,2 +1,2 @@\n Keep\n-- Export to CSV\n+- Dark mode: follows the system"
    found = json.loads(fake_spec_changes({"diffs": [{"title": "Spec", "diff": diff}]}))
    titles = [c["title"] for c in found["changes"]]
    assert titles == ["Dark mode", "Remove: Export to CSV"]
    assert "Spec" in found["changes"][0]["detail"]


def test_clean_changes_caps_untrusted_output():
    raw = {"changes": [{"title": "  A  "}, {"title": "a"}, {"title": ""}, "x"]}
    assert clean_changes(raw) == [{"title": "A", "detail": ""}]
    assert len(clean_changes({"changes": [{"title": str(i)} for i in range(50)]})) == 10
    assert clean_changes(None) == []


def test_clean_plan_keeps_titles_unique_and_covers_every_change():
    changes = [
        {"number": 3, "title": "Dark mode", "detail": "Follow the system."},
        {"number": 4, "title": "Bigger font", "detail": ""},
    ]
    spec = {
        "tasks": [
            {
                "title": "Set up the project skeleton",  # clashes with a Done task
                "component": "Page",
                "priority": "urgent",
                "size": "XL",
                "detail": "x",
                "depends_on": ["Add an expense", "Nope"],
                "change": 3,
            },
            {"title": "Stray", "change": 99},  # not a requested change
        ]
    }
    tasks = clean_plan(spec, changes, ["Set up the project skeleton", "Add an expense"])
    assert [t["title"] for t in tasks] == ["Set up the project skeleton (2)", "Bigger font"]
    assert tasks[0]["priority"] == "must" and tasks[0]["size"] == "M"
    assert tasks[0]["depends_on"] == ["Add an expense"]
    assert [t["change"] for t in tasks] == [3, 4]  # the model forgot #4; it gets a task


def test_fake_change_plan_gives_each_change_a_task():
    plan = json.loads(
        fake_change_plan(
            {
                "changes": [{"number": 1, "title": "Show a chart", "detail": "Per month"}],
                "components": ["Page", "Browser storage"],
            }
        )
    )
    assert plan["tasks"][0]["component"] == "Page" and plan["tasks"][0]["change"] == 1


@pytest.mark.parametrize("stack", list(STACKS))
def test_every_stack_has_packaging_and_demo_files(stack):
    when = datetime(2026, 11, 25, tzinfo=UTC)
    out = package_files(
        {}, app="T", number=1, stack=stack, notes=None, when=when, requests=["#1 Dark mode"]
    )
    assert out[".gitignore"] and "#1 Dark mode" in out["RELEASE.md"]
    assert "Run it" in out["RELEASE.md"] and "```" in out["RELEASE.md"]
    setup = task_files(stack, "Set up", "", setup=True, app="Demo")
    feature = task_files(stack, "Dark mode", "Follows the system", setup=False, app="Demo")
    assert setup and feature
    assert any("test" in path for path in feature)


def test_month_start_is_the_first_in_utc():
    assert month_start(datetime(2026, 11, 25, 13, 5, tzinfo=UTC)) == datetime(
        2026, 11, 1, tzinfo=UTC
    )


# --- API ----------------------------------------------------------------------------------


def _released(app, client, team, stack="static-web"):
    h = team.editor.headers
    _approved_brief(client, team)
    client.post(f"{team.url}/architecture", json={"stack": stack}, headers=h)
    client.post(f"{team.url}/approve-plan", headers=h)
    client.post(f"{team.url}/runs", headers=h)
    work_once(app)
    resp = client.post(f"{team.url}/releases", json={}, headers=h)
    assert resp.status_code == 201
    return resp.get_json()["factory"]


def _edit_spec(client, team, state=EDITED_SPEC):
    resp = client.put(f"/internal/collab/documents/{team.spec.id}", data=state, headers=SECRET)
    assert resp.status_code == 204


def test_change_requests_need_an_approved_plan_and_editors(app, client, team):
    h = team.editor.headers
    body = {"title": "Dark mode"}
    resp = client.post(f"{team.url}/changes", json=body, headers=h)
    assert resp.status_code == 409 and "architecture" in resp.get_json()["message"]
    _released(app, client, team)
    assert (
        client.post(f"{team.url}/changes", json=body, headers=team.viewer.headers).status_code
        == 403
    )
    assert client.post(f"{team.url}/changes", json={"title": ""}, headers=h).status_code == 422
    data = client.post(f"{team.url}/changes", json=body, headers=h).get_json()["factory"]
    change = data["changes"][0]
    assert change["number"] == 1 and change["status"] == "open" and change["origin"] == "review"

    url = f"{team.url}/changes/{change['id']}"
    edited = client.patch(url, json={"detail": "Follow the system."}, headers=h).get_json()
    assert edited["factory"]["changes"][0]["detail"] == "Follow the system."
    dismissed = client.patch(url, json={"status": "dismissed"}, headers=h).get_json()
    assert dismissed["factory"]["changes"][0]["status"] == "dismissed"
    assert client.post(f"{team.url}/changes/plan", headers=h).status_code == 409
    client.patch(url, json={"status": "open"}, headers=h)
    other = f"{team.url}/changes/{uuid.uuid4()}"
    assert client.patch(other, json={"status": "open"}, headers=h).status_code == 404


def test_a_spec_edit_flows_through_to_a_new_release(app, client, team):
    """The F5 promise: change the spec, and a new version of the app comes out."""
    h = team.editor.headers
    released = _released(app, client, team)
    assert released["stage"] == "released" and released["spec_changes"] == []
    first_files = released["build"]["file_count"]
    nothing = client.post(f"{team.url}/changes/from-spec", headers=h)
    assert nothing.status_code == 409

    _edit_spec(client, team)
    data = client.get(team.url, headers=team.viewer.headers).get_json()["factory"]
    assert [s["title"] for s in data["spec_changes"]] == ["Expense tracker spec"]

    resp = client.post(f"{team.url}/changes/from-spec", headers=h)
    assert resp.status_code == 201
    data = resp.get_json()["factory"]
    assert resp.get_json()["created"] == 1
    assert data["spec_changes"] == []  # taken in
    change = data["changes"][0]
    assert change["title"] == "Dark mode" and change["origin"] == "spec"
    assert "follows the system setting" in change["detail"]

    resp = client.post(f"{team.url}/changes/plan", headers=h)
    assert resp.status_code == 201 and resp.get_json()["planned"] == 1
    data = resp.get_json()["factory"]
    assert data["stage"] == "planned"
    assert data["changes"][0]["status"] == "planned"
    assert data["changes"][0]["tasks"] == ["Dark mode"]
    tasks = client.get(f"{team.base}/databases/{data['tasks']['id']}/rows", headers=h).get_json()
    assert len(tasks["rows"]) == data["tasks"]["count"]

    # The build works only on the new task; the others stay Done.
    run = client.post(f"{team.url}/runs", headers=h).get_json()["run"]
    assert run["tasks_total"] - run["tasks_done"] == 1
    work_once(app)
    data = client.get(team.url, headers=h).get_json()["factory"]
    assert data["build"]["latest_run"]["status"] == "passed"
    assert data["build"]["latest_run"]["tasks_done"] == data["build"]["latest_run"]["tasks_total"]
    assert data["stage"] == "built" and data["changes"][0]["status"] == "built"
    assert data["build"]["file_count"] == first_files + 2  # src/dark_mode.mjs and its test
    assert data["delivery"]["unreleased"]["added"] == [
        "src/dark_mode.mjs",
        "tests/dark_mode.test.mjs",
    ]

    second = client.post(f"{team.url}/releases", json={}, headers=h).get_json()
    assert second["release"]["number"] == 2
    assert second["release"]["requests"] == [{"number": 1, "title": "Dark mode"}]
    assert second["factory"]["changes"][0]["status"] == "released"
    assert second["factory"]["changes"][0]["release"] == 2
    zipped = client.get(f"{team.url}/releases/2/download", headers=h)
    readme = zipfile.ZipFile(io.BytesIO(zipped.data)).read("expense-app-release-2/RELEASE.md")
    assert b"#1 Dark mode" in readme


def test_planning_needs_edit_access_to_the_tasks(app, client, team):
    h = team.editor.headers
    _released(app, client, team)
    client.post(f"{team.url}/changes", json={"title": "Dark mode"}, headers=h)
    # A viewer cannot plan; an editor can, once.
    assert client.post(f"{team.url}/changes/plan", headers=team.viewer.headers).status_code == 403
    assert client.post(f"{team.url}/changes/plan", headers=h).status_code == 201
    assert client.post(f"{team.url}/changes/plan", headers=h).status_code == 409


def test_workspace_budget_limits_builds(app, client, team):
    h = team.editor.headers
    budget_url = f"{team.base}/factory-budget"
    budget = client.get(budget_url, headers=h).get_json()["budget"]
    assert budget["used"] == 0 and budget["custom"] is False and budget["can_edit"] is False
    assert client.put(budget_url, json={"monthly_tokens": 10}, headers=h).status_code == 403
    owner = team.owner.headers
    assert client.put(budget_url, json={"monthly_tokens": -1}, headers=owner).status_code == 422

    _released(app, client, team)
    used = client.get(budget_url, headers=h).get_json()["budget"]["used"]
    assert used > 0  # the demo agent counts its tokens

    # A limit below what was used blocks new builds.
    set_low = client.put(budget_url, json={"monthly_tokens": used}, headers=owner).get_json()
    assert set_low["budget"]["remaining"] == 0 and set_low["budget"]["can_edit"] is True
    client.post(f"{team.url}/changes", json={"title": "Dark mode"}, headers=h)
    client.post(f"{team.url}/changes/plan", headers=h)
    blocked = client.post(f"{team.url}/runs", headers=h)
    assert blocked.status_code == 409 and "budget" in blocked.get_json()["message"]

    # A little room left: the build starts but stops when the month's budget runs out.
    client.put(budget_url, json={"monthly_tokens": used + 1000}, headers=owner)
    run = client.post(f"{team.url}/runs", headers=h).get_json()["run"]
    work_once(app)
    data = client.get(f"{team.url}/runs/{run['id']}", headers=h).get_json()["run"]
    assert data["status"] == "failed" and "monthly Factory budget" in data["error"]

    # No limit (0), and back to the server default (null).
    assert (
        client.put(budget_url, json={"monthly_tokens": 0}, headers=owner).get_json()["budget"][
            "remaining"
        ]
        is None
    )
    default = client.put(budget_url, json={"monthly_tokens": None}, headers=owner).get_json()
    assert default["budget"]["custom"] is False
    factory = client.get(team.url, headers=h).get_json()["factory"]
    assert factory["budget"]["limit"] == default["budget"]["default_limit"]


def test_runs_from_last_month_do_not_count(app, client, team):
    _released(app, client, team)
    with app.app_context():
        for run in db_session.query(FactoryRun).all():
            run.created_at = datetime(2020, 1, 15, tzinfo=UTC)
        db_session.commit()
    budget = client.get(f"{team.base}/factory-budget", headers=team.editor.headers).get_json()
    assert budget["budget"]["used"] == 0
    with app.app_context():
        assert db_session.get(Workspace, team.ws.id).factory_monthly_tokens is None


def test_new_stacks_build_with_the_demo_agent(app, client, team):
    for stack in ("node-cli", "python-api"):
        assert stack in STACKS
    data = _released(app, client, team, "python-api")
    assert data["stack"] == "python-api" and data["stage"] == "released"
    zipped = client.get(f"{team.url}/releases/1/download", headers=team.editor.headers)
    names = zipfile.ZipFile(io.BytesIO(zipped.data)).namelist()
    assert "expense-app-release-1/app/main.py" in names
    assert "expense-app-release-1/Dockerfile" in names


def test_spec_tracking_ignores_untouched_sources(app, client, team):
    _released(app, client, team)
    _edit_spec(client, team, SPEC)  # the same text saved again
    data = client.get(team.url, headers=team.editor.headers).get_json()["factory"]
    assert data["spec_changes"] == []
