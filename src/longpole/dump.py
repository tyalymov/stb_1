"""Write a run dump and read it back, in the layout ``docs/dump-format.md`` fixes.

A dump is a directory with two things: ``manifest.json``, what the callback
knew at capture time, and ``raw/``, what CMake wrote: the index, every
snippet it listed and, after a configure, the File API reply. Nothing else
is stored. Steps, graph, toolchains and the host description are parsed out
of ``raw/`` on every read, so the callback never parses anything that could
fail while CMake is about to delete the originals, and a dump written today
reads with whatever a later longpole knows about the data.
"""

from __future__ import annotations

import json
import shutil
import tarfile
from collections.abc import Iterable
from dataclasses import asdict
from pathlib import Path

import zstandard

from longpole.cmake import fileapi, snippets
from longpole.errors import DumpFormatError
from longpole.jsonfields import FieldError, Fields, load_object
from longpole.model import Environment, Manifest, Run

MANIFEST = "manifest.json"
RAW = "raw"
INDEX = "index.json"
SNIPPETS = "snippets"
FILEAPI = "fileapi"

ARCHIVE_SUFFIX = ".tar.zst"
"""Dumps travel as zstd-compressed tarballs: one directory, named after the run."""


def write_dump(
    directory: Path,
    manifest: Manifest,
    *,
    index: Path,
    snippets: Iterable[Path],
    reply_dir: Path | None = None,
) -> None:
    """Write a dump: copies of CMake's files, then the manifest.

    The directory is created if needed. Files are copied, not moved; CMake
    deletes the originals itself once the callback returns. The manifest is
    written last, so a directory that has one is a complete dump.

    Raises:
        OSError: The directory cannot be created or a file cannot be copied.
    """
    raw = directory / RAW
    (raw / SNIPPETS).mkdir(parents=True, exist_ok=True)
    shutil.copy2(index, raw / INDEX)
    for snippet in snippets:
        shutil.copy2(snippet, raw / SNIPPETS / snippet.name)
    if reply_dir is not None:
        shutil.copytree(reply_dir, raw / FILEAPI, dirs_exist_ok=True)
    _write_json(directory / MANIFEST, asdict(manifest))


def read_manifest(directory: Path) -> Manifest:
    """Read a dump's manifest.

    Raises:
        FileNotFoundError: ``manifest.json`` is missing.
        DumpFormatError: The file is not the JSON the format promises, or a
            field has the wrong type.
    """
    try:
        return _manifest(Fields(load_object(directory / MANIFEST)))
    except FieldError as exc:
        raise DumpFormatError(f"run dump {directory}: {exc}") from exc


def read_index(directory: Path) -> snippets.Index:
    """Read the copied index, with snippet paths pointing into the dump.

    Raises:
        FileNotFoundError: ``raw/index.json`` is missing.
        IndexFormatError: The index is not v1 data.
    """
    raw = directory / RAW
    return snippets.read_index(raw / INDEX, data_dir=raw / SNIPPETS, validate_schema=False)


def reply_dir(directory: Path) -> Path:
    """Where a dump keeps its copy of the File API reply, existing or not."""
    return directory / RAW / FILEAPI


def read_run(directory: Path, *, reply_from: Path | None = None) -> Run:
    """Parse a dump into a run.

    The schema check is off here on purpose: data from a CMake newer than
    this longpole has fields the vendored schema forbids, and the point of
    keeping raw files is that such a dump still reads, unknown fields landing
    in ``Step.extra``.

    Raises:
        FileNotFoundError: The manifest, the index or a listed snippet is missing.
        DumpFormatError: The manifest is malformed.
        IndexFormatError: The index is not v1 data.
        SnippetFormatError: A snippet is not v1 data.
        FileApiError: The reply exists but cannot be read.
    """
    manifest = read_manifest(directory)
    index = read_index(directory)
    steps = snippets.read_steps(index, validate_schema=False)
    own = reply_dir(directory)
    source = own if own.is_dir() else reply_from
    reply = fileapi.read_reply(source) if source is not None else None
    return Run(
        manifest=manifest,
        data_version=index.version,
        steps=steps,
        graph=fileapi.graph_from_reply(reply) if reply is not None else None,
        toolchains=fileapi.toolchains_from_reply(reply) if reply is not None else (),
        system=index.system,
    )


