"""What CMake's instrumentation actually does where the manual is silent.

Each test pins one observed behaviour across every installed CMake version.
The findings, with dates and versions, are written up in
docs/cmake-behaviors.md; when a test here starts failing on a new release,
that document needs a new row.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.conftest import cmake_version_of, make_project
from tests.support.cmake import InstrumentationQuery

pytestmark = [pytest.mark.cmake, pytest.mark.slow]

CHAIN_BUILD_STEPS = {"custom": 3, "compile": 3, "link": 3}


def test_late_query_fires_the_hook_but_records_no_build_steps(
    tmp_path: Path, each_cmake: Path
) -> None:
    """A query written after generate is honoured by `cmake --build` only halfway.

    The hook fires and the callback runs, but the index holds a single
    cmakeBuild snippet: the launcher that records compile, link and custom
    steps is baked into the build system at generate time, and CMake does
    not regenerate on its own when a query appears. A reconfigure fixes it.
    """
    project = make_project("chain", tmp_path, each_cmake)
    project.configure()
    project.write_query(
        InstrumentationQuery(hooks=("postCMakeBuild",), callbacks=(project.callback,))
    )

    result = project.build()
    assert "Re-running CMake" not in result.stdout
    assert project.captured()[-1].roles() == {"cmakeBuild": 1}

    project.configure()
    project.build()
    roles = project.captured()[-1].roles()
    assert {role: roles[role] for role in CHAIN_BUILD_STEPS} == CHAIN_BUILD_STEPS


def test_hooks_without_callbacks_throw_the_data_away(tmp_path: Path, each_cmake: Path) -> None:
    """With a hook but no callback, CMake indexes, nobody reads, and the files are gone."""
    project = make_project("chain", tmp_path, each_cmake)
    project.write_query(InstrumentationQuery(hooks=("postCMakeBuild",)))
    project.configure()
    project.build()

    assert project.snippet_files() == []
    index_dir = project.data_dir / "index"
    assert not index_dir.exists() or list(index_dir.iterdir()) == []


def test_without_hooks_snippets_accumulate_across_builds(tmp_path: Path, each_cmake: Path) -> None:
    """An options-only query collects data that nothing ever indexes or deletes.

    The first build of the chain leaves twelve snippets (configure, generate,
    three custom, three compile, three link, cmakeBuild); a second, no-op
    build adds one more cmakeBuild.
    """
    project = make_project("chain", tmp_path, each_cmake)
    project.write_query(InstrumentationQuery(options=("dynamicSystemInformation",)))
    project.configure()
    project.build()
    assert len(project.snippet_files()) == 12
    project.build()
    assert len(project.snippet_files()) == 13


def test_post_generate_indexes_configure_and_generate(tmp_path: Path, each_cmake: Path) -> None:
    """The postGenerate hook fires at the end of configure with exactly its two steps."""
    project = make_project("chain", tmp_path, each_cmake)
    project.write_query(
        InstrumentationQuery(hooks=("postGenerate",), callbacks=(project.callback,))
    )
    project.configure()

    captured = project.captured()
    assert [capture.hook for capture in captured] == ["postGenerate"]
    assert captured[0].roles() == {"configure": 1, "generate": 1}


def test_cmake_install_records_one_cmake_install_snippet(tmp_path: Path, each_cmake: Path) -> None:
    """`cmake --install` fires postCMakeInstall with a cmakeInstall snippet and nothing else.

    The per-script `install` role the manual lists did not appear for this
    project's single install rule.
    """
    project = make_project("chain", tmp_path, each_cmake)
    project.write_query(
        InstrumentationQuery(
            hooks=("postCMakeBuild", "postCMakeInstall"), callbacks=(project.callback,)
        )
    )
    project.configure()
    project.build()
    project.install(tmp_path / "prefix")

    captured = project.captured()
    assert [capture.hook for capture in captured] == ["postCMakeBuild", "postCMakeInstall"]
    assert captured[1].roles() == {"cmakeInstall": 1}


def test_workflow_fires_each_step_hook_then_an_empty_workflow_index(
    tmp_path: Path, each_cmake: Path
) -> None:
    """Inside `cmake --workflow` the per-step hooks fire as usual.

    postGenerate, postCMakeBuild and postCTest each get their own index, so
    by the time postCMakeWorkflow fires there is nothing left to list and
    its index is empty. A callback must tolerate an empty index.
    """
    project = make_project("workflow", tmp_path, each_cmake)
    project.write_query(
        InstrumentationQuery(
            hooks=("postGenerate", "postCMakeBuild", "postCTest", "postCMakeWorkflow"),
            callbacks=(project.callback,),
        )
    )
    project.workflow("all")

    captured = project.captured()
    assert [capture.hook for capture in captured] == [
        "postGenerate",
        "postCMakeBuild",
        "postCTest",
        "postCMakeWorkflow",
    ]
    assert captured[0].roles() == {"configure": 1, "generate": 1}
    assert captured[1].roles() == {"cmakeBuild": 1, "compile": 1, "link": 1}
    assert captured[2].roles() == {"ctest": 1, "test": 1}
    assert captured[3].snippets == ()


def test_ctest_records_tests_without_the_dashboard_variable(
    tmp_path: Path, each_cmake: Path
) -> None:
    """A plain query is enough for ctest; CTEST_USE_INSTRUMENTATION is a dashboard affair."""
    assert "CTEST_USE_INSTRUMENTATION" not in os.environ
    project = make_project("with_tests", tmp_path, each_cmake)
    project.write_query(InstrumentationQuery(hooks=("postCTest",), callbacks=(project.callback,)))
    project.configure()
    project.run_ctest()

    roles = project.captured()[0].roles()
    assert roles["ctest"] == 1
    assert roles["test"] == 4


def test_data_version_follows_the_cmake_minor_line(tmp_path: Path, each_cmake: Path) -> None:
    """Snippets and index carry data version 1.0 on 4.3, 1.1 on 4.4 and 1.2 on 4.5."""
    _, minor, _ = cmake_version_of(each_cmake)
    expected_minor = {3: 0, 4: 1, 5: 2}.get(minor)
    if expected_minor is None:
        pytest.skip(f"no recorded expectation for CMake 4.{minor}")

    project = make_project("chain", tmp_path, each_cmake)
    project.write_query(
        InstrumentationQuery(hooks=("postGenerate",), callbacks=(project.callback,))
    )
    project.configure()

    capture = project.captured()[0]
    assert capture.index["version"] == {"major": 1, "minor": expected_minor}
    assert capture.snippets[0]["version"] == {"major": 1, "minor": expected_minor}
