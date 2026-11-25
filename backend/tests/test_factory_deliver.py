# ruff: noqa: F811  (the team fixture is imported from test_factory)
"""Factory stage F4: releases, the zip download, previews and pushes to git."""

import io
import subprocess
import uuid
import zipfile
from datetime import UTC, datetime

import pytest

from tests.test_factory import _approved_brief, team  # noqa: F401 (fixture)
from ymer.db import db_session
from ymer.factory.git import check_branch, check_remote
from ymer.factory.package import changes_between, package_files, zip_bytes
from ymer.factory.runner import work_once
from ymer.models import FactoryFile

# --- pure helpers -------------------------------------------------------------------------


def test_packaging_adds_standard_files_without_overwriting():
    when = datetime(2026, 11, 25, tzinfo=UTC)
    files = {"src/app/cli.py": "x", ".gitignore": "mine\n"}
    out = package_files(files, app="Tool", number=2, stack="python-cli", notes="Faster.", when=when)
    assert out[".gitignore"] == "mine\n"  # the build's own file wins
    assert "release 2" in out["RELEASE.md"] and "Faster." in out["RELEASE.md"]
    assert "pip install" in out["Dockerfile"]
    web = package_files(
        {"index.html": ""}, app="T", number=1, stack="static-web", notes=None, when=when
    )
    assert "Dockerfile" not in web and "RELEASE.md" in web


def test_zip_is_stable_and_in_one_folder():
    a = zip_bytes({"b.txt": "2", "a/c.txt": "1"}, "app-release-1")
    assert a == zip_bytes({"a/c.txt": "1", "b.txt": "2"}, "app-release-1")
    names = zipfile.ZipFile(io.BytesIO(a)).namelist()
    assert names == ["app-release-1/a/c.txt", "app-release-1/b.txt"]


def test_changes_between_releases():
    assert changes_between({"a": "1", "b": "2"}, {"a": "1", "b": "3", "c": "4"}) == {
        "added": ["c"],
        "changed": ["b"],
        "removed": [],
    }
    assert changes_between(None, {"a": ""})["added"] == ["a"]


@pytest.mark.parametrize(
    "remote",
    [
        "http://github.com/a/b.git",
        "https://evil.example/a/b.git",
        "https://user:secret@github.com/a/b.git",
        "https://github.com/",
        "file:///tmp/x",
        "ssh://git@github.com/a/b.git",
    ],
)
def test_remotes_must_be_https_on_an_allowed_host(remote):
    with pytest.raises(ValueError):
        check_remote(remote, ["github.com"], allow_local=False)


def test_allowed_remotes_and_branches():
    assert check_remote(" https://GitHub.com/a/b.git ", ["github.com"], False).endswith("b.git")
    assert check_remote("file:///tmp/x", [], allow_local=True) == "file:///tmp/x"
    assert check_branch("release/v1") == "release/v1"
    for bad in ["-x", "a..b", "a b", "x.lock", "a/", "", "@{u}"]:
        with pytest.raises(ValueError):
            check_branch(bad)


# --- API ----------------------------------------------------------------------------------


def _built(app, client, team, stack):
    h = team.editor.headers
    _approved_brief(client, team)
    client.post(f"{team.url}/architecture", json={"stack": stack}, headers=h)
    client.post(f"{team.url}/approve-plan", headers=h)
    client.post(f"{team.url}/runs", headers=h)
    work_once(app)
    data = client.get(team.url, headers=h).get_json()["factory"]
    assert data["build"]["latest_run"]["status"] == "passed"
    return data


def test_a_release_needs_a_passed_build(client, team):
    resp = client.post(f"{team.url}/releases", json={}, headers=team.editor.headers)
    assert resp.status_code == 409 and "passed" in resp.get_json()["message"]


def test_release_download_and_changes(app, client, team):
    built = _built(app, client, team, "python-cli")
    assert built["delivery"]["latest_release"] is None
    assert built["delivery"]["unreleased"]["added"]
    h = team.editor.headers
    assert (
        client.post(f"{team.url}/releases", json={}, headers=team.viewer.headers).status_code == 403
    )

    resp = client.post(f"{team.url}/releases", json={"notes": "First cut."}, headers=h)
    assert resp.status_code == 201
    rel = resp.get_json()["release"]
    factory = resp.get_json()["factory"]
    assert rel["number"] == 1 and rel["notes"] == "First cut." and rel["preview"] is False
    assert rel["changes"]["added"] and not rel["changes"]["changed"]
    assert factory["stage"] == "released"
    assert factory["delivery"]["unreleased"] == {"added": [], "changed": [], "removed": []}
    # Nothing changed, so nothing to release.
    assert client.post(f"{team.url}/releases", json={}, headers=h).status_code == 409

    zipped = client.get(f"{team.url}/releases/1/download", headers=team.viewer.headers)
    assert zipped.status_code == 200 and zipped.mimetype == "application/zip"
    assert "expense-app-release-1.zip" in zipped.headers["Content-Disposition"]
    names = zipfile.ZipFile(io.BytesIO(zipped.data)).namelist()
    assert "expense-app-release-1/src/app/cli.py" in names
    assert "expense-app-release-1/RELEASE.md" in names
    assert "expense-app-release-1/Dockerfile" in names
    assert client.get(f"{team.url}/releases/9/download", headers=h).status_code == 404

    # A later change becomes release 2, which lists what changed.
    with app.app_context():
        row = db_session.get(FactoryFile, (uuid.UUID(team.factory["id"]), "src/app/cli.py"))
        row.content += "# tweak\n"
        db_session.commit()
    pending = client.get(team.url, headers=h).get_json()["factory"]["delivery"]["unreleased"]
    assert pending["changed"] == ["src/app/cli.py"]
    second = client.post(f"{team.url}/releases", json={}, headers=h).get_json()["release"]
    assert second["number"] == 2 and second["changes"]["changed"] == ["src/app/cli.py"]
    listed = client.get(f"{team.url}/releases", headers=team.viewer.headers).get_json()["releases"]
    assert [r["number"] for r in listed] == [2, 1]

    # Released factories can still be built again and reopened.
    assert client.post(f"{team.url}/runs", headers=h).status_code == 202
    work_once(app)
    assert (
        client.post(f"{team.url}/reopen", headers=h).get_json()["factory"]["stage"]
        == "architecture"
    )


