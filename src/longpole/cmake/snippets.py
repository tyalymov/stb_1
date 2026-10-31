"""Read CMake's instrumentation index and snippet files into model objects.

CMake hands a callback the path of an index file. The index names the build
tree, the hook that fired and the snippet files written since the previous
index, all relative to its data directory. Once every callback has returned,
CMake deletes the index and the snippets, so whoever reads them has to do it
right then or work from a copy.

Reading is two steps: :func:`read_index` for the index, then
:func:`read_steps` for the snippets it lists. Both validate against the
vendored schemas first, then normalise CMake's field names into
:class:`~longpole.model.Step`. Fields this longpole does not know are kept in
``Step.extra`` rather than dropped.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from longpole.cmake.schema import validate
from longpole.errors import IndexFormatError, SnippetFormatError
from longpole.jsonfields import FieldError, Fields, Json, load_object
from longpole.model import DataVersion, HostLoad, ProcessMetrics, Step, SystemInfo

KNOWN_SNIPPET_FIELDS = frozenset(
    {
        "version",
        "role",
        "command",
        "workingDir",
        "result",
        "timeStart",
        "duration",
        "target",
        "targetLabels",
        "outputs",
        "outputSizes",
        "source",
        "language",
        "config",
        "testName",
        "showOnly",
        "cmakeContent",
        "traceFile",
        "stdout",
        "stderr",
        "interruptSignal",
        "processMetrics",
        "dynamicSystemInformation",
    }
)
"""Snippet fields of data versions 1.0 to 1.2. Anything else lands in ``Step.extra``."""


@dataclass(frozen=True, kw_only=True)
class Index:
    """One parsed index file.

    Attributes:
        path: Where the index was read from.
        version: Data version of the index and its snippets.
        build_dir: Build tree the data belongs to, as CMake wrote it.
        data_dir: Directory the snippet paths are relative to. Usually the
            ``dataDir`` CMake wrote; a relocated copy may say otherwise.
        hook: Hook that triggered the indexing, or ``manual``.
        snippet_paths: Absolute paths of the listed snippets, in index order.
        system: Static host information, when the query asked for it.
        trace_path: Google Trace Event file CMake wrote, when the query asked
            for the ``trace`` option.
    """

    path: Path
    version: DataVersion
    build_dir: Path
    data_dir: Path
    hook: str
    snippet_paths: tuple[Path, ...]
    system: SystemInfo | None
    trace_path: Path | None


def read_index(path: Path, *, data_dir: Path | None = None, validate_schema: bool = True) -> Index:
    """Read an index file.

    Nothing on disk is modified. The returned snippet paths are not checked
    for existence; :func:`read_steps` reports a missing one.

    Args:
        path: The index file, as CMake passed it to the callback.
        data_dir: Directory to resolve the snippet paths against instead of
            the ``dataDir`` recorded in the index. Needed when the index and
            its snippets were copied elsewhere, as a run dump's ``raw/`` is.
        validate_schema: Check the document against the vendored index
            schema before reading it. Off only for data already validated.

    Returns:
        The parsed index.

    Raises:
        FileNotFoundError: ``path`` does not exist. CMake deletes the index
            once callbacks finish, so a late read is a real failure.
        IndexFormatError: The file is not a JSON object or a field has the
            wrong type.
        SchemaError: ``validate_schema`` is on and the document violates the
            index schema.
    """
    try:
        document = load_object(path)
        if validate_schema:
            validate("index", document, source=str(path))
        fields = Fields(document)
        recorded_data_dir = Path(fields.string("dataDir"))
        resolved_data_dir = data_dir if data_dir is not None else recorded_data_dir
        static = fields.optional_object("staticSystemInformation")
        trace = fields.optional_string("trace")
        return Index(
            path=path,
            version=_data_version(fields),
            build_dir=Path(fields.string("buildDir")),
            data_dir=resolved_data_dir,
            hook=fields.string("hook"),
            snippet_paths=tuple(resolved_data_dir / name for name in fields.strings("snippets")),
            system=system_info_from_static(static) if static is not None else None,
            trace_path=resolved_data_dir / trace if trace is not None else None,
        )
    except FieldError as exc:
        raise IndexFormatError(f"index file {path}: {exc}") from exc


def read_steps(index: Index, *, validate_schema: bool = True) -> tuple[Step, ...]:
    """Read every snippet an index lists.

    An index with no snippets, as ``postCMakeWorkflow`` produces, yields an
    empty tuple; that is a valid outcome, not an error.

    Args:
        index: The index whose snippets to read.
        validate_schema: Check each snippet against the vendored schema first.

    Returns:
        One step per snippet, in index order.

    Raises:
        FileNotFoundError: A listed snippet is gone.
        SnippetFormatError: A snippet is not a JSON object or has a field of
            the wrong type.
        SchemaError: ``validate_schema`` is on and a snippet violates the
            snippet schema.
    """
    return tuple(
        read_snippet(path, validate_schema=validate_schema) for path in index.snippet_paths
    )


def read_snippet(path: Path, *, validate_schema: bool = True) -> Step:
    """Read one snippet file.

    Args:
        path: The snippet file.
        validate_schema: Check the document against the vendored schema first.

    Returns:
        The normalised step.

    Raises:
        FileNotFoundError: ``path`` does not exist.
        SnippetFormatError: The file is not a JSON object or has a field of
            the wrong type.
        SchemaError: ``validate_schema`` is on and the document violates the
            snippet schema.
    """
    try:
        document = load_object(path)
    except FieldError as exc:
        raise SnippetFormatError(f"snippet file {path}: {exc}") from exc
    if validate_schema:
        validate("snippet", document, source=str(path))
    return step_from_snippet(document, source=path)


def step_from_snippet(snippet: Json, *, source: Path | None = None) -> Step:
    """Normalise one parsed snippet into a :class:`~longpole.model.Step`.

    Pure: no I/O. Known fields are renamed and typed; absent optional fields
    become ``None`` or empty tuples; every unknown field is copied verbatim
    into ``Step.extra``.

    Args:
        snippet: The snippet as CMake wrote it.
        source: Where it came from, for error messages only.

    Returns:
        The step.

    Raises:
        SnippetFormatError: A required field is missing or a field has the
            wrong type.
    """
    try:
        return _step(snippet)
    except FieldError as exc:
        where = f" file {source}" if source is not None else ""
        raise SnippetFormatError(f"snippet{where}: {exc}") from exc


def _step(snippet: Json) -> Step:
    """Build a Step from a snippet, reporting bad fields as ``FieldError``.

    Args:
        snippet: The snippet as CMake wrote it.

    Returns:
        The step.

    Raises:
        FieldError: A required field is missing or a field has the wrong type.
    """
    fields = Fields(snippet)
    metrics = fields.optional_object("processMetrics")
    load = fields.optional_object("dynamicSystemInformation")
    return Step(
        role=fields.string("role"),
        command=fields.optional_string("command"),
        working_dir=fields.string("workingDir"),
        result=fields.optional_int("result"),
        time_start_ms=fields.integer("timeStart"),
        duration_ms=fields.integer("duration"),
        target=fields.optional_string("target"),
        target_labels=fields.strings("targetLabels"),
        outputs=fields.strings("outputs"),
        output_sizes=fields.integers("outputSizes"),
        source=fields.optional_string("source"),
        language=fields.optional_string("language"),
        config=fields.optional_string("config"),
        test_name=fields.optional_string("testName"),
        show_only=fields.optional_bool("showOnly"),
        cmake_content=fields.optional_string("cmakeContent"),
        trace_file=fields.optional_string("traceFile"),
        stdout=fields.optional_string("stdout"),
        stderr=fields.optional_string("stderr"),
        interrupt_signal=fields.optional_int("interruptSignal"),
        process_metrics=_process_metrics(metrics) if metrics is not None else None,
        host_load=_host_load(load) if load is not None else None,
        extra={key: value for key, value in snippet.items() if key not in KNOWN_SNIPPET_FIELDS},
    )


def system_info_from_static(static: Json) -> SystemInfo:
    """Pick the typed subset out of an index's ``staticSystemInformation``.

    Every value may be ``null`` on a platform CMake cannot query, so nothing
    here is required. The whole object is kept in ``raw``.

    Args:
        static: The ``staticSystemInformation`` object.

    Returns:
        The host description.

    Raises:
        IndexFormatError: A field has the wrong type.
    """
    try:
        return _system_info(static)
    except FieldError as exc:
        raise IndexFormatError(f"staticSystemInformation: {exc}") from exc


def _system_info(static: Json) -> SystemInfo:
    """Convert ``staticSystemInformation``, reporting bad fields as ``FieldError``.

    Args:
        static: The object from the index.

    Returns:
        The host description.

    Raises:
        FieldError: A field has the wrong type.
    """
    fields = Fields(static)
    return SystemInfo(
        os_name=fields.optional_string("OSName"),
        os_version=fields.optional_string("OSVersion"),
        hostname=fields.optional_string("hostname"),
        processor_name=fields.optional_string("processorName"),
        logical_cpus=fields.optional_int("numberOfLogicalCPU"),
        physical_cpus=fields.optional_int("numberOfPhysicalCPU"),
        total_memory_mib=fields.optional_int("totalPhysicalMemory"),
        raw=dict(static),
    )


def _data_version(fields: Fields) -> DataVersion:
    """Read the ``version`` object of an index or snippet.

    Args:
        fields: Accessor over the document.

    Returns:
        The data version.

    Raises:
        FieldError: ``version`` is missing or malformed.
    """
    version = fields.object("version")
    return DataVersion(major=version.integer("major"), minor=version.integer("minor"))


def _process_metrics(metrics: Json) -> ProcessMetrics:
    """Convert a ``processMetrics`` object.

    Args:
        metrics: The object from the snippet.

    Returns:
        Typed metrics.

    Raises:
        FieldError: A field is missing or not an integer.
    """
    fields = Fields(metrics)
    return ProcessMetrics(
        max_rss_kib=fields.integer("maxRSS"),
        user_time_us=fields.integer("userTime"),
        system_time_us=fields.integer("systemTime"),
    )


def _host_load(load: Json) -> HostLoad:
    """Convert a ``dynamicSystemInformation`` object.

    Args:
        load: The object from the snippet.

    Returns:
        Typed host load.

    Raises:
        FieldError: A field is missing or not a number.
    """
    fields = Fields(load)
    return HostLoad(
        memory_used_before_kib=fields.number("beforeHostMemoryUsed"),
        memory_used_after_kib=fields.number("afterHostMemoryUsed"),
        load_average_before=fields.number("beforeCPULoadAverage"),
        load_average_after=fields.number("afterCPULoadAverage"),
    )
