"""The critical path analyzer finds the chain of steps that bounds the build."""

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
from longpole.model import Graph, Run, Step, TargetNode
from tests.support.runs import sample_run

Shape = tuple[str, str, int, int]
"""Target, kind, duration and start of a segment: what the tests compare."""


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


def _shape(path: CriticalPath) -> list[Shape]:
    """Reduce a chain to the numbers the tests assert on.

    Args:
        path: The path.

    Returns:
        One tuple per segment.
    """
    return [(s.target, s.kind, s.duration_ms, s.start_ms) for s in path.chain]


def _step(role: str, target: str, duration_ms: int, **fields: object) -> Step:
    """Build a step with the fields the analyzer reads and placeholders for the rest.

    Args:
        role: Snippet role.
        target: Target the step belongs to.
        duration_ms: Duration.
        **fields: Further :class:`Step` fields, e.g. ``source`` or ``outputs``.

    Returns:
        The step. Wall-clock times are not meaningful; the analyzer ignores them.
    """
    return Step(
        role=role,
        command=f"{role} for {target}",
        working_dir="/work/build",
        result=0,
        time_start_ms=0,
        duration_ms=duration_ms,
        target=target,
        **fields,  # type: ignore[arg-type]
    )


def test_sample_run_overlaps_the_compiles_and_chains_the_links() -> None:
    """Three chained targets: every compile starts at once, each link waits for the one before.

    The bound is one compile and three links, not three compiles and three
    links: b and c compile while a is still linking.
    """
    result = _path(sample_run())
    assert _shape(result) == [
        ("a", "compile", 24, 0),
        ("a", "link", 36, 24),
        ("b", "link", 36, 60),
        ("c", "link", 36, 96),
    ]
    assert result.lower_bound_ms == 132
    assert result.targets == ("a", "b", "c")
    assert result.span_ms == 2406
    assert result.share == pytest.approx(132 / 2406)
    assert result.unattributed == ()


def test_segments_are_contiguous_and_end_at_the_bound() -> None:
    """Each segment starts where the previous one ends; the last ends at the lower bound."""
    result = _path(sample_run())
    cursor = 0
    for segment in result.chain:
        assert segment.start_ms == cursor
        cursor = segment.end_ms
    assert cursor == result.lower_bound_ms


def test_without_a_graph_the_reason_points_at_the_next_configure() -> None:
    """No File API reply means no graph, and the message says what to do about it."""
    result = critical_path(sample_run(with_graph=False))
    assert isinstance(result, Unavailable)
    assert result.reason == NO_GRAPH_REASON
    assert "configure" in result.reason


def test_run_whose_targets_did_nothing_is_not_applicable() -> None:
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
    assert "tests" in result.reason


def test_dependent_target_compiles_while_its_dependency_builds() -> None:
    """A test executable's compile does not wait for the library it links; only its link does.

    This is the shape that made an earlier model overshoot: pricing targets
    one after another counted the library's 4.6 s compile before the test's
    7.9 s compile, 13.7 s on a build that took 8.3 s. The bound is the
    longer compile plus the test's link.
    """
    graph = Graph(
        source="file-api",
        targets=(
            TargetNode(id="gtest::@1", name="gtest", type="STATIC_LIBRARY"),
            TargetNode(id="chrono-test::@1", name="chrono-test", type="EXECUTABLE"),
        ),
        edges=(("chrono-test::@1", "gtest::@1"),),
    )
    steps = (
        _step("compile", "gtest", 4_600, source="/work/test/gtest-all.cc"),
        _step("link", "gtest", 60, outputs=("libgtest.a",)),
        _step("link", "gtest", 40, outputs=("libgtest.a",)),
        _step("compile", "chrono-test", 7_900, source="/work/test/chrono-test.cc"),
        _step("link", "chrono-test", 40, outputs=("bin/chrono-test",)),
    )
    result = _path(replace(sample_run(), steps=steps, graph=graph))
    assert _shape(result) == [
        ("chrono-test", "compile", 7_900, 0),
        ("chrono-test", "link", 40, 7_900),
    ]
    assert result.lower_bound_ms == 7_940
    assert result.chain[0].detail == "/work/test/chrono-test.cc", "the long pole is named"
    assert result.chain[1].detail == "bin/chrono-test"


