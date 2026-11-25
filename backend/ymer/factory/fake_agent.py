"""A scripted stand-in for the build agent, used with ``YMER_AI_PROVIDER=fake``.

It writes a small, real-looking module and a test for each task, runs the tests, and
finishes, so the whole build flow (log, files, task statuses, budgets, stopping) can be
developed and tested without a model. The code it writes is a placeholder: each task
becomes a function that describes the feature, not an implementation of it.
"""

import re
from typing import Any

from ymer.factory.agent import AgentTurn, ToolCall, ToolResult


def slug(text: str, limit: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:limit].strip("_")
    return s or "feature"


def _py_module(title: str, detail: str, name: str) -> str:
    doc = f'"""{title}.\n\n{detail}\n"""' if detail else f'"""{title}."""'
    return (
        f"{doc}\n\n\ndef {name}() -> dict:\n"
        f'    """What this part of the app does (placeholder written by the demo agent)."""\n'
        f'    return {{"feature": {title!r}, "ready": True}}\n'
    )


def _py_test(module: str, name: str, title: str) -> str:
    return (
        f"from {module} import {name}\n\n\n"
        f"def test_{name}_is_ready():\n"
        f"    result = {name}()\n"
        f'    assert result["feature"] == {title!r}\n'
        f'    assert result["ready"] is True\n'
    )


def task_files(stack: str, title: str, detail: str, setup: bool, app: str) -> dict[str, str]:
    name = slug(title)
    if stack == "static-web":
        if setup:
            return {
                "index.html": (
                    '<!doctype html>\n<html lang="en">\n<head>\n  <meta charset="utf-8">\n'
                    f'  <title>{app}</title>\n  <link rel="stylesheet" href="styles.css">\n'
                    '</head>\n<body>\n  <main id="app"></main>\n'
                    '  <script type="module" src="src/main.mjs"></script>\n</body>\n</html>\n'
                ),
                "styles.css": "body { font-family: system-ui, sans-serif; margin: 2rem; }\n",
                "src/main.mjs": f"document.querySelector('#app').textContent = {app!r};\n",
                "tests/setup.test.mjs": (
                    "import { test } from 'node:test';\nimport assert from 'node:assert/strict';\n"
                    "import { readFile } from 'node:fs/promises';\n\n"
                    "test('the page loads the app module', async () => {\n"
                    "  const html = await readFile(new URL('../index.html', import.meta.url), "
                    "'utf8');\n  assert.match(html, /src\\/main\\.mjs/);\n});\n"
                ),
            }
        fn = re.sub(r"_(\w)", lambda m: m.group(1).upper(), name)
        return {
            f"src/{name}.mjs": (
                f"// {title}.\nexport function {fn}() {{\n"
                f"  return {{ feature: {title!r}, ready: true }};\n}}\n"
            ),
            f"tests/{name}.test.mjs": (
                "import { test } from 'node:test';\nimport assert from 'node:assert/strict';\n"
                f"import {{ {fn} }} from '../src/{name}.mjs';\n\n"
                f"test('{name} is ready', () => {{\n  assert.equal({fn}().ready, true);\n}});\n"
            ),
        }
    if stack == "python-cli":
        if setup:
            return {
                "pyproject.toml": (
                    f'[project]\nname = "{slug(app)}"\nversion = "0.1.0"\n'
                    'requires-python = ">=3.12"\n\n[project.scripts]\n'
                    f'{slug(app).replace("_", "-")} = "app.cli:main"\n\n'
                    '[tool.pytest.ini_options]\npythonpath = ["src"]\n'
                ),
                "src/app/__init__.py": f'"""{app}."""\n',
                "src/app/cli.py": (
                    '"""Command-line entry point."""\n\nimport argparse\n\n\n'
                    "def main(argv: list[str] | None = None) -> int:\n"
                    f"    parser = argparse.ArgumentParser(prog={slug(app)!r})\n"
                    "    parser.parse_args(argv)\n"
                    f"    print({app!r})\n    return 0\n"
                ),
                "tests/test_cli.py": (
                    "from app.cli import main\n\n\ndef test_main_runs(capsys):\n"
                    "    assert main([]) == 0\n    assert capsys.readouterr().out.strip()\n"
                ),
            }
        return {
            f"src/app/{name}.py": _py_module(title, detail, name),
            f"tests/test_{name}.py": _py_test(f"app.{name}", name, title),
        }
    if stack == "node-cli":
        if setup:
            return {
                "package.json": (
                    f'{{\n  "name": "{slug(app).replace("_", "-")}",\n  "version": "0.1.0",\n'
                    '  "type": "module",\n'
                    f'  "bin": {{ "{slug(app).replace("_", "-")}": "bin/cli.mjs" }},\n'
                    '  "scripts": { "test": "node --test tests/" }\n}\n'
                ),
                "bin/cli.mjs": (
                    "#!/usr/bin/env node\nimport { parseArgs } from 'node:util';\n\n"
                    "export function main(argv = process.argv.slice(2)) {\n"
                    "  parseArgs({ args: argv, allowPositionals: true });\n"
                    f"  console.log({app!r});\n  return 0;\n}}\n\n"
                    "if (import.meta.url === `file://${process.argv[1]}`) {\n"
                    "  process.exitCode = main();\n}\n"
                ),
                "tests/cli.test.mjs": (
                    "import { test } from 'node:test';\nimport assert from 'node:assert/strict';\n"
                    "import { main } from '../bin/cli.mjs';\n\n"
                    "test('the command runs', () => {\n  assert.equal(main([]), 0);\n});\n"
                ),
            }
        fn = re.sub(r"_(\w)", lambda m: m.group(1).upper(), name)
        return {
            f"src/{name}.mjs": (
                f"// {title}.\nexport function {fn}() {{\n"
                f"  return {{ feature: {title!r}, ready: true }};\n}}\n"
            ),
            f"tests/{name}.test.mjs": (
                "import { test } from 'node:test';\nimport assert from 'node:assert/strict';\n"
                f"import {{ {fn} }} from '../src/{name}.mjs';\n\n"
                f"test('{name} is ready', () => {{\n  assert.equal({fn}().ready, true);\n}});\n"
            ),
        }
    if stack == "python-api":
        if setup:
            return {
                "pyproject.toml": (
                    f'[project]\nname = "{slug(app)}"\nversion = "0.1.0"\n'
                    'requires-python = ">=3.12"\ndependencies = ["fastapi", "sqlalchemy"]\n'
                ),
                "app/__init__.py": f'"""{app}."""\n',
                "app/main.py": (
                    '"""The API."""\n\nfrom fastapi import FastAPI\n\n'
                    f"app = FastAPI(title={app!r})\n\n\n"
                    '@app.get("/health")\ndef health() -> dict:\n    return {"status": "ok"}\n'
                ),
                "tests/conftest.py": (
                    "import sys\nfrom pathlib import Path\n\n"
                    "sys.path.insert(0, str(Path(__file__).resolve().parents[1]))\n"
                ),
                "tests/test_health.py": (
                    "from fastapi.testclient import TestClient\n\nfrom app.main import app\n\n\n"
                    "def test_health():\n"
                    '    assert TestClient(app).get("/health").json() == {"status": "ok"}\n'
                ),
            }
        return {
            f"app/{name}.py": _py_module(title, detail, name),
            f"tests/test_{name}.py": _py_test(f"app.{name}", name, title),
        }
    # flask-react
    if setup:
        return {
            "backend/app/__init__.py": (
                '"""The API."""\n\nfrom flask import Flask\n\n\n'
                "def create_app() -> Flask:\n    app = Flask(__name__)\n\n"
                '    @app.get("/api/health")\n    def health():\n'
                '        return {"status": "ok"}\n\n    return app\n'
            ),
            "backend/tests/conftest.py": (
                "import sys\nfrom pathlib import Path\n\n"
                "sys.path.insert(0, str(Path(__file__).resolve().parents[1]))\n"
            ),
            "backend/tests/test_health.py": (
                "from app import create_app\n\n\ndef test_health():\n"
                "    client = create_app().test_client()\n"
                '    assert client.get("/api/health").json == {"status": "ok"}\n'
            ),
            "frontend/package.json": (
                f'{{\n  "name": "{slug(app).replace("_", "-")}",\n  "private": true,\n'
                '  "type": "module",\n  "scripts": { "dev": "vite", "build": "vite build" }\n}\n'
            ),
            "frontend/src/App.tsx": (
                f"export default function App() {{\n  return <h1>{app}</h1>;\n}}\n"
            ),
        }
    return {
        f"backend/app/{name}.py": _py_module(title, detail, name),
        f"backend/tests/test_{name}.py": _py_test(f"app.{name}", name, title),
    }


