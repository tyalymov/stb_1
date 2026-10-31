"""Write a run to disk and read it back, in the layout ``docs/dump-format.md`` fixes.

A dump is a directory: ``manifest.json``, ``steps.jsonl``, optional
``graph.json`` and ``system.json``, and ``raw/`` with CMake's original index
and snippets. Writing uses the dataclasses as they are; reading goes through
typed field access so a hand-edited or truncated dump fails with a message
naming the file and field.
"""

from __future__ import annotations

import json
import shutil
import tarfile
from collections.abc import Iterable
from dataclasses import asdict
from pathlib import Path
from typing import cast

import zstandard

from longpole.errors import DumpFormatError
from longpole.jsonfields import FieldError, Fields, Json, load_object
from longpole.model import (
    DataVersion,
    Environment,
    GitInfo,
    Graph,
    HostLoad,
    Manifest,
    ProcessMetrics,
    Run,
    Step,
    SystemInfo,
    TargetNode,
    Toolchain,
)

MANIFEST = "manifest.json"
STEPS = "steps.jsonl"
GRAPH = "graph.json"
SYSTEM = "system.json"
RAW = "raw"

ARCHIVE_SUFFIX = ".tar.zst"
"""Dumps travel as zstd-compressed tarballs: one directory, named after the run."""


def write_run(
    run: Run,
    directory: Path,
    *,
    raw_index: Path | None = None,
    raw_snippets: Iterable[Path] = (),
) -> None:
    """Write a run into ``directory`` in dump format v1.

    The directory is created if needed. Optional files are written only
    when the run has the data. Raw files are copied, not moved; CMake deletes
    the originals itself once the callback returns.

    Args:
        run: The run to write.
        directory: Target directory; usually ``<store>/runs/<run_id>``.
        raw_index: CMake's index file, copied to ``raw/index.json``.
        raw_snippets: CMake's snippet files, copied under ``raw/snippets/``.

    Raises:
        OSError: The directory cannot be created or a file cannot be written.
    """
    directory.mkdir(parents=True, exist_ok=True)
    _write_json(directory / MANIFEST, asdict(run.manifest))
    with (directory / STEPS).open("w", encoding="utf-8") as steps:
        for step in run.steps:
            steps.write(json.dumps(asdict(step), sort_keys=True) + "\n")
    if run.graph is not None:
        _write_json(directory / GRAPH, asdict(run.graph))
    if run.system is not None:
        _write_json(directory / SYSTEM, asdict(run.system))

    raw = directory / RAW
    if raw_index is not None:
        raw.mkdir(exist_ok=True)
        shutil.copy2(raw_index, raw / "index.json")
    snippets = list(raw_snippets)
    if snippets:
        (raw / "snippets").mkdir(parents=True, exist_ok=True)
        for snippet in snippets:
            shutil.copy2(snippet, raw / "snippets" / snippet.name)


def read_run(directory: Path) -> Run:
    """Read a run dump.

    Args:
        directory: A directory written by :func:`write_run`.

    Returns:
        The run, equal to the one written.

    Raises:
        FileNotFoundError: ``manifest.json`` or ``steps.jsonl`` is missing.
        DumpFormatError: A file is not the JSON the format promises, or a
            field has the wrong type.
    """
    try:
        manifest = _manifest(Fields(load_object(directory / MANIFEST)))
        steps = tuple(_steps(directory / STEPS))
        graph_path, system_path = directory / GRAPH, directory / SYSTEM
        graph = _graph(Fields(load_object(graph_path))) if graph_path.is_file() else None
        system = _system(Fields(load_object(system_path))) if system_path.is_file() else None
    except FieldError as exc:
        raise DumpFormatError(f"run dump {directory}: {exc}") from exc
    return Run(manifest=manifest, steps=steps, graph=graph, system=system)


