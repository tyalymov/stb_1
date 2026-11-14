"""A run as a trace for Perfetto, in the Chrome JSON trace event format.

Perfetto (https://ui.perfetto.dev) opens JSON trace files directly, so this
is the cheapest way to look at a build on a timeline. Each target becomes a
process, each kind of step a thread within it, and each step a complete
event with its command, result and outputs in ``args``. Steps that overlap
in time go on separate lanes of the same kind, because a thread in this
format is a stack: a slice must nest inside or sit beside its neighbours,
never straddle them. Dependency edges from the target graph become flow
arrows, and the critical path gets a track of its own.

The format is documented at
https://docs.google.com/document/d/1CvAClvFfyA5R-PhYUmn5OOQtYMH4h6I0nSsKchNAySU.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass

from longpole import __version__
from longpole.analyzers import critical_path
from longpole.analyzers.critical_path import CriticalPath, Segment
from longpole.analyzers.report import Report
from longpole.model import Run, Step

CMAKE_PID = 0
"""Process of the steps CMake itself runs: configure, generate and the wrappers."""
CRITICAL_PATH_PID = 1
TESTS_PID = 2
UNATTRIBUTED_PID = 3
FIRST_TARGET_PID = 10

CRITICAL_PATH_CATEGORY = "critical-path"
DEPENDENCY_CATEGORY = "dependency"

Event = dict[str, object]


@dataclass(frozen=True, kw_only=True)
class _Placed:
    """Where a step landed in the trace.

    Attributes:
        step: The step.
        pid: Its process.
        tid: Its thread (lane).
    """

    step: Step
    pid: int
    tid: int


def format_trace(run: Run, report: Report) -> str:
    """Render a run as a JSON trace document."""
    return json.dumps(trace_document(run, report), indent=None, separators=(",", ":"))


def trace_document(run: Run, report: Report) -> dict[str, object]:
    """Build the trace document.

    Timestamps are microseconds from the earliest step start, so the trace
    begins at zero.
    """
    origin_ms = min((step.time_start_ms for step in run.steps), default=0)
    pids = _process_ids(run)
    placed = _place(run.steps, pids)
    events: list[Event] = []
    events.extend(_process_metadata(run, pids))
    events.extend(_thread_metadata(placed))
    events.extend(_step_event(item, origin_ms) for item in placed)
    events.extend(_flow_events(run, placed, origin_ms))
    if isinstance(report.critical_path, CriticalPath):
        events.extend(_critical_path_events(report.critical_path, placed, origin_ms))
    environment = run.manifest.environment
    return {
        "traceEvents": events,
        "displayTimeUnit": "ms",
        "otherData": {
            "longpole": __version__,
            "run_id": run.manifest.run_id,
            "hook": run.manifest.hook,
            "build_dir": run.manifest.build_dir,
            "created_at": run.manifest.created_at,
            "cmake_version": environment.cmake_version,
            "generator": environment.generator,
        },
    }


def _process_ids(run: Run) -> dict[str, int]:
    """Assign a process id to every target."""
    names: list[str] = []
    if run.graph is not None:
        names.extend(target.name for target in run.graph.targets)
    for step in run.steps:
        if step.target is not None and step.target not in names:
            names.append(step.target)
    return {name: FIRST_TARGET_PID + index for index, name in enumerate(names)}


def _pid_of(step: Step, pids: dict[str, int]) -> int:
    """Pick the process a step belongs to."""
    if step.target is not None and step.target in pids:
        return pids[step.target]
    if step.role == "test":
        return TESTS_PID
    if step.role == "custom":
        return UNATTRIBUTED_PID
    return CMAKE_PID


def _place(steps: tuple[Step, ...], pids: dict[str, int]) -> list[_Placed]:
    """Give every step a process and a lane within it.

    Lanes are allocated per process and role: a step takes the first lane
    whose previous occupant finished before it started, so steps on one
    lane never overlap and Perfetto draws them as flat slices.
    """
    ordered = sorted(steps, key=lambda step: (step.time_start_ms, -step.duration_ms))
    lane_ends: dict[tuple[int, str], list[int]] = {}
    placed: list[_Placed] = []
    for step in ordered:
        pid = _pid_of(step, pids)
        lanes = lane_ends.setdefault((pid, step.role), [])
        for lane, busy_until in enumerate(lanes):
            if busy_until <= step.time_start_ms:
                lanes[lane] = step.time_end_ms
                break
        else:
            lane = len(lanes)
            lanes.append(step.time_end_ms)
        placed.append(_Placed(step=step, pid=pid, tid=_tid(step.role, lane)))
    return placed


def _tid(role: str, lane: int) -> int:
    """Number a lane so that lanes of one role sit together in the UI."""
    order = ("configure", "generate", "custom", "compile", "link", "install", "test")
    base = order.index(role) if role in order else len(order)
    return base * 100 + lane + 1


def _process_metadata(run: Run, pids: dict[str, int]) -> Iterable[Event]:
    """Name and order the processes.

    Yields:
        ``process_name`` and ``process_sort_index`` metadata events.
    """
    fixed = {
        CMAKE_PID: "cmake",
        CRITICAL_PATH_PID: "critical path",
        TESTS_PID: "tests",
        UNATTRIBUTED_PID: "custom commands outside targets",
    }
    for pid, name in fixed.items():
        yield _metadata("process_name", pid, 0, {"name": name})
        yield _metadata("process_sort_index", pid, 0, {"sort_index": pid})
    for name, pid in pids.items():
        yield _metadata("process_name", pid, 0, {"name": name})
        yield _metadata("process_sort_index", pid, 0, {"sort_index": pid})
    del run


def _thread_metadata(placed: list[_Placed]) -> Iterable[Event]:
    """Name every lane after its role.

    Yields:
        One ``thread_name`` event per distinct lane.
    """
    seen: set[tuple[int, int]] = set()
    for item in placed:
        key = (item.pid, item.tid)
        if key in seen:
            continue
        seen.add(key)
        lane = (item.tid - 1) % 100
        name = item.step.role if lane == 0 else f"{item.step.role} #{lane + 1}"
        yield _metadata("thread_name", item.pid, item.tid, {"name": name})


def _metadata(kind: str, pid: int, tid: int, args: dict[str, object]) -> Event:
    """A metadata event."""
    return {"ph": "M", "name": kind, "pid": pid, "tid": tid, "args": args}


def _step_event(item: _Placed, origin_ms: int) -> Event:
    """A complete event for one step."""
    step = item.step
    args: dict[str, object] = {"role": step.role, "result": step.result}
    if step.command is not None:
        args["command"] = step.command
    if step.target is not None:
        args["target"] = step.target
    if step.source is not None:
        args["source"] = step.source
    if step.outputs:
        args["outputs"] = list(step.outputs)
        args["output_sizes"] = list(step.output_sizes)
    if step.language is not None:
        args["language"] = step.language
    if step.config:
        args["config"] = step.config
    if step.test_name is not None:
        args["test"] = step.test_name
    if step.process_metrics is not None:
        args["max_rss_kib"] = step.process_metrics.max_rss_kib
        args["cpu_time_us"] = step.process_metrics.cpu_time_us
    return {
        "name": _step_name(step),
        "cat": step.role,
        "ph": "X",
        "ts": (step.time_start_ms - origin_ms) * 1000,
        "dur": step.duration_ms * 1000,
        "pid": item.pid,
        "tid": item.tid,
        "args": args,
    }


def _step_name(step: Step) -> str:
    """Label a step the way a person looks for it."""
    if step.role == "compile" and step.source is not None:
        return step.source.rsplit("/", 1)[-1]
    if step.role == "test" and step.test_name is not None:
        return step.test_name
    if step.outputs:
        return step.outputs[0]
    return step.role


def _flow_events(run: Run, placed: list[_Placed], origin_ms: int) -> Iterable[Event]:
    """Draw an arrow from each dependency's last step to its dependent's first.

    Yields:
        Paired flow start and finish events for every edge whose two
        targets both have steps.
    """
    if run.graph is None:
        return
    by_target: dict[str, list[_Placed]] = {}
    for item in placed:
        if item.step.target is not None:
            by_target.setdefault(item.step.target, []).append(item)
    names = {target.id: target.name for target in run.graph.targets}
    flow_id = 0
    for dependent_id, dependency_id in run.graph.edges:
        dependent = by_target.get(names.get(dependent_id, ""))
        dependency = by_target.get(names.get(dependency_id, ""))
        if not dependent or not dependency:
            continue
        flow_id += 1
        last = max(dependency, key=lambda item: item.step.time_end_ms)
        first = min(dependent, key=lambda item: item.step.time_start_ms)
        common: Event = {"name": "depends on", "cat": DEPENDENCY_CATEGORY, "id": flow_id}
        yield {
            **common,
            "ph": "s",
            "ts": (last.step.time_end_ms - origin_ms) * 1000,
            "pid": last.pid,
            "tid": last.tid,
        }
        yield {
            **common,
            "ph": "f",
            "bp": "e",
            "ts": (first.step.time_start_ms - origin_ms) * 1000,
            "pid": first.pid,
            "tid": first.tid,
        }


def _critical_path_events(
    path: CriticalPath, placed: list[_Placed], origin_ms: int
) -> Iterable[Event]:
    """One slice per segment of the critical path, over the steps the segment stands for.

    A segment is drawn at the wall-clock time its steps actually ran: the
    longest compile, the longest custom command or the link steps of the
    target. Segments of a chain overlap in real time, since the build runs
    dependents alongside their dependencies, so each one gets a lane of its
    own and the lane is named after it.

    Yields:
        Thread metadata and complete events on the critical path process.
    """
    for lane, segment in enumerate(path.chain, start=1):
        steps = _segment_steps(segment, placed)
        if not steps:
            continue
        label = f"{segment.target} {segment.kind}"
        yield {
            "name": "thread_name",
            "ph": "M",
            "pid": CRITICAL_PATH_PID,
            "tid": lane,
            "args": {"name": label},
        }
        start = min(step.time_start_ms for step in steps)
        end = max(step.time_end_ms for step in steps)
        yield {
            "name": label,
            "cat": CRITICAL_PATH_CATEGORY,
            "ph": "X",
            "ts": (start - origin_ms) * 1000,
            "dur": (end - start) * 1000,
            "pid": CRITICAL_PATH_PID,
            "tid": lane,
            "args": {
                "cost_ms": segment.duration_ms,
                "model_start_ms": segment.start_ms,
                "detail": segment.detail,
            },
        }


def _segment_steps(segment: Segment, placed: list[_Placed]) -> list[Step]:
    """Find the steps a segment of the critical path stands for."""
    steps = [item.step for item in placed if item.step.role == segment.kind]
    if segment.kind == critical_path.LINK:
        return [step for step in steps if step.target == segment.target]
    if segment.kind == critical_path.COMPILE:
        own = [step for step in steps if step.target == segment.target]
        exact = [step for step in own if step.source == segment.detail]
        return exact or own
    return [step for step in steps if critical_path.custom_name(step) == segment.detail]
