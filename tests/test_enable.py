"""`longpole enable` writes the query CMake needs and nothing more."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from longpole.cli import main
from longpole.cmake import fileapi, tooling
from longpole.commands import enable
from longpole.model import DataVersion
from longpole.store import RunStore
from tests.conftest import cmake_version_of, make_project

CAPTURE_HOOKS = [
    "postGenerate",
    "postCMakeBuild",
    "postCMakeInstall",
    "postCMakeWorkflow",
    "postCTest",
]
"""The hooks `enable` asks for."""


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


def test_labels_and_store_go_into_the_callback() -> None:
    """The callback gets nothing from the enabling shell, so labels and the store ride along."""
    command = enable.callback_command(store=Path("/opt/runs/my store"), labels=("nightly", "ci:42"))
    assert command == (
        'longpole capture --store "/opt/runs/my store" --label "nightly" --label "ci:42"'
    )


@pytest.mark.cmake
def test_enable_writes_query_graph_request_and_store_marker(
    tmp_path: Path, cmake_on_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Enabling a tree leaves exactly the files CMake and longpole need, with the right minor."""
    build = tmp_path / "build"
    assert main(["enable", str(build), "--store", str(tmp_path / "store")]) == 0

    query = build / ".cmake" / "instrumentation" / "v1" / "query" / "longpole.json"
    document = json.loads(query.read_text(encoding="utf-8"))
    expected_minor = tooling.data_version_for_line(cmake_version_of(cmake_on_path))
    assert expected_minor is not None
    assert document["version"] == {"major": 1, "minor": expected_minor.minor}
    assert document["hooks"] == CAPTURE_HOOKS
    assert document["callbacks"] == [enable.callback_command(store=tmp_path / "store")]
    assert fileapi.query_path(build).is_file()

    assert capsys.readouterr().out.startswith(f"enabled: {query}")


@pytest.mark.cmake
def test_enable_is_idempotent_and_json_reports_the_same_paths(
    tmp_path: Path, cmake_on_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A second enable rewrites identical content; --json carries the paths as strings."""
    build = tmp_path / "build"
    assert main(["enable", str(build), "--store", str(tmp_path / "store")]) == 0
    first = (build / ".cmake" / "instrumentation" / "v1" / "query" / "longpole.json").read_bytes()
    capsys.readouterr()

    store = str(tmp_path / "store")
    assert main(["enable", str(build), "--store", store, "--output-format", "json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert Path(report["query"]).read_bytes() == first
    assert report["file_api_query"] == str(fileapi.query_path(build))


@pytest.mark.cmake
def test_disable_deletes_the_queries_but_keeps_the_store(
    tmp_path: Path, cmake_on_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`disable` undoes `enable` except for the store, which may already hold runs."""
    build = tmp_path / "build"
    assert main(["enable", str(build), "--store", str(tmp_path / "store")]) == 0
    assert main(["disable", str(build)]) == 0
    query = build / ".cmake" / "instrumentation" / "v1" / "query" / "longpole.json"
    assert not query.exists()
    assert not fileapi.query_path(build).exists()
    assert capsys.readouterr().out.splitlines()[-1] == f"removed {query}"

    assert main(["disable", str(build)]) == 0


@pytest.mark.cmake
@pytest.mark.slow
def test_enable_with_label_and_store_records_there_with_the_label(
    tmp_path: Path, cmake_on_path: Path
) -> None:
    """What enable was given reaches every run; an explicit store leaves no in-tree one."""
    project = make_project("chain", tmp_path, cmake_on_path)
    store_dir = tmp_path / "my store"
    assert (
        main(["enable", str(project.build_dir), "--label", "nightly", "--store", str(store_dir)])
        == 0
    )
    project.configure()
    project.build()

    store = RunStore(store_dir)
    assert [summary.hook for summary in store.summaries()] == ["postGenerate", "postCMakeBuild"]
    assert store.read("latest").manifest.labels == ("nightly",)


def test_missing_cmake_is_a_warning_not_a_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No cmake on PATH: the query is still written, for data version 1.0, with a warning."""
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    build = tmp_path / "build"
    assert main(["enable", str(build), "--store", str(tmp_path / "store")]) == 0
    captured = capsys.readouterr()
    assert "WARNING: cannot run cmake" in captured.err
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

    monkeypatch.setattr(tooling, "describe", describe)
    assert main(["enable", str(tmp_path / "build"), "--store", str(tmp_path / "store")]) == 0
    captured = capsys.readouterr()
    assert "WARNING: cmake is 3.31.9, older than 4.3" in captured.err
    assert captured.out.startswith("enabled: ")