def test_preview_serves_a_web_app_in_a_sandbox(app, client, team):
    _built(app, client, team, "static-web")
    h = team.editor.headers
    client.post(f"{team.url}/releases", json={}, headers=h)
    link = client.post(f"{team.url}/releases/1/preview", headers=team.viewer.headers).get_json()
    assert link["url"].startswith("/api/factory-preview/") and link["url"].endswith("/index.html")

    page = client.get(link["url"])  # no login header: the link is the credential
    assert page.status_code == 200 and page.mimetype == "text/html"
    csp = page.headers["Content-Security-Policy"]
    assert csp.startswith("sandbox allow-scripts") and "allow-same-origin" not in csp
    assert "connect-src 'none'" in csp
    assert page.headers["X-Content-Type-Options"] == "nosniff"
    assert b"src/main.mjs" in page.data
    script = client.get(link["url"].replace("index.html", "src/main.mjs"))
    assert script.status_code == 200 and script.mimetype == "text/javascript"
    assert client.get(link["url"].replace("index.html", "nope.js")).status_code == 404
    assert client.get("/api/factory-preview/forged/index.html").status_code == 404

    # The link stops working when its creator loses access to the factory.
    with app.app_context():
        from ymer.models import WorkspaceMember

        db_session.query(WorkspaceMember).filter_by(user_id=team.viewer.id).delete()
        db_session.commit()
    assert client.get(link["url"]).status_code == 404


def test_preview_is_only_for_web_apps(app, client, team):
    _built(app, client, team, "python-cli")
    client.post(f"{team.url}/releases", json={}, headers=team.editor.headers)
    resp = client.post(f"{team.url}/releases/1/preview", headers=team.editor.headers)
    assert resp.status_code == 422


def test_push_a_release_to_a_git_repository(app, client, team, tmp_path):
    _built(app, client, team, "python-cli")
    h = team.editor.headers
    client.post(f"{team.url}/releases", json={"notes": "Hello."}, headers=h)
    url = f"{team.url}/releases/1/push"

    # Only allowed hosts, unless local remotes are turned on.
    resp = client.post(url, json={"remote": "https://evil.example/x.git"}, headers=h)
    assert resp.status_code == 422 and "not allowed" in resp.get_json()["message"]
    assert client.post(url, json={"remote": "file:///x"}, headers=h).status_code == 422
    assert (
        client.post(
            url, json={"remote": "https://github.com/a/b.git"}, headers=team.viewer.headers
        ).status_code
        == 403
    )

    app.extensions["ymer_settings"].factory_git_allow_local = True
    bare = tmp_path / "app.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    remote = f"file://{bare}"
    first = client.post(url, json={"remote": remote, "branch": "release"}, headers=h)
    assert first.status_code == 200, first.get_json()
    pushed = first.get_json()["push"]
    assert pushed["status"] == "pushed" and len(pushed["commit_sha"]) == 40
    log = subprocess.run(
        ["git", "--git-dir", str(bare), "log", "--format=%an|%s", "release"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert log.strip() == "Ymer Factory|Expense app: release 1"
    tree = subprocess.run(
        ["git", "--git-dir", str(bare), "ls-tree", "-r", "--name-only", "release"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert "src/app/cli.py" in tree and "RELEASE.md" in tree and "Dockerfile" in tree

    again = client.post(url, json={"remote": remote, "branch": "release"}, headers=h).get_json()
    assert again["push"]["status"] == "unchanged"
    assert again["push"]["commit_sha"] == pushed["commit_sha"]
    assert [p["status"] for p in again["release"]["pushes"]] == ["unchanged", "pushed"]
    delivery = client.get(team.url, headers=h).get_json()["factory"]["delivery"]
    assert delivery["git_remote"] == remote and delivery["git_branch"] == "release"

    missing = client.post(
        url, json={"remote": f"file://{tmp_path}/missing.git", "token": "s3cret-token"}, headers=h
    )
    assert missing.status_code == 502
    error = missing.get_json()["push"]["error"]
    assert error and "s3cret-token" not in error
