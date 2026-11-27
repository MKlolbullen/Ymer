# ruff: noqa: F811  (the team fixture is imported from test_factory)
"""Checks against real services instead of stand-ins.

The rest of the suite uses fakes for object storage, the build sandbox and git. These
tests use the real thing, and skip themselves when it is not there:

- **MinIO / S3** (``docker compose up -d``): upload through the API, download through
  the presigned URL, delete.
- **Docker** with the sandbox images (``make factory-images``): a full Factory build on
  every stack, with the tests really running in locked-down containers.
- **git over HTTPS**: a local smart-HTTP server (``git http-backend`` behind TLS with a
  self-signed certificate and token auth). The release is pushed to it exactly as it
  would be to GitHub or GitLab, then cloned back.

Run only these with ``pytest -m live``.
"""

import base64
import os
import shutil
import ssl
import subprocess
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from botocore.exceptions import BotoCoreError, ClientError

from tests.factories import make_page, make_user, make_workspace
from tests.test_factory import _approved_brief, team  # noqa: F401 (fixture)
from ymer.app import settings_for
from ymer.extensions import get_s3
from ymer.factory.runner import work_once
from ymer.factory.sandbox.docker import DockerSandbox
from ymer.factory.stacks import STACKS

pytestmark = [pytest.mark.db, pytest.mark.live]


def set_settings(app, **changes):
    app.extensions["ymer_settings"] = app.extensions["ymer_settings"].model_copy(update=changes)


# --- object storage ------------------------------------------------------------------------


@pytest.fixture
def real_s3(app):
    with app.app_context():
        try:
            get_s3().head_bucket(Bucket=settings_for(app).s3_bucket)
        except (BotoCoreError, ClientError) as exc:
            pytest.skip(f"S3 not reachable ({exc.__class__.__name__}); run docker compose up -d")


def test_files_round_trip_through_real_object_storage(app, client, db, real_s3):
    owner = make_user(app, "owner")
    ws = make_workspace(owner, {})
    page = make_page(ws, "Doc")
    url = f"/api/workspaces/{ws.id}/pages/{page.id}/files"
    payload = os.urandom(6 * 1024 * 1024) + b"end"  # above boto's multipart threshold
    from io import BytesIO

    resp = client.post(
        url,
        data={"file": (BytesIO(payload), "big.bin", "application/octet-stream")},
        headers=owner.headers,
        content_type="multipart/form-data",
    )
    assert resp.status_code == 201, resp.get_json()
    file = resp.get_json()["file"]
    with urllib.request.urlopen(file["url"], timeout=30) as download:  # noqa: S310
        assert download.read() == payload
        assert "attachment" in download.headers["Content-Disposition"]

    with app.app_context():
        bucket = settings_for(app).s3_bucket
        key = f"workspaces/{ws.id}/files/{file['id']}"
        assert get_s3().head_object(Bucket=bucket, Key=key)["ContentLength"] == len(payload)
        assert client.delete(f"{url}/{file['id']}", headers=owner.headers).status_code == 204
        with pytest.raises(ClientError):
            get_s3().head_object(Bucket=bucket, Key=key)


def test_storage_outage_is_a_clean_error(app, client, db, real_s3):
    set_settings(app, s3_endpoint_url="http://127.0.0.1:9", s3_timeout=1.0)
    from ymer.extensions import init_s3

    init_s3(app, settings_for(app))
    owner = make_user(app, "owner")
    ws = make_workspace(owner, {})
    page = make_page(ws, "Doc")
    from io import BytesIO

    resp = client.post(
        f"/api/workspaces/{ws.id}/pages/{page.id}/files",
        data={"file": (BytesIO(b"hello"), "a.txt", "text/plain")},
        headers=owner.headers,
        content_type="multipart/form-data",
    )
    assert resp.status_code == 502
    assert "storage" in resp.get_json()["message"]


# --- a git server over HTTPS ---------------------------------------------------------------

GIT_TOKEN = "test-push-token-123"


