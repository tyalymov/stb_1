"""Shared fixtures: which CMake to run, and ready-to-use fixture projects."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

import pytest

from tests.support.cmake import CMakeProject

FIXTURES = Path(__file__).parent / "fixtures"
MINIMUM_VERSION = (4, 3)


def _cmake_from_environment() -> Path | None:
    """Resolve the CMake binary the suite should use.

    ``LONGPOLE_CMAKE`` may point at the ``cmake`` executable or at the
    directory that contains it; without it we take whatever is on ``PATH``.

    Returns:
        Path to an executable ``cmake``, or ``None`` if there is none.
    """
    configured = os.environ.get("LONGPOLE_CMAKE")
    if configured:
        candidate = Path(configured)
        if candidate.is_dir():
            candidate = candidate / "cmake"
        return candidate if candidate.is_file() else None
    found = shutil.which("cmake")
    return Path(found) if found else None


def _version_of(cmake: Path) -> tuple[int, int, int]:
    """Read ``major.minor.patch`` from ``cmake --version``.

    Args:
        cmake: Executable to query.

    Returns:
        The three numeric components; release-candidate suffixes are dropped.

    Raises:
        RuntimeError: The output did not start with ``cmake version X.Y.Z``.
    """
    import subprocess

    output = subprocess.run([str(cmake), "--version"], capture_output=True, text=True, check=True)
    match = re.match(r"cmake version (\d+)\.(\d+)\.(\d+)", output.stdout)
    if match is None:
        raise RuntimeError(f"unexpected `cmake --version` output: {output.stdout!r}")
    return int(match[1]), int(match[2]), int(match[3])


@pytest.fixture(scope="session")
def cmake_binary() -> Path:
    """The CMake executable for this session; skips when none is usable.

    Returns:
        Path to a ``cmake`` of version 4.3 or newer.
    """
    cmake = _cmake_from_environment()
    if cmake is None:
        pytest.skip("no cmake found; set LONGPOLE_CMAKE or put cmake on PATH")
    version = _version_of(cmake)
    if version[:2] < MINIMUM_VERSION:
        pytest.skip(f"cmake {'.'.join(map(str, version))} predates the instrumentation API")
    return cmake


@pytest.fixture(scope="session")
def cmake_version(cmake_binary: Path) -> tuple[int, int, int]:
    """Version of :func:`cmake_binary` as a comparable tuple.

    Args:
        cmake_binary: The session's CMake.

    Returns:
        ``(major, minor, patch)``.
    """
    return _version_of(cmake_binary)


@pytest.fixture(params=["Ninja", "Unix Makefiles"])
def generator(request: pytest.FixtureRequest) -> str:
    """Each generator that supports instrumentation, one test run per value.

    Args:
        request: pytest's parametrization handle.

    Returns:
        The generator name to pass to ``cmake -G``.
    """
    name: str = request.param
    if name == "Ninja" and shutil.which("ninja") is None:
        pytest.skip("ninja not on PATH")
    return name


def make_project(
    fixture: str, tmp_path: Path, cmake: Path, generator: str = "Ninja"
) -> CMakeProject:
    """Build a :class:`CMakeProject` for one fixture inside a fresh temp directory.

    The fixture sources are copied, so a test may edit them without touching
    the checked-in files.

    Args:
        fixture: Directory name under ``tests/fixtures``.
        tmp_path: pytest's per-test temporary directory.
        cmake: CMake executable to drive the project with.
        generator: CMake generator name.

    Returns:
        An unconfigured project with ``source``, ``build`` and ``capture``
        subdirectories under ``tmp_path``.
    """
    source = tmp_path / "source"
    shutil.copytree(FIXTURES / fixture, source)
    return CMakeProject(
        source_dir=source,
        build_dir=tmp_path / "build",
        cmake=cmake,
        generator=generator,
        capture_root=tmp_path / "capture",
    )


@pytest.fixture
def chain(tmp_path: Path, cmake_binary: Path, generator: str) -> CMakeProject:
    """The ``chain`` fixture project: three targets in a strict dependency chain.

    Args:
        tmp_path: pytest's per-test temporary directory.
        cmake_binary: The session's CMake.
        generator: Generator under test.

    Returns:
        An unconfigured project; call ``write_query`` and ``configure`` first.
    """
    return make_project("chain", tmp_path, cmake_binary, generator)
