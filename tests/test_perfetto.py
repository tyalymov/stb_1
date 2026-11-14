"""The Perfetto exporter writes a well-formed trace with one slice per step."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from longpole.analyzers.report import build_report
from longpole.cli import main
from longpole.exporters import perfetto
from longpole.model import Run, Step
from longpole.store import RunStore
from tests.support.runs import sample_run, write_run


def _events(run: Run) -> list[dict[str, object]]:
    """Render a run and parse the events back."""
    document = json.loads(perfetto.format_trace(run, build_report(run)))
    events: list[dict[str, object]] = document["traceEvents"]
    return events


def _slices(
    events: list[dict[str, object]], category: str | None = None
) -> list[dict[str, object]]:
    """Select complete events, optionally of one category."""
    chosen = [event for event in events if event["ph"] == "X"]
    if category is None:
        return [event for event in chosen if event["cat"] != perfetto.CRITICAL_PATH_CATEGORY]
    return [event for event in chosen if event["cat"] == category]


def test_every_step_becomes_a_slice_inside_the_span() -> None:
    """One X event per step, in microseconds from the first start, all inside the span."""
    run = sample_run()
    slices = _slices(_events(run))
    assert len(slices) == len(run.steps)
    span_us = run.span_ms * 1000
    for event in slices:
        ts, dur = int(str(event["ts"])), int(str(event["dur"]))
        assert ts >= 0 and ts + dur <= span_us
    names = {event["name"] for event in slices}
    assert {"gen_a.c", "liba.dylib", "configure"} <= names


def test_targets_are_processes_and_the_graph_becomes_flows() -> None:
    """Each target has a named process; each dependency edge has a flow start and finish."""
    events = _events(sample_run())
    process_names = {
        str(event["args"]["name"])  # type: ignore[index]
        for event in events
        if event["ph"] == "M" and event["name"] == "process_name"
    }
    assert {"cmake", "critical path", "a", "b", "c"} <= process_names
    starts = [event for event in events if event["ph"] == "s"]
    finishes = [event for event in events if event["ph"] == "f"]
    assert len(starts) == len(finishes) == 3, "edges b->a, c->b, c->a"
    assert {event["id"] for event in starts} == {event["id"] for event in finishes}


def test_critical_path_gets_its_own_track() -> None:
    """Each segment of the chain is a slice on its own lane of the critical path process."""
    events = _events(sample_run())
    track = _slices(events, perfetto.CRITICAL_PATH_CATEGORY)
    assert [event["name"] for event in track] == ["a compile", "a link", "b link", "c link"]
    assert all(event["pid"] == perfetto.CRITICAL_PATH_PID for event in track)
    assert len({event["tid"] for event in track}) == len(track), "one lane per segment"
    lane_names = {
        str(event["args"]["name"])  # type: ignore[index]
        for event in events
        if event["ph"] == "M"
        and event["name"] == "thread_name"
        and event["pid"] == perfetto.CRITICAL_PATH_PID
    }
    assert lane_names == {"a compile", "a link", "b link", "c link"}
    without_graph = _slices(_events(sample_run(with_graph=False)), perfetto.CRITICAL_PATH_CATEGORY)
    assert without_graph == []


def test_overlapping_steps_of_one_kind_take_separate_lanes() -> None:
    """Two compiles of one target that overlap in time land on different threads."""
    base = sample_run()
    overlapping = tuple(
        Step(
            role="compile",
            command=f"cc -c {n}.c",
            working_dir="/work/build",
            result=0,
            time_start_ms=1_000 + offset,
            duration_ms=100,
            target="a",
            source=f"/work/{n}.c",
        )
        for n, offset in (("x", 0), ("y", 10), ("z", 200))
    )
    slices = _slices(_events(replace(base, steps=overlapping)))
    by_name = {str(event["name"]): event for event in slices}
    assert by_name["x.c"]["tid"] != by_name["y.c"]["tid"]
    assert by_name["z.c"]["tid"] == by_name["x.c"]["tid"], "a free lane is reused"


def test_export_writes_a_file_or_stdout(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """--perfetto PATH writes the trace; '-' prints it; no format is a usage error."""
    store = RunStore(tmp_path / "store")
    write_run(store, sample_run())
    target = tmp_path / "trace.json"

    assert main(["export", "latest", "--perfetto", str(target), "--store", str(store.root)]) == 0
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["otherData"]["run_id"] == "20261031T120000Z-abc123"
    assert document["displayTimeUnit"] == "ms"

    assert main(["export", "--perfetto", "-", "--store", str(store.root)]) == 0
    assert json.loads(capsys.readouterr().out)["otherData"]["hook"] == "postCMakeBuild"

    assert main(["export", "latest", "--store", str(store.root)]) == 1
    assert "choose at least one format" in capsys.readouterr().err


@pytest.mark.cmake
@pytest.mark.slow
def test_real_chain_trace_has_a_slice_per_step_and_the_path(chain_run: Run) -> None:
    """A real build renders every step; the path track runs from a's generator to c's link."""
    events = _events(chain_run)
    assert len(_slices(events)) == len(chain_run.steps)
    track = _slices(events, perfetto.CRITICAL_PATH_CATEGORY)
    names = [event["name"] for event in track]
    assert names[0] == "a custom" and names[-1] == "c link"
    assert {"a link", "b link"} <= set(names)
    assert len([event for event in events if event["ph"] == "s"]) >= 2