def _write_json(path: Path, document: object) -> None:
    """Write one JSON document with stable key order.

    Args:
        path: Target file.
        document: Anything ``json.dumps`` accepts.
    """
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _steps(path: Path) -> Iterable[Step]:
    """Read ``steps.jsonl`` line by line.

    Args:
        path: The steps file.

    Returns:
        Steps in file order.

    Raises:
        FieldError: A line is not a JSON object or a field has the wrong type.
    """
    steps: list[Step] = []
    with path.open(encoding="utf-8") as lines:
        for number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                document: object = json.loads(line)
            except json.JSONDecodeError as exc:
                raise FieldError(f"{path.name} line {number} is not JSON: {exc}") from exc
            if not isinstance(document, dict):
                raise FieldError(f"{path.name} line {number} is not a JSON object")
            steps.append(_step(Fields(cast(Json, document))))
    return steps


def _manifest(fields: Fields) -> Manifest:
    """Rebuild a :class:`~longpole.model.Manifest`.

    Args:
        fields: Accessor over ``manifest.json``.

    Returns:
        The manifest.

    Raises:
        FieldError: A field is missing or has the wrong type.
    """
    version = fields.object("data_version")
    return Manifest(
        format_version=fields.integer("format_version"),
        run_id=fields.string("run_id"),
        created_at=fields.string("created_at"),
        tool_version=fields.string("tool_version"),
        hook=fields.string("hook"),
        build_dir=fields.string("build_dir"),
        data_version=DataVersion(major=version.integer("major"), minor=version.integer("minor")),
        environment=_environment(fields.object("environment")),
        labels=fields.strings("labels"),
    )


def _environment(fields: Fields) -> Environment:
    """Rebuild an :class:`~longpole.model.Environment`.

    Args:
        fields: Accessor over the ``environment`` object.

    Returns:
        The environment.

    Raises:
        FieldError: A field has the wrong type.
    """
    git = fields.optional_object("git")
    ccache = fields.optional_object("ccache")
    return Environment(
        cmake_version=fields.optional_string("cmake_version"),
        generator=fields.optional_string("generator"),
        cpu_count=fields.optional_int("cpu_count"),
        git=_git(Fields(git)) if git is not None else None,
        toolchains=tuple(
            Toolchain(
                language=toolchain.string("language"),
                compiler_id=toolchain.optional_string("compiler_id"),
                compiler_path=toolchain.optional_string("compiler_path"),
                compiler_version=toolchain.optional_string("compiler_version"),
            )
            for toolchain in fields.objects("toolchains")
        ),
        ccache={key: str(value) for key, value in ccache.items()} if ccache is not None else None,
    )


def _git(fields: Fields) -> GitInfo:
    """Rebuild a :class:`~longpole.model.GitInfo`.

    Args:
        fields: Accessor over the ``git`` object.

    Returns:
        The git state.

    Raises:
        FieldError: A field is missing or has the wrong type.
    """
    dirty = fields.optional_bool("dirty")
    if dirty is None:
        raise FieldError("field 'dirty' must be a boolean, got null")
    return GitInfo(sha=fields.string("sha"), branch=fields.optional_string("branch"), dirty=dirty)


def _step(fields: Fields) -> Step:
    """Rebuild a :class:`~longpole.model.Step` from one ``steps.jsonl`` line.

    Args:
        fields: Accessor over the line's object.

    Returns:
        The step.

    Raises:
        FieldError: A field is missing or has the wrong type.
    """
    metrics = fields.optional_object("process_metrics")
    load = fields.optional_object("host_load")
    extra = fields.optional_object("extra")
    return Step(
        role=fields.string("role"),
        command=fields.optional_string("command"),
        working_dir=fields.string("working_dir"),
        result=fields.optional_int("result"),
        time_start_ms=fields.integer("time_start_ms"),
        duration_ms=fields.integer("duration_ms"),
        target=fields.optional_string("target"),
        target_labels=fields.strings("target_labels"),
        outputs=fields.strings("outputs"),
        output_sizes=fields.integers("output_sizes"),
        source=fields.optional_string("source"),
        language=fields.optional_string("language"),
        config=fields.optional_string("config"),
        test_name=fields.optional_string("test_name"),
        show_only=fields.optional_bool("show_only"),
        cmake_content=fields.optional_string("cmake_content"),
        trace_file=fields.optional_string("trace_file"),
        stdout=fields.optional_string("stdout"),
        stderr=fields.optional_string("stderr"),
        interrupt_signal=fields.optional_int("interrupt_signal"),
        process_metrics=_metrics(Fields(metrics)) if metrics is not None else None,
        host_load=_host_load(Fields(load)) if load is not None else None,
        extra=dict(extra) if extra is not None else {},
    )


