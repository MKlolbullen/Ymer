"""The release version lives in VERSION and must match every package manifest."""

import importlib.util
from pathlib import Path

import pytest

import ymer

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("ymer_version", ROOT / "scripts" / "version.py")
version = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(version)


def test_manifests_match_version_file():
    assert version.check(tag=None) == 0


def test_package_reports_pep440_form():
    assert ymer.__version__ == version.pep440(version.read_version())


def test_tag_must_match():
    assert version.check(tag=f"v{version.read_version()}") == 0
    assert version.check(tag="v0.0.0") == 1


@pytest.mark.parametrize(
    ("semver", "py", "pre"),
    [
        ("1.2.3", "1.2.3", False),
        ("0.1.0-alpha.2", "0.1.0a2", True),
        ("0.1.0-beta.1", "0.1.0b1", True),
        ("1.0.0-rc.3", "1.0.0rc3", True),
    ],
)
def test_pep440_conversion(semver, py, pre):
    assert version.pep440(semver) == py
    assert version.is_prerelease(semver) is pre


@pytest.mark.parametrize(
    ("semver", "msi"),
    [
        ("0.1.0-alpha.1", "0.1.101"),
        ("0.1.0-beta.1", "0.1.201"),
        ("0.1.0-rc.2", "0.1.302"),
        ("0.1.0", "0.1.900"),
        ("0.1.1-beta.1", "0.1.1201"),
        ("2.3.4", "2.3.4900"),
    ],
)
def test_msi_versions_sort_in_release_order(semver, msi):
    assert version.msi(semver) == msi


def test_msi_rejects_versions_that_do_not_fit():
    with pytest.raises(SystemExit):
        version.msi("256.0.0")
    with pytest.raises(SystemExit):
        version.msi("1.0.66-beta.1")


def test_android_version_codes_grow_with_each_release():
    order = [
        "0.1.0-alpha.1",
        "0.1.0-beta.3",
        "0.1.0-rc.1",
        "0.1.0",
        "0.1.1-beta.1",
        "0.2.0",
        "1.0.0",
        "2.3.4",
    ]
    codes = [version.android_code(v) for v in order]
    assert codes == sorted(codes) and len(set(codes)) == len(codes)
    assert version.android_code("0.1.0-beta.3") == 100203
    assert version.android_code("2.3.4") == 20304900
    with pytest.raises(SystemExit):
        version.android_code("1.100.0")


@pytest.mark.parametrize("bad", ["1.2", "v1.2.3", "1.2.3-beta", "1.2.3-dev.1"])
def test_rejects_unsupported_versions(bad):
    with pytest.raises(SystemExit):
        version.parse(bad)
