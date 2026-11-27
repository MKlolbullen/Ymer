"""Packaging a release: the files people download or push, and what changed.

A release keeps the app exactly as the build wrote it. When it is delivered (a zip, or a
push to a git repository) a few standard files are added for the stack, unless the build
already wrote a file with that name:

- ``RELEASE.md``: what the app is, which release this is, and how to run and test it;
- ``.gitignore``;
- a ``Dockerfile`` for stacks delivered as a container (Flask + React) or that can run in
  one (the Python CLI). Ymer does not build the image: that needs network and a Docker
  daemon, so it is left to the team's own CI or ``docker build``.

- the checks: the ``.ymer-ci/`` scripts Ymer ran before the release, and a CI workflow
  that runs them (GitHub Actions and/or GitLab CI, see ``ymer.factory.workflows``). The
  scripts always come from Ymer's templates, so the CI runs exactly what Ymer checked.

These files are written from fixed templates, not by the model.
"""

import io
import zipfile
from datetime import datetime

from ymer.factory.ci_scripts import ci_files
from ymer.factory.stacks import STACKS
from ymer.factory.workflows import PROVIDERS, workflow_files

GITIGNORE = {
    "static-web": "node_modules/\n.DS_Store\n",
    "python-cli": "__pycache__/\n*.pyc\n.venv/\ndist/\n*.egg-info/\n.pytest_cache/\n",
    "flask-react": (
        "__pycache__/\n*.pyc\n.venv/\n.pytest_cache/\n*.sqlite3\n"
        "frontend/node_modules/\nfrontend/dist/\n"
    ),
    "node-cli": "node_modules/\n.DS_Store\n*.tgz\n",
    "python-api": "__pycache__/\n*.pyc\n.venv/\n.pytest_cache/\n*.sqlite3\n",
}

DOCKERFILES = {
    "python-cli": (
        "# Written by Ymer Factory. Build with: docker build -t app .\n"
        "FROM python:3.12-slim\n"
        "WORKDIR /app\n"
        "COPY . .\n"
        "RUN pip install --no-cache-dir .\n"
        'ENTRYPOINT ["python", "-m", "app.cli"]\n'
    ),
    "flask-react": (
        "# Written by Ymer Factory. Build with: docker build -t app .\n"
        "# Stage 1 builds the front end; stage 2 serves the API and the built files.\n"
        "FROM node:22-slim AS frontend\n"
        "WORKDIR /frontend\n"
        "COPY frontend/ .\n"
        "RUN if [ -f package.json ]; then npm install && npm run build; fi; mkdir -p dist\n"
        "\n"
        "FROM python:3.12-slim\n"
        "WORKDIR /app\n"
        "RUN pip install --no-cache-dir flask==3.0.3 sqlalchemy==2.0.36 gunicorn==23.0.0\n"
        "COPY backend/ .\n"
        "COPY --from=frontend /frontend/dist ./static\n"
        "EXPOSE 8000\n"
        'CMD ["gunicorn", "--bind", "0.0.0.0:8000", "app:create_app()"]\n'
    ),
    "node-cli": (
        "# Written by Ymer Factory. Build with: docker build -t app .\n"
        "FROM node:22-slim\n"
        "WORKDIR /app\n"
        "COPY . .\n"
        'ENTRYPOINT ["node", "bin/cli.mjs"]\n'
    ),
    "python-api": (
        "# Written by Ymer Factory. Build with: docker build -t app .\n"
        "FROM python:3.12-slim\n"
        "WORKDIR /app\n"
        "RUN pip install --no-cache-dir fastapi==0.115.6 sqlalchemy==2.0.36 uvicorn==0.32.1\n"
        "COPY . .\n"
        "EXPOSE 8000\n"
        'CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]\n'
    ),
}