class FakeAgent:
    """Scripted turns: look, write, test, finish (or report blocked if tests fail)."""

    def __init__(self, script: dict[str, Any]) -> None:
        self.task = script.get("task") or {}
        title = self.task.get("title") or "Task"
        self.files = task_files(
            script.get("stack") or "flask-react",
            title,
            self.task.get("detail") or "",
            setup=bool(script.get("setup")),
            app=script.get("app") or "App",
        )
        self.phase = 0
        self.last: list[ToolResult] = []
        self.n = 0

    def _id(self) -> str:
        self.n += 1
        return f"fake_{self.n}"

    def step(self) -> AgentTurn:
        self.phase += 1
        tokens = 1200
        if self.phase == 1:
            return AgentTurn(
                "Looking at what is there.",
                [ToolCall(self._id(), "list_files", {})],
                "tool_use",
                tokens,
            )
        if self.phase == 2:
            calls = [
                ToolCall(self._id(), "write_file", {"path": p, "content": c})
                for p, c in self.files.items()
            ]
            return AgentTurn(f"Writing {len(calls)} file(s).", calls, "tool_use", tokens)
        if self.phase == 3:
            return AgentTurn(
                "Running the tests.", [ToolCall(self._id(), "run_tests", {})], "tool_use", tokens
            )
        if self.phase == 4:
            passed = bool(self.last) and self.last[-1].content.startswith("Tests passed")
            outcome = "done" if passed else "blocked"
            summary = (
                f"Added {', '.join(sorted(self.files))}; tests pass."
                if passed
                else "The tests failed and the demo agent cannot fix code."
            )
            return AgentTurn(
                "",
                [ToolCall(self._id(), "finish_task", {"outcome": outcome, "summary": summary})],
                "tool_use",
                tokens,
            )
        return AgentTurn("Done.", [], "end_turn", 0)

    def add_results(self, results: list[ToolResult]) -> None:
        self.last = results

    def nudge(self, text: str) -> None:
        pass