class _GitHandler(BaseHTTPRequestHandler):
    """``git http-backend`` as CGI, behind HTTP basic auth that checks the token."""

    root: Path

    def log_message(self, *args):  # quiet
        pass

    def _authorized(self) -> bool:
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False
        _user, _, password = base64.b64decode(header[6:]).decode().partition(":")
        return password == GIT_TOKEN

    def _body(self) -> bytes:
        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            out = b""
            while True:
                size = int(self.rfile.readline().strip(), 16)
                if size == 0:
                    self.rfile.readline()
                    return out
                out += self.rfile.read(size)
                self.rfile.readline()
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def _serve(self) -> None:
        if not self._authorized():
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="git"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        path, _, query = self.path.partition("?")
        body = self._body() if self.command == "POST" else b""
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "GIT_PROJECT_ROOT": str(self.root),
            "GIT_HTTP_EXPORT_ALL": "1",
            "REMOTE_USER": "pusher",
            "REQUEST_METHOD": self.command,
            "PATH_INFO": path,
            "QUERY_STRING": query,
            "CONTENT_TYPE": self.headers.get("Content-Type", ""),
            "CONTENT_LENGTH": str(len(body)),
            "HTTP_CONTENT_ENCODING": self.headers.get("Content-Encoding", ""),
            "GIT_PROTOCOL": self.headers.get("Git-Protocol", ""),
        }
        done = subprocess.run(
            ["git", "http-backend"], input=body, env=env, capture_output=True, check=False
        )
        head, _, payload = done.stdout.partition(b"\r\n\r\n")
        status, headers = 200, []
        for line in head.decode().split("\r\n"):
            name, _, value = line.partition(":")
            if name.lower() == "status":
                status = int(value.strip().split()[0])
            elif name:
                headers.append((name, value.strip()))
        self.send_response(status)
        for name, value in headers:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = _serve
    do_POST = _serve


@pytest.fixture
def git_server(tmp_path):
    if shutil.which("git") is None or shutil.which("openssl") is None:
        pytest.skip("git and openssl are needed for the HTTPS git server")
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
            "-keyout", str(key), "-out", str(cert), "-subj", "/CN=localhost",
            "-addext", "subjectAltName=DNS:localhost",
        ],
        check=True,
        capture_output=True,
    )  # fmt: skip
    root = tmp_path / "repos"
    subprocess.run(["git", "init", "-q", "--bare", str(root / "app.git")], check=True)
    subprocess.run(
        ["git", "-C", str(root / "app.git"), "config", "http.receivepack", "true"], check=True
    )
    handler = type("Handler", (_GitHandler,), {"root": root})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield type(
            "Git",
            (),
            {
                "url": f"https://localhost:{server.server_address[1]}/app.git",
                "cert": str(cert),
                "bare": root / "app.git",
            },
        )
    finally:
        server.shutdown()


def _clone(git_server, dest: Path, branch: str) -> Path:
    subprocess.run(
        ["git", "clone", "-q", "--branch", branch, str(git_server.bare), str(dest)], check=True
    )
    return dest


# --- a whole Factory build in Docker, released and pushed ----------------------------------


@pytest.fixture
def docker_images():
    if shutil.which("docker") is None:
        pytest.skip("Docker is not installed")
    if subprocess.run(["docker", "info"], capture_output=True, check=False).returncode != 0:
        pytest.skip("The Docker daemon is not running")
    missing = [
        s.image
        for s in STACKS.values()
        if subprocess.run(
            ["docker", "image", "inspect", s.image], capture_output=True, check=False
        ).returncode
        != 0
    ]
    if missing:
        pytest.skip(f"Sandbox images missing (make factory-images): {', '.join(missing)}")


