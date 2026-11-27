#!/usr/bin/env python3
"""Smoke-test a running Ymer server bundle through its public port.

Signs up, creates a workspace and a document, uploads a file and downloads it
through the presigned link (which goes through the bundled nginx to MinIO).

    python3 packaging/smoke.py [http://localhost:8080]
"""

import json
import sys
import urllib.request
import uuid

base = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080").rstrip("/")
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


ready = call("GET", "/health/ready")
assert ready["status"] == "ok", ready
email = f"smoke-{uuid.uuid4().hex[:8]}@example.com"
account = {"email": email, "password": "smoke-test-pass-9", "display_name": "Smoke"}
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
print(f"smoke: ok ({base}, version {ready.get('version', '?')})")
