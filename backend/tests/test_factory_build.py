# ruff: noqa: F811  (the team fixture is imported from test_factory)
"""Factory stage F3: the sandbox, the build agent's tools, and build runs end to end.

Runs use the offline demo model and the fake sandbox, so nothing is executed. The
Docker sandbox is checked through the command line it builds and a stand-in docker.
"""

import stat
import uuid

import pytest

from tests.test_factory import _approved_brief, team  # noqa: F401 (fixture)
from ymer.db import db_session
from ymer.factory import build as build_mod
from ymer.factory.agent import AgentTurn, ToolCall, validate_input
from ymer.factory.build import read_tasks
from ymer.factory.runner import run_now, work_once
from ymer.factory.sandbox import ExecResult, clip
from ymer.factory.sandbox.docker import DockerSandbox, safe_path
from ymer.factory.sandbox.fake import FakeSandbox
from ymer.models import FactoryFile, FactoryRun, FactoryRunEvent

# --- sandbox ------------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["", "/etc/passwd", "../x", "a/../../b", "a\x00b", ".git/config"])
def test_unsafe_paths_are_refused(path):
    with pytest.raises(ValueError):
        safe_path(path)


def test_safe_paths_pass():
    assert str(safe_path("src/app/cli.py")) == "src/app/cli.py"


def test_clip_keeps_head_and_tail():
    text = "a" * 50 + "b" * 50
    cut = clip(text, 20)
    assert cut.startswith("a" * 10) and cut.endswith("b" * 10) and "cut" in cut


def test_fake_sandbox_checks_without_running():
    sandbox = FakeSandbox()
    ok = sandbox.run(
        {
            "src/app/x.py": "def f():\n    return 1\n",
            "tests/test_x.py": "def test_a():\n    pass\n",
        },
        "pytest",
        image="i",
        timeout=5,
    )
    assert ok.ok and ok.simulated and "1 passed" in ok.output
    broken = sandbox.run({"tests/test_x.py": "def test_a(:\n"}, "pytest", image="i", timeout=5)
    assert broken.exit_code == 1 and "SyntaxError" in broken.output
    empty = sandbox.run({"src/a.py": "x = 1\n"}, "pytest", image="i", timeout=5)
    assert not empty.ok and "no tests" in empty.output
    js = sandbox.run(
        {"tests/a.test.mjs": "test('a', () => {}); test('b', () => {});"},
        "node --test",
        image="i",
        timeout=5,
    )
    assert js.ok and "2 passed" in js.output


def test_docker_command_is_locked_down():
    line = DockerSandbox(memory="512m", cpus="0.5").command_line("/tmp/w", "img:1", "pytest", "n")
    joined = " ".join(line)
    for flag in (
        "--rm",
        "--network none",
        "--read-only",
        "--cap-drop ALL",
        "--security-opt no-new-privileges",
        "--memory 512m",
        "--cpus 0.5",
        "--pids-limit 256",
        "--user 65534:65534",
        "--volume /tmp/w:/work:rw",
    ):
        assert flag in joined
    assert line[-4:] == ["img:1", "sh", "-c", "pytest"]


def test_docker_sandbox_copies_files_and_reports_the_exit_code(tmp_path):
    # A stand-in for the docker binary: lists the mounted work directory and fails.
    fake = tmp_path / "docker"
    fake.write_text(
        "#!/bin/sh\n"
        'while [ "$1" != "--volume" ]; do shift; done\n'
        'dir="${2%%:*}"\n'
        'cd "$dir" && find . -type f | sort && cat src/a.py\n'
        "exit 3\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    result = DockerSandbox(docker_bin=str(fake)).run(
        {"src/a.py": "print('hi')\n", "README.md": "x"}, "true", image="i", timeout=10
    )
    assert result.exit_code == 3 and not result.ok
    assert "./README.md" in result.output and "./src/a.py" in result.output
    assert "print('hi')" in result.output


def test_docker_sandbox_refuses_unsafe_files(tmp_path):
    fake = tmp_path / "docker"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    with pytest.raises(ValueError):
        DockerSandbox(docker_bin=str(fake)).run({"../x": ""}, "true", image="i", timeout=5)


# --- agent tools -------------------------------------------------------------------------


def test_tool_inputs_are_validated():
    assert validate_input("write_file", {"path": "a.py", "content": "x"}) is None
    assert "Missing" in validate_input("write_file", {"path": "a.py"})
    assert "string" in validate_input("read_file", {"path": 3})
    assert "Unknown field" in validate_input("list_files", {"x": 1})
    assert "one of" in validate_input("finish_task", {"outcome": "maybe", "summary": ""})
    assert "no tool" in validate_input("rm_rf", {})


