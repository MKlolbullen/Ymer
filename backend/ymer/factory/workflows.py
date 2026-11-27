"""CI workflow files delivered with an app: GitHub Actions and GitLab CI.

They run the same ``.ymer-ci/`` scripts Ymer runs in its sandbox (lint, build, tests,
smoke run), on the same pinned tool versions, so the team's CI keeps checking the app
after it leaves Ymer. The Flask + React stack also installs and builds its front end
there, which the offline sandbox cannot do.

Which files are added depends on where a release goes: a push to GitHub gets the
GitHub workflow, a push to GitLab the GitLab file, and a download or any other host gets
both. Like the other packaging files, they are written from fixed templates and are not
added when the build already wrote a file at that path.
"""

from urllib.parse import urlparse

from ymer.factory.ci_scripts import RUFF, STEPS, step_command
from ymer.factory.stacks import STACKS

GITHUB_PATH = ".github/workflows/ymer-checks.yml"
GITLAB_PATH = ".gitlab-ci.yml"
PROVIDERS = ("github", "gitlab")

# What each stack's sandbox image has preinstalled (see infra/factory/).
PYTHON_PACKAGES = {
    "python-cli": "pytest==8.3.3",
    "flask-react": "flask==3.0.3 sqlalchemy==2.0.36 pytest==8.3.3",
    "python-api": "fastapi==0.115.6 httpx==0.28.1 sqlalchemy==2.0.36 pytest==8.3.3",
}
NODE_STACKS = {"static-web", "node-cli"}

COMMENT = (
    "# Written by Ymer Factory: the checks Ymer runs before every release (lint, build,\n"
    "# tests and a smoke run), using the scripts in .ymer-ci/. Edit freely.\n"
)


def providers_for(remote: str | None) -> tuple[str, ...]:
    """The CI files to deliver for a push to ``remote`` (None: a download, so both)."""
    host = (urlparse(remote).hostname or "").lower() if remote else ""
    if host == "github.com" or host.endswith(".github.com"):
        return ("github",)
    if "gitlab" in host:
        return ("gitlab",)
    return PROVIDERS


def _pip(stack: str) -> str:
    return (
        f"pip install {PYTHON_PACKAGES[stack]} {RUFF} && "
        "if [ -f requirements.txt ]; then pip install -r requirements.txt; fi"
    )


def github_workflow(stack_key: str) -> str:
    stack = STACKS.get(stack_key, STACKS["flask-react"]).key
    lines = [
        COMMENT.rstrip("\n"),
        "name: Ymer checks",
        "",
        "on:",
        "  push:",
        "  pull_request:",
        "",
        "permissions:",
        "  contents: read",
        "",
        "jobs:",
        "  checks:",
        "    runs-on: ubuntu-latest",
        "    timeout-minutes: 20",
        "    steps:",
        "      - uses: actions/checkout@v4",
    ]
    if stack in PYTHON_PACKAGES:
        lines += [
            "      - uses: actions/setup-python@v5",
            "        with:",
            '          python-version: "3.12"',
            "      - name: Install tools",
            f"        run: {_pip(stack)}",
        ]
    if stack in NODE_STACKS or stack == "flask-react":
        lines += [
            "      - uses: actions/setup-node@v4",
            "        with:",
            '          node-version: "22"',
        ]
    for key, name in STEPS:
        lines += [f"      - name: {name}", f"        run: {step_command(key)}"]
    if stack == "flask-react":
        lines += [
            "      - name: Build the front end",
            "        if: hashFiles('frontend/package.json') != ''",
            "        working-directory: frontend",
            "        run: |",
            "          if [ -f package-lock.json ]; then npm ci; else npm install; fi",
            "          npm run build",
        ]
    return "\n".join(lines) + "\n"


def gitlab_ci(stack_key: str) -> str:
    stack = STACKS.get(stack_key, STACKS["flask-react"]).key
    image = "node:22-slim" if stack in NODE_STACKS else "python:3.12-slim"
    lines = [
        COMMENT.rstrip("\n"),
        "stages:",
        "  - checks",
        "",
        "ymer-checks:",
        "  stage: checks",
        f"  image: {image}",
        "  timeout: 20 minutes",
    ]
    if stack in PYTHON_PACKAGES:
        lines += ["  before_script:", f"    - {_pip(stack)}"]
    lines += ["  script:"] + [f"    - {step_command(key)}" for key, _name in STEPS]
    if stack == "flask-react":
        lines += [
            "",
            "frontend-build:",
            "  stage: checks",
            "  image: node:22-slim",
            "  rules:",
            "    - exists:",
            "        - frontend/package.json",
            "  script:",
            "    - cd frontend",
            "    - if [ -f package-lock.json ]; then npm ci; else npm install; fi",
            "    - npm run build",
        ]
    return "\n".join(lines) + "\n"


def workflow_files(stack: str, providers: tuple[str, ...] = PROVIDERS) -> dict[str, str]:
    out = {}
    if "github" in providers:
        out[GITHUB_PATH] = github_workflow(stack)
    if "gitlab" in providers:
        out[GITLAB_PATH] = gitlab_ci(stack)
    return out
