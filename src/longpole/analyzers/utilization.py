"""How many steps ran at once, and for how much of the build.

A build with sixty cores and a parallelism of two is waiting on its
dependency chain, not on hardware; a build whose parallelism sits at the
core count is waiting on hardware. Telling the two apart needs the timeline
of concurrent steps, which the start and end times of the leaf steps give
directly. Wrapper steps (``cmakeBuild`` and friends) enclose the others and
are left out, as they are everywhere else.
"""

from __future__ import annotations

from dataclasses import dataclass

from longpole.analyzers.summary import WRAPPER_ROLES
from longpole.model import Run, Step


@dataclass(frozen=True, kw_only=True)
class Utilization:
    """Parallelism of a run over time.

    Attributes:
        cpu_count: Logical cores of the machine that ran the build, when the
            run recorded them.
        peak_parallelism: Most leaf steps running at the same moment.
        average_parallelism: Steps running on average, weighted by time,
            over the moments when at least one step ran.
        active_ms: Time during which at least one leaf step ran.
        idle_ms: Time between the first leaf step's start and the last one's
            end during which nothing ran: the build tool was deciding what to
            do next, or waiting on something outside the measured steps.
        tail_ms: Part of ``active_ms`` with exactly one step running. A long
            tail is a dependency chain the build could not parallelise.
        recommendation: One or two sentences with the numbers in them.
    """

    cpu_count: int | None
    peak_parallelism: int
    average_parallelism: float
    active_ms: int
    idle_ms: int
    tail_ms: int
    recommendation: str

    @property
    def tail_share(self) -> float:
        """Fraction of the active time spent on a single step; 0 when nothing ran."""
        return self.tail_ms / self.active_ms if self.active_ms else 0.0


def utilization(run: Run) -> Utilization:
    """Measure the parallelism of a run.

    Pure: reads the run, touches nothing else.

    Args:
        run: A stored run; one without leaf steps yields zeros and a
            recommendation saying so.

    Returns:
        The measurements.
    """
    leaf_steps = [step for step in run.steps if step.role not in WRAPPER_ROLES]
    cpu_count = run.manifest.environment.cpu_count
    if cpu_count is None and run.system is not None:
        cpu_count = run.system.logical_cpus
    peak, active, idle, tail, weighted = _sweep(leaf_steps)
    average = weighted / active if active else 0.0
    return Utilization(
        cpu_count=cpu_count,
        peak_parallelism=peak,
        average_parallelism=average,
        active_ms=active,
        idle_ms=idle,
        tail_ms=tail,
        recommendation=_recommend(cpu_count, peak, average, tail / active if active else 0.0),
    )


def _sweep(steps: list[Step]) -> tuple[int, int, int, int, float]:
    """Walk the timeline of step starts and ends.

    Args:
        steps: Leaf steps in any order.

    Returns:
        Peak concurrency, active milliseconds, idle milliseconds, tail
        milliseconds and the time-weighted sum of concurrency (the numerator
        of the average).
    """
    events: list[tuple[int, int]] = []
    for step in steps:
        events.append((step.time_start_ms, 1))
        events.append((step.time_end_ms, -1))
    # Ends sort before starts at the same instant, so a step that begins the
    # moment another finishes does not count as overlapping it.
    events.sort(key=lambda event: (event[0], event[1]))
    peak = active = idle = tail = 0
    weighted = 0.0
    running = 0
    previous: int | None = None
    for moment, change in events:
        if previous is not None:
            elapsed = moment - previous
            if running > 0:
                active += elapsed
                weighted += running * elapsed
                if running == 1:
                    tail += elapsed
            else:
                idle += elapsed
        running += change
        peak = max(peak, running)
        previous = moment
    return peak, active, idle, tail, weighted


def _recommend(cpu_count: int | None, peak: int, average: float, tail_share: float) -> str:
    """Put the numbers into sentences.

    Args:
        cpu_count: Cores of the machine, if known.
        peak: Peak concurrency.
        average: Average concurrency while active.
        tail_share: Fraction of active time on a single step.

    Returns:
        The text. It states what was measured and what it rules out; it does
        not guess a job count.
    """
    if peak == 0:
        return "No leaf steps ran, so there is nothing to measure."
    tail = f"{tail_share:.0%} of the active time ran a single step."
    if cpu_count is None:
        return f"Peak {peak} concurrent steps, {average:.1f} on average. {tail}"
    if peak >= cpu_count:
        return (
            f"At its peak {peak} steps ran at once on {cpu_count} cores ({average:.1f} on "
            f"average), so more jobs would not help on this machine. {tail}"
        )
    return (
        f"At most {peak} of {cpu_count} cores were busy at once ({average:.1f} on average): "
        f"either the job count was below the core count, or there was no more independent "
        f"work. {tail}"
    )
