"""The Markdown exporter writes a snapshot that fits a CI job summary."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from longpole.analyzers.report import build_report
from longpole.cli import main
from longpole.exporters import markdown
from longpole.model import Run, Step
from longpole.store import RunStore
from tests.support.runs import sample_run, write_run


def test_snapshot_has_headline_sections_and_the_gantt_chart() -> None:
    """The headline carries the key numbers; every section is present; the chain is drawn."""
    text = markdown.format_markdown(build_report(sample_run()))
    assert text.startswith("## longpole: postCMakeBuild `20261031T120000Z-abc123`\n")
    assert (
        "**2.4 s** wall clock · 452 ms busy · 8 steps · critical path 132 ms (5% of the span)"
        in text
    )
    for heading in ("### Critical path", "### Parallelism", "### Artifacts"):
        assert heading in text
    assert "```mermaid\ngantt\n    dateFormat x\n" in text
    assert (
        "    a compile (24 ms) :t0, 0, 24\n    a link (36 ms) :t1, 24, 60\n"
        "    b link (36 ms) :t2, 60, 96\n    c link (36 ms) :t3, 96, 132\n" in text
    )
    assert "| target | step | time | what |" in text
    assert "<details><summary>Slowest targets</summary>" in text
    assert "| phase | steps | total | share |" in text
    assert text.endswith("\n") and not text.endswith("\n\n")


def test_without_a_graph_the_section_states_the_reason() -> None:
    """No graph means no chart; the section is one sentence about the next configure."""
    text = markdown.format_markdown(build_report(sample_run(with_graph=False)))
    assert "gantt" not in text
    assert "### Critical path\n\nCritical path pending next configure" in text
    assert "critical path" not in text.split("\n")[4], "the headline skips a missing path"


def test_pipes_in_names_do_not_break_tables() -> None:
    """A pipe in a path is escaped so the row keeps its cells."""
    base = sample_run()
    odd = replace(base.steps[1], source="/work/project/build/a|b.c")
    text = markdown.format_markdown(build_report(replace(base, steps=(odd, *base.steps[2:]))))
    assert "a\\|b.c" in text


def test_the_snapshot_shrinks_to_fit_the_limit() -> None:
    """A thousand-step run under a small limit loses table rows and says so."""
    base = sample_run()
    many = tuple(
        Step(
            role="compile",
            command=f"cc -c file{i}.c",
            working_dir=base.manifest.build_dir,
            result=0,
            time_start_ms=1_700_000_001_000 + i,
            duration_ms=10 + i % 7,
            target=f"t{i % 50}",
            source=f"{base.manifest.build_dir}/src/dir{i % 10}/file{i}.c",
            outputs=(f"CMakeFiles/t{i % 50}.dir/file{i}.c.o",),
            output_sizes=(1000 + i,),
        )
        for i in range(1000)
    )
    report = build_report(replace(base, steps=many, graph=None), top=1000)
    full = markdown.format_markdown(report, limit_bytes=markdown.DEFAULT_LIMIT)
    small = markdown.format_markdown(report, limit_bytes=6_000)
    assert len(full.encode()) > len(small.encode())
    assert len(small.encode()) <= 6_000
    assert "Tables shortened to fit the size limit" in small
    assert "Tables shortened" not in full


def test_export_writes_markdown(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """--markdown PATH writes the snapshot; both formats can go out in one call."""
    store = RunStore(tmp_path / "store")
    write_run(store, sample_run())
    snapshot = tmp_path / "run.md"
    trace = tmp_path / "trace.json"
    assert (
        main(
            [
                "export",
                "--markdown",
                str(snapshot),
                "--perfetto",
                str(trace),
                "--store",
                str(store.root),
            ]
        )
        == 0
    )
    assert snapshot.read_text(encoding="utf-8").startswith("## longpole: postCMakeBuild")
    assert trace.exists()
    assert main(["export", "--markdown", "-", "--store", str(store.root)]) == 0
    assert "### Parallelism" in capsys.readouterr().out


@pytest.mark.cmake
@pytest.mark.slow
def test_real_chain_snapshot_draws_the_chain(chain_run: Run) -> None:
    """A real build gets a Gantt chart with the generator, a's steps and the links of b and c."""
    text = markdown.format_markdown(build_report(chain_run))
    assert "gantt" in text
    for label in ("a custom (", "a compile (", "a link (", "b link (", "c link ("):
        assert label in text
    assert "| library |" in text
    assert "lower bound" in text
