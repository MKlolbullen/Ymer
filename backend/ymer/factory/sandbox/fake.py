"""A sandbox that runs nothing, for development and tests.

It cannot tell whether code works, so it only does what is safe in-process: it parses
Python files (``ast.parse``, which never executes them), counts test functions, and
reports a pass when there are tests and every Python file parses. JavaScript tests are
counted, not checked. The result is marked ``simulated`` so the UI can say so.

Check scripts (``sh .ymer-ci/<step>.sh``) are recognised: the test step works as above,
and lint, build and smoke pass when every Python file parses.
"""

import ast
import re

from ymer.factory.sandbox import ExecResult

JS_TEST = re.compile(r"\btest\s*\(|\bit\s*\(")
CI_STEP = re.compile(r"^sh \.ymer-ci/(\w+)\.sh$")


class FakeSandbox:
    name = "fake"

    def run(self, files: dict[str, str], command: str, *, image: str, timeout: int) -> ExecResult:
        lines = [f"$ {command}", "(demo sandbox: nothing was run; files were only checked)"]
        step = CI_STEP.match(command.strip())
        if step and step.group(1) != "test":
            return self._syntax_only(files, lines)
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

    def _syntax_only(self, files: dict[str, str], lines: list[str]) -> ExecResult:
        errors, count = [], 0
        for path in sorted(files):
            if not path.endswith(".py") or path.startswith(".ymer-ci/"):
                continue
            count += 1
            try:
                ast.parse(files[path], filename=path)
            except SyntaxError as exc:
                errors.append(f"{path}:{exc.lineno}: SyntaxError: {exc.msg}")
        if errors:
            return ExecResult(1, "\n".join(lines + errors), simulated=True)
        return ExecResult(0, "\n".join(lines + [f"{count} Python file(s) parse."]), simulated=True)
