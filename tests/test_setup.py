import subprocess
import sys
from pathlib import Path

from app import cli, counter

ROOT = Path(__file__).resolve().parent.parent


def test_package_modules_import():
    assert callable(cli.main)
    assert counter.TextStats().top_words == []


def test_wc_script_runs():
    result = subprocess.run(
        [sys.executable, str(ROOT / "wc.py"), "-"],
        input="hello world\n",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "words: 2" in result.stdout
