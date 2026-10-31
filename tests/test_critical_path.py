"""The critical path analyzer finds the chain of targets that bounds the build."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from longpole.analyzers.critical_path import (
    NO_GRAPH_REASON,
    NO_WORK_REASON,
    CriticalPath,
    Unavailable,
    critical_path,
)
from longpole.analyzers.summary import WRAPPER_ROLES
from longpole.cmake import fileapi
from longpole.model import Graph, Run, Step, TargetNode
from tests.conftest import cmake_version_of, make_project
from tests.support.cmake import InstrumentationQuery, instrumentation_options
from tests.support.runs import run_from_capture, sample_run


def _path(run: Run) -> CriticalPath:
    """Compute the critical path and insist that there is one.

    Args:
        run: The run.

    Returns:
        The path.
    """
    result = critical_path(run)
    assert isinstance(result, CriticalPath), result
    return result


def test_sample_run_chain_is_a_then_b_then_c() -> None:
    """Three chained targets cost one compile plus one link each; the share is against the span."""
    result = _path(sample_run())
    assert [target.name for target in result.chain] == ["a", "b", "c"]
    assert [target.total_ms for target in result.chain] == [60, 60, 60]
    assert result.lower_bound_ms == 180
    assert result.span_ms == 2406
    assert result.share == pytest.approx(180 / 2406)
    assert result.unattributed == ()


def test_without_a_graph_the_reason_points_at_the_next_configure() -> None:
    """No File API reply means no graph, and the message says what to do about it."""
    result = critical_path(sample_run(with_graph=False))
    assert isinstance(result, Unavailable)
    assert result.reason == NO_GRAPH_REASON
    assert "configure" in result.reason


def test_run_whose_targets_did_nothing_is_unavailable() -> None:
    """A run of only wrapper and test steps has a graph but no work on any target."""
    base = sample_run()
    wrappers = (step for step in base.steps if step.role in WRAPPER_ROLES)
    one_test = Step(
        role="test",
        command="ctest -R x",
        working_dir=base.manifest.build_dir,
        result=0,
        time_start_ms=1_700_000_000_000,
        duration_ms=50,
        test_name="x",
    )
    idle = replace(base, steps=(*wrappers, one_test))
    result = critical_path(idle)
    assert isinstance(result, Unavailable)
    assert result.reason == NO_WORK_REASON


def test_custom_command_joins_the_target_that_compiles_its_output(tmp_path: Path) -> None:
    """A generator with no target of its own is charged to the target whose source it produces."""
    build = tmp_path / "build"
    generated = build / "gen.c"
    graph = Graph(
        source="file-api",
        targets=(TargetNode(id="x::@1", name="x", type="EXECUTABLE", sources=(str(generated),)),),
        edges=(),
    )
    steps = (
        Step(
            role="custom",
            command="cmake -P generate.cmake",
            working_dir=str(build),
            result=0,
            time_start_ms=1_000,
            duration_ms=500,
            outputs=("gen.c",),
        ),
        Step(
            role="custom",
            command="cmake -E touch orphan",
            working_dir=str(build),
            result=0,
            time_start_ms=1_000,
            duration_ms=7,
            outputs=("orphan.txt",),
        ),
        Step(
            role="compile",
            command="cc -c gen.c",
            working_dir=str(build),
            result=0,
            time_start_ms=1_500,
            duration_ms=20,
            target="x",
            source=str(generated),
        ),
        Step(
            role="link",
            command="cc -o x gen.c.o",
            working_dir=str(build),
            result=0,
            time_start_ms=1_520,
            duration_ms=30,
            target="x",
        ),
    )
    run = replace(sample_run(), steps=steps, graph=graph)
    result = _path(run)
    assert [target.name for target in result.chain] == ["x"]
    only = result.chain[0]
    assert (only.custom_ms, only.compile_ms, only.link_ms) == (500, 20, 30)
    assert result.lower_bound_ms == 550
    assert [item.name for item in result.unattributed] == ["orphan.txt"]


def test_parallel_steps_of_one_target_count_once() -> None:
    """Compiles and independent custom commands of a target overlap, so the longest one counts."""
    build = "/work/build"
    graph = Graph(
        source="file-api",
        targets=(TargetNode(id="y::@1", name="y", type="STATIC_LIBRARY"),),
        edges=(),
    )
    compiles = tuple(
        Step(
            role="compile",
            command=f"cc -c {n}.c",
            working_dir=build,
            result=0,
            time_start_ms=0,
            duration_ms=duration,
            target="y",
            source=f"/work/{n}.c",
        )
        for n, duration in (("a", 10), ("b", 40), ("c", 25))
    )
    links = tuple(
        Step(
            role="link",
            command=tool,
            working_dir=build,
            result=0,
            time_start_ms=40,
            duration_ms=duration,
            target="y",
        )
        for tool, duration in (("ar", 5), ("ranlib", 3))
    )
    result = _path(replace(sample_run(), steps=compiles + links, graph=graph))
    assert result.chain[0].compile_ms == 40, "the longest compile, not the sum"
    assert result.chain[0].link_ms == 8, "ar then ranlib add up"


@pytest.mark.cmake
@pytest.mark.slow
def test_real_chain_matches_the_known_answer(chain_run: Run) -> None:
    """The chain fixture is strictly serial, so its path is a, b, c and nearly fills the span."""
    result = _path(chain_run)
    assert [target.name for target in result.chain] == ["a", "b", "c"]
    for target in result.chain:
        assert target.custom_ms > 0, f"generator of {target.name} was not attributed"
        assert target.compile_ms > 0 and target.link_ms > 0
    sleeps_ms = 600 + 300 + 200
    assert result.lower_bound_ms >= sleeps_ms
    assert result.lower_bound_ms <= result.span_ms, "a chain cannot outlast the build"
    assert result.share > 0.7
    assert result.unattributed == ()


@pytest.mark.cmake
@pytest.mark.slow
def test_fanout_built_in_parallel_has_a_path_far_below_its_busy_time(
    tmp_path: Path, cmake_binary: Path
) -> None:
    """Eight independent generators overlap, so the one target's path is about one sleep long."""
    version = cmake_version_of(cmake_binary)
    project = make_project("fanout", tmp_path, cmake_binary)
    project.write_query(
        InstrumentationQuery(
            hooks=("postCMakeBuild",),
            callbacks=(project.callback,),
            options=instrumentation_options(version),
        )
    )
    fileapi.write_query(project.build_dir)
    project.configure()
    project.build("--parallel", "8")
    capture = next(c for c in project.captured() if c.hook == "postCMakeBuild")
    run = run_from_capture(capture, project.build_dir)

    result = _path(run)
    assert [target.name for target in result.chain] == ["fanout"]
    busy_ms = sum(step.duration_ms for step in run.steps if step.role not in WRAPPER_ROLES)
    assert result.lower_bound_ms < busy_ms / 2
    assert 300 <= result.lower_bound_ms <= result.span_ms
