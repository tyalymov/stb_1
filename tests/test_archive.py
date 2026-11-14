"""Dumps travel between machines as .tar.zst archives without losing anything."""

from __future__ import annotations

from pathlib import Path

import pytest

from longpole import dump
from longpole.errors import DumpFormatError
from longpole.model import Run
from longpole.store import RunStore
from tests.support.runs import raw_files, sample_run, write_run


def _dump(run: Run, tmp_path: Path) -> Path:
    """Write a run's dump under ``tmp_path/source``."""
    files = raw_files(run, tmp_path / "raw")
    source = tmp_path / "source" / run.manifest.run_id
    dump.write_dump(
        source, run.manifest, index=files.index, snippets=files.snippets, reply_dir=files.reply_dir
    )
    return source


def test_pack_and_unpack_round_trip(tmp_path: Path) -> None:
    """An archive unpacks to a directory equal to the one packed, including raw files."""
    run = sample_run()
    source = _dump(run, tmp_path)

    archive = dump.pack(source, tmp_path / "out" / "run.tar.zst")
    assert archive.is_file()
    extracted = dump.unpack(archive, tmp_path / "dest")
    assert extracted == tmp_path / "dest" / run.manifest.run_id
    assert dump.read_run(extracted) == run
    assert (extracted / "raw" / "index.json").is_file()
    assert not list((tmp_path / "dest").glob(".unpack-*"))


def test_unpack_refuses_to_overwrite_an_existing_run(tmp_path: Path) -> None:
    """A second unpack of the same run into the same place is an error, not a merge."""
    run = sample_run()
    source = _dump(run, tmp_path)
    archive = dump.pack(source, tmp_path / "run.tar.zst")
    dump.unpack(archive, tmp_path / "dest")
    with pytest.raises(FileExistsError):
        dump.unpack(archive, tmp_path / "dest")
    assert not list((tmp_path / "dest").glob(".unpack-*"))


def test_something_that_is_not_an_archive_is_a_dump_format_error(tmp_path: Path) -> None:
    """Random bytes are reported as not a dump archive, and nothing is left behind."""
    bogus = tmp_path / "bogus.tar.zst"
    bogus.write_bytes(b"definitely not zstd")
    with pytest.raises(DumpFormatError, match="not a run dump archive"):
        dump.unpack(bogus, tmp_path / "dest")
    assert list((tmp_path / "dest").iterdir()) == []


def test_pack_requires_a_dump_directory(tmp_path: Path) -> None:
    """Packing a directory without a manifest fails before writing anything."""
    (tmp_path / "empty").mkdir()
    with pytest.raises(FileNotFoundError):
        dump.pack(tmp_path / "empty", tmp_path / "out.tar.zst")
    assert not (tmp_path / "out.tar.zst").exists()


def test_store_pack_and_import_move_a_run_between_stores(tmp_path: Path) -> None:
    """What one store packs, another imports and indexes like its own run."""
    origin = RunStore(tmp_path / "origin")
    run = sample_run(labels=("baseline",))
    write_run(origin, run)
    archive = origin.pack("latest", tmp_path / "transfer")
    assert archive.name == f"{run.manifest.run_id}.tar.zst"

    target = RunStore(tmp_path / "target")
    imported = target.import_archive(archive)
    assert imported == target.runs_dir / run.manifest.run_id
    assert target.read("latest") == run
    assert target.read("label:baseline") == run
    assert [summary.run_id for summary in target.summaries()] == [run.manifest.run_id]

    with pytest.raises(FileExistsError):
        target.import_archive(archive)
