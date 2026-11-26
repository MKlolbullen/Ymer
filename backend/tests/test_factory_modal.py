"""The hosted Modal sandbox, checked against a stand-in for the ``modal`` package.

Nothing here talks to Modal: a real run needs a Modal account (see
``test_live_services.py`` for the opt-in live check).
"""

import io
import os
import tarfile

import pytest

from ymer.config import Settings
from ymer.factory.sandbox import SandboxError, build_sandbox
from ymer.factory.sandbox.modal import (
    DEFAULT_DOCKERFILES,
    ModalSandbox,
    image_stem,
    pack,
    parse_memory,
)


class Stream:
    def __init__(self, text: str) -> None:
        self.text = text

    def read(self) -> str:
        return self.text


class Process:
    def __init__(self, code: int, out: str = "", err: str = "") -> None:
        self.code, self.stdout, self.stderr = code, Stream(out), Stream(err)

    def wait(self) -> int:
        return self.code


class FakeFilesystem:
    def __init__(self, box: "FakeBox") -> None:
        self.box = box

    def write_bytes(self, data: bytes, path: str) -> None:
        self.box.uploads[path] = data


class FakeBox:
    def __init__(self, modal: "FakeModal", args: tuple, kwargs: dict) -> None:
        self.modal, self.args, self.kwargs = modal, args, kwargs
        self.uploads: dict[str, bytes] = {}
        self.execs: list[tuple[tuple, dict]] = []
        self.terminated = False
        self.filesystem = FakeFilesystem(self)

    def exec(self, *args: str, **kwargs):
        self.execs.append((args, kwargs))
        result = self.modal.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    def terminate(self) -> None:
        self.terminated = True


class FakeModal:
    """Just the parts of the modal SDK the backend uses."""

    def __init__(self) -> None:
        self.boxes: list[FakeBox] = []
        self.results: list = []
        self.lookups: list[tuple[str, dict]] = []
        self.images: list[tuple[str, str]] = []
        self.credentials: list[tuple[str, str]] = []
        self.create_error: Exception | None = None
        modal = self

        class App:
            @staticmethod
            def lookup(name, **kwargs):
                modal.lookups.append((name, kwargs))
                return f"app:{name}"

        class Image:
            @staticmethod
            def from_dockerfile(path):
                modal.images.append(("dockerfile", path))
                return f"image:{path}"

            @staticmethod
            def from_registry(ref):
                modal.images.append(("registry", ref))
                return f"image:{ref}"

        class Client:
            @staticmethod
            def from_credentials(token_id, token_secret):
                modal.credentials.append((token_id, token_secret))
                return "client"

        class Sandbox:
            @staticmethod
            def create(*args, **kwargs):
                if modal.create_error:
                    raise modal.create_error
                box = FakeBox(modal, args, kwargs)
                modal.boxes.append(box)
                return box

        self.App, self.Image, self.Client, self.Sandbox = App, Image, Client, Sandbox


def untar(data: bytes) -> dict[str, str]:
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        return {m.name: tar.extractfile(m).read().decode() for m in tar.getmembers()}


def test_helpers():
    assert parse_memory("1g") == 1024
    assert parse_memory("512m") == 512
    assert parse_memory("2GB") == 2048
    assert parse_memory(str(256 * 1024 * 1024)) == 256
    assert image_stem("ymer-factory/python-cli:1") == "python-cli"
    with pytest.raises(SandboxError):
        image_stem("ymer-factory/..:1")
    assert untar(pack({"b/c.py": "x = 1", "a.txt": "hé"})) == {"a.txt": "hé", "b/c.py": "x = 1"}
    with pytest.raises(ValueError):
        pack({"../escape": ""})


def test_every_stack_has_a_dockerfile_modal_can_build():
    from ymer.factory.stacks import STACKS

    for stack in STACKS.values():
        assert (DEFAULT_DOCKERFILES / f"{image_stem(stack.image)}.Dockerfile").is_file()


