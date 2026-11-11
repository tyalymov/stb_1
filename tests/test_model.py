"""The model's few conveniences behave as documented."""

from __future__ import annotations

import pytest

from longpole.model import (
    DataVersion,
    Environment,
    Graph,
    Manifest,
    ProcessMetrics,
    Run,
    Step,
    TargetNode,
)


def _step(role: str, start: int, duration: int, source: str | None = None) -> Step:
    """Build a step with the required fields filled and the rest defaulted.

    Args:
        role: Snippet role.
        start: ``time_start_ms``.
        duration: ``duration_ms``.
        source: Source file, for compile steps.

    Returns:
        The step.
    """
    return Step(
        role=role,
        command="true",
        working_dir="/tmp",
        result=0,
        time_start_ms=start,
        duration_ms=duration,
        source=source,
    )


def _run(*steps: Step) -> Run:
    """Wrap steps in a run with a minimal manifest.

    Args:
        *steps: Steps of the run, in order.

    Returns:
        The run.
    """
    manifest = Manifest(
        run_id="20261031T000000Z-000000",
        created_at="2026-10-31T00:00:00Z",
        tool_version="0.0.1.dev0",
        hook="postCMakeBuild",
        build_dir="/tmp/build",
        environment=Environment(cmake_version=None, generator=None, cpu_count=None, git=None),
    )
    return Run(manifest=manifest, data_version=DataVersion(major=1, minor=2), steps=steps)


def test_data_version_prints_like_cmake() -> None:
    """``str()`` gives ``major.minor``."""
    assert str(DataVersion(major=1, minor=2)) == "1.2"


def test_process_metrics_cpu_time_sums_user_and_system() -> None:
    """CPU time is user plus system, in microseconds."""
    metrics = ProcessMetrics(max_rss_kib=1, user_time_us=10, system_time_us=5)
    assert metrics.cpu_time_us == 15


def test_step_end_is_start_plus_duration() -> None:
    """``time_end_ms`` derives from the two recorded times."""
    assert _step("compile", 1000, 250).time_end_ms == 1250


def test_run_span_covers_earliest_start_to_latest_end() -> None:
    """The span ignores step order and gaps between steps."""
    run = _run(_step("link", 2000, 100), _step("compile", 1000, 500), _step("compile", 1200, 100))
    assert run.span_ms == 1100


def test_run_span_is_zero_without_steps() -> None:
    """An empty run, like the one ``postCMakeWorkflow`` produces, has no span."""
    assert _run().span_ms == 0


def test_steps_with_role_keeps_index_order() -> None:
    """Filtering by role preserves the order the index listed."""
    first = _step("compile", 1, 1, source="a.c")
    second = _step("compile", 2, 1, source="b.c")
    run = _run(first, _step("link", 3, 1), second)
    assert run.steps_with_role("compile") == (first, second)
    assert run.steps_with_role("test") == ()


def test_graph_lookups() -> None:
    """Targets are found by id and dependencies follow the edges."""
    a = TargetNode(id="a::@1", name="a", type="SHARED_LIBRARY")
    b = TargetNode(id="b::@1", name="b", type="SHARED_LIBRARY")
    graph = Graph(source="file-api", targets=(a, b), edges=(("b::@1", "a::@1"),))
    assert graph.target("b::@1") is b
    assert graph.dependencies_of("b::@1") == ("a::@1",)
    assert graph.dependencies_of("a::@1") == ()
    with pytest.raises(KeyError):
        graph.target("c::@1")
