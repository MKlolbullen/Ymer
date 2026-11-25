"""Run commands in a throwaway Docker container.

Every command gets a new container from the stack's image with:

- no network (``--network none``), so generated code cannot reach anything;
- a read-only root file system, a non-root user and every Linux capability dropped;
- memory, CPU and process-count limits, and a wall-clock timeout;
- the files copied into a fresh temporary directory mounted at ``/work``.

The files are copied in, never mounted from the database or from Ymer's own tree, and
the container is removed when it exits (``--rm``), or killed when it runs too long.
"""

import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path, PurePosixPath

from ymer.factory.sandbox import ExecResult, SandboxError, clip


def safe_path(path: str) -> PurePosixPath:
    """A relative path that stays inside the work directory, or ValueError."""
    p = PurePosixPath(path)
    if (
        not path
        or "\x00" in path
        or p.is_absolute()
        or any(part in ("..", "") for part in p.parts)
        or p.parts[0] == ".git"
    ):
        raise ValueError(f"Not a safe relative path: {path!r}")
    return p


def write_tree(root: Path, files: dict[str, str]) -> None:
    for path, content in files.items():
        target = root.joinpath(*safe_path(path).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


class DockerSandbox:
    name = "docker"

    def __init__(self, *, docker_bin: str = "docker", memory: str = "1g", cpus: str = "1") -> None:
        self.docker_bin = docker_bin
        self.memory = memory
        self.cpus = cpus

    def command_line(self, workdir: str, image: str, command: str, name: str) -> list[str]:
        return [
            self.docker_bin,
            "run",
            "--rm",
            "--name",
            name,
            "--network",
            "none",
            "--memory",
            self.memory,
            "--memory-swap",
            self.memory,
            "--cpus",
            self.cpus,
            "--pids-limit",
            "256",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,size=256m",
            "--user",
            "65534:65534",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--env",
            "HOME=/tmp",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--volume",
            f"{workdir}:/work:rw",
            "--workdir",
            "/work",
            image,
            "sh",
            "-c",
            command,
        ]

    def run(self, files: dict[str, str], command: str, *, image: str, timeout: int) -> ExecResult:
        if shutil.which(self.docker_bin) is None:
            raise SandboxError(f"Docker was not found ({self.docker_bin}).")
        name = f"ymer-factory-{uuid.uuid4().hex[:12]}"
        with tempfile.TemporaryDirectory(prefix="ymer-factory-") as tmp:
            write_tree(Path(tmp), files)
            # The container runs as nobody and must be able to write test caches/output.
            for dirpath, _dirs, _files in os.walk(tmp):
                os.chmod(dirpath, 0o777)
            try:
                done = subprocess.run(
                    self.command_line(tmp, image, command, name),
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired as exc:
                subprocess.run(
                    [self.docker_bin, "kill", name], capture_output=True, timeout=30, check=False
                )
                out = (exc.stdout or "") + (exc.stderr or "")
                if isinstance(out, bytes):
                    out = out.decode(errors="replace")
                return ExecResult(
                    124, clip(f"{out}\nStopped after {timeout} seconds."), timed_out=True
                )
            except OSError as exc:
                raise SandboxError(f"Could not start Docker: {exc}") from exc
        output = (done.stdout or "") + (done.stderr or "")
        if done.returncode == 125:  # docker itself failed (bad image, daemon down)
            raise SandboxError(f"Docker could not run the command: {clip(output, 2000)}")
        return ExecResult(done.returncode, clip(output))