def test_runs_one_locked_down_sandbox_per_command():
    modal = FakeModal()
    modal.results = [Process(0), Process(1, "1 failed\n")]
    sandbox = ModalSandbox(memory="512m", cpus="0.5", modal_module=modal)
    result = sandbox.run(
        {"app.py": "print(1)", "tests/test_app.py": "def test(): pass"},
        "python -m pytest -q",
        image="ymer-factory/python-cli:1",
        timeout=60,
    )
    assert (result.exit_code, result.ok, result.simulated) == (1, False, False)
    assert "1 failed" in result.output

    (box,) = modal.boxes
    assert box.kwargs["block_network"] is True
    assert box.kwargs["cpu"] == 0.5 and box.kwargs["memory"] == 512
    assert box.kwargs["app"] == "app:ymer-factory"
    assert box.kwargs["image"] == f"image:{DEFAULT_DOCKERFILES / 'python-cli.Dockerfile'}"
    assert box.kwargs["timeout"] > 60
    assert "client" not in box.kwargs  # credentials come from MODAL_TOKEN_* in the env
    # The files arrive as one archive and are unpacked in /work.
    assert untar(box.uploads["/tmp/ymer-files.tar"]) == {
        "app.py": "print(1)",
        "tests/test_app.py": "def test(): pass",
    }
    unpack, command = box.execs
    assert "tar -xf /tmp/ymer-files.tar -C /work" in unpack[0][2]
    assert command[0][:2] == ("sh", "-c") and command[0][2].endswith("python -m pytest -q")
    assert command[1] == {"timeout": 60, "workdir": "/work"}
    assert box.terminated


def test_images_are_built_once_and_can_come_from_a_registry():
    modal = FakeModal()
    modal.results = [Process(0), Process(0, "ok"), Process(0), Process(0, "ok")]
    sandbox = ModalSandbox(registry="ghcr.io/me/", modal_module=modal)
    for _ in range(2):
        assert sandbox.run({}, "true", image="ymer-factory/node-cli:1", timeout=5).ok
    assert modal.images == [("registry", "ghcr.io/me/node-cli:1")]
    assert len(modal.lookups) == 1


def test_explicit_credentials():
    modal = FakeModal()
    modal.results = [Process(0), Process(0)]
    sandbox = ModalSandbox(token_id="ak-1", token_secret="as-2", modal_module=modal)
    sandbox.run({}, "true", image="ymer-factory/python-cli:1", timeout=5)
    assert modal.credentials == [("ak-1", "as-2")]
    assert modal.boxes[0].kwargs["client"] == "client"
    assert modal.lookups[0][1]["client"] == "client"


def test_a_command_that_runs_too_long_is_reported_as_timed_out(monkeypatch):
    import ymer.factory.sandbox.modal as backend

    clock = iter([0.0, 0.0, 100.0])
    monkeypatch.setattr(backend.time, "monotonic", lambda: next(clock))
    modal = FakeModal()
    modal.results = [Process(0), Process(-1, "partial output")]
    result = ModalSandbox(modal_module=modal).run(
        {}, "sleep 999", image="ymer-factory/python-cli:1", timeout=30
    )
    assert result.timed_out and result.exit_code == 124
    assert "partial output" in result.output and "30 seconds" in result.output
    assert modal.boxes[0].terminated


def test_the_sandbox_dying_of_old_age_is_a_timeout():
    modal = FakeModal()
    modal.results = [Process(0), TimeoutError("sandbox timed out")]
    result = ModalSandbox(modal_module=modal).run(
        {}, "sleep 999", image="ymer-factory/python-cli:1", timeout=30
    )
    assert result.timed_out
    assert modal.boxes[0].terminated


