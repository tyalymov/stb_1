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
import shutil
import subprocess
from pathlib import Path

from longpole.cmake import fileapi, tooling
from longpole.errors import CMakeToolError
from longpole.model import Environment, GitInfo

log = logging.getLogger(__name__)

_GIT_TIMEOUT = 10.0
"""Seconds to wait for each git command; a hung git must not stall a callback."""

_CCACHE_TIMEOUT = 10.0
"""Seconds to wait for ccache; same reasoning."""


def collect(build_dir: Path, *, reply: fileapi.Reply | None) -> Environment:
    """Gather the environment of a build tree at capture time.

    Reads the cache for the generator and the configuring ``cmake``, asks
    that binary for its full version string (the cache only knows
    ``major.minor.patch``), and looks at the source checkout, the CPUs and
    ccache. Every part tolerates absence: an unconfigured tree yields an
    :class:`~longpole.model.Environment` of ``None`` values.

    Args:
        build_dir: The build tree.
        reply: File API reply for the tree, for the toolchains; ``None`` when
            CMake has not answered yet.

    Returns:
        What could be learned.
    """
    cache = tooling.read_cache(build_dir)
    cmake_version = cache.cmake_version
    if cache.cmake_command is not None:
        try:
            cmake_version = tooling.describe(cache.cmake_command).version
        except CMakeToolError as exc:
            log.debug("cannot describe %s, keeping the cache version: %s", cache.cmake_command, exc)
    return Environment(
        cmake_version=cmake_version,
        generator=cache.generator,
        cpu_count=cpu_count(),
        git=git_info(cache.source_dir or build_dir),
        toolchains=fileapi.toolchains_from_reply(reply) if reply is not None else (),
        ccache=ccache_stats(),
    )


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


def ccache_stats() -> dict[str, str] | None:
    """Read ccache's counters, when ccache is installed.

    Uses ``ccache --print-stats``, the machine-readable form available since
    ccache 4: one ``name<TAB>value`` pair per line.

    Returns:
        Counter names and values as strings, or ``None`` when ccache is not
        on ``PATH`` or does not answer.
    """
    executable = shutil.which("ccache")
    if executable is None:
        return None
    try:
        completed = subprocess.run(
            [executable, "--print-stats"],
            capture_output=True,
            text=True,
            timeout=_CCACHE_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.debug("ccache --print-stats unavailable: %s", exc)
        return None
    if completed.returncode != 0:
        return None
    return parse_ccache_stats(completed.stdout)


def parse_ccache_stats(text: str) -> dict[str, str]:
    """Parse ``ccache --print-stats`` output.

    Args:
        text: The command's standard output.

    Returns:
        Mapping from counter name to its value; lines without a tab are skipped.
    """
    stats: dict[str, str] = {}
    for line in text.splitlines():
        name, tab, value = line.partition("\t")
        if tab:
            stats[name.strip()] = value.strip()
    return stats
