"""`longpole analyze` finds the right run and renders its summary."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from longpole.analyzers.summary import summarize
from longpole.cli import main
from longpole.exporters import terminal
from longpole.model import Run
from longpole.store import RunStore
from tests.support.runs import sample_run


def test_summary_text_has_header_totals_phases_and_targets() -> None:
    """The text report carries the run header, the totals, every phase and the ranked targets."""
    text = terminal.format_summary(summarize(sample_run()))
    assert text.startswith(
        "run 20261031T120000Z-abc123  postCMakeBuild  2026-10-31T12:00:00+00:00\n"
    )
    assert "build dir: /work/project/build\n" in text
    assert "span" in text and "2.4 s" in text
    assert "busy" in text and "452 ms" in text
    for phase in ("configure", "compile", "link"):
        assert phase in text
    assert "slowest targets" in text
    assert "1 compile, 1 link" in text
    assert "slowest tests" not in text, "empty tables are left out"


def test_long_common_source_prefix_moves_into_the_title() -> None:
    """Absolute source paths lose their shared directory, which goes into the caption instead."""
    text = terminal.format_summary(summarize(sample_run()))
    assert "slowest sources (under /work/project/build)" in text
    assert "gen_a.c" in text
    assert "/work/project/build/gen_a.c" not in text


def _store_with(tmp_path: Path, *runs: Run) -> Path:
    """Write runs into a fresh store under ``tmp_path``.

    Args:
        tmp_path: The test's temporary directory.
        *runs: Runs to store, in order.

    Returns:
        The store directory.
    """
    store = RunStore(tmp_path / "store")
    for run in runs:
        store.write(run)
    return store.root


def test_json_defaults_to_the_newest_run_and_accepts_references(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Outside any build tree the default is the store's newest run; latest~1 and ids work too."""
    older = sample_run(run_id="20261031T120000Z-000001", created_at="2026-10-31T12:00:00+00:00")
    newer = sample_run(run_id="20261031T120100Z-000002", created_at="2026-10-31T12:01:00+00:00")
    store = _store_with(tmp_path, older, newer)

    assert main(["analyze", "--store", str(store), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["summary"]["run_id"] == "20261031T120100Z-000002"
    assert main(["analyze", "latest~1", "--store", str(store), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["summary"]["run_id"] == "20261031T120000Z-000001"
    assert main(["analyze", "20261031T120000Z", "--store", str(store), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["summary"]["run_id"] == "20261031T120000Z-000001"
    assert report["summary"]["totals"]["step_count"] == 8
    assert report["summary"]["phases"][0]["role"] == "configure"
    assert report["critical_path"]["available"] is True
    assert report["utilization"]["peak_parallelism"] == 1


def test_top_limits_the_tables(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """--top caps every slowest-items table."""
    store = _store_with(tmp_path, sample_run())
    assert main(["analyze", "--top", "1", "--store", str(store), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert len(report["summary"]["slowest_targets"]) == 1
    assert len(report["summary"]["slowest_sources"]) == 1
    assert len(report["sizes"]["largest"]) == 1


def test_inside_a_build_tree_the_default_is_that_trees_newest_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """From inside a tree the newest run of that tree wins over a newer run from elsewhere."""
    tree = tmp_path / "proj" / "build"
    (tree / "CMakeFiles").mkdir(parents=True)
    (tree / "CMakeCache.txt").write_text("", encoding="utf-8")
    mine = sample_run(
        run_id="20261031T120000Z-000001",
        created_at="2026-10-31T12:00:00+00:00",
        build_dir=str(tree.resolve()),
    )
    elsewhere = sample_run(
        run_id="20261031T120100Z-000002",
        created_at="2026-10-31T12:01:00+00:00",
        build_dir="/somewhere/else/build",
    )
    store = _store_with(tmp_path, mine, elsewhere)

    monkeypatch.chdir(tree / "CMakeFiles")
    assert main(["analyze", "--store", str(store), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["summary"]["run_id"] == "20261031T120000Z-000001"


def test_tree_without_runs_is_an_error_not_a_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Inside a tree that has no runs the command refuses to show some other tree's build."""
    tree = tmp_path / "build"
    tree.mkdir()
    (tree / "CMakeCache.txt").write_text("", encoding="utf-8")
    store = _store_with(tmp_path, sample_run(build_dir="/somewhere/else/build"))
    monkeypatch.chdir(tree)
    assert main(["analyze", "--store", str(store)]) == 1
    err = capsys.readouterr().err
    assert f"no runs from {tree.resolve()}" in err
    assert "longpole enable" in err


def test_empty_store_is_an_error_with_a_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An empty store says so and points at enable; an unknown reference names itself."""
    monkeypatch.chdir(tmp_path)
    assert main(["analyze", "--store", str(tmp_path / "store")]) == 1
    assert "no runs in" in capsys.readouterr().err
    store = _store_with(tmp_path, sample_run())
    assert main(["analyze", "label:nope", "--store", str(store)]) == 1
    assert "no run labelled 'nope'" in capsys.readouterr().err


@pytest.mark.cmake
@pytest.mark.slow
def test_real_chain_run_renders_its_targets_and_custom_commands(
    tmp_path: Path, chain_run: Run, capsys: pytest.CaptureFixture[str]
) -> None:
    """A real build shows a, b and c as targets and the three generators as custom commands."""
    store = _store_with(tmp_path, chain_run)
    assert main(["analyze", "--store", str(store)]) == 0
    text = capsys.readouterr().out
    assert "slowest targets" in text
    assert "slowest custom commands" in text
    for name in ("a", "b", "c"):
        assert f"\n{name} " in text or f" {name} " in text
    assert "gen_a.c" in text
    assert "critical path: " in text and "lower bound" in text
    assert "parallelism: peak" in text
    assert "artifacts: " in text
