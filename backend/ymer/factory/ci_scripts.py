"""The check scripts: what "lint", "build", "test" and "smoke" mean for each stack.

They are plain ``sh`` scripts (plus a few small Python and Node helpers) written into a
``.ymer-ci/`` folder next to the app's files, from these fixed templates and never by the
model. Ymer runs them in the build sandbox, and the CI workflow it delivers with the app
(``ymer.factory.workflows``) runs the very same files, so a check that passes in Ymer
passes in the team's own CI and the other way round.

The sandbox has no network, so every tool a script needs is in the stack's image
(``infra/factory/``). Lint uses ruff when the image has it, and falls back to a syntax
check on images built before ruff was added.
"""

from ymer.factory.stacks import STACKS

CI_DIR = ".ymer-ci"
# Pinned for the delivered workflows; the sandbox images pin the same versions.
RUFF = "ruff==0.8.4"
# Errors only: syntax errors, undefined names and comparisons that are always wrong.
# Style is the team's call, so it is not checked here.
RUFF_SELECT = "E9,F63,F7,F82"

# (key, name) in the order they run.
STEPS: tuple[tuple[str, str], ...] = (
    ("lint", "Lint"),
    ("build", "Build"),
    ("test", "Tests"),
    ("smoke", "Smoke run"),
)
STEP_NAMES = dict(STEPS)

HEADER = (
    "#!/bin/sh\n# Written by Ymer Factory. The same script runs in Ymer and in your CI.\nset -eu\n"
)

PY_LINT = (
    HEADER
    + "here=$(pwd)\n"
    + 'cd "${1:-.}"\n'
    + "if command -v ruff >/dev/null 2>&1; then\n"
    + f"  ruff check --no-cache --select {RUFF_SELECT} --output-format concise .\n"
    + "else\n"
    + '  echo "ruff is not installed here; checking syntax only."\n'
    + f'  python "$here/{CI_DIR}/pycheck.py" .\n'
    + "fi\n"
)

JS_LINT = (
    HEADER
    + "find . -type f \\( -name '*.js' -o -name '*.mjs' \\) -not -path './node_modules/*' "
    + f"-not -path './{CI_DIR}/*' | sort | while IFS= read -r f; do\n"
    + '  node --check "$f" || { echo "Syntax error in $f"; exit 1; }\n'
    + "done\n"
    + 'echo "JavaScript syntax OK."\n'
)

# Compiles every Python file in memory (nothing is written), and checks pyproject.toml.
PYCHECK = '''"""Written by Ymer Factory: compile every Python file and check pyproject.toml."""

import pathlib
import sys
import tomllib

root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
need_pyproject = "--pyproject" in sys.argv
errors, count = [], 0
for path in sorted(root.rglob("*.py")):
    if any(part in {".venv", "node_modules", ".ymer-ci"} for part in path.parts):
        continue
    count += 1
    try:
        compile(path.read_text(encoding="utf-8"), str(path), "exec")
    except SyntaxError as exc:
        errors.append(f"{path}:{exc.lineno}: SyntaxError: {exc.msg}")
pyproject = root / "pyproject.toml"
if pyproject.exists():
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        name = data.get("project", {}).get("name")
        if not name:
            errors.append("pyproject.toml has no [project] name.")
        else:
            print(f"Package {name} {data['project'].get('version', '')}".rstrip())
    except tomllib.TOMLDecodeError as exc:
        errors.append(f"pyproject.toml is not valid TOML: {exc}")
elif need_pyproject:
    errors.append("pyproject.toml is missing.")
print("\\n".join(errors) if errors else f"Compiled {count} Python file(s).")
sys.exit(1 if errors else 0)
'''

# Static web: everything index.html loads must exist.
SITE_CHECK = """// Written by Ymer Factory: check that index.html and what it loads are there.
import { existsSync, readFileSync } from 'node:fs';

if (!existsSync('index.html')) {
  console.log('index.html is missing.');
  process.exit(1);
}
const html = readFileSync('index.html', 'utf8');
const refs = [...html.matchAll(/(?:src|href)\\s*=\\s*["']([^"'#?]+)/g)].map((m) => m[1]);
const external = /^(?:[a-z]+:)?\\/\\/|^(?:data|mailto|tel|javascript):/i;
const local = refs.filter((r) => !external.test(r));
const missing = local.filter((r) => !existsSync(r.replace(/^\\.?\\//, '')));
if (missing.length) {
  console.log('index.html refers to files that do not exist:\\n' + missing.join('\\n'));
  process.exit(1);
}
console.log(`index.html and the ${local.length} file(s) it loads are present.`);
"""