def _metrics(fields: Fields) -> ProcessMetrics:
    """Rebuild :class:`~longpole.model.ProcessMetrics`.

    Args:
        fields: Accessor over the ``process_metrics`` object.

    Returns:
        The metrics.

    Raises:
        FieldError: A field is missing or not an integer.
    """
    return ProcessMetrics(
        max_rss_kib=fields.integer("max_rss_kib"),
        user_time_us=fields.integer("user_time_us"),
        system_time_us=fields.integer("system_time_us"),
    )


def _host_load(fields: Fields) -> HostLoad:
    """Rebuild :class:`~longpole.model.HostLoad`.

    Args:
        fields: Accessor over the ``host_load`` object.

    Returns:
        The host load.

    Raises:
        FieldError: A field is missing or not a number.
    """
    return HostLoad(
        memory_used_before_kib=fields.number("memory_used_before_kib"),
        memory_used_after_kib=fields.number("memory_used_after_kib"),
        load_average_before=fields.number("load_average_before"),
        load_average_after=fields.number("load_average_after"),
    )


def _graph(fields: Fields) -> Graph:
    """Rebuild a :class:`~longpole.model.Graph` from ``graph.json``.

    Args:
        fields: Accessor over the file's object.

    Returns:
        The graph.

    Raises:
        FieldError: A field is missing or has the wrong type, or an edge is
            not a pair of strings.
    """
    edges_value = fields.value("edges")
    if not isinstance(edges_value, list):
        raise FieldError("field 'edges' must be an array")
    edges: list[tuple[str, str]] = []
    for edge in cast(list[object], edges_value):
        pair = cast(list[object], edge) if isinstance(edge, list) else None
        if pair is None or len(pair) != 2 or not all(isinstance(end, str) for end in pair):
            raise FieldError("field 'edges' must contain pairs of target ids")
        edges.append((str(pair[0]), str(pair[1])))
    return Graph(
        source=fields.string("source"),
        targets=tuple(
            TargetNode(
                id=target.string("id"),
                name=target.string("name"),
                type=target.string("type"),
                artifacts=target.strings("artifacts"),
                sources=target.strings("sources"),
            )
            for target in fields.objects("targets")
        ),
        edges=tuple(edges),
    )


def _system(fields: Fields) -> SystemInfo:
    """Rebuild :class:`~longpole.model.SystemInfo` from ``system.json``.

    Args:
        fields: Accessor over the file's object.

    Returns:
        The host description.

    Raises:
        FieldError: A field has the wrong type.
    """
    raw = fields.optional_object("raw")
    return SystemInfo(
        os_name=fields.optional_string("os_name"),
        os_version=fields.optional_string("os_version"),
        hostname=fields.optional_string("hostname"),
        processor_name=fields.optional_string("processor_name"),
        logical_cpus=fields.optional_int("logical_cpus"),
        physical_cpus=fields.optional_int("physical_cpus"),
        total_memory_mib=fields.optional_int("total_memory_mib"),
        raw=dict(raw) if raw is not None else {},
    )


def pack(directory: Path, archive: Path) -> Path:
    """Compress a dump directory into a ``.tar.zst`` archive.

    The archive holds a single top-level directory named like ``directory``
    (the run id), so unpacking next to other runs cannot collide with them.
    Parent directories of ``archive`` are created. An existing file at
    ``archive`` is replaced.

    Args:
        directory: A dump directory written by :func:`write_run`.
        archive: Target file; conventionally ``<run_id>.tar.zst``.

    Returns:
        ``archive``.

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

    Args:
        archive: File written by :func:`pack`.
        destination: Directory to place the dump in; created if needed.

    Returns:
        The extracted dump directory.

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
