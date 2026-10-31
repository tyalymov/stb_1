"""The cmake binary and a build tree's cache answer the questions longpole asks them."""

from __future__ import annotations

from pathlib import Path

import pytest

from longpole.cmake.tooling import (
    CacheInfo,
    config_dir,
    data_version_for_line,
    describe,
    read_cache,
)
from longpole.errors import CMakeToolError
from longpole.model import DataVersion
from tests.conftest import BuiltChain, cmake_version_of


@pytest.mark.cmake
def test_describe_reports_version_and_data_version(each_cmake: Path) -> None:
    """Every installed CMake reports its version; the data version follows the release line."""
    info = describe(each_cmake)
    expected_version = cmake_version_of(each_cmake)
    assert info.executable == each_cmake
    assert info.version_tuple == expected_version
    assert info.version.startswith(".".join(map(str, expected_version)))
    assert info.supports_instrumentation
    assert info.data_version == DataVersion(major=1, minor={3: 0, 4: 1, 5: 2}[expected_version[1]])


@pytest.mark.cmake
def test_config_dir_is_an_absolute_unescaped_path(each_cmake: Path) -> None:
    """``--print-config-dir`` escapes spaces; the result is a plain absolute path."""
    directory = config_dir(each_cmake)
    assert directory.is_absolute()
    assert "\\" not in str(directory)


def test_describe_fails_loudly_for_a_missing_binary(tmp_path: Path) -> None:
    """A path that is not an executable is a CMakeToolError, not an OSError."""
    with pytest.raises(CMakeToolError, match="cannot run"):
        describe(tmp_path / "no-such-cmake")


def test_data_version_by_release_line() -> None:
    """4.3, 4.4 and 4.5 map to 1.0, 1.1 and 1.2; newer lines assume the latest known."""
    assert data_version_for_line((4, 3, 6)) == DataVersion(major=1, minor=0)
    assert data_version_for_line((4, 4, 5)) == DataVersion(major=1, minor=1)
    assert data_version_for_line((4, 5, 0)) == DataVersion(major=1, minor=2)
    assert data_version_for_line((4, 9, 0)) == DataVersion(major=1, minor=2)
    assert data_version_for_line((4, 2, 3)) is None


def test_missing_cache_reads_as_nothing(tmp_path: Path) -> None:
    """A tree that was never configured has no cache; that is not an error."""
    assert read_cache(tmp_path) == CacheInfo(
        generator=None, cmake_command=None, source_dir=None, cmake_version=None
    )


def test_cache_entries_are_parsed(tmp_path: Path) -> None:
    """Only INTERNAL entries are read, and only the ones we care about."""
    (tmp_path / "CMakeCache.txt").write_text(
        "\n".join(
            [
                "# This is the CMakeCache file.",
                "CMAKE_BUILD_TYPE:STRING=",
                "CMAKE_CACHE_MAJOR_VERSION:INTERNAL=4",
                "CMAKE_CACHE_MINOR_VERSION:INTERNAL=4",
                "CMAKE_CACHE_PATCH_VERSION:INTERNAL=5",
                "CMAKE_COMMAND:INTERNAL=/opt/cmake/bin/cmake",
                "CMAKE_GENERATOR:INTERNAL=Unix Makefiles",
                "CMAKE_HOME_DIRECTORY:INTERNAL=/src/project",
            ]
        ),
        encoding="utf-8",
    )
    assert read_cache(tmp_path) == CacheInfo(
        generator="Unix Makefiles",
        cmake_command=Path("/opt/cmake/bin/cmake"),
        source_dir=Path("/src/project"),
        cmake_version="4.4.5",
    )


@pytest.mark.cmake
@pytest.mark.slow
def test_real_cache_names_the_configuring_cmake(built_chain: BuiltChain) -> None:
    """The cache of a tree built by a pinned CMake points back at that binary."""
    cache = read_cache(built_chain.project.build_dir)
    assert cache.generator == "Ninja"
    assert cache.cmake_command is not None
    assert cache.cmake_command.resolve() == built_chain.project.cmake.resolve()
    assert cache.source_dir is not None
    assert cache.source_dir.resolve() == built_chain.project.source_dir.resolve()
    assert cache.cmake_version == ".".join(map(str, built_chain.version))
