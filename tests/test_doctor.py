"""`longpole doctor` tells a tree that records from one that silently will not."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from longpole.cli import main
from longpole.commands import doctor
from tests.conftest import make_project
from tests.support.cmake import InstrumentationQuery


def _findings(output: str) -> dict[str, dict[str, object]]:
    """Index a --json report by check name.

    Args:
        output: The command's standard output.

    Returns:
        Finding objects by their ``check``.
    """
    report = json.loads(output)
    return {str(finding["check"]): finding for finding in report["findings"]}


@pytest.mark.parametrize(
    ("generator", "supported"),
    [
        ("Ninja", True),
        ("Ninja Multi-Config", True),
        ("Unix Makefiles", True),
        ("MinGW Makefiles", True),
        ("FASTBuild", True),
        ("Xcode", False),
        ("Visual Studio 17 2022", False),
    ],
)
def test_generator_support_follows_the_launcher(generator: str, supported: bool) -> None:
    """Only generators that can wrap commands in the launcher record anything."""
    assert doctor.generator_supported(generator) is supported


def test_actions_are_deduplicated_in_order() -> None:
    """Two findings asking for the same cmake run produce one to-do item."""
    report = doctor.Report(
        build_dir=Path("/b"),
        findings=(
            doctor.Finding(check="a", status=doctor.OK, detail="fine"),
            doctor.Finding(check="b", status=doctor.WARNING, detail="x", action="run cmake again"),
            doctor.Finding(check="c", status=doctor.PROBLEM, detail="y", action="enable it"),
            doctor.Finding(check="d", status=doctor.WARNING, detail="z", action="run cmake again"),
        ),
    )
    assert report.actions == ("run cmake again", "enable it")
    text = doctor.describe(report)
    assert "to do:" in text
    assert "  1. run cmake again" in text
    assert "  2. enable it" in text


@pytest.mark.cmake
def test_unconfigured_tree_without_query_needs_enable(
    tmp_path: Path, cmake_binary: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A fresh directory: the only problem is the missing query, and the fix is named."""
    build = tmp_path / "build"
    assert (
        main(["doctor", str(build), "--cmake", str(cmake_binary), "--output-format", "json"]) == 1
    )
    report = json.loads(capsys.readouterr().out)
    findings = {str(f["check"]): f for f in report["findings"]}
    assert findings["cmake"]["status"] == doctor.OK
    assert findings["build tree"]["status"] == doctor.WARNING
    assert findings["query"]["status"] == doctor.PROBLEM
    assert report["actions"] == [f"run `longpole enable {build}`"]


@pytest.mark.cmake
def test_enabled_but_unconfigured_tree_is_fine(
    tmp_path: Path, cmake_on_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """After enable an unconfigured tree needs nothing; the query applies at the first configure."""
    build = tmp_path / "build"
    assert main(["enable", str(build)]) == 0
    capsys.readouterr()
    assert main(["doctor", str(build), "--cmake", str(cmake_on_path)]) == 0
    out = capsys.readouterr().out
    assert "all good" in out
    assert "not configured yet" in out


@pytest.mark.cmake
def test_broken_cmake_is_a_finding_not_a_crash(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A cmake that cannot run shows up in the table with an action, exit code 1."""
    assert (
        main(
            [
                "doctor",
                str(tmp_path),
                "--cmake",
                str(tmp_path / "no-cmake"),
                "--output-format",
                "json",
            ]
        )
        == 1
    )
    findings = _findings(capsys.readouterr().out)
    assert findings["cmake"]["status"] == doctor.PROBLEM
    assert "cannot run" in str(findings["cmake"]["detail"])
    assert findings["cmake"]["action"] == "point --cmake at a working executable"


@pytest.mark.cmake
@pytest.mark.slow
def test_configured_tree_walks_from_problem_to_all_good(
    tmp_path: Path, cmake_binary: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """No query, then a late query, then a reconfigure: each stage gets the right verdict.

    The last stage also exercises the real callback: configuring after
    enable fires postGenerate, and the run lands in the tree's own store.
    """
    project = make_project("chain", tmp_path, cmake_binary)
    project.configure()
    build = str(project.build_dir)

    assert main(["doctor", build, "--output-format", "json"]) == 1
    findings = _findings(capsys.readouterr().out)
    assert findings["build tree"]["detail"] == "configured, Ninja"
    assert findings["generator"]["status"] == doctor.OK
    assert findings["query"]["status"] == doctor.PROBLEM
    assert "build system" not in findings

    assert main(["enable", build]) == 0
    capsys.readouterr()
    assert main(["doctor", build, "--output-format", "json"]) == 1
    report = json.loads(capsys.readouterr().out)
    findings = {str(f["check"]): f for f in report["findings"]}
    assert findings["query"]["status"] == doctor.OK
    assert findings["build system"]["status"] == doctor.WARNING
    assert findings["target graph"]["detail"] == "File API query present, reply pending"
    assert report["actions"] == [f"run cmake again in {build}"]

    project.configure()
    assert main(["doctor", build, "--output-format", "json"]) == 0
    report = json.loads(capsys.readouterr().out)
    findings = {str(f["check"]): f for f in report["findings"]}
    assert report["needs_action"] is False
    assert findings["build system"]["status"] == doctor.OK
    assert findings["target graph"]["status"] == doctor.OK
    assert findings["store"]["detail"].endswith("1 run from this tree")
    assert findings["data"]["detail"] == "no unindexed data"


@pytest.mark.cmake
def test_query_without_callback_is_flagged(
    tmp_path: Path, cmake_binary: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Hooks without a callback make CMake index and delete the data unread; doctor says so."""
    project = make_project("chain", tmp_path, cmake_binary)
    project.write_query(InstrumentationQuery(hooks=("postCMakeBuild",)))
    assert (
        main(
            [
                "doctor",
                str(project.build_dir),
                "--cmake",
                str(cmake_binary),
                "--output-format",
                "json",
            ]
        )
        == 1
    )
    findings = _findings(capsys.readouterr().out)
    assert findings["query"]["status"] == doctor.PROBLEM
    assert "none names a callback" in str(findings["query"]["detail"])
