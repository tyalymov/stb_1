"""`longpole enable` writes the query CMake needs and nothing more."""

from __future__ import annotations

import json
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from longpole.cli import main
from longpole.cmake import fileapi, tooling
from longpole.commands import enable
from longpole.model import DataVersion
from longpole.store import IN_TREE
from tests.conftest import cmake_version_of, make_project


def _which(found: str | None) -> Callable[[str], str | None]:
    """A stand-in for ``shutil.which`` that answers the same path for every name.

    Args:
        found: The path to report, or ``None`` for "not installed".

    Returns:
        The replacement function.
    """

    def which(_name: str) -> str | None:
        return found

    return which


def test_query_asks_for_process_metrics_only_from_data_1_2() -> None:
    """Only data 1.2 (CMake 4.5) has processMetrics; an older CMake ignores a query that asks."""
    old = enable.query_document(DataVersion(major=1, minor=0), "longpole capture")
    new = enable.query_document(DataVersion(major=1, minor=2), "longpole capture")
    assert old["version"] == {"major": 1, "minor": 0}
    assert old["options"] == ["staticSystemInformation", "dynamicSystemInformation"]
    assert new["options"] == [
        "staticSystemInformation",
        "dynamicSystemInformation",
        "processMetrics",
    ]
    assert old["hooks"] == new["hooks"] == list(enable.HOOKS)
    assert old["callbacks"] == ["longpole capture"]


def test_hooks_cover_cmake_and_ctest_but_not_the_build_tool() -> None:
    """Every post-hook of cmake and ctest is requested; the generator's own hooks are not."""
    assert set(enable.HOOKS) == {
        "postGenerate",
        "postCMakeBuild",
        "postCMakeInstall",
        "postCMakeWorkflow",
        "postCTest",
    }


def test_callback_prefers_the_installed_script(monkeypatch: pytest.MonkeyPatch) -> None:
    """With a `longpole` script on PATH the callback names it by absolute path."""
    monkeypatch.setattr(shutil, "which", _which("/opt/tools/bin/longpole"))
    assert enable.callback_command() == "/opt/tools/bin/longpole capture"


def test_callback_falls_back_to_this_interpreter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a script the callback runs the package through the current Python."""
    monkeypatch.setattr(shutil, "which", _which(None))
    assert enable.callback_command() == f"{sys.executable} -m longpole capture"


def test_paths_with_spaces_are_quoted(monkeypatch: pytest.MonkeyPatch) -> None:
    """A path with a space is wrapped in double quotes so CMake keeps it as one argument."""
    monkeypatch.setattr(shutil, "which", _which("/Applications/Dev Tools/longpole"))
    assert enable.callback_command() == '"/Applications/Dev Tools/longpole" capture'


@pytest.mark.cmake
def test_enable_writes_query_graph_request_and_store_marker(
    tmp_path: Path, cmake_binary: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Enabling a tree leaves exactly the files CMake and longpole need, with the right minor."""
    build = tmp_path / "build"
    assert main(["enable", str(build), "--cmake", str(cmake_binary)]) == 0

    query = build / ".cmake" / "instrumentation" / "v1" / "query" / "longpole.json"
    document = json.loads(query.read_text(encoding="utf-8"))
    expected_minor = tooling.data_version_for_line(cmake_version_of(cmake_binary))
    assert expected_minor is not None
    assert document["version"] == {"major": 1, "minor": expected_minor.minor}
    assert document["hooks"] == list(enable.HOOKS)
    assert document["callbacks"] == [enable.callback_command()]
    assert fileapi.query_path(build).is_file()
    assert (build / IN_TREE).is_dir()

    out = capsys.readouterr().out
    assert out.startswith(f"enabled (tree): {query}")
    assert "run cmake again" not in out


@pytest.mark.cmake
def test_enable_is_idempotent_and_json_reports_the_same_paths(
    tmp_path: Path, cmake_binary: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A second enable rewrites identical content; --json carries the paths as strings."""
    build = tmp_path / "build"
    assert main(["enable", str(build), "--cmake", str(cmake_binary)]) == 0
    first = (build / ".cmake" / "instrumentation" / "v1" / "query" / "longpole.json").read_bytes()
    capsys.readouterr()

    assert main(["--json", "enable", str(build), "--cmake", str(cmake_binary)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert Path(report["query"]).read_bytes() == first
    assert report["scope"] == "tree"
    assert report["removed"] is False
    assert report["regenerate_needed"] is False
    assert report["file_api_query"] == str(fileapi.query_path(build))


@pytest.mark.cmake
def test_remove_deletes_the_queries_but_keeps_the_store(
    tmp_path: Path, cmake_binary: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """--remove undoes enable except for the store, which may already hold runs."""
    build = tmp_path / "build"
    assert main(["enable", str(build), "--cmake", str(cmake_binary)]) == 0
    assert main(["enable", str(build), "--cmake", str(cmake_binary), "--remove"]) == 0
    query = build / ".cmake" / "instrumentation" / "v1" / "query" / "longpole.json"
    assert not query.exists()
    assert not fileapi.query_path(build).exists()
    assert (build / IN_TREE).is_dir()
    assert capsys.readouterr().out.splitlines()[-1] == f"removed {query}"

    assert main(["enable", str(build), "--cmake", str(cmake_binary), "--remove"]) == 0


@pytest.mark.cmake
def test_global_writes_to_cmake_config_dir(
    tmp_path: Path,
    cmake_binary: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--global writes where `cmake --print-config-dir` points, which honours CMAKE_CONFIG_DIR."""
    monkeypatch.setenv("CMAKE_CONFIG_DIR", str(tmp_path / "config"))
    assert main(["enable", "--global", "--cmake", str(cmake_binary)]) == 0
    query = tmp_path / "config" / "instrumentation" / "v1" / "query" / "longpole.json"
    assert query.is_file()
    assert capsys.readouterr().out.startswith(f"enabled (global): {query}")
    assert not (tmp_path / "config" / ".cmake").exists()


@pytest.mark.cmake
@pytest.mark.slow
def test_configured_tree_gets_the_regenerate_hint(
    tmp_path: Path, cmake_binary: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Enabling after configure works only after another cmake run, and the command says so.

    Without --cmake the command consults the cmake recorded in the tree's cache.
    """
    project = make_project("chain", tmp_path, cmake_binary)
    project.configure()
    assert main(["--json", "enable", str(project.build_dir)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["regenerate_needed"] is True
    assert report["cmake_version"].startswith(
        ".".join(map(str, cmake_version_of(cmake_binary)[:2]))
    )

    assert main(["enable", str(project.build_dir)]) == 0
    assert "run cmake again" in capsys.readouterr().out


def test_missing_cmake_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No --cmake, no cache, nothing on PATH: a clear message and exit code 1."""
    monkeypatch.setattr(shutil, "which", _which(None))
    assert main(["enable", str(tmp_path / "build")]) == 1
    assert "no cmake executable found on PATH; pass --cmake" in capsys.readouterr().err


def test_old_cmake_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A CMake older than 4.3 has no instrumentation API; the command refuses to write a query."""

    def describe(cmake: Path, *, timeout: float = 30.0) -> tooling.CMakeInfo:
        return tooling.CMakeInfo(
            executable=cmake, version="3.31.9", version_tuple=(3, 31, 9), data_version=None
        )

    monkeypatch.setattr(tooling, "describe", describe)
    assert main(["enable", str(tmp_path / "build"), "--cmake", "/fake/cmake"]) == 1
    err = capsys.readouterr().err
    assert "CMake 3.31.9" in err
    assert "4.3 or newer" in err
    assert not (tmp_path / "build").exists()