# --- runs --------------------------------------------------------------------------------


def _planned(client, team):
    h = team.editor.headers
    _approved_brief(client, team)
    client.post(f"{team.url}/architecture", json={"stack": "python-cli"}, headers=h)
    return client.post(f"{team.url}/approve-plan", headers=h).get_json()["factory"]


def _events(client, team, run_id, after=0):
    return client.get(
        f"{team.url}/runs/{run_id}?after={after}", headers=team.viewer.headers
    ).get_json()


def test_a_build_needs_an_approved_plan(client, team):
    resp = client.post(f"{team.url}/runs", headers=team.editor.headers)
    assert resp.status_code == 409 and "Approve the architecture" in resp.get_json()["message"]


def test_a_build_runs_every_task_and_writes_files(app, client, team):
    planned = _planned(client, team)
    assert planned["build"] == {"latest_run": None, "file_count": 0, "sandbox": "fake"}
    assert client.post(f"{team.url}/runs", headers=team.viewer.headers).status_code == 403

    resp = client.post(f"{team.url}/runs", headers=team.editor.headers)
    assert resp.status_code == 202
    run = resp.get_json()["run"]
    assert run["status"] == "queued" and run["sandbox"] == "fake"
    assert run["tasks_total"] == planned["tasks"]["count"]
    # One build at a time.
    assert client.post(f"{team.url}/runs", headers=team.editor.headers).status_code == 409
    assert client.post(f"{team.url}/reopen", headers=team.editor.headers).status_code == 409

    assert work_once(app) is True
    assert work_once(app) is False

    data = _events(client, team, run["id"])
    assert data["run"]["status"] == "passed", data["run"]["error"]
    assert data["run"]["tasks_done"] == run["tasks_total"]
    assert "checked, not run" in data["run"]["summary"]
    assert data["stage"] == "built"
    kinds = [e["kind"] for e in data["events"]]
    assert kinds[0] == "start" and kinds[-1] == "finish"
    assert kinds.count("task") == run["tasks_total"] == kinds.count("done")
    assert "tests" in kinds and "file" in kinds
    seqs = [e["seq"] for e in data["events"]]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    later = _events(client, team, run["id"], after=seqs[-2])["events"]
    assert [e["seq"] for e in later] == [seqs[-1]]

    # Every task is Done in the Tasks database.
    with app.app_context():
        tasks = read_tasks(db_session, uuid.UUID(planned["tasks"]["id"]))
        assert {t["status"] for t in tasks} == {"Done"}

    files = client.get(f"{team.url}/files", headers=team.viewer.headers).get_json()["files"]
    paths = [f["path"] for f in files]
    assert "pyproject.toml" in paths and "src/app/cli.py" in paths
    assert any(p.startswith("tests/test_") for p in paths)
    content = client.get(
        f"{team.url}/files/content",
        query_string={"path": "src/app/cli.py"},
        headers=team.viewer.headers,
    ).get_json()
    assert "def main" in content["content"]
    assert (
        client.get(
            f"{team.url}/files/content", query_string={"path": "nope"}, headers=team.viewer.headers
        ).status_code
        == 404
    )

    factory = client.get(team.url, headers=team.viewer.headers).get_json()["factory"]
    assert factory["build"]["latest_run"]["status"] == "passed"
    assert factory["build"]["file_count"] == len(files)

    # A second build has nothing left to do and passes; reopening the plan keeps the files.
    second = client.post(f"{team.url}/runs", headers=team.editor.headers).get_json()["run"]
    assert second["tasks_done"] == second["tasks_total"]
    work_once(app)
    assert _events(client, team, second["id"])["run"]["status"] == "passed"
    runs = client.get(f"{team.url}/runs", headers=team.viewer.headers).get_json()["runs"]
    assert [r["id"] for r in runs] == [second["id"], run["id"]]
    back = client.post(f"{team.url}/reopen", headers=team.editor.headers).get_json()["factory"]
    assert back["stage"] == "architecture" and back["build"]["file_count"] == len(files)


def test_a_queued_build_stops_at_once(app, client, team):
    _planned(client, team)
    run = client.post(f"{team.url}/runs", headers=team.editor.headers).get_json()["run"]
    assert (
        client.post(f"{team.url}/runs/{run['id']}/stop", headers=team.viewer.headers).status_code
        == 403
    )
    stopped = client.post(
        f"{team.url}/runs/{run['id']}/stop", headers=team.editor.headers
    ).get_json()["run"]
    assert stopped["status"] == "stopped"
    assert work_once(app) is False


