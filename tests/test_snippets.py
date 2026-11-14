"""Indexes and snippets become Steps, with every data version and every edge case covered."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from longpole.cmake.snippets import (
    KNOWN_SNIPPET_FIELDS,
    read_index,
    read_snippet,
    read_steps,
    step_from_snippet,
)
from longpole.errors import IndexFormatError, SchemaError, SnippetFormatError
from longpole.jsonfields import Json
from longpole.model import DataVersion
from tests.conftest import BuiltChain


def _compile_snippet(minor: int = 0, **overrides: object) -> Json:
    """A compile snippet of the given data minor version."""
    snippet: Json = {
        "version": {"major": 1, "minor": minor},
        "role": "compile",
        "command": "cc -c /src/a.c -o a.o",
        "workingDir": "/build",
        "result": 0,
        "timeStart": 1700000000000,
        "duration": 12,
        "target": "a",
        "targetLabels": ["core"],
        "language": "C",
        "source": "/src/a.c",
        "outputs": ["a.o"],
        "outputSizes": [100],
        "config": "",
        "cmakeContent": "content/cmake-1.json",
    }
    snippet.update(overrides)
    return snippet


def test_known_fields_are_renamed_and_typed() -> None:
    """Every documented 1.0 field lands in its Step attribute."""
    step = step_from_snippet(_compile_snippet())
    assert step.role == "compile"
    assert step.command == "cc -c /src/a.c -o a.o"
    assert step.working_dir == "/build"
    assert step.result == 0
    assert step.time_start_ms == 1700000000000
    assert step.duration_ms == 12
    assert step.time_end_ms == 1700000000012
    assert step.target == "a"
    assert step.target_labels == ("core",)
    assert step.language == "C"
    assert step.source == "/src/a.c"
    assert step.outputs == ("a.o",)
    assert step.output_sizes == (100,)
    assert step.config == ""
    assert step.cmake_content == "content/cmake-1.json"
    assert step.extra == {}


def test_fields_of_newer_data_versions_are_none_when_absent() -> None:
    """A 1.0 snippet has no trace file, output capture, signal or metrics."""
    step = step_from_snippet(_compile_snippet())
    assert step.trace_file is None
    assert step.stdout is None
    assert step.stderr is None
    assert step.interrupt_signal is None
    assert step.process_metrics is None
    assert step.host_load is None


def test_data_1_2_fields_are_read() -> None:
    """Process metrics and host load are typed when present."""
    step = step_from_snippet(
        _compile_snippet(
            minor=2,
            processMetrics={"maxRSS": 27216, "userTime": 10889, "systemTime": 9964},
            dynamicSystemInformation={
                "beforeHostMemoryUsed": 27907856.0,
                "afterHostMemoryUsed": 27908112.0,
                "beforeCPULoadAverage": 3.5,
                "afterCPULoadAverage": 3.6,
            },
            traceFile=None,
        )
    )
    assert step.process_metrics is not None
    assert step.process_metrics.max_rss_kib == 27216
    assert step.process_metrics.cpu_time_us == 10889 + 9964
    assert step.host_load is not None
    assert step.host_load.load_average_after == 3.6
    assert step.trace_file is None


def test_null_metrics_and_result_are_none() -> None:
    """CMake writes ``null`` where it could not measure; that is not an error."""
    step = step_from_snippet(_compile_snippet(minor=2, processMetrics=None, result=None))
    assert step.process_metrics is None
    assert step.result is None


def test_build_role_has_no_command() -> None:
    """The ``build`` role is the one snippet CMake writes without a command."""
    snippet: Json = {
        "version": {"major": 1, "minor": 0},
        "role": "build",
        "workingDir": "/build",
        "result": None,
        "timeStart": 1,
        "duration": 2,
    }
    step = step_from_snippet(snippet)
    assert step.role == "build"
    assert step.command is None
    assert step.outputs == ()


def test_unknown_fields_survive_in_extra() -> None:
    """A field this version does not know is kept verbatim, not dropped."""
    step = step_from_snippet(_compile_snippet(futureField={"nested": [1, 2]}))
    assert step.extra == {"futureField": {"nested": [1, 2]}}
    assert "futureField" not in KNOWN_SNIPPET_FIELDS


def test_wrong_type_is_a_format_error_naming_the_field() -> None:
    """Without schema validation, a mistyped field is still caught, by name."""
    with pytest.raises(SnippetFormatError, match="'duration'"):
        step_from_snippet(_compile_snippet(duration="12"))


def test_read_snippet_validates_against_the_schema(tmp_path: Path) -> None:
    """Reading a file validates it unless told otherwise."""
    path = tmp_path / "compile-x.json"
    snippet = _compile_snippet()
    del snippet["timeStart"]
    path.write_text(json.dumps(snippet), encoding="utf-8")
    with pytest.raises(SchemaError, match="timeStart"):
        read_snippet(path)
    with pytest.raises(SnippetFormatError, match="'timeStart'"):
        read_snippet(path, validate_schema=False)


def _write_index(directory: Path, snippets: list[str], **overrides: object) -> Path:
    """Write a minimal index file into ``directory``."""
    index: Json = {
        "version": {"major": 1, "minor": 0},
        "buildDir": str(directory / "build"),
        "dataDir": str(directory),
        "hook": "postCMakeBuild",
        "snippets": snippets,
    }
    index.update(overrides)
    path = directory / "index.json"
    path.write_text(json.dumps(index), encoding="utf-8")
    return path


def test_empty_index_yields_no_steps(tmp_path: Path) -> None:
    """``postCMakeWorkflow`` hands over an index with no snippets; that reads as zero steps."""
    index = read_index(_write_index(tmp_path, [], hook="postCMakeWorkflow"))
    assert index.hook == "postCMakeWorkflow"
    assert index.snippet_paths == ()
    assert read_steps(index) == ()


def test_index_resolves_snippets_against_data_dir(tmp_path: Path) -> None:
    """Snippet paths are joined to the recorded dataDir, or to an override for relocated copies."""
    (tmp_path / "compile-1.json").write_text(json.dumps(_compile_snippet()), encoding="utf-8")
    path = _write_index(tmp_path, ["compile-1.json"])

    index = read_index(path)
    assert index.snippet_paths == (tmp_path / "compile-1.json",)
    assert index.version == DataVersion(major=1, minor=0)
    assert index.build_dir == tmp_path / "build"
    assert index.system is None
    assert index.trace_path is None
    assert [step.role for step in read_steps(index)] == ["compile"]

    elsewhere = tmp_path / "copy"
    elsewhere.mkdir()
    relocated = read_index(path, data_dir=elsewhere)
    assert relocated.snippet_paths == (elsewhere / "compile-1.json",)


def test_missing_snippet_is_reported_as_file_not_found(tmp_path: Path) -> None:
    """A listed snippet that is gone is a real failure, not an empty step."""
    index = read_index(_write_index(tmp_path, ["compile-gone.json"]))
    with pytest.raises(FileNotFoundError):
        read_steps(index)


def test_non_json_index_is_a_format_error(tmp_path: Path) -> None:
    """Garbage in the index file is named as such."""
    path = tmp_path / "index.json"
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(IndexFormatError, match="not JSON"):
        read_index(path)


@pytest.mark.cmake
@pytest.mark.slow
def test_real_indexes_read_back_with_every_step(built_chain: BuiltChain) -> None:
    """What CMake wrote for the chain fixture reads into steps whose roles match the raw counts."""
    expected_minor = {3: 0, 4: 1, 5: 2}[built_chain.version[1]]
    for capture in built_chain.captures:
        index = read_index(
            capture.directory / "index.json", data_dir=capture.directory / "snippets"
        )
        steps = read_steps(index)

        assert index.version == DataVersion(major=1, minor=expected_minor)
        assert index.hook == capture.hook
        assert index.build_dir == built_chain.project.build_dir
        roles: dict[str, int] = {}
        for step in steps:
            roles[step.role] = roles.get(step.role, 0) + 1
        assert roles == capture.roles()
        assert all(step.extra == {} for step in steps)

        assert index.system is not None
        assert index.system.logical_cpus is not None
        assert index.system.logical_cpus > 0


@pytest.mark.cmake
@pytest.mark.slow
def test_real_compile_steps_carry_what_their_data_version_allows(built_chain: BuiltChain) -> None:
    """Host load is present on every line; process metrics only from data 1.2 on."""
    capture = built_chain.build_capture
    index = read_index(capture.directory / "index.json", data_dir=capture.directory / "snippets")
    compiles = [step for step in read_steps(index) if step.role == "compile"]

    assert {step.target for step in compiles} == {"a", "b", "c"}
    assert all(step.host_load is not None for step in compiles)
    assert all(step.output_sizes[0] > 0 for step in compiles)
    has_metrics = all(step.process_metrics is not None for step in compiles)
    assert has_metrics == (built_chain.version[:2] >= (4, 5))
