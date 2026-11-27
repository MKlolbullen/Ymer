# ruff: noqa: F811  (the team fixture is imported from test_factory)
"""Factory checks (CI): the scripts, the workflows, the gate, history and repair."""

import os
import shutil
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.test_factory import _approved_brief, team  # noqa: F401 (fixture)
from ymer.db import db_session
from ymer.factory.build import read_tasks
from ymer.factory.ci import files_hash
from ymer.factory.ci_scripts import CI_DIR, STEPS, ci_files, step_command
from ymer.factory.fake_agent import task_files
from ymer.factory.package import package_files
from ymer.factory.runner import work_once
from ymer.factory.sandbox import ExecResult
from ymer.factory.sandbox.docker import write_tree
from ymer.factory.sandbox.fake import FakeSandbox
from ymer.factory.stacks import STACKS
from ymer.factory.workflows import (
    GITHUB_PATH,
    GITLAB_PATH,
    github_workflow,
    gitlab_ci,
    providers_for,
)
from ymer.models import (
    DatabaseRow,
    DatabaseView,
    FactoryCiRun,
    FactoryFile,
    FactoryRun,
    Page,
    ViewType,
)

# --- pure helpers -------------------------------------------------------------------------


def test_files_hash_ignores_order_and_sees_every_change():
    a = files_hash({"a.py": "x", "b.py": "y"})
    assert a == files_hash({"b.py": "y", "a.py": "x"})
    assert a != files_hash({"a.py": "x", "b.py": "z"})
    assert a != files_hash({"a.py": "x", "c.py": "y"})
    # Paths and contents cannot run into each other.
    assert files_hash({"ab": "c"}) != files_hash({"a": "bc"})


@pytest.mark.parametrize("stack", list(STACKS))
def test_every_stack_has_a_script_per_step(stack):
    files = ci_files(stack)
    for key, _name in STEPS:
        script = files[f"{CI_DIR}/{key}.sh"]
        assert script.startswith("#!/bin/sh") and "set -eu" in script
    assert STACKS[stack].sandbox_test in files[f"{CI_DIR}/test.sh"]
    assert all(path.startswith(f"{CI_DIR}/") for path in files)


@pytest.mark.parametrize("stack", list(STACKS))
def test_workflows_run_the_same_scripts(stack):
    github, gitlab = github_workflow(stack), gitlab_ci(stack)
    for key, _name in STEPS:
        assert f"run: {step_command(key)}" in github
        assert f"- {step_command(key)}" in gitlab
    assert "actions/checkout@v4" in github and "permissions:\n  contents: read" in github
    assert "\t" not in github + gitlab
    if stack in ("python-cli", "python-api", "flask-react"):
        assert "ruff==" in github and "python-version" in github and "ruff==" in gitlab
    if stack == "flask-react":
        assert "npm run build" in github and "frontend-build:" in gitlab


def test_ci_providers_follow_the_remote():
    assert providers_for("https://github.com/me/app.git") == ("github",)
    assert providers_for("https://gitlab.example.org/me/app.git") == ("gitlab",)
    assert providers_for("https://codeberg.org/me/app.git") == ("github", "gitlab")
    assert providers_for(None) == ("github", "gitlab")


def test_delivery_adds_check_scripts_and_workflows():
    when = datetime(2026, 11, 27, tzinfo=UTC)
    files = {
        "src/app/cli.py": "x = 1\n",
        GITLAB_PATH: "# the team's own\n",
        f"{CI_DIR}/lint.sh": "exit 0\n",  # a build cannot replace Ymer's checks
    }
    out = package_files(files, app="T", number=1, stack="python-cli", notes=None, when=when)
    assert out[GITLAB_PATH] == "# the team's own\n"
    assert out[GITHUB_PATH] == github_workflow("python-cli")
    assert out[f"{CI_DIR}/lint.sh"] == ci_files("python-cli")[f"{CI_DIR}/lint.sh"]
    assert "sh .ymer-ci/smoke.sh" in out["RELEASE.md"]
    github_only = package_files(
        {}, app="T", number=1, stack="node-cli", notes=None, when=when, ci=("github",)
    )
    assert GITHUB_PATH in github_only and GITLAB_PATH not in github_only


