"""`longpole runs` lists the store and manages it."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from longpole.cli import main
from longpole.commands.runs import duration
from longpole.store import RunStore
from tests.support.runs import sample_run, write_run


def _store(tmp_path: Path) -> Path:
    """A store with two runs from two trees, the newer one labelled.

    Args:
        tmp_path: The test's temporary directory.

    Returns:
        The store directory.
    """
    store = RunStore(tmp_path / "store")
    write_run(
        store,
        sample_run(
            run_id="20261031T120000Z-000001",
            created_at="2026-10-31T12:00:00+00:00",
            build_dir="/elsewhere/build",
        ),
    )
    write_run(
        store,
        sample_run(
            run_id="20261031T120100Z-000002",
            created_at="2026-10-31T12:01:00+00:00",
            labels=("ci:42",),
        ),
    )
    return store.root


def test_duration_parses_minutes_hours_and_days() -> None:
    """Ages read as a number and a unit; anything else is a usage error."""
    assert duration("90m") == timedelta(minutes=90)
    assert duration("12h") == timedelta(hours=12)
    assert duration("7d") == timedelta(days=7)
    with pytest.raises(Exception, match="not an age"):
        duration("soon")


def test_listing_shows_every_run_with_its_tree(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Every run in the store is listed, oldest first, with its tree when there are several."""
    assert main(["runs", "--store", str(_store(tmp_path))]) == 0
    out = capsys.readouterr().out
    assert out.index("20261031T120000Z-000001") < out.index("20261031T120100Z-000002")
    assert "    /elsewhere/build\n" in out, "the tree follows on its own indented line"
    assert "ci:42" in out
    assert "2026-10-31 12:01" in out


def test_filters_by_label_and_age(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """--label keeps labelled runs; --since keeps runs captured within the age; bad ages fail."""
    store = RunStore(tmp_path / "store")
    write_run(
        store, sample_run(run_id="20200101T000000Z-000001", created_at="2020-01-01T00:00:00+00:00")
    )
    fresh = datetime.now(UTC)
    write_run(
        store,
        sample_run(
            run_id=fresh.strftime("%Y%m%dT%H%M%SZ") + "-000002",
            created_at=fresh.isoformat(),
            labels=("ci:42",),
        ),
    )
    assert (
        main(["runs", "--label", "ci:42", "--store", str(store.root), "--output-format", "json"])
        == 0
    )
    assert [e["labels"] for e in json.loads(capsys.readouterr().out)] == [["ci:42"]]
    assert (
        main(["runs", "--since", "1h", "--store", str(store.root), "--output-format", "json"]) == 0
    )
    listed = json.loads(capsys.readouterr().out)
    assert len(listed) == 1 and listed[0]["labels"] == ["ci:42"]
    assert main(["runs", "--since", "yesterday", "--store", str(store.root)]) == 1


def test_show_prints_the_manifest(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The show action prints the manifest as a table, or as JSON with the step count."""
    store = _store(tmp_path)
    assert main(["runs", "show", "latest", "--store", str(store)]) == 0
    out = capsys.readouterr().out
    assert "20261031T120100Z-000002" in out
    assert "/work/project/build" in out
    assert main(["runs", "show", "latest~1", "--store", str(store), "--output-format", "json"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["run_id"] == "20261031T120000Z-000001"
    assert document["steps"] == 8
    assert document["has_graph"] is True


def test_prune_deletes_old_runs_and_needs_a_limit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Pruning with --keep 1 removes the older run and names it; prune alone is a usage error."""
    store = _store(tmp_path)
    assert main(["runs", "prune", "--store", str(store)]) == 1
    assert "give --keep" in capsys.readouterr().err
    assert main(["runs", "prune", "--keep", "1", "--store", str(store)]) == 0
    assert capsys.readouterr().out.strip() == "20261031T120000Z-000001"
    assert main(["runs", "--store", str(store), "--output-format", "json"]) == 0
    assert [e["run_id"] for e in json.loads(capsys.readouterr().out)] == ["20261031T120100Z-000002"]
