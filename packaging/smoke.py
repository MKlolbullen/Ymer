#!/usr/bin/env python3
"""Smoke-test a running Ymer server bundle through its public port.

Uses self-registration or provisions two private workspace owners, then creates
a document, uploads a file and downloads it
through the presigned link (which goes through the bundled nginx to MinIO).

    python3 packaging/smoke.py [http://localhost:8080]
    python3 packaging/smoke.py --private-bundle /path/to/extracted/bundle
"""

import argparse
import json
import secrets
import subprocess
import urllib.error
import urllib.request
import uuid
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("url", nargs="?", default="http://localhost:8080")
parser.add_argument(
    "--private-bundle", type=Path, help="Directory containing the running bundle's ymerctl"
)
args = parser.parse_args()
base = args.url.rstrip("/")
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(method, path, body=None, token=None, raw=None, ctype="application/json"):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(base + "/api" + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", ctype)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with opener.open(req, timeout=30) as resp:
        return json.load(resp)


def new_account(name):
    return {
        "email": f"smoke-{uuid.uuid4().hex}@example.com",
        "display_name": name,
        "password": secrets.token_urlsafe(32),
    }


def provision(bundle, account):
    password = account["password"]
    subprocess.run(
        [
            str(bundle.resolve() / "ymerctl"),
            "compose",
            "exec",
            "-T",
            "api",
            "flask",
            "--app",
            "wsgi",
            "create-user",
            "--email",
            account["email"],
            "--display-name",
            account["display_name"],
        ],
        input=f"{password}\n{password}\n",
        text=True,
        check=True,
        timeout=60,
        stdout=subprocess.DEVNULL,
    )
    return call(
        "POST",
        "/auth/login",
        {
            "email": account["email"],
            "password": password,
            "session": "token",
        },
    )["access_token"]


def private_workspace(bundle):
    assert call("GET", "/auth/registration")["enabled"] is False, "registration is open"
    try:
        call("POST", "/auth/register", {**new_account("Third user"), "session": "token"})
    except urllib.error.HTTPError as exc:
        if exc.code != 403:
            raise
    else:
        raise AssertionError("public registration created a third account")

    first, second = new_account("First owner"), new_account("Second owner")
    first_token = provision(bundle, first)
    second_token = provision(bundle, second)
    workspace = call("POST", "/workspaces", {"name": "Private smoke"}, first_token)["workspace"][
        "id"
    ]
    path = f"/workspaces/{workspace}"
    call("POST", path + "/members", {"email": second["email"], "role": "owner"}, first_token)
    for owner, token in ((first, first_token), (second, second_token)):
        name = f"Renamed by {owner['display_name']}"
        updated = call("PATCH", path, {"name": name}, token)["workspace"]
        assert (updated["role"], updated["name"]) == ("owner", name)
    return workspace, second_token


ready = call("GET", "/health/ready")
assert ready["status"] == "ok", ready
if args.private_bundle:
    workspace, token = private_workspace(args.private_bundle)
else:
    account = new_account("Smoke")
    token = call("POST", "/auth/register", {**account, "session": "token"})["access_token"]
    workspace = call("POST", "/workspaces", {"name": "Smoke"}, token)["workspace"]["id"]
page = call("POST", f"/workspaces/{workspace}/pages", {"title": "Doc", "type": "document"}, token)
page_id = (page.get("page") or page)["id"]
boundary = uuid.uuid4().hex
payload = (
    f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="hello.txt"\r\n'
    f"Content-Type: text/plain\r\n\r\nhello from the smoke test\n\r\n--{boundary}--\r\n"
).encode()
uploaded = call(
    "POST",
    f"/workspaces/{workspace}/pages/{page_id}/files",
    token=token,
    raw=payload,
    ctype=f"multipart/form-data; boundary={boundary}",
)
with opener.open(uploaded["file"]["url"], timeout=30) as resp:
    assert resp.read() == b"hello from the smoke test\n"
with opener.open(base + "/w/anything", timeout=30) as resp:
    assert b'id="root"' in resp.read(), "the app shell is not served for client routes"
print(f"smoke: ok ({base}, {'private' if args.private_bundle else 'open'} registration)")