def test_stopping_twice_forces_a_stuck_build_to_stop(app, client, team):
    _planned(client, team)
    run = client.post(f"{team.url}/runs", headers=team.editor.headers).get_json()["run"]
    with app.app_context():
        db_session.get(FactoryRun, uuid.UUID(run["id"])).status = "running"
        db_session.commit()
    url = f"{team.url}/runs/{run['id']}/stop"
    assert client.post(url, headers=team.editor.headers).get_json()["run"]["status"] == "stopping"
    assert client.post(url, headers=team.editor.headers).get_json()["run"]["status"] == "stopped"
    assert client.post(f"{team.url}/runs", headers=team.editor.headers).status_code == 202


def test_a_running_build_stops_between_steps(app, client, team):
    planned = _planned(client, team)
    run = client.post(f"{team.url}/runs", headers=team.editor.headers).get_json()["run"]
    with app.app_context():
        row = db_session.get(FactoryRun, uuid.UUID(run["id"]))
        row.status = "stopping"
        db_session.commit()
    run_now(app, uuid.UUID(run["id"]))
    data = _events(client, team, run["id"])
    assert data["run"]["status"] == "stopped" and data["stage"] == "planned"
    with app.app_context():
        tasks = read_tasks(db_session, uuid.UUID(planned["tasks"]["id"]))
        assert {t["status"] for t in tasks} == {"To do"}


class FailingSandbox:
    name = "failing"

    def run(self, files, command, *, image, timeout):
        return ExecResult(1, "1 failed")


def test_failing_tests_stop_the_build_at_that_task(app, client, team, monkeypatch):
    planned = _planned(client, team)
    app.extensions["ymer_factory_sandbox"] = FailingSandbox()
    run = client.post(f"{team.url}/runs", headers=team.editor.headers).get_json()["run"]
    work_once(app)
    data = _events(client, team, run["id"])
    assert data["run"]["status"] == "failed" and data["stage"] == "planned"
    assert "Stopped at" in data["run"]["error"]
    assert data["run"]["tasks_done"] == 0
    assert any(e["kind"] == "blocked" for e in data["events"])
    with app.app_context():
        tasks = read_tasks(db_session, uuid.UUID(planned["tasks"]["id"]))
        assert tasks[0]["status"] == "To do"
        # Files written before the failure are kept for the next attempt.
        assert db_session.query(FactoryFile).count() > 0


def test_the_step_budget_stops_a_build(app, client, team):
    _planned(client, team)
    app.extensions["ymer_settings"].factory_max_steps = 3
    run = client.post(f"{team.url}/runs", headers=team.editor.headers).get_json()["run"]
    work_once(app)
    data = _events(client, team, run["id"])
    assert data["run"]["status"] == "failed" and "3 steps" in data["run"]["error"]


class ScriptedAgent:
    """Says it is done before testing, then tests, then finishes."""

    def __init__(self):
        self.turns = [
            [
                ToolCall(
                    "1",
                    "write_file",
                    {"path": "tests/test_a.py", "content": "def test_a(): pass\n"},
                )
            ],
            [ToolCall("2", "finish_task", {"outcome": "done", "summary": "early"})],
            [ToolCall("3", "write_file", {"path": "../escape.py", "content": "x"})],
            [ToolCall("4", "run_tests", {})],
            [ToolCall("5", "finish_task", {"outcome": "done", "summary": "tested"})],
        ]
        self.results = []

    def step(self):
        return AgentTurn("", self.turns.pop(0), "tool_use", 10)

    def add_results(self, results):
        self.results.append(results)

    def nudge(self, text):
        pass


def test_finishing_needs_passing_tests_and_paths_stay_inside(app, client, team, monkeypatch):
    _planned(client, team)
    agents = []

    def start(chat, **kwargs):
        agents.append(ScriptedAgent())
        return agents[-1]

    monkeypatch.setattr(build_mod, "start_session", start)
    run = client.post(f"{team.url}/runs", headers=team.editor.headers).get_json()["run"]
    work_once(app)
    assert _events(client, team, run["id"])["run"]["status"] == "passed"
    first = agents[0].results
    assert first[1][0].is_error and "have not passed" in first[1][0].content
    assert first[2][0].is_error and "safe" in first[2][0].content
    with app.app_context():
        assert db_session.get(FactoryFile, (uuid.UUID(team.factory["id"]), "../escape.py")) is None
        assert db_session.query(FactoryRunEvent).count() > 0
