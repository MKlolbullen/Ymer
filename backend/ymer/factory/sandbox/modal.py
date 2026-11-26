"""Run commands in a hosted Modal sandbox, so the Ymer server needs no Docker.

Every command gets a new Modal Sandbox (a gVisor-isolated container on Modal's machines):

- the image is built by Modal from the same Dockerfiles as the Docker backend
  (``infra/factory/<stack>.Dockerfile``), or pulled from a registry when
  ``YMER_FACTORY_MODAL_REGISTRY`` is set; Modal caches built images;
- no network (``block_network=True``), so generated code cannot reach anything;
- CPU and memory limits, a wall-clock timeout on the command and a hard lifetime on the
  whole sandbox;
- the files are packed into a tar archive, uploaded into the fresh sandbox and unpacked
  in ``/work``; nothing is shared between commands, runs or factories.

The sandbox is terminated when the command finishes, fails or times out.

Credentials are Modal's own ``MODAL_TOKEN_ID`` / ``MODAL_TOKEN_SECRET``, either from the
environment (read by the Modal client) or from the ``YMER_FACTORY_MODAL_TOKEN_*``
settings. The ``modal`` package is optional: ``pip install -e '.[modal]'``.
"""

import contextlib
import io
import tarfile
import time
from pathlib import Path
from typing import Any

from ymer.factory.sandbox import ExecResult, SandboxError, clip
from ymer.factory.sandbox.docker import safe_path

# Repository default: backend/ymer/factory/sandbox/modal.py -> <repo>/infra/factory
DEFAULT_DOCKERFILES = Path(__file__).resolve().parents[4] / "infra" / "factory"
ARCHIVE = "/tmp/ymer-files.tar"
WORKDIR = "/work"
UNPACK = f"mkdir -p {WORKDIR} && tar -xf {ARCHIVE} -C {WORKDIR} && rm {ARCHIVE}"
# Extra lifetime for the sandbox beyond the command's own timeout: start-up and upload.
GRACE_SECONDS = 120


