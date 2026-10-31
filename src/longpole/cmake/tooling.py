"""Ask the ``cmake`` executable and a build tree's cache about themselves.

Three facts drive everything else: which data version a given CMake writes
(to request the right query minor), where the user-level configuration
directory is (to enable instrumentation machine-wide), and which generator
and which ``cmake`` a build tree was configured with (to report and to find
the matching binary from inside a callback).
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from longpole.errors import CMakeToolError
from longpole.model import DataVersion

MINIMUM_VERSION = (4, 3)
"""First CMake with the stable instrumentation API."""

_DATA_VERSION_BY_LINE: dict[tuple[int, int], DataVersion] = {
    (4, 3): DataVersion(major=1, minor=0),
    (4, 4): DataVersion(major=1, minor=1),
}
"""Data versions of the release lines that predate the ``instrumentation`` capability key."""

_LATEST_KNOWN_DATA_VERSION = DataVersion(major=1, minor=2)
"""What 4.5 writes; assumed for newer releases that do not say otherwise."""


@dataclass(frozen=True, kw_only=True)
class CMakeInfo:
    """What ``cmake -E capabilities`` says about one executable.

    Attributes:
        executable: The binary that was queried.
        version: Full version string, e.g. ``4.5.0-rc2``.
        version_tuple: ``(major, minor, patch)`` without any suffix.
        data_version: Instrumentation data version this CMake writes, or
            ``None`` for a release older than 4.3.
    """

    executable: Path
    version: str
    version_tuple: tuple[int, int, int]
    data_version: DataVersion | None

    @property
    def supports_instrumentation(self) -> bool:
        """Whether this CMake has the stable instrumentation API at all."""
        return self.data_version is not None


@dataclass(frozen=True, kw_only=True)
class CacheInfo:
    """The handful of ``CMakeCache.txt`` entries longpole cares about.

    Attributes:
        generator: ``CMAKE_GENERATOR``, e.g. ``Ninja`` or ``Unix Makefiles``.
        cmake_command: The ``cmake`` that configured the tree.
        source_dir: ``CMAKE_HOME_DIRECTORY``, the top-level source directory.
        cmake_version: Version of the CMake that wrote the cache, from the
            ``CMAKE_CACHE_*_VERSION`` entries.
    """

    generator: str | None
    cmake_command: Path | None
    source_dir: Path | None
    cmake_version: str | None


def describe(cmake: Path, *, timeout: float = 30.0) -> CMakeInfo:
    """Run ``cmake -E capabilities`` and read the version and instrumentation support.

    CMake 4.5 reports the data version it writes under an ``instrumentation``
    key. Older releases do not have the key, so their data version is taken
    from the release line: 4.3 writes 1.0, 4.4 writes 1.1. Anything before
    4.3 has no instrumentation API.

    Args:
        cmake: The executable to query.
        timeout: Seconds to wait for it.

    Returns:
        Version and data-version facts about that binary.

    Raises:
        CMakeToolError: The binary cannot run, exits non-zero, times out or
            prints something other than the capabilities JSON.
    """
    output = _run(cmake, "-E", "capabilities", timeout=timeout)
    try:
        capabilities: object = json.loads(output)
    except json.JSONDecodeError as exc:
        raise CMakeToolError(f"{cmake} -E capabilities did not print JSON: {exc}") from exc
    document = _as_json(capabilities)
    if not document:
        raise CMakeToolError(f"{cmake} -E capabilities printed something other than an object")

    version = _as_json(document.get("version"))
    string = version.get("string")
    major, minor, patch = version.get("major"), version.get("minor"), version.get("patch")
    if not isinstance(string, str) or not all(isinstance(n, int) for n in (major, minor, patch)):
        raise CMakeToolError(f"{cmake} -E capabilities has no usable version object")
    version_tuple = (int(str(major)), int(str(minor)), int(str(patch)))

    return CMakeInfo(
        executable=cmake,
        version=string,
        version_tuple=version_tuple,
        data_version=_data_version(document.get("instrumentation"), version_tuple),
    )


def config_dir(cmake: Path, *, timeout: float = 30.0) -> Path:
    """Run ``cmake --print-config-dir`` to find the user-level configuration directory.

    Query files under ``<config dir>/instrumentation/v1/query/`` apply to
    every build on the machine. CMake prints the path with each space
    preceded by a backslash (``~/Library/Application Support/CMake`` comes
    out escaped on macOS); the escaping is undone here.

    Args:
        cmake: The executable to ask; the directory does not depend on the
            version, but asking is more robust than guessing per platform.
        timeout: Seconds to wait for it.

    Returns:
        The configuration directory. It may not exist yet.

    Raises:
        CMakeToolError: The binary cannot run, exits non-zero or times out.
    """
    output = _run(cmake, "--print-config-dir", timeout=timeout).strip()
    if not output:
        raise CMakeToolError(f"{cmake} --print-config-dir printed nothing")
    return Path(output.replace("\\ ", " "))


def read_cache(build_dir: Path) -> CacheInfo:
    """Read generator, configuring ``cmake`` and source directory from ``CMakeCache.txt``.

    Only the ``INTERNAL`` entries CMake itself writes are consulted; a tree
    without a cache yields a :class:`CacheInfo` of ``None`` values rather
    than an error, because a callback may legitimately run before anything
    was configured.

    Args:
        build_dir: The build tree.

    Returns:
        The entries found; any of them ``None`` when absent.
    """
    cache = build_dir / "CMakeCache.txt"
    if not cache.is_file():
        return CacheInfo(generator=None, cmake_command=None, source_dir=None, cmake_version=None)
    entries = _internal_entries(cache.read_text(encoding="utf-8", errors="replace"))
    command = entries.get("CMAKE_COMMAND")
    source = entries.get("CMAKE_HOME_DIRECTORY")
    parts = [entries.get(f"CMAKE_CACHE_{part}_VERSION") for part in ("MAJOR", "MINOR", "PATCH")]
    version = ".".join(part for part in parts if part) if all(parts) else None
    return CacheInfo(
        generator=entries.get("CMAKE_GENERATOR"),
        cmake_command=Path(command) if command else None,
        source_dir=Path(source) if source else None,
        cmake_version=version,
    )


def data_version_for_line(version_tuple: tuple[int, int, int]) -> DataVersion | None:
    """Instrumentation data version a CMake release line writes, by convention.

    Args:
        version_tuple: ``(major, minor, patch)`` of the CMake.

    Returns:
        1.0 for 4.3, 1.1 for 4.4, 1.2 for 4.5 and anything newer; ``None``
        before 4.3.
    """
    line = version_tuple[:2]
    if line < MINIMUM_VERSION:
        return None
    return _DATA_VERSION_BY_LINE.get(line, _LATEST_KNOWN_DATA_VERSION)


def _data_version(reported: object, version_tuple: tuple[int, int, int]) -> DataVersion | None:
    """Prefer the data version CMake reports; fall back to the release line.

    Args:
        reported: The ``instrumentation`` object from the capabilities, if any.
        version_tuple: The CMake version, for the fallback.

    Returns:
        The data version, or ``None`` when the CMake has no instrumentation API.
    """
    data_version = _as_json(_as_json(reported).get("dataVersion"))
    major, minor = data_version.get("major"), data_version.get("minor")
    if isinstance(major, int) and isinstance(minor, int):
        return DataVersion(major=major, minor=minor)
    return data_version_for_line(version_tuple)


def _internal_entries(cache_text: str) -> dict[str, str]:
    """Collect ``NAME:INTERNAL=value`` lines of a cache file.

    Args:
        cache_text: Contents of ``CMakeCache.txt``.

    Returns:
        Mapping from entry name to its value.
    """
    pattern = re.compile(r"^([A-Za-z0-9_-]+):INTERNAL=(.*)$")
    entries: dict[str, str] = {}
    for line in cache_text.splitlines():
        match = pattern.match(line)
        if match:
            entries[match[1]] = match[2]
    return entries


def _run(cmake: Path, *arguments: str, timeout: float) -> str:
    """Run the executable and return its standard output.

    Args:
        cmake: The executable.
        *arguments: Command-line arguments.
        timeout: Seconds to wait.

    Returns:
        Captured standard output as text.

    Raises:
        CMakeToolError: The process cannot start, exits non-zero or times out.
    """
    command = [str(cmake), *arguments]
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, check=False
        )
    except OSError as exc:
        raise CMakeToolError(f"cannot run {cmake}: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise CMakeToolError(f"{' '.join(command)} did not finish within {timeout:g} s") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"exit code {completed.returncode}"
        raise CMakeToolError(f"{' '.join(command)} failed: {detail}")
    return completed.stdout


def _as_json(value: object) -> dict[str, object]:
    """Treat a parsed JSON value as an object, or as empty when it is not one.

    Args:
        value: Any parsed JSON value.

    Returns:
        The object itself, or an empty mapping for anything else.
    """
    if isinstance(value, dict):
        # JSON object keys are always strings; the cast only tells the type checker so.
        return cast(dict[str, object], value)
    return {}
