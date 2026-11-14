"""Runs round-trip through the dump format, and the store finds them again by reference."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from longpole import dump
from longpole.cmake import fileapi, snippets
from longpole.env import collect
from longpole.errors import DumpFormatError, SnippetFormatError, UnknownRunError
from longpole.model import DataVersion, Manifest, Run
from longpole.store import RunStore, RunSummary, new_run_id
from tests.conftest import BuiltChain
from tests.support.runs import raw_files, sample_run, write_run


def _dump(run: Run, tmp_path: Path) -> Path:
    """Write a run's dump under ``tmp_path/run`` from synthesised raw files."""
    files = raw_files(run, tmp_path / "raw")
    directory = tmp_path / "run"
    dump.write_dump(
        directory,
        run.manifest,
        index=files.index,
        snippets=files.snippets,
        reply_dir=files.reply_dir,
    )
    return directory


def test_dump_round_trip_preserves_every_field(tmp_path: Path) -> None:
    """Raw files written for a run read back as an equal run, optional parts included."""
    run = sample_run()
    assert dump.read_run(_dump(run, tmp_path)) == run


def test_dump_layout_matches_the_documented_format(tmp_path: Path) -> None:
    """The files docs/dump-format.md lists are there: a manifest and CMake's own files."""
    run = sample_run(labels=("ci:42",))
    directory = _dump(run, tmp_path)

    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["format_version"] == 2
    assert manifest["labels"] == ["ci:42"]
    assert manifest["environment"]["generator"] == "Ninja"
    assert sorted(path.name for path in directory.iterdir()) == ["manifest.json", "raw"]
    assert (directory / "raw" / "index.json").is_file()
    assert len(list((directory / "raw" / "snippets").iterdir())) == len(run.steps)
    assert (directory / "raw" / "fileapi" / "codemodel-v2.json").is_file()


def test_a_run_without_a_reply_has_no_graph_unless_lent_one(tmp_path: Path) -> None:
    """Without raw/fileapi the run reads back graphless; a reply directory passed in fills it."""
    run = sample_run(with_graph=False, with_system=False)
    directory = _dump(run, tmp_path)
    assert not (directory / "raw" / "fileapi").exists()
    assert dump.read_run(directory) == run

    lender = raw_files(sample_run(), tmp_path / "lender")
    assert lender.reply_dir is not None
    borrowed = dump.read_run(directory, reply_from=lender.reply_dir)
    assert borrowed.graph == sample_run().graph
    assert borrowed.toolchains == sample_run().toolchains


def test_unknown_snippet_fields_survive_a_read(tmp_path: Path) -> None:
    """A field this longpole does not know lands in Step.extra instead of failing the read."""
    run = sample_run()
    loaded = dump.read_run(_dump(run, tmp_path))
    assert [step.extra for step in loaded.steps if step.extra] == [{"futureField": 2}]


def test_broken_snippet_is_a_format_error_naming_the_file(tmp_path: Path) -> None:
    """A corrupted snippet is reported by name; the dump stays on disk for a fix."""
    directory = _dump(sample_run(), tmp_path)
    victim = sorted((directory / "raw" / "snippets").iterdir())[1]
    victim.write_text("{broken", encoding="utf-8")
    with pytest.raises(SnippetFormatError, match=victim.name):
        dump.read_run(directory)


def test_broken_manifest_is_a_dump_format_error(tmp_path: Path) -> None:
    """A manifest with a wrong field type is reported as a dump problem with the location."""
    directory = _dump(sample_run(), tmp_path)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    manifest["hook"] = 7
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(DumpFormatError, match="hook"):
        dump.read_run(directory)


def test_run_ids_sort_by_time() -> None:
    """Ids embed the capture time, so lexical order is capture order."""
    earlier = new_run_id(datetime(2026, 10, 31, 12, 0, 0, tzinfo=UTC))
    later = new_run_id(datetime(2026, 10, 31, 12, 0, 1, tzinfo=UTC))
    assert earlier.startswith("20261031T120000Z-")
    assert len(earlier) == len("20261031T120000Z-") + 6
    assert earlier < later


def _three_runs(store: RunStore) -> list[Run]:
    """Store three runs with distinct ids, commits and labels."""
    runs = [
        sample_run(
            run_id="20261031T120000Z-000001",
            created_at="2026-10-31T12:00:00+00:00",
            labels=("first",),
        ),
        sample_run(
            run_id="20261031T120100Z-000002",
            created_at="2026-10-31T12:01:00+00:00",
            labels=("ci:7",),
            build_dir="/elsewhere/build",
        ),
        sample_run(
            run_id="20261031T120200Z-000003",
            created_at="2026-10-31T12:02:00+00:00",
            labels=(),
        ),
    ]
    for run in runs:
        write_run(store, run)
    return runs


