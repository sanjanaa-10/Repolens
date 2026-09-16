"""Shared safe Git execution for RepoLens (Phase 9).

Every git subprocess in the application (clone, fetch, rev-parse, diff) goes
through this module so the security posture is identical everywhere:

* argument arrays only - never ``shell=True``, never string concatenation;
* a controlled environment that strips ambient Git/SSH variables that could
  redirect Git to a hostile configuration, credential helper, or pager;
* mandatory ``-c`` overrides that pin hooks, credential helpers, and local
  file/ext protocol access;
* an explicit timeout and a bounded stdout/stderr capture;
* process-group creation on Windows so a timeout can terminate child helpers.

Repository working trees are untrusted. Git never executes hooks or aliases for
the command families RepoLens uses, but the environment below enforces it as
defense in depth so an accidental config cannot turn into command execution.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_MAX_OUTPUT_BYTES = 64 * 1024 * 1024  # 64 MiB safety ceiling on any capture


class GitSafetyError(RuntimeError):
    """A git safety bound was exceeded (output cap, environment, timeout)."""


# Ambient variables that could make git behave unpredictably or attach to an
# untrusted location (work trees, object stores, ssh transport, credentials,
# askpass). They are stripped so only RepoLens' hardened settings remain.
_STRIPPED_ENV = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_NAMESPACE",
    "GIT_CEILING_DIRECTORIES",
    "GIT_SSH",
    "GIT_SSH_COMMAND",
    "GIT_ASKPASS",
    "GIT_CONFIG_NOSYSTEM",
    "GIT_CONFIG_SYSTEM",
    "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG",
    "GIT_AUTHOR_NAME",
    "GIT_AUTHOR_EMAIL",
    "GIT_COMMITTER_NAME",
    "GIT_COMMITTER_EMAIL",
)

# Values applied on top of the (sanitized) process environment.
_HARDENED_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_EDITOR": "",
    "GIT_PAGER": "cat",
    "GIT_MERGE_AUTOEDIT": "no",
    "GIT_DISCOVERY_ACROSS_FILESYSTEM": "0",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "LC_ALL": "C",
    "LANG": "C",
}

# Mandatory per-invocation overrides. These are argv values (``-c key=value``)
# passed to git itself, so they cannot be overridden by repository-local
# ``.git/config``:
#   core.hooksPath=          - never run hooks from any directory
#   credential.helper=       - never read a credential store or prompt
#   protocol.file.allow=never - never allow local-path clones/pushes
#   protocol.ext.allow=never  - never launch ext:: subprocess helpers
_CMD_OVERRIDES = (
    "-c",
    "core.hooksPath=",
    "-c",
    "credential.helper=",
    "-c",
    "protocol.file.allow=never",
    "-c",
    "protocol.ext.allow=never",
)

_MAX_OUTPUT_BYTES_ATTR = "_max_output_bytes"


def safe_env() -> dict[str, str]:
    """Return the hardened environment used for every git invocation."""
    env = {k: v for k, v in os.environ.items() if k not in _STRIPPED_ENV}
    env.update(_HARDENED_ENV)
    return env


def git_command(args: list[str]) -> list[str]:
    """Build a git argv with the mandatory safety overrides prepended.

    ``args`` must be the command and its arguments (e.g. ``["clone", ...]``),
    never a shell string.
    """
    cmd = ["git", *_CMD_OVERRIDES, *args]
    if not args or args[0].startswith("-"):
        # Defensive: no positional command at the front would let an argument
        # be interpreted as a git option; callers must pass a real command.
        raise GitSafetyError("git_command requires a command as first argument")
    return cmd


def run_git_capture(
    args: list[str],
    *,
    cwd: Path,
    timeout: int,
    env: dict[str, str] | None = None,
    max_output_bytes: int = _MAX_OUTPUT_BYTES,
) -> str:
    """Run git and return captured stdout, enforcing all safety bounds.

    Raises:
        GitSafetyError: output exceeded the cap.
        subprocess.TimeoutExpired: the command did not finish in time.
        FileNotFoundError: the git executable is not on PATH.
        subprocess.CalledProcessError: git exited non-zero.
    """
    cmd = git_command(args)
    run_env = env or safe_env()
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=run_env,
        creationflags=creationflags,
        encoding="utf-8",
        errors="replace",
    )
    if len(result.stdout.encode("utf-8", errors="replace")) > max_output_bytes or len(
        result.stderr.encode("utf-8", errors="replace")
    ) > max_output_bytes:
        raise GitSafetyError(
            f"git output exceeded the safety cap ({max_output_bytes} bytes)"
        )
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, cmd, output=result.stdout, stderr=result.stderr
        )
    return result.stdout