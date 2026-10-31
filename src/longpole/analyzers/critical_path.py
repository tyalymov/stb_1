"""The chain of steps that bounds how fast a build can finish.

A build system runs independent work side by side, so adding up step
durations says little about wall-clock time. What does is the longest chain
of steps that have to wait for each other: however many cores there are,
the build cannot finish before that chain does. This module finds the chain
from the target graph and the steps that belong to each target, and reports
its length as a lower bound on the build's wall-clock time.

The bound counts only the ordering the target graph guarantees, which is
how CMake's generators schedule a build. Custom commands start as soon as
the build does. A target's compile steps start once its own custom commands
and those of the targets it depends on have run, an order-only dependency,
and they run alongside each other and alongside the compile and link steps
of the targets they depend on. A target's link steps wait for its own
compiles and for the link steps of its dependencies, and run one after
another (a static library is ``ar`` followed by ``ranlib``).

Ordering the graph does not record, such as a custom command that
``DEPENDS`` on another target's artifact, is not counted. The real build can
therefore be slower than the bound, never faster. Inside a target, compile
steps and independent custom commands each contribute their longest member.
A custom command joins a target either because CMake recorded the target on
the snippet or because its outputs are among the target's sources in the
File API, which is how a generated source is attributed to the target that
compiles it.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from longpole.model import Graph, Run, Step, TargetNode

CUSTOM = "custom"
COMPILE = "compile"
LINK = "link"


@dataclass(frozen=True, kw_only=True)
class Segment:
    """One link of the critical path: the steps of one kind on one target.

    Attributes:
        target: Target name.
        kind: :data:`CUSTOM`, :data:`COMPILE` or :data:`LINK`.
        duration_ms: How long the segment holds the build: the longest custom
            or compile step of the target, or its link steps added up.
        start_ms: When the segment can start, counted from the start of the
            build with unlimited parallelism. The chain is contiguous: each
            segment starts where the one before it ends.
        detail: What the segment is: the source of the longest compile, the
            outputs of the longest custom command, or the link outputs.
    """

    target: str
    kind: str
    duration_ms: int
    start_ms: int
    detail: str

    @property
    def end_ms(self) -> int:
        """Start plus duration."""
        return self.start_ms + self.duration_ms


@dataclass(frozen=True, kw_only=True)
class Unattributed:
    """A custom command no target claims.

    Attributes:
        name: The command's outputs, or its command line when CMake recorded none.
        duration_ms: How long it ran.
    """

    name: str
    duration_ms: int


@dataclass(frozen=True, kw_only=True)
class CriticalPath:
    """The longest chain of dependent steps in a run.

    Attributes:
        chain: Segments in order, each starting where the previous one ends.
            Targets that sit on the path without measured work of their own
            are left out, so the list reads as work rather than as graph
            structure.
        lower_bound_ms: Length of the chain: the least wall-clock time the
            build could take with unlimited parallelism. Never more than the
            span the build actually took.
        span_ms: Wall-clock span the run actually took, wrapper steps included.
        unattributed: Custom commands that belong to no target. Their time is
            not in the chain.
    """

    chain: tuple[Segment, ...]
    lower_bound_ms: int
    span_ms: int
    unattributed: tuple[Unattributed, ...]

    @property
    def share(self) -> float:
        """Lower bound as a fraction of the span; 0 for a run with no span.

        Close to 1 means the build already ran at the speed its dependencies
        allow. Well below 1 means either more parallelism would help or the
        build waited on ordering the target graph does not show.
        """
        return self.lower_bound_ms / self.span_ms if self.span_ms else 0.0

    @property
    def targets(self) -> tuple[str, ...]:
        """Distinct targets on the chain, in build order."""
        seen: dict[str, None] = {}
        for segment in self.chain:
            seen.setdefault(segment.target, None)
        return tuple(seen)


@dataclass(frozen=True, kw_only=True)
class Unavailable:
    """Why no critical path could be computed for a run.

    Attributes:
        reason: One sentence for a person, e.g. that the run has no target graph.
    """

    reason: str


NO_GRAPH_REASON = (
    "critical path pending next configure: the run has no target graph because "
    "no File API reply existed when it was captured"
)
NO_WORK_REASON = (
    "not applicable: no target did measurable work in this run, as in a run of tests only"
)


def critical_path(run: Run) -> CriticalPath | Unavailable:
    """Find the critical path of a run.

    Pure: reads the run, touches nothing else.

    Args:
        run: A stored run; its graph may be missing.

    Returns:
        The path, or :class:`Unavailable` with the reason when the run has no
        graph or no target did any measured work (a test-only run, say).
    """
    if run.graph is None or not run.graph.targets:
        return Unavailable(reason=NO_GRAPH_REASON)
    work, unattributed = _attribute(run.steps, run.graph)
    schedule = _schedule(run.graph, work)
    if not schedule:
        return Unavailable(reason=NO_WORK_REASON)
    last = max(schedule, key=lambda target_id: (schedule[target_id].finish_ms, target_id))
    if schedule[last].finish_ms == 0:
        return Unavailable(reason=NO_WORK_REASON)
    names = {target.id: target.name for target in run.graph.targets}
    return CriticalPath(
        chain=tuple(_trace(last, schedule, work, names)),
        lower_bound_ms=schedule[last].finish_ms,
        span_ms=run.span_ms,
        unattributed=tuple(unattributed),
    )


def custom_name(step: Step) -> str:
    """Name a custom command by what it produces.

    Args:
        step: A custom step.

    Returns:
        Its outputs joined with commas, or the command line when CMake
        recorded no outputs.
    """
    if step.outputs:
        return ", ".join(step.outputs)
    return step.command or "(custom command)"


@dataclass(frozen=True, kw_only=True)
class _Longest:
    """The longest step of one kind on a target.

    Attributes:
        duration_ms: Its duration.
        detail: What it was: a compile's source or a custom command's outputs.
    """

    duration_ms: int
    detail: str


@dataclass(frozen=True, kw_only=True)
class _Work:
    """What one target did in the run, reduced to the costs the model uses.

    Attributes:
        custom: Longest custom command, or ``None`` when none ran.
        compile: Longest compile step, or ``None`` when none ran.
        link_ms: Link steps added up; 0 when none ran.
        link_detail: Outputs of the link steps, deduplicated.
    """

    custom: _Longest | None
    compile: _Longest | None
    link_ms: int
    link_detail: str

    @property
    def custom_ms(self) -> int:
        """Duration of the longest custom command, 0 without one."""
        return self.custom.duration_ms if self.custom else 0

    @property
    def compile_ms(self) -> int:
        """Duration of the longest compile step, 0 without one."""
        return self.compile.duration_ms if self.compile else 0


@dataclass(frozen=True, kw_only=True)
class _Schedule:
    """When one target's steps can run with unlimited parallelism.

    Attributes:
        custom_ready_ms: When every custom command the target's compiles wait
            for has run: its own and those of its dependencies.
        custom_from: Dependency whose custom commands set ``custom_ready_ms``,
            or ``None`` when the target's own did (or none ran).
        compile_end_ms: ``custom_ready_ms`` plus the longest compile.
        link_start_ms: The later of ``compile_end_ms`` and the dependencies'
            ``finish_ms``.
        link_gate: Dependency whose finish set ``link_start_ms``, or ``None``
            when the target's own compiles did.
        finish_ms: ``link_start_ms`` plus the link steps.
    """

    custom_ready_ms: int
    custom_from: str | None
    compile_end_ms: int
    link_start_ms: int
    link_gate: str | None
    finish_ms: int


def _attribute(
    steps: tuple[Step, ...], graph: Graph
) -> tuple[dict[str, _Work], list[Unattributed]]:
    """Attribute steps to targets and reduce them to per-target costs.

    Args:
        steps: The run's steps.
        graph: The target graph the steps belong to.

    Returns:
        Work by target id for every target of the graph, and the custom
        commands that matched no target.
    """
    by_name = {target.name: target for target in graph.targets}
    sources_to_target = _generated_sources(graph.targets)
    custom: dict[str, _Longest | None] = {target.id: None for target in graph.targets}
    compile: dict[str, _Longest | None] = dict(custom)
    link_ms: dict[str, int] = {target.id: 0 for target in graph.targets}
    link_outputs: dict[str, list[str]] = {target.id: [] for target in graph.targets}
    unattributed: list[Unattributed] = []
    for step in steps:
        target = _target_of(step, by_name, sources_to_target)
        if target is None:
            if step.role == CUSTOM:
                unattributed.append(
                    Unattributed(name=custom_name(step), duration_ms=step.duration_ms)
                )
            continue
        if step.role == COMPILE:
            detail = step.source or step.command or "(compile)"
            compile[target.id] = _longer(compile[target.id], step.duration_ms, detail)
        elif step.role == LINK:
            link_ms[target.id] += step.duration_ms
            for output in step.outputs:
                if output not in link_outputs[target.id]:
                    link_outputs[target.id].append(output)
        elif step.role == CUSTOM:
            custom[target.id] = _longer(custom[target.id], step.duration_ms, custom_name(step))
    work = {
        target.id: _Work(
            custom=custom[target.id],
            compile=compile[target.id],
            link_ms=link_ms[target.id],
            link_detail=", ".join(link_outputs[target.id]) or "(link)",
        )
        for target in graph.targets
    }
    return work, unattributed


def _longer(current: _Longest | None, duration_ms: int, detail: str) -> _Longest:
    """Keep the longer of a recorded step and a new one; the first wins a tie.

    Args:
        current: The longest step seen so far, if any.
        duration_ms: Duration of the new step.
        detail: What the new step was.

    Returns:
        Whichever is longer.
    """
    if current is not None and current.duration_ms >= duration_ms:
        return current
    return _Longest(duration_ms=duration_ms, detail=detail)


def _target_of(
    step: Step, by_name: dict[str, TargetNode], sources_to_target: dict[Path, TargetNode]
) -> TargetNode | None:
    """Decide which target a step belongs to.

    Args:
        step: The step.
        by_name: Graph targets by name.
        sources_to_target: Absolute paths of generated sources, by the target
            that compiles them.

    Returns:
        The target CMake recorded on the step; else the target whose source
        the step produced; else ``None``.
    """
    if step.target is not None:
        return by_name.get(step.target)
    if step.role != CUSTOM:
        return None
    for output in step.outputs:
        owner = sources_to_target.get(_absolute(step.working_dir, output))
        if owner is not None:
            return owner
    return None


def _generated_sources(targets: tuple[TargetNode, ...]) -> dict[Path, TargetNode]:
    """Index the absolute source paths of every target.

    The File API writes a source path relative to the source directory when
    the file lives there and absolute otherwise. Generated sources live in
    the build directory, so they come out absolute, and those are the ones a
    custom command can produce. Relative ones cannot be resolved without the
    source directory and are skipped.

    Args:
        targets: The graph's targets.

    Returns:
        Absolute source path to owning target; the first owner wins.
    """
    owners: dict[Path, TargetNode] = {}
    for target in targets:
        for source in target.sources:
            path = Path(source)
            if path.is_absolute():
                owners.setdefault(path.resolve(), target)
    return owners


def _absolute(working_dir: str, output: str) -> Path:
    """Resolve a step output against the directory the step ran in.

    Args:
        working_dir: The step's working directory.
        output: An entry of the step's ``outputs``.

    Returns:
        The absolute, symlink-resolved path.
    """
    return (Path(working_dir) / output).resolve()


def _schedule(graph: Graph, work: dict[str, _Work]) -> dict[str, _Schedule]:
    """Place every target's work on the timeline of a build with unlimited cores.

    Targets are visited in topological order (dependencies before dependents)
    with Kahn's algorithm, so each target's times follow from its
    dependencies' times and its own costs. A cycle cannot occur in a graph
    CMake generated, but if one did, the targets on it and everything that
    depends on them would be left out rather than looping forever.

    Args:
        graph: The target graph.
        work: Costs of every target in the graph.

    Returns:
        A schedule for each target reached, by id.
    """
    dependencies: dict[str, list[str]] = {target.id: [] for target in graph.targets}
    dependents: dict[str, list[str]] = {target.id: [] for target in graph.targets}
    for dependent, dependency in graph.edges:
        if dependent in dependencies and dependency in dependencies:
            dependencies[dependent].append(dependency)
            dependents[dependency].append(dependent)
    pending = {target_id: len(deps) for target_id, deps in dependencies.items()}

    schedule: dict[str, _Schedule] = {}
    ready = deque(target_id for target_id, count in pending.items() if count == 0)
    while ready:
        target_id = ready.popleft()
        schedule[target_id] = _place(work[target_id], dependencies[target_id], schedule)
        for dependent in dependents[target_id]:
            pending[dependent] -= 1
            if pending[dependent] == 0:
                ready.append(dependent)
    return schedule


def _place(own: _Work, dependencies: list[str], schedule: dict[str, _Schedule]) -> _Schedule:
    """Compute one target's times from its costs and its dependencies' schedules.

    Args:
        own: The target's costs.
        dependencies: Ids of its dependencies, all already scheduled.
        schedule: Schedules computed so far.

    Returns:
        The target's schedule.
    """
    custom_from = _latest(dependencies, lambda dep: schedule[dep].custom_ready_ms)
    deps_custom_ready = schedule[custom_from].custom_ready_ms if custom_from else 0
    if own.custom_ms >= deps_custom_ready:
        custom_ready, custom_from = own.custom_ms, None
    else:
        custom_ready = deps_custom_ready
    compile_end = custom_ready + own.compile_ms

    link_gate = _latest(dependencies, lambda dep: schedule[dep].finish_ms)
    deps_finish = schedule[link_gate].finish_ms if link_gate else 0
    if compile_end >= deps_finish:
        link_start, link_gate = compile_end, None
    else:
        link_start = deps_finish
    return _Schedule(
        custom_ready_ms=custom_ready,
        custom_from=custom_from,
        compile_end_ms=compile_end,
        link_start_ms=link_start,
        link_gate=link_gate,
        finish_ms=link_start + own.link_ms,
    )


def _latest(candidates: list[str], time_of: Callable[[str], int]) -> str | None:
    """Pick the candidate with the latest time; ties go to the larger id, for determinism.

    Args:
        candidates: Target ids to choose from.
        time_of: Time to compare by.

    Returns:
        The chosen id, or ``None`` when there are no candidates.
    """
    if not candidates:
        return None
    return max(candidates, key=lambda target_id: (time_of(target_id), target_id))


def _trace(
    last: str, schedule: dict[str, _Schedule], work: dict[str, _Work], names: dict[str, str]
) -> list[Segment]:
    """Walk back from the target that finishes last and collect the chain.

    Each target's link steps are gated either by a dependency's finish, in
    which case the walk continues into that dependency, or by its own
    compiles, in which case the chain ends with the compile and the custom
    commands that gated it. Zero-length groups are skipped so the chain
    reads as work.

    Args:
        last: Id of the target with the latest finish.
        schedule: Every target's schedule.
        work: Every target's costs.
        names: Target names by id.

    Returns:
        The segments in build order.
    """
    backwards: list[Segment] = []
    cursor = last
    while True:
        placed, own = schedule[cursor], work[cursor]
        if own.link_ms > 0:
            backwards.append(
                Segment(
                    target=names[cursor],
                    kind=LINK,
                    duration_ms=own.link_ms,
                    start_ms=placed.link_start_ms,
                    detail=own.link_detail,
                )
            )
        if placed.link_gate is not None:
            cursor = placed.link_gate
            continue
        if own.compile is not None:
            backwards.append(
                Segment(
                    target=names[cursor],
                    kind=COMPILE,
                    duration_ms=own.compile.duration_ms,
                    start_ms=placed.custom_ready_ms,
                    detail=own.compile.detail,
                )
            )
        owner = cursor
        while (origin := schedule[owner].custom_from) is not None:
            owner = origin
        longest = work[owner].custom
        if longest is not None:
            backwards.append(
                Segment(
                    target=names[owner],
                    kind=CUSTOM,
                    duration_ms=longest.duration_ms,
                    start_ms=0,
                    detail=longest.detail,
                )
            )
        break
    backwards.reverse()
    return backwards