# Static web: every module in src/ loads. Modules that need a browser at load time are
# reported, not failed, since Node has no DOM.
MODULE_SMOKE = """// Written by Ymer Factory: load every module in src/.
import { readdirSync, statSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const BROWSER =
  /\\b(document|window|localStorage|sessionStorage|navigator|HTMLElement) is not defined/;
const walk = (dir) => readdirSync(dir).flatMap((name) => {
  const path = join(dir, name);
  return statSync(path).isDirectory() ? walk(path) : [path];
});
let files = [];
try { files = walk('src').filter((f) => /\\.m?js$/.test(f)); } catch { files = []; }
let failed = 0;
for (const file of files) {
  try {
    await import(pathToFileURL(resolve(file)).href);
    console.log(`ok      ${file}`);
  } catch (err) {
    if (BROWSER.test(String(err && err.message))) {
      console.log(`browser ${file} (needs a browser to load)`);
    } else {
      failed += 1;
      console.log(`FAILED  ${file}: ${err && err.stack ? err.stack : err}`);
    }
  }
}
const loaded = `${files.length - failed} of ${files.length} module(s) load.`;
console.log(files.length ? loaded : 'No modules in src/.');
process.exit(failed ? 1 : 0);
"""

# Node CLI: package.json is valid and its command starts.
NODE_PACKAGE = """// Written by Ymer Factory: check package.json and its bin entry.
import { existsSync, readFileSync } from 'node:fs';

let pkg;
try {
  pkg = JSON.parse(readFileSync('package.json', 'utf8'));
} catch (err) {
  console.log(`package.json is missing or not valid JSON: ${err.message}`);
  process.exit(1);
}
const bins = typeof pkg.bin === 'string' ? { [pkg.name]: pkg.bin } : (pkg.bin || {});
const problems = [];
if (!pkg.name) problems.push('package.json has no name.');
if (!Object.keys(bins).length) problems.push('package.json has no bin entry.');
for (const [name, path] of Object.entries(bins)) {
  if (!existsSync(path)) problems.push(`The command ${name} points at ${path}, which is missing.`);
}
if (problems.length) {
  console.log(problems.join('\\n'));
  process.exit(1);
}
const names = Object.keys(bins).join(', ');
console.log(`Package ${pkg.name} ${pkg.version || ''} with command(s): ${names}`);
"""

NODE_CLI_SMOKE = """// Written by Ymer Factory: start the command with --help.
import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';

const pkg = JSON.parse(readFileSync('package.json', 'utf8'));
const bin = typeof pkg.bin === 'string' ? pkg.bin : Object.values(pkg.bin || {})[0];
const done = spawnSync(process.execPath, [bin, '--help'], { encoding: 'utf8', timeout: 20000 });
const out = `${done.stdout || ''}${done.stderr || ''}`;
console.log(`$ node ${bin} --help\\n${out.trim()}`);
if (done.error) {
  console.log(`The command did not finish: ${done.error.message}`);
  process.exit(1);
}
if (done.status === 0) process.exit(0);
// Commands without a --help option refuse it; that still shows they start.
if (out.includes('ERR_PARSE_ARGS')) {
  console.log('The command started (it has no --help option).');
  process.exit(0);
}
console.log(`The command exited with code ${done.status}.`);
process.exit(1);
"""

PY_CLI_SMOKE = '''"""Written by Ymer Factory: run the command's --help."""

import importlib
import sys

sys.path.insert(0, "src")
cli = importlib.import_module("app.cli")
main = getattr(cli, "main", None)
if main is None:
    print("app.cli has no main() function.")
    sys.exit(1)
try:
    code = main(["--help"])
except SystemExit as exc:
    code = exc.code
print(f"main(['--help']) returned {code!r}.")
sys.exit(0 if code in (0, None) else 1)
'''

