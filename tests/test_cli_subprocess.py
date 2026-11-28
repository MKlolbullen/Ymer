"""End-to-end tests that run the real wc.py launcher in a subprocess."""

import subprocess
import sys
from pathlib import Path

WC = Path(__file__).resolve().parent.parent / "wc.py"

# 16 words: one x1, two x2, three x3, four x4, five x5, six x1.
TEXT = "one two two three three three\nfour four four four five five five five five six\n"


def run(*args, stdin=None):
    return subprocess.run(
        [sys.executable, str(WC), *args],
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def top_entries(out: str) -> list[str]:
    return out.split("top words:\n")[1].splitlines()


def write(tmp_path, text=TEXT):
    path = tmp_path / "input.txt"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_runs_on_file(tmp_path):
    result = run(write(tmp_path))
    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout.startswith(f"words: 16\nlines: 2\ncharacters: {len(TEXT)}\ntop words:\n")


def test_default_top_is_five(tmp_path):
    result = run(write(tmp_path))
    assert top_entries(result.stdout) == ["five 5", "four 4", "three 3", "two 2", "one 1"]


def test_top_three(tmp_path):
    result = run(write(tmp_path), "--top", "3")
    assert result.returncode == 0
    assert top_entries(result.stdout) == ["five 5", "four 4", "three 3"]


def test_stdin_with_dash():
    result = run("-", "--top", "1", stdin="Dog dog CAT\n")
    assert result.returncode == 0
    assert result.stdout == "words: 3\nlines: 1\ncharacters: 12\ntop words:\ndog 2\n"


def test_missing_file_exits_1(tmp_path):
    result = run(str(tmp_path / "missing.txt"))
    assert result.returncode == 1
    assert result.stdout == ""
    assert "cannot read" in result.stderr


def test_invalid_top_exits_2(tmp_path):
    for value in ["0", "-3", "many"]:
        result = run(write(tmp_path), "--top", value)
        assert result.returncode == 2, value
        assert result.stdout == ""
        assert "--top" in result.stderr


def test_missing_file_argument_exits_2():
    result = run()
    assert result.returncode == 2
    assert "FILE" in result.stderr
