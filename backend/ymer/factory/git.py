"""Pushing a release to a git repository.

The Ymer server does the push, after an editor asks for it; the build agent never sees
a repository or a token. Rules:

- Only ``https://`` remotes on an allowed host (``YMER_FACTORY_GIT_HOSTS``), with no
  user name or password in the URL. ``file://`` only when ``YMER_FACTORY_GIT_ALLOW_LOCAL``
  is on (tests).
- The token comes with the request, goes to git through ``GIT_ASKPASS`` (never on a
  command line or in a URL), and is not stored. Any output shown is scrubbed of it.
- The push adds one commit on top of the branch as it is (or starts the branch), and is
  never forced: if someone pushed in between, the push fails and can be tried again.
"""

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from ymer.factory.sandbox.docker import write_tree

BRANCH = re.compile(r"^(?!-)(?!.*\.\.)(?!.*//)(?!.*@\{)[A-Za-z0-9._/-]{1,100}(?<![./])$")


class GitError(Exception):
    """The push failed; the message is safe to show (no token)."""


@dataclass
class PushResult:
    commit_sha: str
    changed: bool


def check_remote(remote: str, hosts: list[str], allow_local: bool) -> str:
    remote = remote.strip()
    parts = urlsplit(remote)
    if parts.scheme == "file" and allow_local:
        return remote
    if parts.scheme != "https":
        raise ValueError("Use an https:// repository URL.")
    if parts.username or parts.password:
        raise ValueError("Leave the user name and token out of the URL; give the token separately.")
    host = (parts.hostname or "").lower()
    if host not in {h.strip().lower() for h in hosts if h.strip()}:
        raise ValueError(f"Pushing to {host or 'that host'} is not allowed on this server.")
    if not parts.path.strip("/"):
        raise ValueError(
            "The URL needs the repository's path, like https://github.com/team/app.git"
        )
    return remote


def check_branch(branch: str) -> str:
    branch = branch.strip()
    if not BRANCH.match(branch) or branch.endswith(".lock"):
        raise ValueError("That is not a valid branch name.")
    return branch


ASKPASS = """#!/bin/sh
case "$1" in
  Username*) echo "x-access-token" ;;
  *) echo "$YMER_GIT_TOKEN" ;;
esac
"""


def push_files(
    files: dict[str, str],
    *,
    remote: str,
    branch: str,
    token: str,
    message: str,
    author: tuple[str, str],
    timeout: int,
    git_bin: str = "git",
) -> PushResult:
    with tempfile.TemporaryDirectory(prefix="ymer-git-") as tmp:
        home = Path(tmp) / "home"
        work = Path(tmp) / "repo"
        home.mkdir()
        work.mkdir()
        askpass = home / "askpass.sh"
        askpass.write_text(ASKPASS)
        askpass.chmod(0o700)
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(home),
            "GIT_ASKPASS": str(askpass),
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "YMER_GIT_TOKEN": token,
            "GIT_AUTHOR_NAME": author[0],
            "GIT_AUTHOR_EMAIL": author[1],
            "GIT_COMMITTER_NAME": author[0],
            "GIT_COMMITTER_EMAIL": author[1],
        }

        def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
            try:
                done = subprocess.run(
                    [git_bin, "-c", "credential.helper=", "-c", "core.hooksPath=/dev/null", *args],
                    cwd=work,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired as exc:
                raise GitError(f"git {args[0]} took longer than {timeout} seconds.") from exc
            except OSError as exc:
                raise GitError(f"Could not run git: {exc}") from exc
            if check and done.returncode != 0:
                output = (done.stderr or done.stdout or "").strip()
                if token:
                    output = output.replace(token, "***")
                raise GitError(f"git {args[0]} failed: {output[-1500:]}")
            return done

        git("init", "-q")
        git("remote", "add", "origin", remote)
        fetched = git("fetch", "-q", "--depth", "1", "origin", f"refs/heads/{branch}", check=False)
        if fetched.returncode == 0:
            git("checkout", "-q", "-B", branch, "FETCH_HEAD")
            git("rm", "-r", "-q", "--ignore-unmatch", ".")
        elif "couldn't find remote ref" in (fetched.stderr or "").lower():
            git("checkout", "-q", "--orphan", branch)  # a new branch (or an empty repository)
        else:
            output = (fetched.stderr or "").strip()
            if token:
                output = output.replace(token, "***")
            raise GitError(f"Could not reach the repository: {output[-1500:]}")
        write_tree(work, files)
        git("add", "-A")
        if (
            git("diff", "--cached", "--quiet", check=False).returncode == 0
            and fetched.returncode == 0
        ):
            sha = git("rev-parse", "HEAD").stdout.strip()
            return PushResult(sha, changed=False)
        git("commit", "-q", "--no-verify", "-m", message)
        git("push", "-q", "origin", f"HEAD:refs/heads/{branch}")
        return PushResult(git("rev-parse", "HEAD").stdout.strip(), changed=True)
