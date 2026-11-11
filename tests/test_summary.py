"""The summary adds up a run the way a person would read it."""

from __future__ import annotations

from dataclasses import replace

import pytest

from longpole.analyzers import summary
from longpole.model import Run
from tests.support.runs import sample_run


def test_totals_separate_span_from_busy_time() -> None:
    """Span is wall clock over every step; busy time adds up only the leaf steps."""
    totals = summary.summarize(sample_run()).totals
    assert totals.span_ms == 2406
    assert totals.busy_ms == 272 + 3 * 24 + 3 * 36
    assert totals.step_count == 8
    assert totals.failed_count == 0


def test_phases_follow_the_display_order_and_skip_wrappers() -> None:
    """Configure before compile before link; the build wrapper step is not a phase."""
    phases = summary.summarize(sample_run()).phases
    assert [(p.role, p.count, p.total_ms) for p in phases] == [
        ("configure", 1, 272),
        ("compile", 3, 72),
        ("link", 3, 108),
    ]


def test_targets_add_up_every_step_that_names_them() -> None:
    """A target's total is its compiles plus its links, with the mix spelled out."""
    targets = summary.summarize(sample_run()).slowest_targets
    assert [(t.name, t.total_ms, t.count, t.detail) for t in targets] == [
        ("a", 60, 2, "1 compile, 1 link"),
        ("b", 60, 2, "1 compile, 1 link"),
        ("c", 60, 2, "1 compile, 1 link"),
    ]


def test_sources_are_ranked_by_compile_time() -> None:
    """Translation units are named by their source path and carry the language."""
    sources = summary.summarize(sample_run(), top=2).slowest_sources
    assert len(sources) == 2
    assert all(entry.total_ms == 24 and entry.detail == "C" for entry in sources)
    assert [entry.name for entry in sources] == [
        "/work/project/build/gen_a.c",
        "/work/project/build/gen_b.c",
    ]


def test_empty_run_summarises_to_zeros() -> None:
    """A run without steps has zero totals and empty tables rather than errors."""
    run = sample_run()
    empty = Run(
        manifest=run.manifest,
        data_version=run.data_version,
        steps=(),
        graph=run.graph,
        system=run.system,
    )
    result = summary.summarize(empty)
    assert result.totals == summary.Totals(span_ms=0, busy_ms=0, step_count=0, failed_count=0)
    assert result.phases == ()
    assert result.slowest_targets == ()


def test_result_detail_reports_the_first_failure() -> None:
    """Tests and custom commands say whether they passed, with the exit code when not."""
    run = sample_run()
    failing = replace(run.steps[1], role="test", test_name="t1", result=3, target=None)
    passing = replace(run.steps[1], role="test", test_name="t2", result=0, target=None)
    result = summary.summarize(
        Run(manifest=run.manifest, data_version=run.data_version, steps=(failing, passing))
    )
    assert [(t.name, t.detail) for t in result.slowest_tests] == [
        ("t1", "failed (exit 3)"),
        ("t2", "passed"),
    ]
    assert result.totals.failed_count == 1


@pytest.mark.cmake
@pytest.mark.slow
def test_real_chain_build_summarises_its_three_targets(chain_run: Run) -> None:
    """On a real build the phases and target mix match what the fixture does."""
    result = summary.summarize(chain_run)
    assert result.hook == "postCMakeBuild"
    assert {(p.role, p.count) for p in result.phases} == {
        ("custom", 3),
        ("compile", 3),
        ("link", 3),
    }
    assert result.totals.step_count == 10
    assert result.totals.failed_count == 0
    assert result.totals.span_ms >= result.totals.busy_ms > 0

    by_name = {entry.name: entry for entry in result.slowest_targets}
    assert set(by_name) == {"a", "b", "c"}
    assert all(
        "1 compile" in entry.detail and "1 link" in entry.detail for entry in by_name.values()
    )

    custom = result.slowest_custom
    assert len(custom) == 3
    assert custom[0].total_ms >= 600, "gen_a.c sleeps 0.6 s and should top the custom table"
    assert custom[0].name.endswith("gen_a.c")
    assert len(result.slowest_sources) == 3