@pytest.mark.parametrize("stack", sorted(STACKS))
def test_build_in_docker_then_push_over_https(app, client, team, docker_images, git_server, stack):
    app.extensions["ymer_factory_sandbox"] = DockerSandbox()
    set_settings(
        app,
        factory_git_hosts="localhost",
        factory_git_ca_bundle=git_server.cert,
        factory_command_timeout=180,
    )
    h = team.editor.headers
    _approved_brief(client, team)
    client.post(f"{team.url}/architecture", json={"stack": stack}, headers=h)
    client.post(f"{team.url}/approve-plan", headers=h)
    run = client.post(f"{team.url}/runs", headers=h).get_json()["run"]
    assert run["sandbox"] == "docker"
    assert work_once(app) is True
    data = client.get(f"{team.url}/runs/{run['id']}", headers=h).get_json()
    assert data["run"]["status"] == "passed", (data["run"]["error"], data["events"][-3:])
    tests = [e for e in data["events"] if e["kind"] == "tests"]
    assert tests, "the build never ran the tests"
    # Real output from the container, not the demo sandbox's "checked, not run".
    assert all("nothing was run" not in (e["detail"] or "") for e in tests)
    assert "checked, not run" not in (data["run"]["summary"] or "")
    # The checks ran for real after the build: lint, build, tests and smoke all pass.
    checks = client.get(team.url, headers=h).get_json()["factory"]["checks"]["latest"]
    assert checks["status"] == "passed", [(s["key"], s["output"]) for s in checks["steps"]]
    assert checks["simulated"] is False and len(checks["steps"]) == 4

    assert client.post(f"{team.url}/releases", json={}, headers=h).status_code == 201

    # A wrong token is refused by the server, and the token never shows in the error.
    bad = client.post(
        f"{team.url}/releases/1/push",
        json={"remote": git_server.url, "branch": "main", "token": "wrong-token-456"},
        headers=h,
    )
    assert bad.status_code == 502
    assert "wrong-token-456" not in str(bad.get_json())

    resp = client.post(
        f"{team.url}/releases/1/push",
        json={"remote": git_server.url, "branch": "main", "token": GIT_TOKEN},
        headers=h,
    )
    assert resp.status_code == 200, resp.get_json()
    push = resp.get_json()["push"]
    assert push["status"] == "pushed" and len(push["commit_sha"]) == 40
    again = client.post(
        f"{team.url}/releases/1/push",
        json={"remote": git_server.url, "branch": "main", "token": GIT_TOKEN},
        headers=h,
    ).get_json()["push"]
    assert again["status"] == "unchanged" and again["commit_sha"] == push["commit_sha"]

    checkout = _clone(git_server, Path(git_server.bare).parent / "checkout", "main")
    head = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"], capture_output=True, text=True
    )
    assert head.stdout.strip() == push["commit_sha"]
    assert (checkout / "RELEASE.md").exists() and (checkout / ".gitignore").exists()
    # The team's CI gets the same check scripts, and a workflow for each CI system
    # (this test server is neither GitHub nor GitLab).
    assert (checkout / ".ymer-ci" / "smoke.sh").exists()
    assert (checkout / ".github/workflows/ymer-checks.yml").exists()
    assert (checkout / ".gitlab-ci.yml").exists()


def test_push_without_the_ca_bundle_is_refused(git_server):
    """Without trusting the server's certificate git refuses to talk to it at all."""
    from ymer.factory.git import GitError, push_files

    with pytest.raises(GitError) as err:
        push_files(
            {"README.md": "hi\n"},
            remote=git_server.url,
            branch="main",
            token=GIT_TOKEN,
            message="x",
            author=("A", "a@example.com"),
            timeout=30,
        )
    assert "certificate" in str(err.value).lower() or "ssl" in str(err.value).lower()
    assert GIT_TOKEN not in str(err.value)


def test_pushes_build_on_newer_commits_instead_of_forcing(app, git_server, tmp_path):
    from ymer.factory.git import push_files

    def push(files):
        return push_files(
            files,
            remote=git_server.url,
            branch="main",
            token=GIT_TOKEN,
            message="release",
            author=("A", "a@example.com"),
            timeout=30,
            ca_bundle=git_server.cert,
        )

    first = push({"a.txt": "1\n"})
    # Someone else pushes on top.
    other = _clone(git_server, tmp_path / "other", "main")
    (other / "b.txt").write_text("theirs\n")
    env = {**os.environ, "GIT_AUTHOR_NAME": "B", "GIT_AUTHOR_EMAIL": "b@example.com"}
    env |= {"GIT_COMMITTER_NAME": "B", "GIT_COMMITTER_EMAIL": "b@example.com"}
    subprocess.run(["git", "-C", str(other), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(other), "commit", "-qm", "theirs"], check=True, env=env)
    subprocess.run(["git", "-C", str(other), "push", "-q", "origin", "main"], check=True)

    # The next release builds on their commit (fetch first), so nothing is lost.
    second = push({"a.txt": "2\n"})
    log = subprocess.run(
        ["git", "-C", str(git_server.bare), "log", "--format=%s", "main"],
        capture_output=True,
        text=True,
    ).stdout.split()
    assert log == ["release", "theirs", "release"]
    assert second.commit_sha != first.commit_sha
