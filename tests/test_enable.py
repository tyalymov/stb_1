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
from longpole.store import IN_TREE, RunStore
from tests.conftest import cmake_version_of, make_project

CAPTURE_HOOKS = [
    "postGenerate",
    "postCMakeBuild",
    "postCMakeInstall",
    "postCMakeWorkflow",
    "postCTest",
]
"""The hooks `enable` asks for."""


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
    assert old["callbacks"] == ["longpole capture"]
    # every post-hook of cmake and ctest; not the generator's own preBuild/postBuild
    assert old["hooks"] == new["hooks"] == CAPTURE_HOOKS


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


def test_labels_and_store_go_into_the_callback(monkeypatch: pytest.MonkeyPatch) -> None:
    """The callback gets nothing from the enabling shell, so labels and the store ride along."""
    monkeypatch.setattr(shutil, "which", _which("/opt/tools/bin/longpole"))
    command = enable.callback_command(labels=("nightly", "ci:42"), store=Path("/opt/runs/my store"))
    assert command == (
        '/opt/tools/bin/longpole capture --label nightly --label ci:42 --store "/opt/runs/my store"'
    )


@pytest.mark.cmake
def test_enable_writes_query_graph_request_and_store_marker(
    tmp_path: Path, cmake_on_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Enabling a tree leaves exactly the files CMake and longpole need, with the right minor."""
    build = tmp_path / "build"
    assert main(["enable", str(build)]) == 0

    query = build / ".cmake" / "instrumentation" / "v1" / "query" / "longpole.json"
    document = json.loads(query.read_text(encoding="utf-8"))
    expected_minor = tooling.data_version_for_line(cmake_version_of(cmake_on_path))
    assert expected_minor is not None
    assert document["version"] == {"major": 1, "minor": expected_minor.minor}
    assert document["hooks"] == CAPTURE_HOOKS
    assert document["callbacks"] == [enable.callback_command()]
    assert fileapi.query_path(build).is_file()
    assert (build / IN_TREE).is_dir()

    out = capsys.readouterr().out
    assert out.startswith(f"enabled: {query}")
    assert "run cmake again" not in out


@pytest.mark.cmake
def test_enable_is_idempotent_and_json_reports_the_same_paths(
    tmp_path: Path, cmake_on_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A second enable rewrites identical content; --json carries the paths as strings."""
    build = tmp_path / "build"
    assert main(["enable", str(build)]) == 0
    first = (build / ".cmake" / "instrumentation" / "v1" / "query" / "longpole.json").read_bytes()
    capsys.readouterr()

    assert main(["enable", str(build), "--output-format", "json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert Path(report["query"]).read_bytes() == first
    assert report["regenerate_needed"] is False
    assert report["file_api_query"] == str(fileapi.query_path(build))


@pytest.mark.cmake
def test_disable_deletes_the_queries_but_keeps_the_store(
    tmp_path: Path, cmake_on_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`disable` undoes `enable` except for the store, which may already hold runs."""
    build = tmp_path / "build"
    assert main(["enable", str(build)]) == 0
    assert main(["disable", str(build)]) == 0
    query = build / ".cmake" / "instrumentation" / "v1" / "query" / "longpole.json"
    assert not query.exists()
    assert not fileapi.query_path(build).exists()
    assert (build / IN_TREE).is_dir()
    assert capsys.readouterr().out.splitlines()[-1] == f"removed {query}"

    assert main(["disable", str(build)]) == 0


@pytest.mark.cmake
@pytest.mark.slow
def test_configured_tree_gets_the_regenerate_hint(
    tmp_path: Path, cmake_on_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Enabling after configure works only after another cmake run, and the command says so."""
    project = make_project("chain", tmp_path, cmake_on_path)
    project.configure()
    assert main(["enable", str(project.build_dir), "--output-format", "json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["regenerate_needed"] is True

    assert main(["enable", str(project.build_dir)]) == 0
    assert "run cmake again" in capsys.readouterr().out


@pytest.mark.cmake
@pytest.mark.slow
def test_enable_with_label_and_store_records_there_with_the_label(
    tmp_path: Path, cmake_on_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What enable was given reaches every run; an explicit store leaves no in-tree one."""
    monkeypatch.delenv("LONGPOLE_STORE", raising=False)
    project = make_project("chain", tmp_path, cmake_on_path)
    store_dir = tmp_path / "my store"
    assert (
        main(["enable", str(project.build_dir), "--label", "nightly", "--store", str(store_dir)])
        == 0
    )
    assert not (project.build_dir / IN_TREE).exists()
    project.configure()
    project.build()

    store = RunStore(store_dir)
    assert [summary.hook for summary in store.summaries()] == ["postGenerate", "postCMakeBuild"]
    assert store.read("latest").manifest.labels == ("nightly",)


def test_missing_cmake_is_a_warning_not_a_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Nothing on PATH: the query is still written, for data version 1.0, with a warning."""
    monkeypatch.setattr(shutil, "which", _which(None))
    build = tmp_path / "build"
    assert main(["enable", str(build)]) == 0
    captured = capsys.readouterr()
    assert "WARNING: no cmake on PATH" in captured.err
    assert captured.out.startswith("enabled: ")
    document = json.loads(
        (build / ".cmake" / "instrumentation" / "v1" / "query" / "longpole.json").read_text()
    )
    assert document["version"] == {"major": 1, "minor": 0}
    assert "processMetrics" not in document["options"]


def test_old_cmake_is_a_warning_not_a_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A CMake older than 4.3 records nothing; the query is written anyway and the user is told."""

    def describe(cmake: Path, *, timeout: float = 30.0) -> tooling.CMakeInfo:
        return tooling.CMakeInfo(
            executable=cmake, version="3.31.9", version_tuple=(3, 31, 9), data_version=None
        )

    monkeypatch.setattr(shutil, "which", _which("/fake/cmake"))
    monkeypatch.setattr(tooling, "describe", describe)
    assert main(["enable", str(tmp_path / "build")]) == 0
    captured = capsys.readouterr()
    assert "WARNING: cmake on PATH is 3.31.9, older than 4.3" in captured.err
    assert captured.out.startswith("enabled: ")
