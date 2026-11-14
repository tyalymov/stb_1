"""Environment collectors describe a checkout and a machine, and shrug at what is missing."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from longpole.env import ccache_stats, collect, cpu_count, parse_ccache_stats
from longpole.model import Environment
from tests.conftest import BuiltChain


def test_cpu_count_is_a_positive_integer() -> None:
    """Every machine the suite runs on reports its CPUs."""
    count = cpu_count()
    assert count is not None
    assert count >= 1


def test_print_stats_lines_become_a_mapping() -> None:
    """Tab-separated counters parse; anything else on a line is skipped."""
    text = "cache_hit_direct\t12\ncache_miss\t3\n\nSome human line\nfiles_in_cache\t0\n"
    assert parse_ccache_stats(text) == {
        "cache_hit_direct": "12",
        "cache_miss": "3",
        "files_in_cache": "0",
    }


def test_without_ccache_on_path_there_are_no_stats(monkeypatch: pytest.MonkeyPatch) -> None:
    """A machine without ccache is ordinary; the collector returns None."""
    monkeypatch.setenv("PATH", "")
    assert ccache_stats() is None


def test_ccache_on_path_is_asked_for_its_counters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whatever executable called ccache sits first on PATH gets ``--print-stats``."""
    fake = tmp_path / "ccache"
    fake.write_text(
        '#!/bin/sh\ntest "$1" = "--print-stats" || exit 2\nprintf "cache_hit_direct\\t7\\n"\n',
        encoding="utf-8",
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    assert ccache_stats() == {"cache_hit_direct": "7"}


def test_unconfigured_tree_collects_to_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A directory with no cache and no ccache gives an empty environment."""
    monkeypatch.setenv("PATH", "")
    environment = collect(tmp_path)
    assert environment == Environment(
        cmake_version=None, generator=None, cpu_count=cpu_count(), ccache=None
    )


@pytest.mark.cmake
@pytest.mark.slow
def test_built_tree_collects_cmake_version_and_generator(built_chain: BuiltChain) -> None:
    """A configured tree yields the CMake version from its cache and the generator."""
    environment = collect(built_chain.project.build_dir)
    assert environment.cmake_version == ".".join(map(str, built_chain.version))
    assert environment.generator == "Ninja"
    assert environment.cpu_count is not None