def _write_json(path: Path, document: object) -> None:
    """Write one JSON document with stable key order."""
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _manifest(fields: Fields) -> Manifest:
    """Rebuild a :class:`~longpole.model.Manifest`.

    Raises:
        FieldError: A field is missing or has the wrong type.
    """
    return Manifest(
        format_version=fields.integer("format_version"),
        run_id=fields.string("run_id"),
        created_at=fields.string("created_at"),
        tool_version=fields.string("tool_version"),
        hook=fields.string("hook"),
        build_dir=fields.string("build_dir"),
        environment=_environment(fields.object("environment")),
        labels=fields.strings("labels"),
    )


def _environment(fields: Fields) -> Environment:
    """Rebuild an :class:`~longpole.model.Environment`.

    Raises:
        FieldError: A field has the wrong type.
    """
    ccache = fields.optional_object("ccache")
    return Environment(
        cmake_version=fields.optional_string("cmake_version"),
        generator=fields.optional_string("generator"),
        cpu_count=fields.optional_int("cpu_count"),
        ccache={key: str(value) for key, value in ccache.items()} if ccache is not None else None,
    )


def pack(directory: Path, archive: Path) -> Path:
    """Compress a dump directory into a ``.tar.zst`` archive.

    The archive holds a single top-level directory named like ``directory``
    (the run id), so unpacking next to other runs cannot collide with them.
    Parent directories of ``archive`` are created. An existing file at
    ``archive`` is replaced.

    Raises:
        FileNotFoundError: ``directory`` has no ``manifest.json``.
        OSError: The archive cannot be written.
    """
    if not (directory / MANIFEST).is_file():
        raise FileNotFoundError(f"{directory} is not a run dump: no {MANIFEST}")
    archive.parent.mkdir(parents=True, exist_ok=True)
    with (
        archive.open("wb") as raw,
        zstandard.ZstdCompressor().stream_writer(raw) as compressed,
        tarfile.open(fileobj=compressed, mode="w|") as tar,
    ):
        tar.add(directory, arcname=directory.name)
    return archive


def unpack(archive: Path, destination: Path) -> Path:
    """Extract a ``.tar.zst`` dump archive into ``destination``.

    The archive is unpacked into a staging directory first and checked to
    hold exactly one dump; only then is that dump moved to
    ``destination / <run_id>``. ``destination`` may already contain other
    dumps. Member paths that would escape the staging directory are rejected
    by tarfile's ``data`` filter.

    Raises:
        FileNotFoundError: ``archive`` does not exist.
        FileExistsError: ``destination`` already has a dump with that run id.
        DumpFormatError: The file is not a zstd tarball, or it does not hold
            exactly one directory with a ``manifest.json``.
    """
    destination.mkdir(parents=True, exist_ok=True)
    staging = destination / f".unpack-{archive.stem}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()
    try:
        with (
            archive.open("rb") as raw,
            zstandard.ZstdDecompressor().stream_reader(raw) as plain,
            tarfile.open(fileobj=plain, mode="r|") as tar,
        ):
            tar.extractall(staging, filter="data")
        extracted = [path for path in staging.iterdir() if (path / MANIFEST).is_file()]
        if len(extracted) != 1:
            raise DumpFormatError(
                f"{archive} must contain exactly one run dump, found {len(extracted)}"
            )
        final = destination / extracted[0].name
        if final.exists():
            raise FileExistsError(f"{final} already exists")
        extracted[0].rename(final)
    except (zstandard.ZstdError, tarfile.TarError) as exc:
        raise DumpFormatError(f"{archive} is not a run dump archive: {exc}") from exc
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return final
