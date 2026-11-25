"""A sandbox that runs nothing, for development and tests.

It cannot tell whether code works, so it only does what is safe in-process: it parses
Python files (``ast.parse``, which never executes them), counts test functions, and
reports a pass when there are tests and every Python file parses. JavaScript tests are
counted, not checked. The result is marked ``simulated`` so the UI can say so.
"""

import ast
import re

from ymer.factory.sandbox import ExecResult

JS_TEST = re.compile(r"\btest\s*\(|\bit\s*\(")


class FakeSandbox:
    name = "fake"

    def run(self, files: dict[str, str], command: str, *, image: str, timeout: int) -> ExecResult:
        lines = [f"$ {command}", "(demo sandbox: nothing was run; files were only checked)"]
        errors, tests = [], 0
        for path in sorted(files):
            content = files[path]
            name = path.rsplit("/", 1)[-1]
            if path.endswith(".py"):
                try:
                    tree = ast.parse(content, filename=path)
                except SyntaxError as exc:
                    errors.append(f"{path}:{exc.lineno}: SyntaxError: {exc.msg}")
                    continue
                if name.startswith("test_"):
                    found = sum(
                        isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
                        and n.name.startswith("test")
                        for n in ast.walk(tree)
                    )
                    tests += found
                    lines.append(f"{path}: {found} test(s)")
            elif ".test." in name:
                found = len(JS_TEST.findall(content))
                tests += found
                lines.append(f"{path}: {found} test(s)")
        if errors:
            return ExecResult(1, "\n".join(lines + errors + ["FAILED"]), simulated=True)
        if not tests:
            return ExecResult(5, "\n".join(lines + ["no tests ran"]), simulated=True)
        return ExecResult(0, "\n".join(lines + [f"{tests} passed"]), simulated=True)
