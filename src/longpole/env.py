"""Facts about the machine and the checkout that CMake's data does not carry.

A run dump should let someone compare two builds a month apart and know
whether the machine or the sources moved under them. This module collects
the facts for that which are true only at capture time: ccache's counters,
how many CPUs the build could use, and which CMake and generator produced
the tree. Everything here tolerates absence; a machine without ccache is
ordinary, not an error. The source revision is not collected: the caller
knows it better and passes it as a label.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from longpole.cmake import tooling
from longpole.model import Environment

log = logging.getLogger(__name__)

_CCACHE_TIMEOUT = 10.0
"""Seconds to wait for ccache; a hung ccache must not stall a callback."""


def collect(build_dir: Path) -> Environment:
    """Gather the environment of a build tree at capture time.

    Reads the cache for the generator and the CMake version, and looks at
    the CPUs and ccache. Every part tolerates absence:
    an unconfigured tree yields an :class:`~longpole.model.Environment` of
    ``None`` values.

    Args:
        build_dir: The build tree.

    Returns:
        What could be learned.
    """
    cache = tooling.read_cache(build_dir)
    return Environment(
        cmake_version=cache.cmake_version,
        generator=cache.generator,
        cpu_count=cpu_count(),
        ccache=ccache_stats(),
    )


def cpu_count() -> int | None:
    """Logical CPUs available to this process.

    Returns:
        The count, or ``None`` when the platform does not report one.
    """
    return os.cpu_count()


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
