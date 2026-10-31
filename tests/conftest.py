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
from tests.support.runs import run_from_capture

FIXTURES = Path(__file__).parent / "fixtures"
MINIMUM_VERSION = (4, 3)


def _cmake_from_environment() -> Path | None:
    """Resolve the CMake binary the suite should use.

    ``LONGPOLE_CMAKE`` may point at the ``cmake`` executable or at the
    directory that contains it, as an absolute or a relative path; without
    it we take whatever is on ``PATH``.

    Returns:
        Absolute path to an executable ``cmake``, or ``None`` if there is none.
    """
    configured = os.environ.get("LONGPOLE_CMAKE")
    if configured:
        candidate = Path(configured).expanduser().resolve()
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
def built_chain(cmake_binary: Path, tmp_path_factory: pytest.TempPathFactory) -> BuiltChain:
    """The chain fixture built once per session and shared by read-only tests.

    A full configure and build costs a couple of seconds; tests that only
    parse what CMake wrote share one build instead of repeating it. The File
    API query is in place before configure, so the reply exists too. Tests
    that modify the tree must not use this fixture.

    Args:
        cmake_binary: The session's CMake.
        tmp_path_factory: pytest's session-wide temporary directory factory.

    Returns:
        The built project and everything the callback captured.
    """
    if cmake_binary not in _BUILT_CHAINS:
        version = cmake_version_of(cmake_binary)
        project = make_project("chain", tmp_path_factory.mktemp("built-chain"), cmake_binary)
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
        _BUILT_CHAINS[cmake_binary] = BuiltChain(
            project=project, version=version, captures=tuple(project.captured())
        )
    return _BUILT_CHAINS[cmake_binary]


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


_FANOUT_RUNS: dict[Path, Run] = {}


@pytest.fixture
def fanout_run(cmake_binary: Path, tmp_path_factory: pytest.TempPathFactory) -> Run:
    """The fan-out fixture built once with eight jobs, with its graph, for parallelism tests.

    Eight independent generators overlap when the build tool may run eight
    jobs, which is what the utilization and critical path analyzers need to
    see. Built once per session with the default CMake; the tree is not
    exposed, so nothing can modify it.

    Args:
        cmake_binary: The session's CMake.
        tmp_path_factory: pytest's session-wide temporary directory factory.

    Returns:
        The ``postCMakeBuild`` run of that build.
    """
    if cmake_binary not in _FANOUT_RUNS:
        project = make_project("fanout", tmp_path_factory.mktemp("built-fanout"), cmake_binary)
        project.write_query(
            InstrumentationQuery(
                hooks=("postCMakeBuild",),
                callbacks=(project.callback,),
                options=instrumentation_options(cmake_version_of(cmake_binary)),
            )
        )
        fileapi.write_query(project.build_dir)
        project.configure()
        project.build("--parallel", "8")
        capture = next(c for c in project.captured() if c.hook == "postCMakeBuild")
        _FANOUT_RUNS[cmake_binary] = run_from_capture(capture, project.build_dir)
    return _FANOUT_RUNS[cmake_binary]
