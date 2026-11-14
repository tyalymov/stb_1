"""The utilization analyzer measures how many steps ran at once."""

from __future__ import annotations

from dataclasses import replace

import pytest

from longpole.analyzers.utilization import utilization
from longpole.model import Run, Step
from tests.support.runs import sample_run


def _step(start_ms: int, duration_ms: int, role: str = "compile") -> Step:
    """A minimal step occupying a time interval."""
    return Step(
        role=role,
        command="cc",
        working_dir="/work/build",
        result=0,
        time_start_ms=start_ms,
        duration_ms=duration_ms,
        target="t",
    )


def test_overlapping_steps_count_as_parallel() -> None:
    """Two steps overlapping for five of fifteen milliseconds: peak 2, average 4/3, tail 10."""
    run = replace(sample_run(), steps=(_step(0, 10), _step(5, 10)))
    result = utilization(run)
    assert result.peak_parallelism == 2
    assert result.active_ms == 15
    assert result.idle_ms == 0
    assert result.tail_ms == 10
    assert result.tail_share == pytest.approx(10 / 15)
    assert result.average_parallelism == pytest.approx(20 / 15)


def test_gaps_between_steps_are_idle_not_active() -> None:
    """Time with nothing running is idle; a step that starts as another ends does not overlap it."""
    run = replace(sample_run(), steps=(_step(0, 10), _step(10, 10), _step(30, 5)))
    result = utilization(run)
    assert result.peak_parallelism == 1
    assert result.active_ms == 25
    assert result.idle_ms == 10
    assert result.tail_share == 1.0


def test_wrapper_steps_are_ignored() -> None:
    """The cmakeBuild wrapper encloses everything and would otherwise double every count."""
    wrapper = _step(0, 1000, role="cmakeBuild")
    run = replace(sample_run(), steps=(wrapper, _step(100, 10)))
    result = utilization(run)
    assert result.peak_parallelism == 1
    assert result.active_ms == 10


def test_sample_run_is_serial_on_twelve_cores() -> None:
    """The sample run never overlaps steps, and the text says how little of the machine it used."""
    result = utilization(sample_run())
    assert result.cpu_count == 12
    assert result.peak_parallelism == 1
    assert result.average_parallelism == 1.0
    assert result.tail_share == 1.0
    assert "At most 1 of 12 cores" in result.recommendation
    assert "100% of the active time ran a single step" in result.recommendation


def test_cpu_count_falls_back_to_the_host_description() -> None:
    """Without a recorded core count the static host information supplies it."""
    base = sample_run()
    environment = replace(base.manifest.environment, cpu_count=None)
    run = replace(base, manifest=replace(base.manifest, environment=environment))
    assert utilization(run).cpu_count == 12
    assert utilization(replace(run, system=None)).cpu_count is None
    assert "Peak 1 concurrent steps" in utilization(replace(run, system=None)).recommendation


def test_no_leaf_steps_measures_nothing() -> None:
    """A run of wrapper steps only has nothing to measure and says so."""
    run = replace(sample_run(), steps=(_step(0, 1000, role="cmakeBuild"),))
    result = utilization(run)
    assert result.peak_parallelism == 0
    assert result.average_parallelism == 0.0
    assert result.recommendation.startswith("No leaf steps ran")


@pytest.mark.cmake
@pytest.mark.slow
def test_fanout_with_eight_jobs_overlaps_its_generators(fanout_run: Run) -> None:
    """Eight independent sleeps under eight jobs run side by side for most of the build."""
    result = utilization(fanout_run)
    assert result.peak_parallelism >= 4
    assert result.average_parallelism > 1.5
    assert result.tail_share < 0.9
    assert result.active_ms > 0
