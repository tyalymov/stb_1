"""Totals, phases and the slowest items of one run.

The summary answers the first questions about a build: how long it took,
how much of that was spent working, where the time went by kind of step,
and which translation units, targets, tests and custom commands cost the
most. It groups by what a person thinks of as one thing: a static library
produces two link snippets (``ar`` and ``ranlib``), so target totals add
up every step that names the target.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from longpole.model import Run, Step

WRAPPER_ROLES = frozenset({"build", "cmakeBuild", "cmakeInstall", "ctest"})
"""Roles that time a whole invocation and enclose the other steps.

They are counted as steps but left out of phase totals and busy time, since
adding them to the steps they contain would count that time twice.
"""

PHASE_ORDER: tuple[str, ...] = (
    "configure",
    "generate",
    "custom",
    "compile",
    "link",
    "install",
    "test",
)
"""Display order of the leaf roles; roles CMake adds later sort after these."""


@dataclass(frozen=True, kw_only=True)
class Totals:
    """Headline numbers of a run.

    Attributes:
        span_ms: Wall-clock time from the first step's start to the last
            step's end, wrapper steps included.
        busy_ms: Sum of the durations of the leaf steps; with perfect
            parallelism the span would approach ``busy_ms`` divided by the
            number of cores.
        step_count: Every step, wrappers included.
        failed_count: Steps that exited with a non-zero status.
    """

    span_ms: int
    busy_ms: int
    step_count: int
    failed_count: int


@dataclass(frozen=True, kw_only=True)
class Phase:
    """Time spent in one kind of step.

    Attributes:
        role: The step role.
        count: Steps of that role.
        total_ms: Their durations added up.
    """

    role: str
    count: int
    total_ms: int


@dataclass(frozen=True, kw_only=True)
class Entry:
    """One row of a slowest-items table.

    Attributes:
        name: What the row is about: a source path, a target, a test name
            or a custom command's outputs.
        total_ms: Durations of the grouped steps added up.
        count: How many steps were grouped.
        detail: A short note, e.g. ``1 compile, 1 link`` or ``failed (exit 1)``.
    """

    name: str
    total_ms: int
    count: int
    detail: str


@dataclass(frozen=True, kw_only=True)
class Summary:
    """The summary of one run.

    Attributes:
        run_id: The run's store id.
        hook: Hook that produced the run.
        build_dir: Build tree the run came from.
        created_at: Capture time.
        totals: Headline numbers.
        phases: Time by step role, in :data:`PHASE_ORDER`; only roles present.
        slowest_sources: Translation units by compile time, longest first.
        slowest_targets: Targets by the time of every step that names them.
        slowest_tests: Tests by duration.
        slowest_custom: Custom commands by duration, named by their outputs.
    """

    run_id: str
    hook: str
    build_dir: str
    created_at: str
    totals: Totals
    phases: tuple[Phase, ...]
    slowest_sources: tuple[Entry, ...]
    slowest_targets: tuple[Entry, ...]
    slowest_tests: tuple[Entry, ...]
    slowest_custom: tuple[Entry, ...]


def summarize(run: Run, *, top: int = 10) -> Summary:
    """Summarise a run."""
    leaf_steps = [step for step in run.steps if step.role not in WRAPPER_ROLES]
    return Summary(
        run_id=run.manifest.run_id,
        hook=run.manifest.hook,
        build_dir=run.manifest.build_dir,
        created_at=run.manifest.created_at,
        totals=Totals(
            span_ms=run.span_ms,
            busy_ms=sum(step.duration_ms for step in leaf_steps),
            step_count=len(run.steps),
            failed_count=sum(1 for step in run.steps if step.result not in (0, None)),
        ),
        phases=phases(leaf_steps),
        slowest_sources=slowest(
            (s for s in run.steps if s.role == "compile" and s.source is not None),
            key=lambda step: step.source or "",
            detail=_language_detail,
            top=top,
        ),
        slowest_targets=slowest(
            (s for s in run.steps if s.target is not None),
            key=lambda step: step.target or "",
            detail=_role_mix_detail,
            top=top,
        ),
        slowest_tests=slowest(
            (s for s in run.steps if s.role == "test" and s.test_name is not None),
            key=lambda step: step.test_name or "",
            detail=_result_detail,
            top=top,
        ),
        slowest_custom=slowest(
            (s for s in run.steps if s.role == "custom"),
            key=_custom_name,
            detail=_result_detail,
            top=top,
        ),
    )


def phases(steps: Iterable[Step]) -> tuple[Phase, ...]:
    """Add up durations by role."""
    counts: Counter[str] = Counter()
    totals: Counter[str] = Counter()
    for step in steps:
        counts[step.role] += 1
        totals[step.role] += step.duration_ms
    known = [role for role in PHASE_ORDER if role in counts]
    unknown = sorted(role for role in counts if role not in PHASE_ORDER)
    return tuple(
        Phase(role=role, count=counts[role], total_ms=totals[role]) for role in known + unknown
    )


def slowest(
    steps: Iterable[Step],
    *,
    key: Callable[[Step], str],
    detail: Callable[[list[Step]], str],
    top: int,
) -> tuple[Entry, ...]:
    """Group steps by a name and rank the groups by total duration."""
    groups: dict[str, list[Step]] = {}
    for step in steps:
        groups.setdefault(key(step), []).append(step)
    entries = [
        Entry(
            name=name,
            total_ms=sum(step.duration_ms for step in members),
            count=len(members),
            detail=detail(members),
        )
        for name, members in groups.items()
    ]
    entries.sort(key=lambda entry: (-entry.total_ms, entry.name))
    return tuple(entries[:top])


def _language_detail(steps: list[Step]) -> str:
    """Languages of a group of compile steps."""
    return ", ".join(sorted({step.language for step in steps if step.language}))


def _role_mix_detail(steps: list[Step]) -> str:
    """What kinds of step a target's total is made of."""
    counts = Counter(step.role for step in steps)
    ordered = [role for role in PHASE_ORDER if role in counts] + sorted(
        role for role in counts if role not in PHASE_ORDER
    )
    return ", ".join(f"{counts[role]} {role}" for role in ordered)


def _result_detail(steps: list[Step]) -> str:
    """Whether a group of steps succeeded."""
    failures = [step.result for step in steps if step.result not in (0, None)]
    return f"failed (exit {failures[0]})" if failures else "passed"


def _custom_name(step: Step) -> str:
    """Name a custom command by what it produces."""
    if step.outputs:
        return ", ".join(step.outputs)
    return step.command or "(custom command)"
