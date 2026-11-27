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
    python scripts/version.py msi          print the Windows Installer form
    python scripts/version.py android-code print the Android version code

The desktop client (desktop/) gets the version in package.json, Cargo.toml,
Cargo.lock and tauri.conf.json. Windows Installer versions must be numeric, so
tauri.conf.json also carries an MSI form: patch * 1000 + 100/200/300 for
alpha/beta/rc + N, or + 900 for a final release (0.1.0-beta.1 is 0.1.201,
0.1.0 is 0.1.900), which keeps them in release order.

The phone apps share desktop/src-tauri. Android needs a growing whole number per
release, so tauri.android.conf.json carries a version code built the same way:
major * 10000000 + minor * 100000 + the MSI build number (0.1.0-beta.3 is 100203).
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
NPM_PACKAGES = ("collab", "frontend", "desktop")
PYPROJECT = ROOT / "backend" / "pyproject.toml"
PY_INIT = ROOT / "backend" / "ymer" / "__init__.py"
PYPROJECT_RE = re.compile(r'^version = "([^"]*)"$', re.M)
PY_INIT_RE = re.compile(r'^__version__ = "([^"]*)"$', re.M)
TAURI_DIR = ROOT / "desktop" / "src-tauri"
CARGO_TOML = TAURI_DIR / "Cargo.toml"
CARGO_LOCK = TAURI_DIR / "Cargo.lock"
TAURI_CONF = TAURI_DIR / "tauri.conf.json"
ANDROID_CONF = TAURI_DIR / "tauri.android.conf.json"
# The first version line of Cargo.toml is the [package] one.
CARGO_TOML_RE = re.compile(r'^version = "([^"]*)"$', re.M)
CARGO_LOCK_RE = re.compile(r'^name = "ymer-desktop"\nversion = "([^"]*)"$', re.M)
MSI_OFFSETS = {"alpha": 100, "beta": 200, "rc": 300}


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


def msi(version: str) -> str:
    match = parse(version)
    major, minor, patch = (int(part) for part in match["core"].split("."))
    build = patch * 1000 + (
        MSI_OFFSETS[match["label"]] + int(match["num"]) if match["label"] else 900
    )
    if major > 255 or minor > 255 or build > 65535 or (match["num"] and int(match["num"]) > 99):
        raise SystemExit(f"version: {version} does not fit a Windows Installer version")
    return f"{major}.{minor}.{build}"


def android_code(version: str) -> int:
    """The Android versionCode: grows with every release, like the MSI version."""
    major, minor, build = (int(part) for part in msi(version).split("."))
    if minor > 99 or build > 99_999:
        raise SystemExit(f"version: {version} does not fit an Android version code")
    return major * 10_000_000 + minor * 100_000 + build


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
    found["desktop/src-tauri/Cargo.toml"] = CARGO_TOML_RE.search(CARGO_TOML.read_text())[1]
    found["desktop/src-tauri/Cargo.lock"] = _single(
        CARGO_LOCK_RE, CARGO_LOCK.read_text(), CARGO_LOCK
    )[1]
    conf = json.loads(TAURI_CONF.read_text())
    found["desktop/src-tauri/tauri.conf.json"] = conf["version"]
    found["desktop/src-tauri/tauri.conf.json (MSI)"] = conf["bundle"]["windows"]["wix"]["version"]
    android = json.loads(ANDROID_CONF.read_text())
    found["desktop/src-tauri/tauri.android.conf.json (versionCode)"] = str(
        android["bundle"]["android"].get("versionCode")
    )
    return found


def expected_versions(version: str) -> dict[str, str]:
    py = pep440(version)
    expected = {key: py if key.startswith("backend/") else version for key in found_versions()}
    expected["desktop/src-tauri/tauri.conf.json (MSI)"] = msi(version)
    expected["desktop/src-tauri/tauri.android.conf.json (versionCode)"] = str(android_code(version))
    return expected


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
    cargo = CARGO_TOML.read_text()
    first = CARGO_TOML_RE.search(cargo)
    CARGO_TOML.write_text(cargo[: first.start()] + f'version = "{version}"' + cargo[first.end() :])
    lock = CARGO_LOCK.read_text()
    entry = _single(CARGO_LOCK_RE, lock, CARGO_LOCK)
    CARGO_LOCK.write_text(lock[: entry.start(1)] + version + lock[entry.end(1) :])
    conf = json.loads(TAURI_CONF.read_text())
    conf["version"] = version
    conf["bundle"]["windows"]["wix"]["version"] = msi(version)
    _write_json(TAURI_CONF, conf)
    android = json.loads(ANDROID_CONF.read_text())
    android["bundle"]["android"]["versionCode"] = android_code(version)
    _write_json(ANDROID_CONF, android)
    print(f"version: set {version} (Python {py}, MSI {msi(version)}, Android {android_code(version)})")
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
    sub.add_parser("msi", help="print the Windows Installer form of the version")
    sub.add_parser("android-code", help="print the Android version code")
    args = parser.parse_args(argv)
    if args.command == "check":
        return check(args.tag)
    if args.command == "set":
        return set_version(args.version)
    if args.command == "pep440":
        print(pep440(read_version()))
    elif args.command == "msi":
        print(msi(read_version()))
    elif args.command == "android-code":
        print(android_code(read_version()))
    elif args.command == "prerelease":
        print("true" if is_prerelease(read_version()) else "false")
    else:
        print(read_version())
    return 0


if __name__ == "__main__":
    sys.exit(main())
