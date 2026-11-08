"""The entry point: parsing, exit codes, logging and the help text."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator

import pytest

from longpole import __version__, cli, output
from longpole.cli import main
from longpole.errors import LongpoleError


def echo(word: str, /) -> int:
    """Print a word.

    Args:
        word: What to print.

    Returns:
        0.
    """
    output.stdout(word)
    return 0


Register = Callable[[Callable[..., int]], None]


@pytest.fixture
def register() -> Iterator[Register]:
    """Let a test add commands to the app; they are removed again on teardown.

    Yields:
        A function that registers one command; its name is the command word.
    """
    added: list[str] = []

    def add(command: Callable[..., int]) -> None:
        cli.app.command(command)
        added.append(command.__name__)

    yield add
    for name in added:
        del cli.app[name]


def _lines(text: str) -> list[str]:
    """Split text into lines without trailing whitespace, which rich pads for alignment.

    Args:
        text: The text.

    Returns:
        The lines.
    """
    return [line.rstrip() for line in text.splitlines()]


def test_help_prints_and_returns_zero(capsys: pytest.CaptureFixture[str]) -> None:
    """`--help` goes to stdout, ends with exit code 0, and is the module docstring of cli.py."""
    assert main(["--help"]) == 0
    captured = capsys.readouterr()
    assert _lines(captured.out) == _lines(cli.__doc__ or "")
    assert captured.err == ""


def test_command_help_lists_its_own_arguments(capsys: pytest.CaptureFixture[str]) -> None:
    """`longpole <command> --help` describes that command's arguments and returns 0."""
    assert main(["export", "--help"]) == 0
    out = capsys.readouterr().out
    assert "Usage: longpole export" in out
    assert "--perfetto PATH" in out
    assert "--markdown PATH" in out


def test_version_prints_the_package_version(capsys: pytest.CaptureFixture[str]) -> None:
    """`--version` prints `longpole <version>` and exits 0."""
    assert main(["--version"]) == 0
    assert capsys.readouterr().out == f"longpole {__version__}\n"


def test_bare_program_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    """No command means the help on stdout and exit code 0, like `--help`."""
    assert main([]) == 0
    captured = capsys.readouterr()
    assert _lines(captured.out) == _lines(cli.__doc__ or "")
    assert captured.err == ""


def test_unknown_command_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    """A word that is not a command is reported with the list of commands."""
    assert main(["frobnicate"]) == 1
    err = capsys.readouterr().err
    assert err.startswith('ERROR: Unknown command "frobnicate"')
    assert "enable, disable, capture, doctor, analyze, export, runs" in err


def test_unknown_option_is_a_usage_error(
    register: Register, capsys: pytest.CaptureFixture[str]
) -> None:
    """An option the command does not know is reported, not swallowed."""
    register(echo)
    assert main(["echo", "hi", "--frobnicate"]) == 1
    assert "--frobnicate" in capsys.readouterr().err


def test_bad_log_level_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    """`--log-level` accepts only the four known names."""
    assert main(["--log-level", "loud", "analyze"]) == 1
    err = capsys.readouterr().err
    assert "--log-level" in err
    assert "loud" in err


def test_command_runs_with_its_arguments(
    register: Register, capsys: pytest.CaptureFixture[str]
) -> None:
    """A registered command gets its arguments typed from the command line."""
    register(echo)
    assert main(["echo", "hello"]) == 0
    assert capsys.readouterr().out == "hello\n"


def test_missing_argument_is_a_usage_error(
    register: Register, capsys: pytest.CaptureFixture[str]
) -> None:
    """A required positional left out is a usage error naming it."""
    register(echo)
    assert main(["echo"]) == 1
    assert "WORD" in capsys.readouterr().err


def test_failure_is_logged_once_and_exits_1(
    register: Register, capsys: pytest.CaptureFixture[str]
) -> None:
    """An expected error is one line on stderr, nothing on stdout, exit code 1."""

    def fail() -> int:
        raise LongpoleError("the disk is on fire")

    register(fail)
    assert main(["fail"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "ERROR: the disk is on fire\n"


def test_unexpected_exception_shows_the_stack_only_at_debug(
    register: Register, capsys: pytest.CaptureFixture[str]
) -> None:
    """A bug is the same one line by default; --log-level debug adds the traceback."""

    def explode() -> int:
        raise ZeroDivisionError("boom")

    register(explode)
    assert main(["explode"]) == 1
    assert capsys.readouterr().err == "ERROR: boom\n"
    assert main(["--log-level", "debug", "explode"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("ERROR: boom\n")
    assert "ZeroDivisionError: boom" in err


def test_log_level_controls_what_reaches_stderr(
    register: Register, capsys: pytest.CaptureFixture[str]
) -> None:
    """Debug records show up only when asked for; the handler follows the current stderr."""
    log = logging.getLogger("longpole.test")

    def chatty() -> int:
        log.debug("whisper")
        log.warning("shout")
        return 0

    register(chatty)
    assert main(["chatty"]) == 0
    assert capsys.readouterr().err == "WARNING: shout\n"
    assert main(["--log-level", "debug", "chatty"]) == 0
    assert capsys.readouterr().err == "DEBUG: whisper\nWARNING: shout\n"