RUN = {
    "static-web": (
        "Open `index.html` in a browser, or serve the folder with any static file server:\n\n"
        "```sh\npython -m http.server 8000\n```\n\nRun the tests (Node 22):\n\n"
        "```sh\nnode --test 'tests/**/*.test.mjs'\n```\n"
    ),
    "python-cli": (
        "```sh\npython -m venv .venv && . .venv/bin/activate\npip install . pytest\n"
        "python -m app.cli --help\npython -m pytest\n```\n\n"
        "Or as a container: `docker build -t app . && docker run --rm app --help`.\n"
    ),
    "flask-react": (
        "Back end:\n\n```sh\ncd backend\npython -m venv .venv && . .venv/bin/activate\n"
        "pip install flask sqlalchemy pytest\npython -m pytest\n"
        'flask --app "app:create_app()" run\n```\n\n'
        "Front end: `cd frontend && npm install && npm run dev`.\n\n"
        "As one container: `docker build -t app . && docker run --rm -p 8000:8000 app`.\n"
    ),
    "node-cli": (
        "```sh\nnode bin/cli.mjs --help\nnode --test 'tests/**/*.test.mjs'\n"
        "npm link   # puts the command on "
        "your PATH\n```\n\nOr as a container: `docker build -t app . && docker run --rm app "
        "--help`.\n"
    ),
    "python-api": (
        "```sh\npython -m venv .venv && . .venv/bin/activate\n"
        "pip install fastapi sqlalchemy uvicorn httpx pytest\npython -m pytest\n"
        "uvicorn app.main:app --reload\n```\n\nThe API docs are at "
        "http://localhost:8000/docs. As a container: `docker build -t app . && "
        "docker run --rm -p 8000:8000 app`.\n"
    ),
}


def release_readme(
    app: str,
    number: int,
    stack_key: str,
    notes: str | None,
    when: datetime,
    requests: list[str] | None = None,
) -> str:
    stack = STACKS.get(stack_key, STACKS["flask-react"])
    parts = [
        f"# {app or 'App'}, release {number}\n",
        f"Built by Ymer Factory on {when:%Y-%m-%d} with the {stack.name} stack "
        f"({stack.languages}). Delivered as: {stack.delivers.lower()}.\n",
    ]
    if notes:
        parts.append(f"## Release notes\n\n{notes.strip()}\n")
    if requests:
        lines = "\n".join(f"- {r}" for r in requests)
        parts.append(f"## Changes requested and built in this release\n\n{lines}\n")
    parts.append(f"## Run it\n\n{RUN.get(stack.key, '')}")
    parts.append(
        "## Checks\n\nYmer ran these before the release; your CI runs the same scripts "
        "(`.github/workflows/ymer-checks.yml` or `.gitlab-ci.yml`):\n\n"
        "```sh\nsh .ymer-ci/lint.sh\nsh .ymer-ci/build.sh\nsh .ymer-ci/test.sh\n"
        "sh .ymer-ci/smoke.sh\n```\n"
    )
    return "\n".join(parts)


def package_files(
    files: dict[str, str],
    *,
    app: str,
    number: int,
    stack: str,
    notes: str | None,
    when: datetime,
    requests: list[str] | None = None,
    ci: tuple[str, ...] = PROVIDERS,
) -> dict[str, str]:
    """The release's files plus the standard ones it does not already have, and the check
    scripts (always Ymer's own). ``ci`` names the CI systems to write a workflow for."""
    extra = {
        "RELEASE.md": release_readme(app, number, stack, notes, when, requests),
        ".gitignore": GITIGNORE.get(stack, ""),
        **workflow_files(stack, ci),
    }
    if stack in DOCKERFILES:
        extra["Dockerfile"] = DOCKERFILES[stack]
    out = dict(files)
    for path, content in extra.items():
        if content and path not in out:
            out[path] = content
    out.update(ci_files(stack))
    return out


def zip_bytes(files: dict[str, str], folder: str) -> bytes:
    """A zip with every file under one top-level folder, in a stable order."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            info = zipfile.ZipInfo(f"{folder}/{path}", date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, files[path])
    return buffer.getvalue()


def changes_between(old: dict[str, str] | None, new: dict[str, str]) -> dict[str, list[str]]:
    old = old or {}
    return {
        "added": sorted(p for p in new if p not in old),
        "changed": sorted(p for p in new if p in old and old[p] != new[p]),
        "removed": sorted(p for p in old if p not in new),
    }


def slug(text: str) -> str:
    out = "".join(c if c.isalnum() else "-" for c in (text or "app").lower())
    return "-".join(part for part in out.split("-") if part)[:60] or "app"