def test_slow_dependency_gates_the_dependent_link_only() -> None:
    """When the library is the long pole, the chain is its compile and link, then the app's link."""
    graph = Graph(
        source="file-api",
        targets=(
            TargetNode(id="lib::@1", name="lib", type="SHARED_LIBRARY"),
            TargetNode(id="app::@1", name="app", type="EXECUTABLE"),
        ),
        edges=(("app::@1", "lib::@1"),),
    )
    steps = (
        _step("compile", "lib", 9_000, source="/work/lib.cc"),
        _step("link", "lib", 100, outputs=("liblib.so",)),
        _step("compile", "app", 1_000, source="/work/app.cc"),
        _step("link", "app", 40, outputs=("app",)),
    )
    result = _path(replace(sample_run(), steps=steps, graph=graph))
    assert _shape(result) == [
        ("lib", "compile", 9_000, 0),
        ("lib", "link", 100, 9_000),
        ("app", "link", 40, 9_100),
    ]
    assert result.lower_bound_ms == 9_140


def test_custom_commands_of_a_dependency_gate_the_dependent_compiles() -> None:
    """A dependency's generated sources gate the dependent's compiles (an order-only dependency)."""
    graph = Graph(
        source="file-api",
        targets=(
            TargetNode(id="gen::@1", name="gen", type="UTILITY"),
            TargetNode(id="app::@1", name="app", type="EXECUTABLE"),
        ),
        edges=(("app::@1", "gen::@1"),),
    )
    steps = (
        _step("custom", "gen", 500, outputs=("version.h",)),
        _step("compile", "app", 100, source="/work/app.cc"),
        _step("link", "app", 30, outputs=("app",)),
    )
    result = _path(replace(sample_run(), steps=steps, graph=graph))
    assert _shape(result) == [
        ("gen", "custom", 500, 0),
        ("app", "compile", 100, 500),
        ("app", "link", 30, 600),
    ]
    assert result.lower_bound_ms == 630


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
        _step("compile", "x", 20, source=str(generated)),
        _step("link", "x", 30, outputs=("x",)),
    )
    run = replace(sample_run(), steps=steps, graph=graph)
    result = _path(run)
    assert _shape(result) == [
        ("x", "custom", 500, 0),
        ("x", "compile", 20, 500),
        ("x", "link", 30, 520),
    ]
    assert result.chain[0].detail == "gen.c"
    assert result.lower_bound_ms == 550
    assert [item.name for item in result.unattributed] == ["orphan.txt"]


def test_parallel_steps_of_one_target_count_once_and_links_add_up() -> None:
    """Compiles of one target overlap, so the longest counts; ar and ranlib run in turn and add."""
    graph = Graph(
        source="file-api",
        targets=(TargetNode(id="y::@1", name="y", type="STATIC_LIBRARY"),),
        edges=(),
    )
    compiles = tuple(
        _step("compile", "y", duration, source=f"/work/{name}.c")
        for name, duration in (("a", 10), ("b", 40), ("c", 25))
    )
    links = (
        _step("link", "y", 5, outputs=("liby.a",)),
        _step("link", "y", 3, outputs=("liby.a",)),
    )
    result = _path(replace(sample_run(), steps=compiles + links, graph=graph))
    assert _shape(result) == [("y", "compile", 40, 0), ("y", "link", 8, 40)]
    assert result.chain[0].detail == "/work/b.c"
    assert result.chain[1].detail == "liby.a"


