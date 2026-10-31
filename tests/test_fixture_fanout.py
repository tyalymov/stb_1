"""The fanout fixture: independent steps that run side by side."""

from __future__ import annotations

import pytest

from tests.support.cmake import CMakeProject, InstrumentationQuery

pytestmark = [pytest.mark.cmake, pytest.mark.slow]


def test_every_step_belongs_to_the_single_target(fanout: CMakeProject) -> None:
    """Eight generated parts plus main.c compile into one target named fanout."""
    fanout.write_query(
        InstrumentationQuery(hooks=("postCMakeBuild",), callbacks=(fanout.callback,))
    )
    fanout.configure()
    fanout.build("--parallel", "8")

    capture = fanout.captured()[0]
    roles = capture.roles()
    assert roles["custom"] == 8
    assert roles["compile"] == 9
    assert roles["link"] == 1
    assert {snippet["target"] for snippet in capture.with_role("compile")} == {"fanout"}


def test_independent_steps_overlap_in_time(fanout: CMakeProject) -> None:
    """With eight jobs the eight sleeps overlap: the span is far below their sum."""
    fanout.write_query(
        InstrumentationQuery(hooks=("postCMakeBuild",), callbacks=(fanout.callback,))
    )
    fanout.configure()
    fanout.build("--parallel", "8")

    customs = fanout.captured()[0].with_role("custom")
    starts = [snippet["timeStart"] for snippet in customs]
    ends = [snippet["timeStart"] + snippet["duration"] for snippet in customs]
    span = max(ends) - min(starts)
    total = sum(snippet["duration"] for snippet in customs)
    assert total >= 8 * 300
    assert span < total / 2