def test_modal_failures_become_sandbox_errors():
    modal = FakeModal()
    modal.create_error = RuntimeError("Token missing")
    with pytest.raises(SandboxError, match="Could not start a Modal sandbox: Token missing"):
        ModalSandbox(modal_module=modal).run(
            {}, "true", image="ymer-factory/python-cli:1", timeout=5
        )

    modal = FakeModal()

    def unreachable(name, **kwargs):
        raise ConnectionError("Could not connect to the Modal server.")

    modal.App.lookup = staticmethod(unreachable)
    with pytest.raises(SandboxError, match="Could not connect to the Modal server"):
        ModalSandbox(modal_module=modal).run(
            {}, "true", image="ymer-factory/python-cli:1", timeout=5
        )

    modal = FakeModal()
    modal.results = [Process(2, "", "tar: no space")]
    with pytest.raises(SandboxError, match="Could not copy the files"):
        ModalSandbox(modal_module=modal).run(
            {}, "true", image="ymer-factory/python-cli:1", timeout=5
        )
    assert modal.boxes[0].terminated

    modal = FakeModal()
    modal.results = [Process(0), ConnectionError("lost the sandbox")]
    with pytest.raises(SandboxError, match="lost the sandbox"):
        ModalSandbox(modal_module=modal).run(
            {}, "true", image="ymer-factory/python-cli:1", timeout=5
        )
    assert modal.boxes[0].terminated


def test_unknown_stack_and_unsafe_files_never_start_a_sandbox(tmp_path):
    modal = FakeModal()
    sandbox = ModalSandbox(dockerfiles=str(tmp_path), modal_module=modal)
    with pytest.raises(SandboxError, match="No Dockerfile"):
        sandbox.run({}, "true", image="ymer-factory/python-cli:1", timeout=5)
    with pytest.raises(ValueError):
        sandbox.run({"/etc/passwd": ""}, "true", image="ymer-factory/python-cli:1", timeout=5)
    assert modal.boxes == []


def test_missing_modal_package_is_a_clear_error(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_modal(name, *args, **kwargs):
        if name == "modal":
            raise ImportError("No module named 'modal'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_modal)
    with pytest.raises(SandboxError, match=r"pip install -e '\.\[modal\]'"):
        ModalSandbox().run({}, "true", image="ymer-factory/python-cli:1", timeout=5)


def test_settings_pick_the_modal_backend():
    sandbox = build_sandbox(
        Settings(
            factory_sandbox="modal",
            factory_memory="2g",
            factory_modal_app="ymer-test",
            factory_modal_registry="ghcr.io/me",
        )
    )
    assert isinstance(sandbox, ModalSandbox)
    assert sandbox.name == "modal"
    assert (sandbox.app_name, sandbox.memory_mib, sandbox.registry) == (
        "ymer-test",
        2048,
        "ghcr.io/me",
    )


# --- Against real Modal ------------------------------------------------------------------


@pytest.fixture
def real_modal():
    pytest.importorskip("modal", reason="pip install -e '.[modal]'")
    if not (os.environ.get("MODAL_TOKEN_ID") and os.environ.get("MODAL_TOKEN_SECRET")):
        pytest.skip("set MODAL_TOKEN_ID and MODAL_TOKEN_SECRET to run against Modal")
    return ModalSandbox(app_name="ymer-factory-tests")


@pytest.mark.live
def test_real_modal_runs_tests_without_network(real_modal):
    files = {
        "calc.py": "def add(a, b):\n    return a + b\n",
        "tests/test_calc.py": (
            "from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n"
        ),
    }
    command = "PYTHONPATH=. python -m pytest -q -p no:cacheprovider"
    passed = real_modal.run(files, command, image="ymer-factory/python-cli:1", timeout=300)
    assert passed.ok, passed.output
    assert "1 passed" in passed.output

    files["calc.py"] = "def add(a, b):\n    return a - b\n"
    failed = real_modal.run(files, command, image="ymer-factory/python-cli:1", timeout=300)
    assert failed.exit_code == 1 and "1 failed" in failed.output

    offline = real_modal.run(
        {},
        "python -c \"import urllib.request as u; u.urlopen('https://example.com', timeout=5)\"",
        image="ymer-factory/python-cli:1",
        timeout=120,
    )
    assert not offline.ok

    slow = real_modal.run({}, "sleep 60", image="ymer-factory/python-cli:1", timeout=5)
    assert slow.timed_out
