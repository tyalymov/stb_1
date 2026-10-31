"""`longpole capture`, the callback: from a CMake index to a stored run."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from longpole.cmake import fileapi
from longpole.commands import capture
from longpole.model import Run
from longpole.store import RunStore
from tests.conftest import cmake_version_of, make_project
from tests.support.cmake import CMakeProject, InstrumentationQuery, instrumentation_options

CHAIN_BUILD_ROLES = {"custom": 3, "compile": 3, "link": 3, "cmakeBuild": 1}


def _instrument(project: CMakeProject, store: Path, *hooks: str, label: str | None = None) -> None:
    """Point a fixture project at the real callback and at a store of the test's own.

    Args:
        project: The fixture project.
        store: Where the callback should write runs (through ``LONGPOLE_STORE``).
        *hooks: Hooks to request.
        label: ``--label`` value to pass to the callback, if any.
    """
    callback = f"{sys.executable} -m longpole capture"
    if label is not None:
        callback += f" --label {label}"
    project.set_env(LONGPOLE_STORE=str(store), GITHUB_RUN_ID="4242")
    project.write_query(
        InstrumentationQuery(
            hooks=hooks,
            callbacks=(callback,),
            options=instrumentation_options(cmake_version_of(project.cmake)),
        )
    )


def _newest(store: RunStore, hook: str) -> Run:
    """The newest stored run of a hook.

    Args:
        store: The store.
        hook: The hook whose run to read.

    Returns:
        The run.
    """
    summary = [s for s in store.summaries() if s.hook == hook][-1]
    return store.read(summary.run_id)


def _roles(store: RunStore, hook: str) -> dict[str, int]:
    """Count steps per role in the newest run of a hook.

    Args:
        store: The store.
        hook: The hook whose run to read.

    Returns:
        Role counts.
    """
    counts: dict[str, int] = {}
    for step in _newest(store, hook).steps:
        counts[step.role] = counts.get(step.role, 0) + 1
    return counts


@pytest.mark.cmake
@pytest.mark.slow
def test_each_hook_becomes_a_run_with_graph_environment_and_labels(
    tmp_path: Path, each_cmake: Path
) -> None:
    """Configure, build and ctest each leave one run; the build run carries everything."""
    project = make_project("chain", tmp_path, each_cmake)
    store_dir = tmp_path / "store"
    _instrument(project, store_dir, "postGenerate", "postCMakeBuild", "postCTest", label="nightly")
    fileapi.write_query(project.build_dir)
    project.configure()
    project.build()
    project.run_ctest()

    store = RunStore(store_dir)
    assert [summary.hook for summary in store.summaries()] == [
        "postGenerate",
        "postCMakeBuild",
        "postCTest",
    ]
    assert _roles(store, "postGenerate") == {"configure": 1, "generate": 1}
    assert _roles(store, "postCMakeBuild") == CHAIN_BUILD_ROLES
    assert _roles(store, "postCTest") == {"ctest": 1}

    build_run = _newest(store, "postCMakeBuild")
    assert build_run.manifest.labels == ("nightly", "ci:4242")
    assert store.read("label:nightly").manifest.hook == "postCTest", "label: picks the newest"
    assert build_run.manifest.build_dir == str(project.build_dir.resolve())
    assert (
        build_run.manifest.data_version.minor == {3: 0, 4: 1, 5: 2}[cmake_version_of(each_cmake)[1]]
    )
    assert build_run.graph is not None
    assert sorted(target.name for target in build_run.graph.targets) == ["a", "b", "c"]
    assert build_run.manifest.environment.generator == "Ninja"
    assert build_run.system is not None

    dump_dir = store.resolve(build_run.manifest.run_id)
    assert (dump_dir / "raw" / "index.json").is_file()
    assert len(list((dump_dir / "raw" / "snippets").iterdir())) == sum(CHAIN_BUILD_ROLES.values())


@pytest.mark.cmake
@pytest.mark.slow
def test_workflow_records_the_steps_but_not_the_empty_workflow_index(
    tmp_path: Path, cmake_binary: Path
) -> None:
    """`cmake --workflow` fires the per-step hooks; its own empty index records nothing."""
    project = make_project("workflow", tmp_path, cmake_binary)
    store_dir = tmp_path / "store"
    _instrument(project, store_dir, *capture_hooks())
    project.workflow("all")

    hooks = [summary.hook for summary in RunStore(store_dir).summaries()]
    assert hooks == ["postGenerate", "postCMakeBuild", "postCTest"]


def capture_hooks() -> tuple[str, ...]:
    """Every hook `enable` requests, so the workflow test sees the same set.

    Returns:
        The hook names.
    """
    from longpole.commands.enable import HOOKS

    return HOOKS


@pytest.mark.cmake
@pytest.mark.slow
def test_tree_generated_before_the_query_is_labelled_until_reconfigured(
    tmp_path: Path, cmake_binary: Path
) -> None:
    """A late query records only cmakeBuild and says so; the next configure makes it whole."""
    project = make_project("chain", tmp_path, cmake_binary)
    project.configure()
    store_dir = tmp_path / "store"
    _instrument(project, store_dir, "postGenerate", "postCMakeBuild")
    project.build()

    store = RunStore(store_dir)
    half = store.read("latest")
    assert capture.NEEDS_RECONFIGURE_LABEL in half.manifest.labels
    assert capture.NO_GRAPH_LABEL in half.manifest.labels
    assert [step.role for step in half.steps] == ["cmakeBuild"]
    assert fileapi.query_path(project.build_dir).is_file()

    project.configure()
    project.build()
    full = store.read("latest")
    assert capture.NEEDS_RECONFIGURE_LABEL not in full.manifest.labels
    assert capture.NO_GRAPH_LABEL not in full.manifest.labels
    assert full.graph is not None
    assert _roles(store, "postCMakeBuild") == CHAIN_BUILD_ROLES


def test_empty_index_records_nothing(tmp_path: Path) -> None:
    """An index without snippets, as postCMakeWorkflow produces, leaves the store untouched."""
    index = tmp_path / "index-1.json"
    index.write_text(
        json.dumps(
            {
                "version": {"major": 1, "minor": 2},
                "buildDir": str(tmp_path / "build"),
                "dataDir": str(tmp_path / "build" / ".cmake" / "instrumentation" / "v1" / "data"),
                "hook": "postCMakeWorkflow",
                "snippets": [],
            }
        ),
        encoding="utf-8",
    )
    outcome = capture.capture(index, store_override=tmp_path / "store", environ={})
    assert outcome.run is None
    assert outcome.hook == "postCMakeWorkflow"
    assert not (tmp_path / "store").exists()
    assert capture.status_line(outcome) == (
        "longpole: postCMakeWorkflow: empty index, nothing recorded"
    )


def test_status_line_names_the_run_and_the_hints() -> None:
    """The one stderr line carries the hook, the id, the size and what to do next."""
    from tests.support.runs import sample_run

    run = sample_run()
    plain = capture.Outcome(
        hook="postCMakeBuild", run=run, directory=None, graph_pending=False, needs_reconfigure=False
    )
    assert capture.status_line(plain) == (
        "longpole: postCMakeBuild recorded as 20261031T120000Z-abc123: 8 steps, 2.4 s, "
        "critical path 180 ms (7%) (longpole analyze)"
    )
    hinted = capture.Outcome(
        hook="postCMakeBuild",
        run=sample_run(with_graph=False),
        directory=None,
        graph_pending=True,
        needs_reconfigure=True,
    )
    assert capture.status_line(hinted) == (
        "longpole: postCMakeBuild recorded as 20261031T120000Z-abc123: 8 steps, 2.4 s; "
        "run cmake again to record compile and link steps; target graph pending next "
        "configure (longpole analyze)"
    )