def test_fake_sandbox_checks_syntax_for_ci_steps():
    sandbox = FakeSandbox()
    good = {"app.py": "x = 1\n", "tests/test_app.py": "def test_x():\n    pass\n"}
    assert sandbox.run(good, step_command("lint"), image="i", timeout=5).ok
    assert sandbox.run(good, step_command("test"), image="i", timeout=5).ok
    bad = {**good, "app.py": "def broken(:\n"}
    result = sandbox.run(bad, step_command("smoke"), image="i", timeout=5)
    assert not result.ok and result.simulated and "app.py:1" in result.output


# --- the real scripts, run here (the tools the sandbox images carry) --------------------

BIN = str(Path(sys.executable).parent)


class LocalSandbox:
    """Runs commands in a temporary folder on this machine, for tests only."""

    name = "local"

    def run(self, files, command, *, image, timeout):
        with __import__("tempfile").TemporaryDirectory() as tmp:
            write_tree(Path(tmp), files)
            env = {**os.environ, "PATH": f"{BIN}:{os.environ.get('PATH', '')}"}
            done = subprocess.run(
                ["sh", "-c", command],
                cwd=tmp,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
        return ExecResult(done.returncode, done.stdout + done.stderr)


def _app_files(stack: str) -> dict[str, str]:
    files = task_files(stack, "Set up the project skeleton", "", setup=True, app="Demo")
    files |= task_files(stack, "Monthly totals", "Totals per month", setup=False, app="Demo")
    return files


def _check(stack: str, files: dict[str, str], key: str) -> ExecResult:
    snapshot = {**files, **ci_files(stack)}
    return LocalSandbox().run(snapshot, step_command(key), image="", timeout=120)


LOCAL = [
    pytest.param("python-cli", id="python-cli"),
    pytest.param(
        "static-web",
        id="static-web",
        marks=pytest.mark.skipif(not shutil.which("node"), reason="needs Node"),
    ),
    pytest.param(
        "node-cli",
        id="node-cli",
        marks=pytest.mark.skipif(not shutil.which("node"), reason="needs Node"),
    ),
    pytest.param("flask-react", id="flask-react"),
]


@pytest.mark.parametrize("stack", LOCAL)
def test_check_scripts_pass_on_a_working_app(stack):
    files = _app_files(stack)
    for key, _name in STEPS:
        result = _check(stack, files, key)
        assert result.ok, f"{key}:\n{result.output}"


def test_check_scripts_catch_real_problems():
    files = _app_files("python-cli")
    # An undefined name: lint fails (ruff), the rest still compile.
    broken = {**files, "src/app/extra.py": "def f():\n    return missing_name\n"}
    lint = _check("python-cli", broken, "lint")
    assert not lint.ok and "missing_name" in lint.output
    # A crash on start: the smoke run fails although the tests pass.
    crash = {**files, "src/app/cli.py": "def main(argv=None):\n    raise RuntimeError('boom')\n"}
    smoke = _check("python-cli", crash, "smoke")
    assert not smoke.ok and "boom" in smoke.output
    # No pyproject.toml: the build fails.
    no_project = {k: v for k, v in files.items() if k != "pyproject.toml"}
    assert not _check("python-cli", no_project, "build").ok


@pytest.mark.skipif(not shutil.which("node"), reason="needs Node")
def test_static_web_build_finds_missing_files():
    files = _app_files("static-web")
    files["index.html"] = files["index.html"].replace("</body>", '<img src="logo.png"></body>')
    result = _check("static-web", files, "build")
    assert not result.ok and "logo.png" in result.output


# --- API ----------------------------------------------------------------------------------


class FailingSmoke:
    """The fake sandbox, except that the smoke run fails until ``fixed`` is set."""

    name = "fake"

    def __init__(self):
        self.fake = FakeSandbox()
        self.fixed = False

    def run(self, files, command, *, image, timeout):
        if command == step_command("smoke") and not self.fixed:
            return ExecResult(1, "Traceback ...\nRuntimeError: boom on start", simulated=True)
        return self.fake.run(files, command, image=image, timeout=timeout)


def _build(app, client, team, stack="python-cli"):
    h = team.editor.headers
    _approved_brief(client, team)
    client.post(f"{team.url}/architecture", json={"stack": stack}, headers=h)
    client.post(f"{team.url}/approve-plan", headers=h)
    assert client.post(f"{team.url}/runs", headers=h).status_code == 202
    assert work_once(app) is True
    return client.get(team.url, headers=h).get_json()["factory"]


def _factory_id(team) -> uuid.UUID:
    return uuid.UUID(team.factory["id"])


def test_checks_run_after_a_build_and_fill_the_history_database(app, client, team):
    data = _build(app, client, team)
    checks = data["checks"]
    latest = checks["latest"]
    assert latest["number"] == 1 and latest["status"] == "passed", latest
    assert latest["trigger"] == "build" and latest["simulated"] is True
    assert [s["key"] for s in latest["steps"]] == ["lint", "build", "test", "smoke"]
    assert checks["pass_rate"] == 1.0 and checks["release_blocker"] is None
    with app.app_context():
        page = db_session.get(Page, uuid.UUID(checks["page_id"]))
        assert page.title == "Checks" and page.parent_id == _factory_id(team)
        assert db_session.query(DatabaseRow).filter_by(database_id=page.id).count() == 1
        views = db_session.query(DatabaseView).filter_by(database_id=page.id).all()
        assert ViewType.CHART in {v.type for v in views}
    # The history rows read like any database.
    rows = client.get(
        f"/api/workspaces/{team.ws.id}/databases/{checks['page_id']}/rows",
        headers=team.viewer.headers,
    )
    assert rows.status_code == 200
    # Checks passed on these files, so they can be released.
    assert client.post(f"{team.url}/releases", json={}, headers=team.editor.headers).status_code
    assert (
        client.post(f"{team.url}/releases", json={}, headers=team.editor.headers).status_code == 409
    )


def test_running_checks_by_hand(app, client, team):
    h = team.editor.headers
    assert client.post(f"{team.url}/checks", headers=h).status_code == 409  # nothing built
    _build(app, client, team)
    assert client.post(f"{team.url}/checks", headers=team.viewer.headers).status_code == 403
    resp = client.post(f"{team.url}/checks", headers=h)
    assert resp.status_code == 202 and resp.get_json()["run"]["status"] == "queued"
    assert client.post(f"{team.url}/checks", headers=h).status_code == 409  # already queued
    assert work_once(app) is True
    one = client.get(f"{team.url}/checks/2", headers=team.viewer.headers).get_json()["run"]
    assert one["status"] == "passed" and one["trigger"] == "manual"
    assert all("output" in s for s in one["steps"])
    listed = client.get(f"{team.url}/checks", headers=team.viewer.headers).get_json()
    assert [r["number"] for r in listed["runs"]] == [2, 1]
    assert "output" not in listed["runs"][0]["steps"][0]
    assert client.get(f"{team.url}/checks/9", headers=h).status_code == 404


def test_failed_checks_block_the_release_and_become_a_fix_task(app, client, team):
    sandbox = FailingSmoke()
    app.extensions["ymer_factory_sandbox"] = sandbox
    h = team.editor.headers
    data = _build(app, client, team)
    latest = data["checks"]["latest"]
    assert latest["status"] == "failed"
    assert [s["status"] for s in latest["steps"]] == ["passed", "passed", "passed", "failed"]
    assert "boom on start" in latest["steps"][3]["output"]
    assert latest["change"]["title"] == "Fix failing checks: Smoke run"

    # The release waits for the checks.
    blocker = data["checks"]["release_blocker"]
    assert "Smoke run" in blocker
    resp = client.post(f"{team.url}/releases", json={}, headers=h)
    assert resp.status_code == 409 and "checks failed" in resp.get_json()["message"]

    # The failure is a planned change request with a fix task that carries the log.
    change = next(c for c in data["changes"] if c["origin"] == "ci")
    assert change["status"] == "planned" and change["tasks"] == ["Fix failing checks (checks #1)"]
    assert data["stage"] == "planned"
    with app.app_context():
        tasks = read_tasks(db_session, uuid.UUID(data["tasks"]["id"]))
    fix = next(t for t in tasks if t["title"] == "Fix failing checks (checks #1)")
    assert fix["status"] == "To do" and "boom on start" in fix["detail"]
    assert fix["detail"].startswith(f"Change #{change['number']}.")

    # Checking again without a build refreshes the same request and task.
    client.post(f"{team.url}/checks", headers=h)
    work_once(app)
    data = client.get(team.url, headers=h).get_json()["factory"]
    assert [c["origin"] for c in data["changes"]].count("ci") == 1
    with app.app_context():
        titles = [t["title"] for t in read_tasks(db_session, uuid.UUID(data["tasks"]["id"]))]
    assert titles.count("Fix failing checks (checks #1)") == 1
    assert "Fix failing checks (checks #2)" not in titles

    # The next build works on the fix; its checks pass and the release goes through.
    sandbox.fixed = True
    assert client.post(f"{team.url}/runs", headers=h).status_code == 202
    work_once(app)
    data = client.get(team.url, headers=h).get_json()["factory"]
    assert data["checks"]["latest"]["status"] == "passed"
    assert data["checks"]["pass_rate"] == pytest.approx(1 / 3, abs=0.001)
    assert next(c for c in data["changes"] if c["origin"] == "ci")["status"] == "built"
    assert client.post(f"{team.url}/releases", json={}, headers=h).status_code == 201


def test_auto_fix_builds_until_its_limit(app, client, team):
    app.extensions["ymer_factory_sandbox"] = FailingSmoke()  # never fixed
    h = team.editor.headers
    url = f"{team.url}/checks/settings"
    assert client.put(url, json={"auto_fix": True}, headers=team.viewer.headers).status_code == 403
    assert client.put(url, json={"auto_fix": True}, headers=h).get_json()["factory"]["checks"][
        "auto_fix"
    ]
    data = _build(app, client, team)
    with app.app_context():
        builds = db_session.query(FactoryRun).filter_by(factory_id=_factory_id(team)).count()
        runs = (
            db_session.query(FactoryCiRun)
            .filter_by(factory_id=_factory_id(team))
            .order_by(FactoryCiRun.number)
            .all()
        )
        assert builds == 4  # the first build and three fix builds
        assert [r.status for r in runs] == ["failed"] * 4
        assert all(r.fix_run_id for r in runs[:3]) and runs[3].fix_run_id is None
        fix_build = db_session.get(FactoryRun, runs[0].fix_run_id)
        assert fix_build.context["auto_fix"] == 1 and fix_build.status == "passed"
    assert "Auto-fix paused after 3 tries" in data["checks"]["latest"]["summary"]
    assert data["checks"]["fix_streak"] == 3
    assert work_once(app) is False  # nothing left queued
    # Turning auto-fix on again resets its tries.
    data = client.put(url, json={"auto_fix": True}, headers=h).get_json()["factory"]
    assert data["checks"]["fix_streak"] == 0


def test_checks_see_exactly_the_files_released(app, client, team):
    h = team.editor.headers
    _build(app, client, team)
    with app.app_context():
        row = db_session.get(FactoryFile, (_factory_id(team), "src/app/cli.py"))
        row.content += "# changed after the checks\n"
        db_session.commit()
    data = client.get(team.url, headers=h).get_json()["factory"]
    assert "Run the checks" in data["checks"]["release_blocker"]


def test_the_gate_can_be_turned_off(app, client, team):
    app.extensions["ymer_factory_sandbox"] = FailingSmoke()
    _build(app, client, team)
    app.extensions["ymer_settings"].factory_ci_gate = False
    resp = client.post(f"{team.url}/releases", json={}, headers=team.editor.headers)
    assert resp.status_code == 201
