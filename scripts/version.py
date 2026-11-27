#!/usr/bin/env python3
"""Keep Ymer's version in one place.

VERSION at the repository root is the source of truth, written as SemVer
(for example 0.1.0-beta.1). This script copies it into every package manifest:
the npm packages take it as is, and the Python package takes the PEP 440 form
(0.1.0b1).

    python scripts/version.py              print the version
    python scripts/version.py check        fail if any manifest disagrees
    python scripts/version.py check --tag v0.1.0-beta.1
                                           also fail if the tag does not match
    python scripts/version.py set 0.1.0-beta.2
                                           write a new version everywhere
    python scripts/version.py pep440       print the Python form
    python scripts/version.py prerelease   print "true" for a pre-release
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "VERSION"
SEMVER = re.compile(r"^(?P<core>\d+\.\d+\.\d+)(?:-(?P<label>alpha|beta|rc)\.(?P<num>\d+))?$")
PEP440_LABELS = {"alpha": "a", "beta": "b", "rc": "rc"}
NPM_PACKAGES = ("collab", "frontend")
PYPROJECT = ROOT / "backend" / "pyproject.toml"
PY_INIT = ROOT / "backend" / "ymer" / "__init__.py"
PYPROJECT_RE = re.compile(r'^version = "([^"]*)"$', re.M)
PY_INIT_RE = re.compile(r'^__version__ = "([^"]*)"$', re.M)


def parse(version: str) -> re.Match[str]:
    match = SEMVER.match(version)
    if not match:
        raise SystemExit(f"version: {version!r} is not X.Y.Z or X.Y.Z-(alpha|beta|rc).N")
    return match


def pep440(version: str) -> str:
    match = parse(version)
    if not match["label"]:
        return match["core"]
    return f"{match['core']}{PEP440_LABELS[match['label']]}{match['num']}"


def is_prerelease(version: str) -> bool:
    return parse(version)["label"] is not None


def read_version() -> str:
    version = VERSION_FILE.read_text().strip()
    parse(version)
    return version


def _single(pattern: re.Pattern[str], text: str, path: Path) -> re.Match[str]:
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise SystemExit(f"version: expected one version line in {path}")
    return matches[0]


def found_versions() -> dict[str, str]:
    """Return the version each manifest currently declares, keyed by file."""
    found: dict[str, str] = {}
    for path, pattern in ((PYPROJECT, PYPROJECT_RE), (PY_INIT, PY_INIT_RE)):
        found[str(path.relative_to(ROOT))] = _single(pattern, path.read_text(), path)[1]
    for package in NPM_PACKAGES:
        manifest = ROOT / package / "package.json"
        found[str(manifest.relative_to(ROOT))] = json.loads(manifest.read_text())["version"]
        lock_path = ROOT / package / "package-lock.json"
        lock = json.loads(lock_path.read_text())
        rel = str(lock_path.relative_to(ROOT))
        found[rel] = lock["version"]
        found[f"{rel} (root package)"] = lock["packages"][""]["version"]
    return found


def expected_versions(version: str) -> dict[str, str]:
    py = pep440(version)
    return {key: py if key.startswith("backend/") else version for key in found_versions()}


def check(tag: str | None) -> int:
    version = read_version()
    errors = []
    expected = expected_versions(version)
    for key, value in found_versions().items():
        if value != expected[key]:
            errors.append(f"{key} has {value}, expected {expected[key]}")
    if tag is not None and tag != f"v{version}":
        errors.append(f"tag {tag} does not match VERSION (expected v{version})")
    for error in errors:
        print(f"version: {error}", file=sys.stderr)
    if errors:
        print("version: run `python scripts/version.py set <version>`", file=sys.stderr)
        return 1
    print(f"version: {version} everywhere")
    return 0


def _replace(path: Path, pattern: re.Pattern[str], line: str) -> None:
    text = path.read_text()
    match = _single(pattern, text, path)
    path.write_text(text[: match.start()] + line + text[match.end() :])


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def set_version(version: str) -> int:
    parse(version)
    py = pep440(version)
    VERSION_FILE.write_text(version + "\n")
    _replace(PYPROJECT, PYPROJECT_RE, f'version = "{py}"')
    _replace(PY_INIT, PY_INIT_RE, f'__version__ = "{py}"')
    for package in NPM_PACKAGES:
        manifest_path = ROOT / package / "package.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["version"] = version
        _write_json(manifest_path, manifest)
        lock_path = ROOT / package / "package-lock.json"
        lock = json.loads(lock_path.read_text())
        lock["version"] = version
        lock["packages"][""]["version"] = version
        _write_json(lock_path, lock)
    print(f"version: set {version} (Python {py})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command")
    check_cmd = sub.add_parser("check", help="fail if any manifest disagrees")
    check_cmd.add_argument("--tag", help="a git tag that must equal v<VERSION>")
    set_cmd = sub.add_parser("set", help="write a new version everywhere")
    set_cmd.add_argument("version")
    sub.add_parser("pep440", help="print the Python form of the version")
    sub.add_parser("prerelease", help='print "true" for a pre-release')
    args = parser.parse_args(argv)
    if args.command == "check":
        return check(args.tag)
    if args.command == "set":
        return set_version(args.version)
    if args.command == "pep440":
        print(pep440(read_version()))
    elif args.command == "prerelease":
        print("true" if is_prerelease(read_version()) else "false")
    else:
        print(read_version())
    return 0


if __name__ == "__main__":
    sys.exit(main())
