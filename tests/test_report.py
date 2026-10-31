"""The report bundles every analysis and renders as JSON or text."""

from __future__ import annotations

import json

import pytest

from longpole.analyzers.critical_path import CriticalPath, Unavailable
from longpole.analyzers.report import build_report, to_json
from longpole.exporters import terminal
from longpole.model import Run
from tests.support.runs import sample_run


def test_report_holds_all_four_analyses() -> None:
    """One call runs the summary, the critical path, the utilization and the sizes."""
    report = build_report(sample_run(), top=2)
    assert report.summary.totals.step_count == 8
    assert isinstance(report.critical_path, CriticalPath)
    assert report.utilization.peak_parallelism == 1
    assert len(report.sizes.largest) == 2


def test_json_layout_carries_the_derived_numbers() -> None:
    """The JSON adds the share and availability of the path and the tail share of utilization."""
    data = to_json(build_report(sample_run()))
    assert set(data) == {"summary", "critical_path", "utilization", "sizes"}
    path = data["critical_path"]
    assert isinstance(path, dict)
    assert path["available"] is True
    assert path["lower_bound_ms"] == 180
    assert path["share"] == pytest.approx(180 / 2406)
    parallelism = data["utilization"]
    assert isinstance(parallelism, dict)
    assert parallelism["tail_share"] == 1.0
    json.dumps(data)


def test_json_without_a_graph_says_why() -> None:
    """A run without a graph serialises the reason and marks the path unavailable."""
    report = build_report(sample_run(with_graph=False))
    assert isinstance(report.critical_path, Unavailable)
    path = to_json(report)["critical_path"]
    assert isinstance(path, dict)
    assert path["available"] is False
    assert path["reason"] == report.critical_path.reason
    assert "configure" in report.critical_path.reason
    assert "share" not in path


def test_text_report_has_every_section() -> None:
    """The text shows the summary first, then the path, the parallelism and the sizes."""
    text = terminal.format_report(build_report(sample_run()))
    assert text.index("slowest targets") < text.index("critical path: ")
    assert "critical path: 180 ms lower bound, 7% of the 2.4 s span" in text
    assert "parallelism: peak 1 of 12 cores, average 1.0" in text
    assert "At most 1 of 12 cores" in text
    assert "artifacts: 99.3 KiB in 6 files" in text
    assert "largest artifacts" in text


def test_text_report_without_a_graph_prints_the_reason() -> None:
    """Without a path the section is one line with the reason, not an empty table."""
    text = terminal.format_report(build_report(sample_run(with_graph=False)))
    assert "critical path: critical path pending next configure" in text
    assert "lower bound" not in text


@pytest.mark.cmake
@pytest.mark.slow
def test_real_chain_report_names_the_chain(chain_run: Run) -> None:
    """On the chain build the path section lists a, b and c with a lower bound in seconds."""
    text = terminal.format_report(build_report(chain_run))
    section = text[text.index("critical path: ") :]
    assert "lower bound" in section
    for name in ("a", "b", "c"):
        assert f"\n{name} " in section
