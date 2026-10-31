"""Runs round-trip through the dump format, and the store finds them again by reference."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from longpole import dump
from longpole.cmake import fileapi, snippets
from longpole.env import collect
from longpole.errors import DumpFormatError, UnknownRunError
from longpole.model import DataVersion, Manifest, Run
from longpole.store import IN_TREE, STORE_ENV, RunStore, RunSummary, new_run_id, resolve_store
from tests.conftest import BuiltChain
from tests.support.runs import sample_run


def test_dump_round_trip_preserves_every_field(tmp_path: Path) -> None:
    """Writing a run and reading it back gives an equal run, optional parts included."""
    run = sample_run()
    dump.write_run(run, tmp_path / "run")
    assert dump.read_run(tmp_path / "run") == run


def test_dump_layout_matches_the_documented_format(tmp_path: Path) -> None:
    """The files docs/dump-format.md lists are there, with the documented shapes."""
    run = sample_run(labels=("ci:42",))
    index = tmp_path / "index.json"
    index.write_text("{}", encoding="utf-8")
    snippet = tmp_path / "compile-1.json"
    snippet.write_text("{}", encoding="utf-8")
    directory = tmp_path / "run"
    dump.write_run(run, directory, raw_index=index, raw_snippets=[snippet])

    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["format_version"] == 1
    assert manifest["labels"] == ["ci:42"]
    assert manifest["environment"]["git"]["branch"] == "main"
    lines = (directory / "steps.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(run.steps)
    assert json.loads(lines[1])["process_metrics"]["max_rss_kib"] == 27216
    assert (directory / "graph.json").is_file()
    assert (directory / "system.json").is_file()
    assert (directory / "raw" / "index.json").is_file()
    assert (directory / "raw" / "snippets" / "compile-1.json").is_file()


def test_optional_files_are_absent_when_the_run_has_no_data(tmp_path: Path) -> None:
    """A run without graph or system information writes neither file and reads back the same."""
    run = sample_run(with_graph=False, with_system=False, sha=None)
    dump.write_run(run, tmp_path / "run")
    assert not (tmp_path / "run" / "graph.json").exists()
    assert not (tmp_path / "run" / "system.json").exists()
    assert dump.read_run(tmp_path / "run") == run


def test_broken_steps_line_is_a_dump_format_error(tmp_path: Path) -> None:
    """A corrupted line is reported with the file name and line number."""
    dump.write_run(sample_run(), tmp_path / "run")
    steps = tmp_path / "run" / "steps.jsonl"
    steps.write_text(steps.read_text(encoding="utf-8") + "{broken\n", encoding="utf-8")
    with pytest.raises(DumpFormatError, match=r"steps\.jsonl line"):
        dump.read_run(tmp_path / "run")


def test_run_ids_sort_by_time() -> None:
    """Ids embed the capture time, so lexical order is capture order."""
    earlier = new_run_id(datetime(2026, 10, 31, 12, 0, 0, tzinfo=UTC))
    later = new_run_id(datetime(2026, 10, 31, 12, 0, 1, tzinfo=UTC))
    assert earlier.startswith("20261031T120000Z-")
    assert len(earlier) == len("20261031T120000Z-") + 6
    assert earlier < later


def _three_runs(store: RunStore) -> list[Run]:
    """Store three runs with distinct ids, commits and labels.

    Args:
        store: Target store.

    Returns:
        The runs, oldest first.
    """
    runs = [
        sample_run(
            run_id="20261031T120000Z-000001",
            created_at="2026-10-31T12:00:00+00:00",
            sha="aaaa" + "0" * 36,
            labels=("first",),
        ),
        sample_run(
            run_id="20261031T120100Z-000002",
            created_at="2026-10-31T12:01:00+00:00",
            sha="bbbb" + "0" * 36,
            labels=("ci:7",),
            build_dir="/elsewhere/build",
        ),
        sample_run(
            run_id="20261031T120200Z-000003",
            created_at="2026-10-31T12:02:00+00:00",
            sha="bbbb" + "1" * 36,
            labels=(),
        ),
    ]
    for run in runs:
        store.write(run)
    return runs


def test_store_lists_oldest_first_and_points_latest_at_the_newest(tmp_path: Path) -> None:
    """Summaries come out in capture order; latest is the last one written."""
    store = RunStore(tmp_path / "store")
    runs = _three_runs(store)
    assert [s.run_id for s in store.summaries()] == [r.manifest.run_id for r in runs]
    assert store.summaries()[0] == RunSummary.of(runs[0])
    latest = store.latest()
    assert latest is not None
    assert latest == runs[2]
    assert (tmp_path / "store" / "latest").read_text(encoding="utf-8").strip() == runs[
        2
    ].manifest.run_id


def test_references_resolve_to_the_right_run(tmp_path: Path) -> None:
    """latest, latest~N, id prefixes, sha: and label: all find their run; a path is itself."""
    store = RunStore(tmp_path / "store")
    first, second, third = _three_runs(store)
    assert store.read("latest") == third
    assert store.read("latest~1") == second
    assert store.read("latest~2") == first
    assert store.read("20261031T120000Z") == first
    assert store.read("sha:aaaa") == first
    assert store.read("sha:bbbb1") == third
    assert store.read("sha:bbbb") == third  # newest match wins
    assert store.read("label:ci:7") == second
    assert store.read(str(store.runs_dir / second.manifest.run_id)) == second


def test_unresolvable_references_say_why(tmp_path: Path) -> None:
    """Nothing found, too far back, and an ambiguous prefix are distinct messages."""
    store = RunStore(tmp_path / "store")
    _three_runs(store)
    with pytest.raises(UnknownRunError, match="no run matches"):
        store.resolve("zzz")
    with pytest.raises(UnknownRunError, match="only 3"):
        store.resolve("latest~3")
    with pytest.raises(UnknownRunError, match="ambiguous"):
        store.resolve("20261031T12")
    with pytest.raises(UnknownRunError, match="labelled"):
        store.resolve("label:nope")
    with pytest.raises(UnknownRunError, match="commit"):
        store.resolve("sha:ffff")


def test_empty_store_has_no_latest(tmp_path: Path) -> None:
    """A store that was never written to is empty, not broken."""
    store = RunStore(tmp_path / "store")
    assert store.latest() is None
    assert store.summaries() == []
    with pytest.raises(UnknownRunError):
        store.resolve("latest")


def test_list_filters_by_build_dir_and_label(tmp_path: Path) -> None:
    """Filters narrow the listing without changing its order."""
    store = RunStore(tmp_path / "store")
    first, second, third = _three_runs(store)
    assert [s.run_id for s in store.list(build_dir=Path("/work/project/build"))] == [
        first.manifest.run_id,
        third.manifest.run_id,
    ]
    assert [s.run_id for s in store.list(label="ci:7")] == [second.manifest.run_id]
    since = datetime(2026, 10, 31, 12, 1, 0, tzinfo=UTC)
    assert [s.run_id for s in store.list(since=since)] == [
        second.manifest.run_id,
        third.manifest.run_id,
    ]


def test_writing_the_same_id_twice_is_refused(tmp_path: Path) -> None:
    """Run ids are unique; a second write with the same id is an error, not an overwrite."""
    store = RunStore(tmp_path / "store")
    store.write(sample_run())
    with pytest.raises(FileExistsError):
        store.write(sample_run())


def test_summaries_are_rebuilt_when_the_index_is_damaged(tmp_path: Path) -> None:
    """A corrupt runs.jsonl is regenerated from the manifests on disk."""
    store = RunStore(tmp_path / "store")
    runs = _three_runs(store)
    (tmp_path / "store" / "runs.jsonl").write_text("garbage\n", encoding="utf-8")
    assert [s.run_id for s in store.summaries()] == [r.manifest.run_id for r in runs]
    rebuilt = (tmp_path / "store" / "runs.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rebuilt) == 3


def test_prune_keeps_the_newest_and_fixes_latest(tmp_path: Path) -> None:
    """Pruning by count removes the oldest runs and leaves the index consistent."""
    store = RunStore(tmp_path / "store")
    first, second, third = _three_runs(store)
    removed = store.prune(keep=1)
    assert removed == [first.manifest.run_id, second.manifest.run_id]
    assert [s.run_id for s in store.summaries()] == [third.manifest.run_id]
    assert not (store.runs_dir / first.manifest.run_id).exists()
    assert store.read("latest") == third


def test_prune_by_age_uses_the_capture_time(tmp_path: Path) -> None:
    """Runs captured before the window go; a run from last year is gone, today's stays."""
    store = RunStore(tmp_path / "store")
    old = sample_run(run_id="20250101T000000Z-000001", created_at="2025-01-01T00:00:00+00:00")
    recent = sample_run(run_id="20991231T000000Z-000002", created_at=datetime.now(UTC).isoformat())
    store.write(old)
    store.write(recent)
    assert store.prune(older_than=timedelta(days=30)) == [old.manifest.run_id]
    assert store.read("latest") == recent

    assert store.prune(older_than=timedelta(seconds=-1)) == [recent.manifest.run_id]
    assert store.latest() is None
    assert not (tmp_path / "store" / "latest").exists()


