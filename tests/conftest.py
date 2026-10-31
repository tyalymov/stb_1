"""Shared fixtures: which CMake to run, and ready-to-use fixture projects."""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest

from longpole.cmake import fileapi, snippets
from longpole.env import collect
from longpole.model import Manifest, Run
from longpole.store import new_run_id
from tests.support.cmake import (
    CapturedIndex,
    CMakeProject,
    InstrumentationQuery,
    instrumentation_options,
)

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


def cmake_version_of(cmake: Path) -> tuple[int, int, int]:
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
    version = cmake_version_of(cmake)
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
    return cmake_version_of(cmake_binary)


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


@pytest.fixture
def fanout(tmp_path: Path, cmake_binary: Path, generator: str) -> CMakeProject:
    """The ``fanout`` fixture project: eight independent generated sources, one executable.

    Args:
        tmp_path: pytest's per-test temporary directory.
        cmake_binary: The session's CMake.
        generator: Generator under test.

    Returns:
        An unconfigured project.
    """
    return make_project("fanout", tmp_path, cmake_binary, generator)


@pytest.fixture
def with_tests(tmp_path: Path, cmake_binary: Path) -> CMakeProject:
    """The ``with_tests`` fixture project: four ctest tests, one failing, nothing to compile.

    Uses Ninja only; the generator does not matter for a project with no
    languages, and ctest writes test snippets itself.

    Args:
        tmp_path: pytest's per-test temporary directory.
        cmake_binary: The session's CMake.

    Returns:
        An unconfigured project.
    """
    return make_project("with_tests", tmp_path, cmake_binary)


def _version_string(cmake: Path) -> str:
    """Format ``cmake --version`` as ``X.Y.Z``, dropping any release-candidate suffix.

    Args:
        cmake: Executable to query.

    Returns:
        Dotted version used as a test id.
    """
    return ".".join(map(str, cmake_version_of(cmake)))


def installed_cmakes() -> list[Path]:
    """Every CMake the suite can reach: pinned releases under ``.tools/cmake`` plus the default.

    Pinned releases come from ``scripts/install-cmake.sh`` (macOS bundle or Linux
    layout); the default is whatever :func:`_cmake_from_environment` finds. One
    entry per distinct version, oldest first.

    Returns:
        Executable paths; empty when nothing is installed.
    """
    tools = Path(__file__).resolve().parents[1] / ".tools" / "cmake"
    candidates = sorted(tools.glob("*/cmake-*/CMake.app/Contents/bin/cmake")) + sorted(
        tools.glob("*/cmake-*/bin/cmake")
    )
    default = _cmake_from_environment()
    if default is not None:
        candidates.append(default)
    by_version: dict[tuple[int, int, int], Path] = {}
    for candidate in candidates:
        if candidate.is_file():
            by_version.setdefault(cmake_version_of(candidate), candidate)
    return [by_version[version] for version in sorted(by_version)]


@pytest.fixture(params=installed_cmakes(), ids=_version_string)
def each_cmake(request: pytest.FixtureRequest) -> Path:
    """One test run per installed CMake version, for behaviour that may differ between lines.

    Args:
        request: pytest's parametrization handle.

    Returns:
        Path to the ``cmake`` executable for this run.
    """
    cmake: Path = request.param
    return cmake


@dataclass(frozen=True, kw_only=True)
class BuiltChain:
    """The ``chain`` fixture configured and built once with a particular CMake.

    Attributes:
        project: The build tree; still usable for further CMake invocations.
        version: Version of the CMake that built it.
        captures: Indexes the callback saved: ``postGenerate`` then ``postCMakeBuild``.
    """

    project: CMakeProject
    version: tuple[int, int, int]
    captures: tuple[CapturedIndex, ...]

    @property
    def build_capture(self) -> CapturedIndex:
        """The ``postCMakeBuild`` index, which carries the compile, link and custom steps."""
        return next(capture for capture in self.captures if capture.hook == "postCMakeBuild")


_BUILT_CHAINS: dict[Path, BuiltChain] = {}


@pytest.fixture
def built_chain(each_cmake: Path, tmp_path_factory: pytest.TempPathFactory) -> BuiltChain:
    """The chain fixture built once per installed CMake version and shared by read-only tests.

    A full configure and build costs a couple of seconds per version; tests
    that only parse what CMake wrote share one build instead of repeating it.
    The File API query is in place before configure, so the reply exists too.
    Tests that modify the tree must not use this fixture.

    Args:
        each_cmake: The CMake binary for this parametrised run.
        tmp_path_factory: pytest's session-wide temporary directory factory.

    Returns:
        The built project and everything the callback captured.
    """
    if each_cmake not in _BUILT_CHAINS:
        version = cmake_version_of(each_cmake)
        project = make_project("chain", tmp_path_factory.mktemp("built-chain"), each_cmake)
        project.write_query(
            InstrumentationQuery(
                hooks=("postGenerate", "postCMakeBuild"),
                callbacks=(project.callback,),
                options=instrumentation_options(version),
            )
        )
        fileapi.write_query(project.build_dir)
        project.configure()
        project.build()
        _BUILT_CHAINS[each_cmake] = BuiltChain(
            project=project, version=version, captures=tuple(project.captured())
        )
    return _BUILT_CHAINS[each_cmake]


@pytest.fixture
def chain_run(built_chain: BuiltChain) -> Run:
    """The chain fixture's build run assembled from what CMake wrote, graph included.

    Args:
        built_chain: The shared build.

    Returns:
        A run as `longpole capture` would have stored it.
    """
    capture = built_chain.build_capture
    index = snippets.read_index(
        capture.directory / "index.json", data_dir=capture.directory / "snippets"
    )
    reply = fileapi.read_reply(built_chain.project.build_dir)
    assert reply is not None
    return Run(
        manifest=Manifest(
            run_id=new_run_id(),
            created_at="2026-10-31T12:00:00+00:00",
            tool_version="test",
            hook=index.hook,
            build_dir=str(index.build_dir),
            data_version=index.version,
            environment=collect(built_chain.project.build_dir, reply=reply),
        ),
        steps=snippets.read_steps(index),
        graph=fileapi.graph_from_reply(reply),
        system=index.system,
    )