def test_store_lists_oldest_first_and_points_latest_at_the_newest(tmp_path: Path) -> None:
    """Summaries come out in capture order; latest is the last one written."""
    store = RunStore(tmp_path / "store")
    runs = _three_runs(store)
    assert [s.run_id for s in store.summaries()] == [r.manifest.run_id for r in runs]
    assert store.summaries()[0] == RunSummary.of(runs[0].manifest, len(runs[0].steps))
    latest = store.latest()
    assert latest is not None
    assert latest == runs[2]
    assert (tmp_path / "store" / "latest").read_text(encoding="utf-8").strip() == runs[
        2
    ].manifest.run_id


def test_references_resolve_to_the_right_run(tmp_path: Path) -> None:
    """latest, latest~N, id prefixes and label: all find their run; a path is itself."""
    store = RunStore(tmp_path / "store")
    first, second, third = _three_runs(store)
    assert store.read("latest") == third
    assert store.read("latest~1") == second
    assert store.read("latest~2") == first
    assert store.read("20261031T120000Z") == first
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
    write_run(store, sample_run())
    with pytest.raises(FileExistsError):
        write_run(store, sample_run())


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
    write_run(store, old)
    write_run(store, recent)
    assert store.prune(older_than=timedelta(days=30)) == [old.manifest.run_id]
    assert store.read("latest") == recent

    assert store.prune(older_than=timedelta(seconds=-1)) == [recent.manifest.run_id]
    assert store.latest() is None
    assert not (tmp_path / "store" / "latest").exists()


@pytest.mark.cmake
@pytest.mark.slow
def test_a_real_capture_round_trips_through_the_store(
    built_chain: BuiltChain, tmp_path: Path
) -> None:
    """What the callback copies from a real build reads back as a complete run."""
    capture = built_chain.build_capture
    index = snippets.read_index(
        capture.directory / "index.json", data_dir=capture.directory / "snippets"
    )
    manifest = Manifest(
        run_id=new_run_id(datetime.now(UTC)),
        created_at=datetime.now(UTC).isoformat(),
        tool_version="test",
        hook=index.hook,
        build_dir=str(index.build_dir),
        environment=collect(built_chain.project.build_dir),
        labels=("test",),
    )
    store = RunStore(tmp_path / "store")
    store.write(
        manifest,
        index=index.path,
        snippets=index.snippet_paths,
        reply_dir=fileapi.reply_dir(built_chain.project.build_dir),
    )

    loaded = store.read("latest")
    assert loaded.manifest == manifest
    assert loaded.steps == snippets.read_steps(index)
    assert loaded.graph is not None
    assert sorted(target.name for target in loaded.graph.targets) == ["a", "b", "c"]
    assert [toolchain.language for toolchain in loaded.toolchains] == ["C"]
    assert loaded.system == index.system
    assert loaded.data_version == DataVersion(
        major=1, minor={3: 0, 4: 1, 5: 2}[built_chain.version[1]]
    )
    dump_dir = store.runs_dir / manifest.run_id
    assert len(list((dump_dir / "raw" / "snippets").iterdir())) == len(loaded.steps)
    assert (dump_dir / "raw" / "fileapi").is_dir()


def test_a_build_borrows_the_graph_from_the_configure_before_it(tmp_path: Path) -> None:
    """A run without a reply gets the newest earlier reply of its tree, and only its tree."""
    store = RunStore(tmp_path / "store")
    configure = sample_run(run_id="20261031T120000Z-000001", created_at="2026-10-31T12:00:00+00:00")
    build = sample_run(
        run_id="20261031T120100Z-000002",
        created_at="2026-10-31T12:01:00+00:00",
        with_graph=False,
    )
    elsewhere = sample_run(
        run_id="20261031T120200Z-000003",
        created_at="2026-10-31T12:02:00+00:00",
        build_dir="/elsewhere/build",
        with_graph=False,
    )
    earlier_build = sample_run(
        run_id="20261031T115900Z-000000",
        created_at="2026-10-31T11:59:00+00:00",
        with_graph=False,
    )
    for run in (earlier_build, configure, build, elsewhere):
        write_run(store, run)

    assert store.read(build.manifest.run_id).graph == configure.graph
    assert store.read(build.manifest.run_id).toolchains == configure.toolchains
    assert store.read(elsewhere.manifest.run_id).graph is None, "another tree"
    assert store.read(earlier_build.manifest.run_id).graph is None, "before any configure"


def test_runs_captured_within_one_second_keep_capture_order(tmp_path: Path) -> None:
    """Ids have whole-second resolution; created_at orders a build and the ctest run after it."""
    store = RunStore(tmp_path)
    later = sample_run(run_id="20261031T120000Z-aaaaaa", created_at="2026-10-31T12:00:00.900+00:00")
    earlier = sample_run(
        run_id="20261031T120000Z-zzzzzz", created_at="2026-10-31T12:00:00.100+00:00"
    )
    write_run(store, earlier)
    write_run(store, later)
    assert [summary.run_id for summary in store.summaries()] == [
        "20261031T120000Z-zzzzzz",
        "20261031T120000Z-aaaaaa",
    ]
    assert store.resolve("latest") == store.runs_dir / "20261031T120000Z-aaaaaa"
    assert store.resolve("latest~1") == store.runs_dir / "20261031T120000Z-zzzzzz"