def test_store_resolution_follows_the_documented_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Explicit path, then the environment, then an in-tree store, then the user directory."""
    build = tmp_path / "build"
    (build / IN_TREE).mkdir(parents=True)
    monkeypatch.delenv(STORE_ENV, raising=False)

    assert resolve_store(tmp_path / "explicit", build_dir=build) == tmp_path / "explicit"
    assert resolve_store(build_dir=build) == build / IN_TREE
    assert resolve_store(build_dir=tmp_path / "no-store") != tmp_path / "no-store" / IN_TREE
    assert resolve_store(build_dir=None).name == "longpole"

    monkeypatch.setenv(STORE_ENV, str(tmp_path / "from-env"))
    assert resolve_store(build_dir=build) == tmp_path / "from-env"
    assert resolve_store(tmp_path / "explicit", build_dir=build) == tmp_path / "explicit"


@pytest.mark.cmake
@pytest.mark.slow
def test_a_real_capture_round_trips_through_the_store(
    built_chain: BuiltChain, tmp_path: Path
) -> None:
    """Everything Phase B reads from a real build assembles into a Run that stores and reloads."""
    capture = built_chain.build_capture
    index = snippets.read_index(
        capture.directory / "index.json", data_dir=capture.directory / "snippets"
    )
    reply = fileapi.read_reply(built_chain.project.build_dir)
    assert reply is not None
    run = Run(
        manifest=Manifest(
            run_id=new_run_id(),
            created_at=datetime.now(UTC).isoformat(),
            tool_version="test",
            hook=index.hook,
            build_dir=str(index.build_dir),
            data_version=index.version,
            environment=collect(built_chain.project.build_dir, reply=reply),
            labels=("test",),
        ),
        steps=snippets.read_steps(index),
        graph=fileapi.graph_from_reply(reply),
        system=index.system,
    )
    store = RunStore(tmp_path / "store")
    store.write(run, raw_index=index.path, raw_snippets=index.snippet_paths)

    loaded = store.read("latest")
    assert loaded == run
    assert loaded.manifest.data_version == DataVersion(
        major=1, minor={3: 0, 4: 1, 5: 2}[built_chain.version[1]]
    )
    assert len(list((store.runs_dir / run.manifest.run_id / "raw" / "snippets").iterdir())) == len(
        run.steps
    )
