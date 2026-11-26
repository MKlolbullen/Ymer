"""Where the code the build agent writes is run.

The build never runs generated code in the Ymer process. It hands a snapshot of the
files and one shell command to a :class:`Sandbox`, which runs it somewhere isolated and
returns the exit code and output. Each call is stateless: the sandbox gets every file,
runs, and is thrown away, so nothing leaks between commands, runs or factories.

Backends (``YMER_FACTORY_SANDBOX``):

- ``docker``: a fresh, locked-down container per command (``ymer.factory.sandbox.docker``).
- ``modal``: the same on Modal's hosted sandboxes, so the server needs no Docker
  (``ymer.factory.sandbox.modal``).
- ``fake``: runs nothing; checks test files for syntax and reports what would run. For
  development and tests on machines without Docker.
- ``off``: builds are turned off.

Another backend only has to implement ``run``.
"""

from dataclasses import dataclass
from typing import Protocol

from ymer.config import Settings

# A command's output is cut to this many characters (head and tail are kept).
MAX_OUTPUT = 20_000


@dataclass
class ExecResult:
    exit_code: int
    output: str
    timed_out: bool = False
    # True when nothing really ran (the fake sandbox); the UI says so.
    simulated: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


class SandboxError(Exception):
    """The sandbox itself failed (not the command); the message is safe to show."""


class Sandbox(Protocol):
    name: str

    def run(self, files: dict[str, str], command: str, *, image: str, timeout: int) -> ExecResult:
        """Run ``command`` with ``files`` (path -> text) as the working directory."""
        ...


def clip(text: str, limit: int = MAX_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    return f"{text[:half]}\n... [{len(text) - limit} characters cut] ...\n{text[-half:]}"


def build_sandbox(settings: Settings) -> Sandbox | None:
    if settings.factory_sandbox == "docker":
        from ymer.factory.sandbox.docker import DockerSandbox

        return DockerSandbox(
            docker_bin=settings.factory_docker_bin,
            memory=settings.factory_memory,
            cpus=settings.factory_cpus,
        )
    if settings.factory_sandbox == "modal":
        from ymer.factory.sandbox.modal import ModalSandbox

        return ModalSandbox(
            app_name=settings.factory_modal_app,
            memory=settings.factory_memory,
            cpus=settings.factory_cpus,
            dockerfiles=settings.factory_modal_dockerfiles,
            registry=settings.factory_modal_registry,
            token_id=settings.factory_modal_token_id,
            token_secret=settings.factory_modal_token_secret,
        )
    if settings.factory_sandbox == "fake":
        from ymer.factory.sandbox.fake import FakeSandbox

        return FakeSandbox()
    return None
