"""`longpole capture`, the callback: from a CMake index to a stored run."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from longpole.cli import main
from longpole.cmake import fileapi
from longpole.model import Run
from longpole.store import RunStore
from tests.conftest import cmake_version_of, make_project
from tests.support.cmake import CMakeProject, InstrumentationQuery, instrumentation_options

CHAIN_BUILD_ROLES = {"custom": 3, "compile": 3, "link": 3, "cmakeBuild": 1}


def _instrument(project: CMakeProject, store: Path, *hooks: str, label: str | None = None) -> None:
    """Point a fixture project at the real callback and at a store of the test's own.

    Args:
        project: The fixture project.
        store: Where the callback should write runs, as ``enable`` would bake it in.
        *hooks: Hooks to request.
        label: ``--label`` value to pass to the callback, if any.
    """
    callback = f'{sys.executable} -m longpole capture --store "{store}"'
    if label is not None:
        callback += f" --label {label}"
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
    tmp_path: Path, cmake_binary: Path
) -> None:
    """Configure, build and ctest each leave one run; the build run carries everything."""
    project = make_project("chain", tmp_path, cmake_binary)
    store_dir = tmp_path / "store"
    _instrument(project, store_dir, "postGenerate", "postCMakeBuild", "postCTest", label="nightly")
    fileapi.write_query(project.build_dir)
    project.configure()
    built = project.build()
    project.run_ctest()
    assert "longpole: postCMakeBuild recorded as " in built.stderr
    assert built.stderr.rstrip().endswith("snippets (longpole analyze)")

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
    assert build_run.manifest.labels == ("nightly",)
    assert store.read("label:nightly").manifest.hook == "postCTest", "label: picks the newest"
    assert build_run.manifest.build_dir == str(project.build_dir.resolve())
    expected_minor = {3: 0, 4: 1, 5: 2}[cmake_version_of(cmake_binary)[1]]
    assert build_run.data_version.minor == expected_minor
    assert build_run.graph is not None, "borrowed from the postGenerate run"
    assert sorted(target.name for target in build_run.graph.targets) == ["a", "b", "c"]
    assert build_run.manifest.environment.generator == "Ninja"
    assert build_run.system is not None

    dump_dir = store.resolve(build_run.manifest.run_id)
    assert (dump_dir / "raw" / "index.json").is_file()
    assert len(list((dump_dir / "raw" / "snippets").iterdir())) == sum(CHAIN_BUILD_ROLES.values())
    assert not (dump_dir / "raw" / "fileapi").exists(), "only a configure copies the reply"
    configure_dir = store.resolve(_newest(store, "postGenerate").manifest.run_id)
    assert (configure_dir / "raw" / "fileapi").is_dir()


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
    return ("postGenerate", "postCMakeBuild", "postCMakeInstall", "postCMakeWorkflow", "postCTest")


@pytest.mark.cmake
@pytest.mark.slow
def test_tree_generated_before_the_query_records_only_the_wrapper_until_reconfigured(
    tmp_path: Path, cmake_binary: Path
) -> None:
    """A late query records only cmakeBuild and no graph; the next configure makes it whole."""
    project = make_project("chain", tmp_path, cmake_binary)
    project.configure()
    store_dir = tmp_path / "store"
    _instrument(project, store_dir, "postGenerate", "postCMakeBuild")
    fileapi.write_query(project.build_dir)
    project.build()

    store = RunStore(store_dir)
    half = store.read("latest")
    assert [step.role for step in half.steps] == ["cmakeBuild"]
    assert half.graph is None, "no configure was recorded before this build"

    project.configure()
    project.build()
    full = store.read("latest")
    assert full.graph is not None
    assert _roles(store, "postCMakeBuild") == CHAIN_BUILD_ROLES


def test_empty_index_records_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
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
    assert main(["capture", str(index), "--store", str(tmp_path / "store")]) == 0
    assert not (tmp_path / "store").exists()
    assert capsys.readouterr().err.splitlines() == [
        "longpole: postCMakeWorkflow: empty index, nothing recorded"
    ]


def test_index_of_a_newer_data_version_is_stored(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Data the vendored schema does not know is copied anyway and reads back."""
    build = tmp_path / "build"
    data = build / ".cmake" / "instrumentation" / "v1" / "data"
    data.mkdir(parents=True)
    snippet = data / "configure-1.json"
    snippet.write_text(
        json.dumps(
            {
                "version": {"major": 1, "minor": 3},
                "role": "configure",
                "command": "cmake -S . -B build",
                "workingDir": str(build),
                "result": 0,
                "timeStart": 1_700_000_000_000,
                "duration": 10,
                "fieldFromTheFuture": True,
            }
        ),
        encoding="utf-8",
    )
    index = data / "index-1.json"
    index.write_text(
        json.dumps(
            {
                "version": {"major": 1, "minor": 3},
                "buildDir": str(build),
                "dataDir": str(data),
                "hook": "postGenerate",
                "snippets": [snippet.name],
            }
        ),
        encoding="utf-8",
    )
    store_dir = tmp_path / "store"
    assert main(["capture", str(index), "--store", str(store_dir)]) == 0
    assert "recorded as" in capsys.readouterr().err

    run = RunStore(store_dir).read("latest")
    assert run.data_version.minor == 3
    assert [step.role for step in run.steps] == ["configure"]
    assert run.steps[0].extra == {"fieldFromTheFuture": True}


@pytest.mark.cmake
@pytest.mark.slow
def test_build_tree_with_a_space_in_its_path_records_its_runs(
    tmp_path: Path, cmake_binary: Path
) -> None:
    """CMake hands the index path to the callback intact even when the path has a space."""
    import shutil

    from tests.conftest import FIXTURES

    root = tmp_path / "with space"
    source = root / "src"
    shutil.copytree(FIXTURES / "chain", source)
    project = CMakeProject(
        source_dir=source,
        build_dir=root / "build dir",
        cmake=cmake_binary,
        generator="Ninja",
        capture_root=root / "capture",
    )
    store_dir = root / "store"
    _instrument(project, store_dir, "postCMakeBuild")
    project.configure()
    project.build()

    store = RunStore(store_dir)
    # Only the build hook is registered, so configure and generate land in its index too.
    assert _roles(store, "postCMakeBuild") == {**CHAIN_BUILD_ROLES, "configure": 1, "generate": 1}
    assert _newest(store, "postCMakeBuild").manifest.build_dir == str(project.build_dir.resolve())