FLASK_SMOKE = '''"""Written by Ymer Factory: start the Flask app and request a few pages."""

import os
import sys

sys.path.insert(0, os.getcwd())
from app import create_app  # noqa: E402

try:
    app = create_app()
except TypeError:
    app = create_app({"TESTING": True})
app.testing = True
client = app.test_client()
rules = sorted(r.rule for r in app.url_map.iter_rules() if r.endpoint != "static")
print(f"The app started with {len(rules)} route(s).")
failed = False
for path in ["/", *[r for r in rules if "<" not in r and r.startswith("/api")][:5]]:
    status = client.get(path).status_code
    print(f"GET {path} -> {status}")
    failed = failed or status >= 500
sys.exit(1 if failed else 0)
'''

ASGI_SMOKE = '''"""Written by Ymer Factory: start the API, request its schema and a few routes."""

import os
import sys

sys.path.insert(0, os.getcwd())
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

failed = False
with TestClient(app) as client:
    schema = client.get("/openapi.json")
    print(f"GET /openapi.json -> {schema.status_code}")
    failed = schema.status_code != 200
    paths = schema.json().get("paths", {}) if schema.status_code == 200 else {}
    print(f"The API started with {len(paths)} path(s).")
    for path, methods in list(paths.items())[:5]:
        if "{" in path or "get" not in methods:
            continue
        status = client.get(path).status_code
        print(f"GET {path} -> {status}")
        failed = failed or status >= 500
sys.exit(1 if failed else 0)
'''


def _script(*lines: str) -> str:
    return HEADER + "\n".join(lines) + "\n"


def ci_files(stack_key: str) -> dict[str, str]:
    """The ``.ymer-ci/`` files for a stack: one script per step, plus their helpers."""
    stack = STACKS.get(stack_key, STACKS["flask-react"])
    d = CI_DIR
    test = _script(stack.sandbox_test)
    match stack.key:
        case "static-web":
            return {
                f"{d}/lint.sh": JS_LINT,
                f"{d}/build.sh": _script(f"node {d}/site-check.mjs"),
                f"{d}/test.sh": test,
                f"{d}/smoke.sh": _script(f"node {d}/module-smoke.mjs"),
                f"{d}/site-check.mjs": SITE_CHECK,
                f"{d}/module-smoke.mjs": MODULE_SMOKE,
            }
        case "node-cli":
            return {
                f"{d}/lint.sh": JS_LINT,
                f"{d}/build.sh": _script(f"node {d}/package-check.mjs"),
                f"{d}/test.sh": test,
                f"{d}/smoke.sh": _script(f"node {d}/cli-smoke.mjs"),
                f"{d}/package-check.mjs": NODE_PACKAGE,
                f"{d}/cli-smoke.mjs": NODE_CLI_SMOKE,
            }
        case "python-cli":
            return {
                f"{d}/lint.sh": PY_LINT,
                f"{d}/build.sh": _script(f"python {d}/pycheck.py . --pyproject"),
                f"{d}/test.sh": test,
                f"{d}/smoke.sh": _script(f"python {d}/cli-smoke.py"),
                f"{d}/pycheck.py": PYCHECK,
                f"{d}/cli-smoke.py": PY_CLI_SMOKE,
            }
        case "python-api":
            return {
                f"{d}/lint.sh": PY_LINT,
                f"{d}/build.sh": _script(f"python {d}/pycheck.py . --pyproject"),
                f"{d}/test.sh": test,
                f"{d}/smoke.sh": _script(f"python {d}/api-smoke.py"),
                f"{d}/pycheck.py": PYCHECK,
                f"{d}/api-smoke.py": ASGI_SMOKE,
            }
    # flask-react: the back end is checked here; the front end needs npm packages, so
    # it is built by the delivered workflow, which has network.
    return {
        f"{d}/lint.sh": _script(f"sh {d}/pylint.sh backend"),
        f"{d}/pylint.sh": PY_LINT,
        f"{d}/build.sh": _script(
            f"python {d}/pycheck.py backend",
            "if [ -f frontend/package.json ]; then",
            "  python -c \"import json; json.load(open('frontend/package.json'))\"",
            '  echo "frontend/package.json is valid JSON."',
            "fi",
        ),
        f"{d}/test.sh": test,
        f"{d}/smoke.sh": _script(f"cd backend && python ../{d}/flask-smoke.py"),
        f"{d}/pycheck.py": PYCHECK,
        f"{d}/flask-smoke.py": FLASK_SMOKE,
    }


def step_command(key: str) -> str:
    return f"sh {CI_DIR}/{key}.sh"
