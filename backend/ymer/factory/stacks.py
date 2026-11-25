"""The stacks the Factory builds with.

A stack is a known-good project shape: its languages, how it is laid out, how its tests
run and what gets delivered. Building on a few well-understood stacks is what makes the
later stages (build, test, package) reliable, so the architecture stage picks one of
these rather than inventing a new setup per app.
"""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Stack:
    key: str
    name: str
    summary: str
    good_for: str
    languages: str
    # Shown to people; the build runs ``sandbox_test`` in ``image``.
    test_command: str
    delivers: str
    image: str
    sandbox_test: str
    # Where the agent should put things, so every app of a stack has the same shape.
    layout: str


STACKS: dict[str, Stack] = {
    s.key: s
    for s in (
        Stack(
            key="static-web",
            name="Static web tool",
            summary="HTML, CSS and JavaScript that runs entirely in the browser.",
            good_for="Calculators, converters, dashboards over a file, small single-user tools.",
            languages="HTML, CSS, JavaScript (ES modules), browser storage",
            test_command="node --test",
            delivers="A folder of static files to host anywhere",
            image="ymer-factory/static-web:1",
            sandbox_test="node --test tests/",
            layout=(
                "index.html and styles.css at the root; logic as ES modules in src/ that do "
                "not touch the DOM where possible; tests in tests/*.test.mjs using node:test "
                "and node:assert, importing from ../src/."
            ),
        ),
        Stack(
            key="python-cli",
            name="Python command-line tool",
            summary="A Python 3.12 command with subcommands, packaged with pyproject.toml.",
            good_for="Scripts, data processing, automation and developer tools.",
            languages="Python 3.12, argparse, SQLite or files for storage",
            test_command="pytest",
            delivers="An installable Python package",
            image="ymer-factory/python-cli:1",
            sandbox_test="python -m pytest -q -p no:cacheprovider",
            layout=(
                "pyproject.toml; the package in src/app/ (cli.py holds main()); tests in "
                "tests/test_*.py that import from app (pytest runs with pythonpath=src)."
            ),
        ),
        Stack(
            key="flask-react",
            name="Web app (Flask + React)",
            summary=(
                "A Flask JSON API with SQLite, and a React + TypeScript front end built by Vite."
            ),
            good_for="Multi-user apps with shared data, forms, lists and dashboards.",
            languages="Python 3.12 (Flask, SQLAlchemy), TypeScript (React, Vite)",
            test_command="pytest && npm test",
            delivers="A container image that serves the API and the built front end",
            image="ymer-factory/flask-react:1",
            sandbox_test="cd backend && python -m pytest -q -p no:cacheprovider",
            layout=(
                "backend/ (Flask app factory in backend/app/__init__.py, tests in "
                "backend/tests/test_*.py using the Flask test client and SQLite in memory) and "
                "frontend/ (React + TypeScript with Vite). Builds run the backend tests; the "
                "front end is built when the app is delivered."
            ),
        ),
        Stack(
            key="node-cli",
            name="Node.js command-line tool",
            summary="A Node 22 command with subcommands, using only Node's standard library.",
            good_for="Developer tools, file and text processing, and scripts for JS teams.",
            languages="JavaScript (ES modules, Node 22), node:util parseArgs, JSON files",
            test_command="node --test",
            delivers="An npm package with a bin command",
            image="ymer-factory/node-cli:1",
            sandbox_test="node --test tests/",
            layout=(
                "package.json (type module, a bin entry pointing at bin/cli.mjs); the command "
                "in bin/cli.mjs, parsing arguments with node:util parseArgs and calling logic "
                "in src/*.mjs that does no input or output; tests in tests/*.test.mjs using "
                "node:test and node:assert. No npm dependencies."
            ),
        ),
        Stack(
            key="python-api",
            name="JSON API (FastAPI)",
            summary="A FastAPI JSON service with SQLite, for other apps or scripts to call.",
            good_for="Back ends for other apps, webhooks, integrations and data services.",
            languages="Python 3.12 (FastAPI, SQLAlchemy), SQLite",
            test_command="pytest",
            delivers="A container image that serves the API",
            image="ymer-factory/python-api:1",
            sandbox_test="python -m pytest -q -p no:cacheprovider",
            layout=(
                "pyproject.toml; the app in app/ (app/main.py creates the FastAPI app as "
                "`app`, routes in app/routes/, models in app/models.py); tests in "
                "tests/test_*.py using fastapi.testclient.TestClient and SQLite in memory, "
                "with a tests/conftest.py that puts the project root on sys.path."
            ),
        ),
    )
}


def stacks_json() -> list[dict]:
    return [asdict(s) for s in STACKS.values()]
