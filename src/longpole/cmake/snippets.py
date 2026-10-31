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

import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from longpole.cmake.schema import Json, validate
from longpole.errors import IndexFormatError, SnippetFormatError
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
        document = _load_object(path)
        if validate_schema:
            validate("index", document, source=str(path))
        fields = _Fields(document)
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
    except _FieldError as exc:
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
        document = _load_object(path)
    except _FieldError as exc:
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
    except _FieldError as exc:
        where = f" file {source}" if source is not None else ""
        raise SnippetFormatError(f"snippet{where}: {exc}") from exc


def _step(snippet: Json) -> Step:
    """Build a Step from a snippet, reporting bad fields as ``_FieldError``.

    Args:
        snippet: The snippet as CMake wrote it.

    Returns:
        The step.

    Raises:
        _FieldError: A required field is missing or a field has the wrong type.
    """
    fields = _Fields(snippet)
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
    except _FieldError as exc:
        raise IndexFormatError(f"staticSystemInformation: {exc}") from exc


def _system_info(static: Json) -> SystemInfo:
    """Convert ``staticSystemInformation``, reporting bad fields as ``_FieldError``.

    Args:
        static: The object from the index.

    Returns:
        The host description.

    Raises:
        _FieldError: A field has the wrong type.
    """
    fields = _Fields(static)
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


def _data_version(fields: _Fields) -> DataVersion:
    """Read the ``version`` object of an index or snippet.

    Args:
        fields: Accessor over the document.

    Returns:
        The data version.

    Raises:
        _FieldError: ``version`` is missing or malformed.
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
        _FieldError: A field is missing or not an integer.
    """
    fields = _Fields(metrics)
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
        _FieldError: A field is missing or not a number.
    """
    fields = _Fields(load)
    return HostLoad(
        memory_used_before_kib=fields.number("beforeHostMemoryUsed"),
        memory_used_after_kib=fields.number("afterHostMemoryUsed"),
        load_average_before=fields.number("beforeCPULoadAverage"),
        load_average_after=fields.number("afterCPULoadAverage"),
    )


def _load_object(path: Path) -> Json:
    """Read a file as a JSON object.

    Args:
        path: File to read.

    Returns:
        The parsed object.

    Raises:
        FileNotFoundError: ``path`` does not exist.
        _FieldError: The content is not JSON or not a JSON object.
    """
    text = path.read_text(encoding="utf-8")
    try:
        document: object = json.loads(text)
    except json.JSONDecodeError as exc:
        raise _FieldError(f"not JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise _FieldError("not a JSON object")
    # JSON object keys are always strings; the cast only tells the type checker so.
    return cast(Json, document)


class _FieldError(Exception):
    """A field is missing or has the wrong type.

    Raised inside this module only; the public functions catch it and
    re-raise :class:`IndexFormatError` or :class:`SnippetFormatError` with
    the file the document came from.
    """


class _Fields:
    """Typed access to the fields of a parsed JSON object.

    Each accessor checks the runtime type and raises :class:`_FieldError`
    naming the field, so a malformed file is reported as such rather than as
    a stray ``TypeError`` later.
    """

    def __init__(self, document: Json) -> None:
        """Wrap a document.

        Args:
            document: Parsed JSON object.
        """
        self._document = document

    def string(self, key: str) -> str:
        """A required string field.

        Args:
            key: Field name.

        Returns:
            Its value.

        Raises:
            _FieldError: When the field is missing or not a string.
        """
        value = self._document.get(key)
        if not isinstance(value, str):
            raise _FieldError(f"field {key!r} must be a string, got {value!r}")
        return value

    def optional_string(self, key: str) -> str | None:
        """A string field that may be absent or ``null``.

        Args:
            key: Field name.

        Returns:
            The string, or ``None``.

        Raises:
            _FieldError: When the field is present but not a string.
        """
        value = self._document.get(key)
        if value is None:
            return None
        if not isinstance(value, str):
            raise _FieldError(f"field {key!r} must be a string, got {value!r}")
        return value

    def integer(self, key: str) -> int:
        """A required integer field.

        Args:
            key: Field name.

        Returns:
            Its value.

        Raises:
            _FieldError: When the field is missing or not an integer.
        """
        value = self._document.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            raise _FieldError(f"field {key!r} must be an integer, got {value!r}")
        return value

    def optional_int(self, key: str) -> int | None:
        """An integer field that may be absent or ``null``.

        Args:
            key: Field name.

        Returns:
            The integer, or ``None``.

        Raises:
            _FieldError: When the field is present but not an integer.
        """
        value = self._document.get(key)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise _FieldError(f"field {key!r} must be an integer, got {value!r}")
        return value

    def optional_bool(self, key: str) -> bool | None:
        """A boolean field that may be absent.

        Args:
            key: Field name.

        Returns:
            The boolean, or ``None``.

        Raises:
            _FieldError: When the field is present but not a boolean.
        """
        value = self._document.get(key)
        if value is None:
            return None
        if not isinstance(value, bool):
            raise _FieldError(f"field {key!r} must be a boolean, got {value!r}")
        return value

    def number(self, key: str) -> float:
        """A required numeric field; integers are accepted and widened.

        Args:
            key: Field name.

        Returns:
            The value as a float.

        Raises:
            _FieldError: When the field is missing or not a number.
        """
        value = self._document.get(key)
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise _FieldError(f"field {key!r} must be a number, got {value!r}")
        return float(value)

    def strings(self, key: str) -> tuple[str, ...]:
        """An optional array of strings; absent means empty.

        Args:
            key: Field name.

        Returns:
            The strings, in order.

        Raises:
            _FieldError: When the field is not an array of strings.
        """
        items = self._optional_list(key)
        if not all(isinstance(item, str) for item in items):
            raise _FieldError(f"field {key!r} must be an array of strings")
        return tuple(str(item) for item in items)

    def integers(self, key: str) -> tuple[int, ...]:
        """An optional array of integers; absent means empty.

        Args:
            key: Field name.

        Returns:
            The integers, in order.

        Raises:
            _FieldError: When the field is not an array of integers.
        """
        items = self._optional_list(key)
        if not all(isinstance(item, int) and not isinstance(item, bool) for item in items):
            raise _FieldError(f"field {key!r} must be an array of integers")
        return tuple(int(cast(int, item)) for item in items)

    def object(self, key: str) -> _Fields:
        """A required nested object, wrapped for further typed access.

        Args:
            key: Field name.

        Returns:
            Accessor over the nested object.

        Raises:
            _FieldError: When the field is missing or not an object.
        """
        nested = self.optional_object(key)
        if nested is None:
            raise _FieldError(f"field {key!r} must be an object, got null")
        return _Fields(nested)

    def optional_object(self, key: str) -> Json | None:
        """A nested object that may be absent or ``null``.

        Args:
            key: Field name.

        Returns:
            The object, or ``None``.

        Raises:
            _FieldError: When the field is present but not an object.
        """
        value = self._document.get(key)
        if value is None:
            return None
        if not isinstance(value, dict):
            raise _FieldError(f"field {key!r} must be an object, got {value!r}")
        return cast(Json, value)

    def _optional_list(self, key: str) -> list[object]:
        """An array field that may be absent; absent means empty.

        Args:
            key: Field name.

        Returns:
            The raw items.

        Raises:
            _FieldError: When the field is present but not an array.
        """
        value = self._document.get(key)
        if value is None:
            return []
        if not isinstance(value, list):
            raise _FieldError(f"field {key!r} must be an array, got {value!r}")
        return cast(list[object], value)
