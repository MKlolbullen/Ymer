"""Written by Ymer Factory: compile every Python file and check pyproject.toml."""

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
print("\n".join(errors) if errors else f"Compiled {count} Python file(s).")
sys.exit(1 if errors else 0)