def _costs(run: Run) -> dict[str, tuple[int, int, int]]:
    """Reduce a real run to per-target costs: longest custom, longest compile, links added up.

    Custom commands are matched to targets the way the fixtures lay them
    out: the generator of ``gen_<t>.c`` belongs to target ``t``.

    Args:
        run: A run of the chain fixture.

    Returns:
        ``(custom_ms, compile_ms, link_ms)`` by target name.
    """
    costs: dict[str, list[int]] = {}
    for step in run.steps:
        if step.role == "custom":
            name = step.outputs[0].removeprefix("gen_").removesuffix(".c")
            entry = costs.setdefault(name, [0, 0, 0])
            entry[0] = max(entry[0], step.duration_ms)
        elif step.role == "compile" and step.target is not None:
            entry = costs.setdefault(step.target, [0, 0, 0])
            entry[1] = max(entry[1], step.duration_ms)
        elif step.role == "link" and step.target is not None:
            entry = costs.setdefault(step.target, [0, 0, 0])
            entry[2] += step.duration_ms
    return {name: (c[0], c[1], c[2]) for name, c in costs.items()}


@pytest.mark.cmake
@pytest.mark.slow
def test_real_chain_bound_follows_the_target_graph_not_the_generators(chain_run: Run) -> None:
    """On the chain fixture the bound is one generator, a's steps, then b's and c's links.

    The fixture really is serial: each generator ``DEPENDS`` on the previous
    target's artifact. The target graph does not record that kind of
    dependency, so the model lets the three generators start together and
    the bound counts only the longest, gen_a.c. The real build takes longer
    than the bound, which is the direction a lower bound is allowed to miss.
    """
    result = _path(chain_run)
    costs = _costs(chain_run)
    a, b, c = costs["a"], costs["b"], costs["c"]
    assert a[0] > max(b[0], c[0]), "gen_a.c sleeps longest, so every compile waits for it"
    ready = a[0]
    finish_a = ready + a[1] + a[2]
    finish_b = max(ready + b[1], finish_a) + b[2]
    finish_c = max(ready + c[1], finish_b, finish_a) + c[2]
    assert result.lower_bound_ms == finish_c
    assert result.chain[0].kind == "custom" and result.chain[0].target == "a"
    assert result.chain[0].detail == "gen_a.c"
    assert result.chain[-1].kind == "link" and result.chain[-1].target == "c"
    assert result.lower_bound_ms <= result.span_ms, "a bound cannot outlast the build"
    assert result.lower_bound_ms < a[0] + b[0] + c[0], "the DEPENDS chain is not in the graph"
    assert result.unattributed == ()


@pytest.mark.cmake
@pytest.mark.slow
def test_fanout_built_in_parallel_has_a_path_far_below_its_busy_time(fanout_run: Run) -> None:
    """Eight independent generators overlap, so the one target's path is about one sleep long."""
    result = _path(fanout_run)
    assert result.targets == ("fanout",)
    assert [segment.kind for segment in result.chain] == ["custom", "compile", "link"]
    busy_ms = sum(step.duration_ms for step in fanout_run.steps if step.role not in WRAPPER_ROLES)
    assert result.lower_bound_ms < busy_ms / 2
    assert 300 <= result.lower_bound_ms <= result.span_ms


@pytest.mark.cmake
@pytest.mark.slow
def test_lower_bound_never_exceeds_the_span_of_a_serial_build(chain_run: Run) -> None:
    """A lower bound on wall-clock time is at most the wall-clock time the build took."""
    result = _path(chain_run)
    assert result.lower_bound_ms <= result.span_ms


@pytest.mark.cmake
@pytest.mark.slow
def test_lower_bound_never_exceeds_the_span_of_a_parallel_build(fanout_run: Run) -> None:
    """The same invariant on a build whose steps overlap heavily."""
    result = _path(fanout_run)
    assert result.lower_bound_ms <= result.span_ms
