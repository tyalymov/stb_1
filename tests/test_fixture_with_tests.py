"""The with_tests fixture: ctest records one snippet per test and one per run."""

from __future__ import annotations

import pytest

from tests.support.cmake import CMakeProject, InstrumentationQuery

pytestmark = [pytest.mark.cmake, pytest.mark.slow]


def test_ctest_run_is_indexed_at_post_ctest(with_tests: CMakeProject) -> None:
    """Running ctest triggers postCTest with a ctest snippet and one test snippet per test."""
    with_tests.write_query(
        InstrumentationQuery(hooks=("postCTest",), callbacks=(with_tests.callback,))
    )
    with_tests.configure()
    result = with_tests.run_ctest()
    assert result.returncode != 0, "the fixture's failing test must make ctest fail"

    captured = with_tests.captured()
    assert [capture.hook for capture in captured] == ["postCTest"]
    roles = captured[0].roles()
    assert roles["ctest"] == 1
    assert roles["test"] == 4


def test_test_snippets_carry_name_result_and_duration(with_tests: CMakeProject) -> None:
    """Each test snippet names the test, records its exit status and its wall time."""
    with_tests.write_query(
        InstrumentationQuery(hooks=("postCTest",), callbacks=(with_tests.callback,))
    )
    with_tests.configure()
    with_tests.run_ctest()

    tests = {snippet["testName"]: snippet for snippet in with_tests.captured()[0].with_role("test")}
    assert set(tests) == {"fast", "medium", "slow", "failing"}
    assert tests["fast"]["result"] == 0
    assert tests["failing"]["result"] != 0
    assert tests["slow"]["duration"] >= 500
    assert tests["medium"]["duration"] >= 300
