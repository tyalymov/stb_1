"""Facts about the machine and the checkout that CMake's data does not carry.

A run dump should let someone compare two builds a month apart and know
whether the machine or the sources moved under them. This module collects
the cheap facts for that: the git state of the sources, how many CPUs the
build could use, which CMake and generator produced the tree, and which
compilers served it. Everything here tolerates absence; a build tree outside
a git repository or a machine without ccache is ordinary, not an error.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from longpole.model import GitInfo

log = logging.getLogger(__name__)

_GIT_TIMEOUT = 10.0
"""Seconds to wait for each git command; a hung git must not stall a callback."""


def git_info(path: Path) -> GitInfo | None:
    """Describe the git checkout containing ``path``.

    Dirty means tracked files have uncommitted changes; untracked files do
    not count, because build trees and editor leftovers would make every
    checkout dirty.

    Args:
        path: Any directory inside the checkout, usually the source directory.

    Returns:
        Commit, branch and dirtiness, or ``None`` when ``path`` is not inside
        a git repository, git is not installed, or git fails to answer.
    """
    sha = _git(path, "rev-parse", "HEAD")
    if sha is None:
        return None
    branch = _git(path, "symbolic-ref", "--quiet", "--short", "HEAD")
    status = _git(path, "status", "--porcelain", "--untracked-files=no")
    return GitInfo(sha=sha, branch=branch or None, dirty=bool(status))


def cpu_count() -> int | None:
    """Logical CPUs available to this process.

    Returns:
        The count, or ``None`` when the platform does not report one.
    """
    return os.cpu_count()


def _git(path: Path, *arguments: str) -> str | None:
    """Run one git command in ``path`` and return its trimmed output.

    Args:
        path: Working directory for git.
        *arguments: Git subcommand and options.

    Returns:
        Standard output without surrounding whitespace, or ``None`` when git
        is missing, exits non-zero (not a repository, detached head for
        ``symbolic-ref``) or times out.
    """
    try:
        completed = subprocess.run(
            ["git", "-C", str(path), *arguments],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.debug("git %s in %s unavailable: %s", " ".join(arguments), path, exc)
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()
