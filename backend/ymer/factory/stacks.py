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
    test_command: str
    delivers: str


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
        ),
        Stack(
            key="python-cli",
            name="Python command-line tool",
            summary="A Python 3.12 command with subcommands, packaged with pyproject.toml.",
            good_for="Scripts, data processing, automation and developer tools.",
            languages="Python 3.12, argparse, SQLite or files for storage",
            test_command="pytest",
            delivers="An installable Python package",
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
        ),
    )
}


def stacks_json() -> list[dict]:
    return [asdict(s) for s in STACKS.values()]
