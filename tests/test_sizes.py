"""The artifact size analyzer counts what the build wrote, once each."""

from __future__ import annotations

from dataclasses import replace

import pytest

from longpole.analyzers.sizes import EXECUTABLE, GENERATED, LIBRARY, OBJECT, artifact_sizes
from longpole.model import Run, Step
from tests.support.runs import sample_run


def _link(outputs: tuple[str, ...], sizes: tuple[int, ...], *, tool: str, target: str) -> Step:
    """A link step writing the given outputs."""
    return Step(
        role="link",
        command=tool,
        working_dir="/work/build",
        result=0,
        time_start_ms=0,
        duration_ms=1,
        target=target,
        outputs=outputs,
        output_sizes=sizes,
    )


def test_sample_run_is_classified_through_the_graph() -> None:
    """Objects come from compiles; link outputs take their kind from the target type."""
    result = artifact_sizes(sample_run())
    assert result.total_bytes == 552 + 553 + 554 + 3 * 33336
    kinds = {total.kind: (total.count, total.total_bytes) for total in result.by_kind}
    assert kinds == {EXECUTABLE: (1, 33336), LIBRARY: (2, 66672), OBJECT: (3, 1659)}
    assert [total.kind for total in result.by_kind] == [EXECUTABLE, LIBRARY, OBJECT]
    assert [total.target for total in result.by_target] == ["c", "b", "a"], (
        "the objects are 552, 553 and 554 bytes, so c is the largest target"
    )
    assert result.largest[0].size_bytes == 33336
    assert result.largest[-1].path == "CMakeFiles/a.dir/gen_a.c.o"


def test_an_output_written_twice_counts_once_at_its_final_size() -> None:
    """The archive ar creates and ranlib rewrites is one artifact, not two."""
    steps = (
        _link(("liby.a",), (1000,), tool="ar", target="y"),
        _link(("liby.a",), (1040,), tool="ranlib", target="y"),
    )
    result = artifact_sizes(replace(sample_run(), steps=steps, graph=None))
    assert result.total_bytes == 1040
    assert [artifact.path for artifact in result.largest] == ["liby.a"]
    assert result.by_target[0].count == 1


def test_without_a_graph_the_extension_decides() -> None:
    """A link output with a library suffix is a library, anything else an executable."""
    steps = (
        _link(("libz.so.1.2",), (10,), tool="cc", target="z"),
        _link(("tool",), (20,), tool="cc", target="tool"),
        _link(("w.dll",), (30,), tool="link", target="w"),
    )
    result = artifact_sizes(replace(sample_run(), steps=steps, graph=None))
    kinds = {artifact.path: artifact.kind for artifact in result.largest}
    assert kinds == {"libz.so.1.2": LIBRARY, "tool": EXECUTABLE, "w.dll": LIBRARY}


def test_custom_outputs_are_generated_files() -> None:
    """Outputs of custom commands are reported under their own kind."""
    generator = Step(
        role="custom",
        command="cmake -P gen.cmake",
        working_dir="/work/build",
        result=0,
        time_start_ms=0,
        duration_ms=1,
        outputs=("gen.c",),
        output_sizes=(77,),
    )
    result = artifact_sizes(replace(sample_run(), steps=(generator,)))
    assert (
        result.by_kind == (pytest.approx(result.by_kind[0]),)
        and result.by_kind[0].kind == GENERATED
    )
    assert result.by_target == (), "a generator without a target is not charged to one"


def test_top_limits_the_ranked_tables() -> None:
    """``top`` caps the per-target and largest-artifact tables but not the totals."""
    result = artifact_sizes(sample_run(), top=1)
    assert len(result.by_target) == 1
    assert len(result.largest) == 1
    assert result.total_bytes == 552 + 553 + 554 + 3 * 33336


def test_run_without_recorded_sizes_is_empty() -> None:
    """A run of steps with no outputs yields zero and empty tables."""
    result = artifact_sizes(replace(sample_run(), steps=()))
    assert result.total_bytes == 0
    assert result.by_kind == ()
    assert result.largest == ()


@pytest.mark.cmake
@pytest.mark.slow
def test_real_chain_sizes_follow_the_target_types(chain_run: Run) -> None:
    """The chain build writes three objects, two shared libraries and one executable."""
    result = artifact_sizes(chain_run)
    kinds = {total.kind: total.count for total in result.by_kind}
    assert kinds[OBJECT] == 3
    assert kinds[LIBRARY] == 2
    assert kinds[EXECUTABLE] == 1
    assert kinds[GENERATED] == 3, "the generated sources are artifacts too"
    assert all(artifact.size_bytes > 0 for artifact in result.largest)
    sizes = [artifact.size_bytes for artifact in result.largest]
    assert sizes == sorted(sizes, reverse=True)
    assert {total.target for total in result.by_target} == {"a", "b", "c"}
    assert result.total_bytes == sum(total.total_bytes for total in result.by_kind), (
        "kinds partition the artifacts"
    )