def parse_memory(value: str) -> int:
    """Docker-style memory ("1g", "512m", "2048") to MiB, as Modal expects."""
    text = value.strip().lower().removesuffix("b")
    units = {"k": 1 / 1024, "m": 1, "g": 1024, "t": 1024 * 1024}
    if text and text[-1] in units:
        return max(1, int(float(text[:-1]) * units[text[-1]]))
    # A bare number is bytes, as in Docker.
    return max(1, int(text) // (1024 * 1024))


def pack(files: dict[str, str]) -> bytes:
    """The files as a tar archive, with every path checked first."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for path, content in sorted(files.items()):
            name = str(safe_path(path))
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            info.mtime = int(time.time())
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def image_stem(image: str) -> str:
    """``ymer-factory/python-cli:1`` -> ``python-cli``."""
    name = image.rsplit("/", 1)[-1].split(":", 1)[0]
    if not name or name.startswith(".") or not all(c.isalnum() or c in "-_." for c in name):
        raise SandboxError(f"Unknown sandbox image {image!r}.")
    return name


class ModalSandbox:
    name = "modal"

    def __init__(
        self,
        *,
        app_name: str = "ymer-factory",
        memory: str = "1g",
        cpus: str = "1",
        dockerfiles: str | None = None,
        registry: str | None = None,
        token_id: str | None = None,
        token_secret: str | None = None,
        modal_module: Any = None,
    ) -> None:
        self.app_name = app_name
        self.memory_mib = parse_memory(memory)
        self.cpus = float(cpus)
        self.dockerfiles = Path(dockerfiles) if dockerfiles else DEFAULT_DOCKERFILES
        self.registry = registry.rstrip("/") if registry else None
        self.token_id = token_id
        self.token_secret = token_secret
        self._modal = modal_module
        self._app = None
        self._client = None
        self._images: dict[str, Any] = {}

    # --- Modal objects, created once per process ------------------------------------

    def _module(self) -> Any:
        if self._modal is None:
            try:
                import modal
            except ImportError as exc:
                raise SandboxError(
                    "The Modal sandbox needs the modal package: pip install -e '.[modal]'."
                ) from exc
            self._modal = modal
        return self._modal

    def _get_client(self) -> Any:
        if self._client is None and self.token_id and self.token_secret:
            self._client = self._module().Client.from_credentials(self.token_id, self.token_secret)
        return self._client

    def _get_app(self) -> Any:
        if self._app is None:
            kwargs = {"create_if_missing": True}
            if self._get_client() is not None:
                kwargs["client"] = self._client
            self._app = self._module().App.lookup(self.app_name, **kwargs)
        return self._app

    def image_for(self, image: str) -> Any:
        if image not in self._images:
            modal = self._module()
            stem = image_stem(image)
            if self.registry:
                tag = image.rsplit(":", 1)[-1] if ":" in image else "latest"
                built = modal.Image.from_registry(f"{self.registry}/{stem}:{tag}")
            else:
                dockerfile = self.dockerfiles / f"{stem}.Dockerfile"
                if not dockerfile.is_file():
                    raise SandboxError(f"No Dockerfile for the {stem} stack at {dockerfile}.")
                built = modal.Image.from_dockerfile(str(dockerfile))
            self._images[image] = built
        return self._images[image]

    # --- The Sandbox protocol --------------------------------------------------------

    def run(self, files: dict[str, str], command: str, *, image: str, timeout: int) -> ExecResult:
        archive = pack(files)  # ValueError on an unsafe path, before anything is started
        modal = self._module()
        started = time.monotonic()
        image_ref = self.image_for(image)  # SandboxError for an unknown stack
        try:
            kwargs: dict[str, Any] = {
                "app": self._get_app(),
                "image": image_ref,
                "block_network": True,
                "cpu": self.cpus,
                "memory": self.memory_mib,
                "timeout": timeout + GRACE_SECONDS,
                "workdir": WORKDIR,
                "env": {"HOME": "/tmp", "PYTHONDONTWRITEBYTECODE": "1"},
            }
            if self._get_client() is not None:
                kwargs["client"] = self._client
            # Keep the sandbox alive while the files are copied in and the command runs; it
            # is terminated as soon as the command is done.
            sandbox = modal.Sandbox.create("sleep", str(timeout + GRACE_SECONDS), **kwargs)
        except Exception as exc:  # the Modal SDK raises many error types; all mean "not started"
            raise SandboxError(f"Could not start a Modal sandbox: {exc}") from exc
        try:
            try:
                sandbox.filesystem.write_bytes(archive, ARCHIVE)
                unpack = sandbox.exec("sh", "-c", UNPACK)
                if unpack.wait() != 0:
                    raise SandboxError(
                        f"Could not copy the files into the Modal sandbox: "
                        f"{clip(unpack.stdout.read() + unpack.stderr.read(), 2000)}"
                    )
                started = time.monotonic()
                # One stream, like the Docker backend: stderr goes where stdout goes.
                process = sandbox.exec(
                    "sh", "-c", f"exec 2>&1\n{command}", timeout=timeout, workdir=WORKDIR
                )
                code = process.wait()
                output = process.stdout.read()
            except SandboxError:
                raise
            except TimeoutError:
                return ExecResult(124, f"Stopped after {timeout} seconds.", timed_out=True)
            except Exception as exc:
                raise SandboxError(f"The Modal sandbox failed: {exc}") from exc
        finally:
            with contextlib.suppress(Exception):  # already gone: nothing left to clean up
                sandbox.terminate()
        # Modal reports a command that ran past its timeout as exit code -1.
        if code == -1 and time.monotonic() - started >= timeout - 1:
            return ExecResult(
                124, clip(f"{output}\nStopped after {timeout} seconds."), timed_out=True
            )
        return ExecResult(code, clip(output))
