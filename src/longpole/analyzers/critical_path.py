"""The chain of targets that bounds how fast a build can finish.

A build system runs independent targets side by side, so adding up step
durations says little about wall-clock time. What does is the longest chain
of targets that have to wait for each other: however many cores there are,
the build cannot finish before that chain does. This module finds the chain
from the target graph and the steps that belong to each target, and reports
its length as the lower bound on the build's wall-clock time.

Costs are per target, not per step, because the graph is per target. Inside
a target the compile steps run in parallel, and so do independent custom
commands, so each of those groups contributes its longest member; link
steps run one after another (a static library is ``ar`` followed by
``ranlib``), so they add up. A custom command joins a target either because
CMake recorded the target on the snippet or because its outputs are among
the target's sources in the File API, which is how a generated source is
attributed to the target that compiles it. The result is an approximation
that is exact for the common shapes and never worse than the sum of a
target's steps.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path

from longpole.model import Graph, Run, Step, TargetNode


@dataclass(frozen=True, kw_only=True)
class TargetCost:
    """How long one target held the build, split by kind of step.

    Attributes:
        id: Graph id of the target.
        name: Target name.
        compile_ms: Longest compile step; compiles of one target run in parallel.
        link_ms: Link steps added up; they run one after another.
        custom_ms: Longest custom command attributed to the target.
    """

    id: str
    name: str
    compile_ms: int
    link_ms: int
    custom_ms: int

    @property
    def total_ms(self) -> int:
        """Compile, link and custom time together."""
        return self.compile_ms + self.link_ms + self.custom_ms


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
    """The longest chain of dependent targets in a run.

    Attributes:
        chain: Targets in build order, each depending on the one before it.
            Targets without any measured step are left out of the chain even
            when they sit on it, so the list reads as work rather than as
            graph structure.
        lower_bound_ms: Total cost of the chain: the least wall-clock time
            the build could take with unlimited parallelism.
        span_ms: Wall-clock span the run actually took, wrapper steps included.
        unattributed: Custom commands that belong to no target. Their time is
            not in the chain.
    """

    chain: tuple[TargetCost, ...]
    lower_bound_ms: int
    span_ms: int
    unattributed: tuple[Unattributed, ...]

    @property
    def share(self) -> float:
        """Lower bound as a fraction of the span; 0 for a run with no span.

        Close to 1 means the build already ran at the speed its dependencies
        allow. Well below 1 means more parallelism would help.
        """
        return self.lower_bound_ms / self.span_ms if self.span_ms else 0.0


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
NO_WORK_REASON = "no target did measurable work in this run"


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
    costs, unattributed = target_costs(run.steps, run.graph)
    chain = _longest_chain(run.graph, costs)
    lower_bound = sum(cost.total_ms for cost in chain)
    if lower_bound == 0:
        return Unavailable(reason=NO_WORK_REASON)
    return CriticalPath(
        chain=tuple(cost for cost in chain if cost.total_ms > 0),
        lower_bound_ms=lower_bound,
        span_ms=run.span_ms,
        unattributed=tuple(unattributed),
    )


def target_costs(
    steps: tuple[Step, ...], graph: Graph
) -> tuple[dict[str, TargetCost], list[Unattributed]]:
    """Attribute steps to targets and price each target.

    Args:
        steps: The run's steps.
        graph: The target graph the steps belong to.

    Returns:
        Costs by target id for every target of the graph, and the custom
        commands that matched no target.
    """
    by_name = {target.name: target for target in graph.targets}
    sources_to_target = _generated_sources(graph.targets)
    compile_ms: dict[str, int] = {target.id: 0 for target in graph.targets}
    link_ms: dict[str, int] = dict(compile_ms)
    custom_ms: dict[str, int] = dict(compile_ms)
    unattributed: list[Unattributed] = []
    for step in steps:
        target = _target_of(step, by_name, sources_to_target)
        if target is None:
            if step.role == "custom":
                unattributed.append(
                    Unattributed(name=_custom_name(step), duration_ms=step.duration_ms)
                )
            continue
        if step.role == "compile":
            compile_ms[target.id] = max(compile_ms[target.id], step.duration_ms)
        elif step.role == "link":
            link_ms[target.id] += step.duration_ms
        elif step.role == "custom":
            custom_ms[target.id] = max(custom_ms[target.id], step.duration_ms)
    costs = {
        target.id: TargetCost(
            id=target.id,
            name=target.name,
            compile_ms=compile_ms[target.id],
            link_ms=link_ms[target.id],
            custom_ms=custom_ms[target.id],
        )
        for target in graph.targets
    }
    return costs, unattributed


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
    if step.role != "custom":
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


def _custom_name(step: Step) -> str:
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


def _longest_chain(graph: Graph, costs: dict[str, TargetCost]) -> list[TargetCost]:
    """Find the heaviest path through the dependency graph.

    Targets are visited in topological order (dependencies before
    dependents) with Kahn's algorithm; the best path ending at each target is
    its own cost plus the best path ending at its heaviest dependency. A
    cycle cannot occur in a graph CMake generated, but if one did, the
    targets on it would be left out rather than looping forever.

    Args:
        graph: The target graph.
        costs: Cost of every target in the graph.

    Returns:
        The chain in build order; empty when the graph has no targets.
    """
    dependents: dict[str, list[str]] = {target.id: [] for target in graph.targets}
    pending: dict[str, int] = {target.id: 0 for target in graph.targets}
    for dependent, dependency in graph.edges:
        if dependent in pending and dependency in pending:
            dependents[dependency].append(dependent)
            pending[dependent] += 1

    best: dict[str, int] = {}
    via: dict[str, str | None] = {}
    ready = deque(target_id for target_id, count in pending.items() if count == 0)
    while ready:
        target_id = ready.popleft()
        heaviest = max(
            (dep for dep in graph.dependencies_of(target_id) if dep in best),
            key=lambda dep: best[dep],
            default=None,
        )
        best[target_id] = costs[target_id].total_ms + (best[heaviest] if heaviest else 0)
        via[target_id] = heaviest
        for dependent in dependents[target_id]:
            pending[dependent] -= 1
            if pending[dependent] == 0:
                ready.append(dependent)

    if not best:
        return []
    end = max(best, key=lambda target_id: (best[target_id], target_id))
    chain: list[TargetCost] = []
    cursor: str | None = end
    while cursor is not None:
        chain.append(costs[cursor])
        cursor = via[cursor]
    chain.reverse()
    return chain
