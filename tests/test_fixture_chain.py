"""The chain fixture builds, and CMake hands its steps to our callback."""

from __future__ import annotations

import pytest

from tests.support.cmake import CMakeProject, InstrumentationQuery

pytestmark = [pytest.mark.cmake, pytest.mark.slow]


def test_one_build_yields_one_index_with_every_step(chain: CMakeProject) -> None:
    """A full configure and build lands in a single postCMakeBuild index.

    Configure and generate snippets are written before any hook fires, so
    the first index after the build carries them too.
    """
    chain.write_query(InstrumentationQuery(hooks=("postCMakeBuild",), callbacks=(chain.callback,)))
    chain.configure()
    chain.build()

    captured = chain.captured()
    assert [capture.hook for capture in captured] == ["postCMakeBuild"]

    roles = captured[0].roles()
    assert roles["configure"] == 1
    assert roles["generate"] == 1
    assert roles["custom"] == 3
    assert roles["compile"] == 3
    assert roles["link"] == 3
    assert roles["cmakeBuild"] == 1


def test_steps_carry_their_target_and_source(chain: CMakeProject) -> None:
    """Compile snippets name the target and the generated source they belong to."""
    chain.write_query(InstrumentationQuery(hooks=("postCMakeBuild",), callbacks=(chain.callback,)))
    chain.configure()
    chain.build()

    compiles = chain.captured()[0].with_role("compile")
    assert {snippet["target"] for snippet in compiles} == {"a", "b", "c"}
    assert {snippet["source"].rsplit("/", 1)[-1] for snippet in compiles} == {
        "gen_a.c",
        "gen_b.c",
        "gen_c.c",
    }
    assert all(snippet["language"] == "C" for snippet in compiles)


def test_custom_commands_took_at_least_their_sleep(chain: CMakeProject) -> None:
    """Durations are real wall-clock: each generator step is at least its sleep."""
    chain.write_query(InstrumentationQuery(hooks=("postCMakeBuild",), callbacks=(chain.callback,)))
    chain.configure()
    chain.build()

    by_output = {
        snippet["outputs"][0].rsplit("/", 1)[-1]: snippet["duration"]
        for snippet in chain.captured()[0].with_role("custom")
    }
    assert by_output["gen_a.c"] >= 600
    assert by_output["gen_b.c"] >= 300
    assert by_output["gen_c.c"] >= 200


def test_data_is_gone_after_callbacks_ran(chain: CMakeProject) -> None:
    """CMake deletes the index and snippets once callbacks return; the copy is all we keep."""
    chain.write_query(InstrumentationQuery(hooks=("postCMakeBuild",), callbacks=(chain.callback,)))
    chain.configure()
    chain.build()

    assert chain.snippet_files() == []
    assert len(chain.captured()[0].snippets) > 0
