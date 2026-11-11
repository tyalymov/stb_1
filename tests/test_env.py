"""Environment collectors describe a checkout and a machine, and shrug at what is missing."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

from longpole.cmake import fileapi
from longpole.env import ccache_stats, collect, cpu_count, git_info, parse_ccache_stats
from longpole.model import Environment
from tests.conftest import BuiltChain


def _git(repo: Path, *arguments: str) -> str:
    """Run git in a test repository with a fixed identity.

    Args:
        repo: Repository directory.
        *arguments: Git subcommand and options.

    Returns:
        Trimmed standard output.
    """
    completed = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", *arguments],
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def _repo_with_one_commit(root: Path) -> Path:
    """Create a repository with a single committed file.

    Args:
        root: Directory to create the repository in.

    Returns:
        The repository directory.
    """
    repo = root / "repo"
    repo.mkdir()
    _git(repo, "init", "--quiet")
    (repo / "file.txt").write_text("one\n", encoding="utf-8")
    _git(repo, "add", "file.txt")
    _git(repo, "commit", "--quiet", "-m", "one")
    return repo


def test_clean_checkout_reports_commit_and_branch(tmp_path: Path) -> None:
    """A fresh commit on the default branch: full sha, branch name, not dirty."""
    repo = _repo_with_one_commit(tmp_path)
    info = git_info(repo)
    assert info is not None
    assert info.sha == _git(repo, "rev-parse", "HEAD")
    assert len(info.sha) == 40
    assert info.branch == _git(repo, "symbolic-ref", "--short", "HEAD")
    assert info.dirty is False


def test_modified_tracked_file_is_dirty_but_untracked_is_not(tmp_path: Path) -> None:
    """Dirtiness tracks committed files only; a stray file does not count."""
    repo = _repo_with_one_commit(tmp_path)
    (repo / "scratch.txt").write_text("ignored\n", encoding="utf-8")
    info = git_info(repo)
    assert info is not None
    assert info.dirty is False

    (repo / "file.txt").write_text("two\n", encoding="utf-8")
    info = git_info(repo)
    assert info is not None
    assert info.dirty is True


def test_detached_head_has_no_branch(tmp_path: Path) -> None:
    """Checking out a commit directly leaves the branch unknown."""
    repo = _repo_with_one_commit(tmp_path)
    _git(repo, "checkout", "--quiet", "--detach")
    info = git_info(repo)
    assert info is not None
    assert info.branch is None


def test_subdirectory_resolves_to_the_same_checkout(tmp_path: Path) -> None:
    """A build tree inside the repository sees the repository's state."""
    repo = _repo_with_one_commit(tmp_path)
    build = repo / "build"
    build.mkdir()
    info = git_info(build)
    assert info is not None
    assert info.sha == _git(repo, "rev-parse", "HEAD")


def test_outside_any_repository_is_none(tmp_path: Path) -> None:
    """A directory with no repository above it yields no git info, quietly."""
    plain = tmp_path / "plain"
    plain.mkdir()
    assert git_info(plain) is None


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
    """A directory with no cache, no repository and no ccache gives an empty environment."""
    monkeypatch.setenv("PATH", "")
    environment = collect(tmp_path, reply=None)
    assert environment == Environment(
        cmake_version=None,
        generator=None,
        cpu_count=cpu_count(),
        git=None,
        toolchains=(),
        ccache=None,
    )


@pytest.mark.cmake
@pytest.mark.slow
def test_built_tree_collects_cmake_generator_and_toolchain(built_chain: BuiltChain) -> None:
    """A configured tree yields the exact CMake version, its generator and the C compiler."""
    reply = fileapi.read_reply(fileapi.reply_dir(built_chain.project.build_dir))
    environment = collect(built_chain.project.build_dir, reply=reply)
    assert environment.cmake_version is not None
    assert environment.cmake_version.startswith(".".join(map(str, built_chain.version)))
    assert environment.generator == "Ninja"
    assert environment.cpu_count is not None
    assert [toolchain.language for toolchain in environment.toolchains] == ["C"]
    assert environment.git is None
