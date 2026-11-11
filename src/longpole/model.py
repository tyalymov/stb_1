"""Data model of one captured build run.

Everything longpole stores or analyses is one of these frozen dataclasses.
They mirror what CMake records, under our own names: a :class:`Step` is one
instrumentation snippet, a :class:`Graph` is the target dependency graph from
the File API, :class:`SystemInfo` is the static host description from an
index, and a :class:`Run` ties them together with a :class:`Manifest` that
says where, when and how the data was captured.

Times are integer milliseconds, as CMake writes them. Fields that only newer
data versions carry are ``None`` when the data predates them. Anything in a
snippet this version of longpole does not know ends up in ``Step.extra``, so a
dump written today can be re-read by a smarter reader tomorrow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

DUMP_FORMAT_VERSION: Final = 2
"""Version of the on-disk run dump layout described in ``docs/dump-format.md``."""

KNOWN_ROLES: Final = frozenset(
    {
        "configure",
        "generate",
        "compile",
        "link",
        "custom",
        "build",
        "cmakeBuild",
        "cmakeInstall",
        "install",
        "ctest",
        "test",
    }
)
"""Snippet roles the v1 API defines. A step may carry another role if CMake grows one."""


@dataclass(frozen=True, kw_only=True)
class DataVersion:
    """Version of CMake's instrumentation data, ``major.minor``.

    Attributes:
        major: Always 1 for the v1 API.
        minor: 0 for CMake 4.3, 1 for 4.4, 2 for 4.5.
    """

    major: int
    minor: int

    def __str__(self) -> str:
        """Format the version the way CMake prints it.

        Returns:
            ``major.minor``, e.g. ``1.2``.
        """
        return f"{self.major}.{self.minor}"


@dataclass(frozen=True, kw_only=True)
class ProcessMetrics:
    """Kernel-reported resource usage of one step's child processes (data 1.2 and later).

    Attributes:
        max_rss_kib: Peak resident set size in KiB.
        user_time_us: CPU time spent in user mode, microseconds.
        system_time_us: CPU time spent in the kernel, microseconds.
    """

    max_rss_kib: int
    user_time_us: int
    system_time_us: int

    @property
    def cpu_time_us(self) -> int:
        """User plus system time, the noise-resistant cost of a step."""
        return self.user_time_us + self.system_time_us


@dataclass(frozen=True, kw_only=True)
class HostLoad:
    """Host-wide memory and load average sampled around one step.

    CMake records these when the query asks for ``dynamicSystemInformation``.
    They describe the whole machine, not the step's own process.

    Attributes:
        memory_used_before_kib: Host memory in use when the step started.
        memory_used_after_kib: Host memory in use when the step finished.
        load_average_before: One-minute load average at start.
        load_average_after: One-minute load average at finish.
    """

    memory_used_before_kib: float
    memory_used_after_kib: float
    load_average_before: float
    load_average_after: float


@dataclass(frozen=True, kw_only=True)
class Step:
    """One command CMake launched and timed: a normalised instrumentation snippet.

    One snippet is one launched command, not one CMake-level action. A static
    library therefore yields two ``link`` steps (``ar`` and ``ranlib``) and a
    custom command with two ``COMMAND`` entries yields two ``custom`` steps.
    Group by ``target`` or ``outputs`` before reasoning about a target's cost.

    Attributes:
        role: What kind of command this was; one of :data:`KNOWN_ROLES` unless
            CMake has added a role this version does not know.
        command: Full command line. ``None`` only for the ``build`` role, which
            CMake records without one.
        working_dir: Directory the command ran in.
        result: Exit code, or ``None`` when CMake could not determine it.
        time_start_ms: Start time, milliseconds since the Unix epoch.
        duration_ms: Wall-clock duration in milliseconds.
        target: CMake target the command belongs to; set for compile and link
            steps and for custom commands attached to a target.
        target_labels: ``LABELS`` property of that target, if any.
        outputs: Files the command produced, relative to ``working_dir``.
        output_sizes: Size in bytes of each entry of ``outputs``, same order.
        source: Source file of a compile step.
        language: Language of a compile or link step as the snippet spells it,
            ``C`` or ``C++``; the File API and CMake variables say ``CXX`` for
            the same language.
        config: Build configuration such as ``Debug``; empty for single-config
            generators, ``None`` for roles that do not carry one.
        test_name: Name of the test for a ``test`` step.
        show_only: Whether ``ctest`` ran with ``--show-only``; ``ctest`` steps only.
        cmake_content: Path of the CMake content file, relative to the data
            directory, or ``None``.
        trace_file: Compiler trace copied by CMake, relative to the data
            directory (data 1.1 and later); ``None`` when absent.
        stdout: Captured standard output (data 1.1 and later, by request).
        stderr: Captured standard error (data 1.1 and later, by request).
        interrupt_signal: Signal that interrupted the command (data 1.2 and later).
        process_metrics: Resource usage of the command (data 1.2 and later).
        host_load: Host memory and load around the command, when requested.
        extra: Snippet fields this version does not know, verbatim.
    """

    role: str
    command: str | None
    working_dir: str
    result: int | None
    time_start_ms: int
    duration_ms: int
    target: str | None = None
    target_labels: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    output_sizes: tuple[int, ...] = ()
    source: str | None = None
    language: str | None = None
    config: str | None = None
    test_name: str | None = None
    show_only: bool | None = None
    cmake_content: str | None = None
    trace_file: str | None = None
    stdout: str | None = None
    stderr: str | None = None
    interrupt_signal: int | None = None
    process_metrics: ProcessMetrics | None = None
    host_load: HostLoad | None = None
    extra: dict[str, object] = field(default_factory=dict[str, object])

    @property
    def time_end_ms(self) -> int:
        """Start plus duration, milliseconds since the epoch."""
        return self.time_start_ms + self.duration_ms


@dataclass(frozen=True, kw_only=True)
class TargetNode:
    """One CMake target as the File API describes it.

    Attributes:
        id: File API target id, unique within a build tree, e.g.
            ``b::@6890427a1f51a3e7e1df``.
        name: Target name as written in ``CMakeLists.txt``.
        type: ``EXECUTABLE``, ``SHARED_LIBRARY``, ``STATIC_LIBRARY``,
            ``OBJECT_LIBRARY``, ``UTILITY`` and so on.
        artifacts: Files the target produces, relative to the build directory.
        sources: Source files of the target, as the File API lists them.
    """

    id: str
    name: str
    type: str
    artifacts: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class Graph:
    """Target dependency graph of a build tree.

    An edge ``(dependent, dependency)`` means ``dependent`` cannot be built
    before ``dependency`` is done. The File API lists the dependencies a
    generator enforces, which includes transitive link dependencies, so the
    graph may contain edges that are implied by others.

    Attributes:
        source: Where the graph came from; ``file-api`` for now.
        targets: Every target of the build tree.
        edges: ``(dependent_id, dependency_id)`` pairs over target ids.
    """

    source: str
    targets: tuple[TargetNode, ...]
    edges: tuple[tuple[str, str], ...]

    def target(self, target_id: str) -> TargetNode:
        """Look a target up by its File API id.

        Args:
            target_id: Id as it appears in :attr:`edges`.

        Returns:
            The matching node.

        Raises:
            KeyError: No target has that id.
        """
        for node in self.targets:
            if node.id == target_id:
                return node
        raise KeyError(target_id)

    def dependencies_of(self, target_id: str) -> tuple[str, ...]:
        """Ids of the targets that must be built before the given one.

        Args:
            target_id: Id of the dependent target.

        Returns:
            Dependency ids in edge order; empty for a target with none.
        """
        return tuple(dependency for dependent, dependency in self.edges if dependent == target_id)


@dataclass(frozen=True, kw_only=True)
class SystemInfo:
    """Static description of the host, from an index's ``staticSystemInformation``.

    CMake fills these from ``cmake_host_system_information()``; any of them
    may be ``None`` when the platform does not report it. ``raw`` keeps the
    complete object so nothing is lost to the typed subset.

    Attributes:
        os_name: Operating system name, e.g. ``macOS`` or ``Linux``.
        os_version: Operating system version or build id.
        hostname: Host name.
        processor_name: Processor model string.
        logical_cpus: Number of logical cores.
        physical_cpus: Number of physical cores.
        total_memory_mib: Total physical memory in MiB.
        raw: The whole ``staticSystemInformation`` object as CMake wrote it.
    """

    os_name: str | None
    os_version: str | None
    hostname: str | None
    processor_name: str | None
    logical_cpus: int | None
    physical_cpus: int | None
    total_memory_mib: int | None
    raw: dict[str, object] = field(default_factory=dict[str, object])


@dataclass(frozen=True, kw_only=True)
class GitInfo:
    """State of the source checkout when the run was captured.

    Attributes:
        sha: Full commit hash of ``HEAD``.
        branch: Current branch, or ``None`` on a detached head.
        dirty: Whether tracked files had uncommitted changes.
    """

    sha: str
    branch: str | None
    dirty: bool


@dataclass(frozen=True, kw_only=True)
class Toolchain:
    """Compiler CMake selected for one language, from the File API ``toolchains`` reply.

    Attributes:
        language: ``C``, ``CXX`` and so on.
        compiler_id: CMake's compiler id, e.g. ``AppleClang`` or ``GNU``.
        compiler_path: Path to the compiler executable.
        compiler_version: Version string as CMake detected it.
    """

    language: str
    compiler_id: str | None
    compiler_path: str | None
    compiler_version: str | None


@dataclass(frozen=True, kw_only=True)
class Environment:
    """Everything about the machine and checkout that is not in CMake's data.

    Attributes:
        cmake_version: Version of the CMake that configured the tree, from its cache.
        generator: CMake generator of the build tree, e.g. ``Ninja``.
        cpu_count: Logical CPUs visible to longpole when it captured the run.
        git: Checkout state, or ``None`` outside a git repository.
        ccache: ``ccache`` statistics as key/value text, or ``None`` without ccache.
    """

    cmake_version: str | None
    generator: str | None
    cpu_count: int | None
    git: GitInfo | None
    ccache: dict[str, str] | None = None


@dataclass(frozen=True, kw_only=True)
class Manifest:
    """Where, when and how a run was captured.

    Attributes:
        format_version: Dump layout version, :data:`DUMP_FORMAT_VERSION`.
        run_id: Store id, time-ordered: ``YYYYMMDDTHHMMSSZ-xxxxxx``.
        created_at: Capture time as an ISO 8601 UTC timestamp.
        tool_version: longpole version that wrote the dump.
        hook: Hook that produced the index: ``postCMakeBuild``, ``postCTest``,
            ``manual`` and so on.
        build_dir: Build tree the data came from, with symlinks resolved so
            two spellings of one directory compare equal (``/var`` and
            ``/private/var`` on macOS).
        data_version: Version of the instrumentation data.
        environment: Machine and checkout state.
        labels: Free-form tags, e.g. ``ci:<run id>`` or a user label.
    """

    format_version: int = DUMP_FORMAT_VERSION
    run_id: str
    created_at: str
    tool_version: str
    hook: str
    build_dir: str
    data_version: DataVersion
    environment: Environment
    labels: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class Run:
    """One captured index: its steps, the target graph and the host description.

    Attributes:
        manifest: Capture metadata.
        steps: Every step the index listed, in index order.
        graph: Target dependency graph, or ``None`` when no File API reply
            describes the tree as it was for this run.
        toolchains: Compilers per language from the same reply; empty without one.
        system: Static host information, or ``None`` when the query did not
            ask for it.
    """

    manifest: Manifest
    steps: tuple[Step, ...]
    graph: Graph | None = None
    toolchains: tuple[Toolchain, ...] = ()
    system: SystemInfo | None = None

    def steps_with_role(self, role: str) -> tuple[Step, ...]:
        """Select steps of one role, keeping index order.

        Args:
            role: Role name as CMake writes it, e.g. ``compile``.

        Returns:
            Matching steps; empty if none.
        """
        return tuple(step for step in self.steps if step.role == role)

    @property
    def span_ms(self) -> int:
        """Wall-clock span from the earliest start to the latest end; 0 with no steps."""
        if not self.steps:
            return 0
        start = min(step.time_start_ms for step in self.steps)
        end = max(step.time_end_ms for step in self.steps)
        return end - start
