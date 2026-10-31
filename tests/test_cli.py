"""The entry point: parsing, exit codes, logging and the help text."""

from __future__ import annotations

import argparse
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from longpole import __version__, cli, context
from longpole.cli import main
from longpole.cli_config import HelpPrinted, build_parser, parse_cli
from longpole.command import Command
from longpole.commands import COMMANDS
from longpole.env_config import load_env
from longpole.errors import LongpoleError, UsageError


@dataclass(frozen=True, kw_only=True)
class EchoOptions:
    """Options of the test-only ``echo`` command.

    Attributes:
        word: What to print.
    """

    word: str


def _register_echo(parser: argparse.ArgumentParser) -> None:
    """Declare the single positional argument of ``echo``.

    Args:
        parser: The command's subparser.
    """
    parser.add_argument("word", help="what to print")


def _echo_options(namespace: argparse.Namespace) -> EchoOptions:
    """Type the parsed arguments of ``echo``.

    Args:
        namespace: argparse's result.

    Returns:
        The options object.
    """
    return EchoOptions(word=str(namespace.word))


def _run_echo() -> int:
    """Print the word through the output layer; the test reads it back with capsys.

    Returns:
        0.
    """
    from longpole import output

    output.stdout(context.options(EchoOptions).word)
    return 0


ECHO = Command(
    name="echo",
    help="print a word",
    description="Print a word.",
    register=_register_echo,
    options_from=_echo_options,
    run=_run_echo,
)


def _command(name: str, run: Callable[[], int]) -> Command:
    """A command with no arguments of its own, for tests of the entry point.

    Args:
        name: The command word.
        run: What it does.

    Returns:
        The command.
    """
    return Command(
        name=name,
        help=name,
        description=f"{name.capitalize()}.",
        register=lambda _parser: None,
        options_from=lambda _namespace: None,
        run=run,
    )


def test_module_docstring_is_the_help_text() -> None:
    """The entry module's docstring and `longpole --help` are the same text."""
    assert cli.__doc__ == build_parser(COMMANDS).format_help()


def test_help_prints_and_returns_zero(capsys: pytest.CaptureFixture[str]) -> None:
    """`--help` goes to stdout and ends with exit code 0 instead of a SystemExit."""
    assert main(["--help"]) == 0
    captured = capsys.readouterr()
    assert captured.out == cli.__doc__
    assert captured.err == ""


def test_version_prints_the_package_version(capsys: pytest.CaptureFixture[str]) -> None:
    """`--version` prints `longpole <version>` and exits 0."""
    assert main(["--version"]) == 0
    assert capsys.readouterr().out == f"longpole {__version__}\n"


def test_missing_command_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    """No command means usage on stderr, nothing on stdout, exit code 2."""
    assert main([]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("usage: longpole")
    assert "error:" in captured.err


def test_unknown_option_is_a_usage_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An option argparse does not know is reported, not swallowed."""
    monkeypatch.setitem(COMMANDS, "echo", ECHO)
    assert main(["--frobnicate", "echo", "hi"]) == 2
    assert "unrecognized arguments: --frobnicate" in capsys.readouterr().err


def test_bad_log_level_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    """`--log-level` accepts only the four known names."""
    assert main(["--log-level", "loud"]) == 2
    assert "invalid choice" in capsys.readouterr().err


def test_parse_cli_types_global_options_and_command_options() -> None:
    """Global flags land in CliConfig; the command's flags land in its own options object."""
    config = parse_cli(
        ["--log-level", "debug", "--store", "/tmp/store", "--json", "echo", "hi"],
        commands={"echo": ECHO},
    )
    assert config.log_level == "debug"
    assert config.store == Path("/tmp/store")
    assert config.json_output is True
    assert config.command == "echo"
    assert config.options == EchoOptions(word="hi")


def test_parse_cli_defaults() -> None:
    """Without global flags: warning level, no store override, tables."""
    config = parse_cli(["echo", "hi"], commands={"echo": ECHO})
    assert (config.log_level, config.store, config.json_output) == ("warning", None, False)


def test_parser_raises_instead_of_exiting() -> None:
    """The parser never calls sys.exit: help and errors surface as exceptions."""
    parser = build_parser({"echo": ECHO})
    with pytest.raises(HelpPrinted):
        parser.parse_args(["echo", "--help"])
    with pytest.raises(UsageError) as error:
        parser.parse_args(["echo"])
    assert error.value.exit_code == 2
    assert "the following arguments are required: word" in str(error.value)


def test_command_runs_with_its_options_published(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A registered command runs inside an active context and can read its options."""
    monkeypatch.setitem(COMMANDS, "echo", ECHO)
    assert main(["echo", "hello"]) == 0
    assert capsys.readouterr().out == "hello\n"


def test_context_is_cleared_after_main(monkeypatch: pytest.MonkeyPatch) -> None:
    """Once main returns, nothing is active any more, so the next call starts clean."""
    monkeypatch.setitem(COMMANDS, "echo", ECHO)
    assert main(["echo", "hello"]) == 0
    with pytest.raises(RuntimeError, match="no active configuration"):
        context.cli()
    with pytest.raises(RuntimeError, match="no active configuration"):
        context.env()


def test_known_failure_maps_to_its_exit_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A LongpoleError is logged once on stderr and becomes the error's exit code."""

    class DiskError(LongpoleError):
        exit_code = 3

    def fail() -> int:
        raise DiskError("the disk is on fire")

    monkeypatch.setitem(COMMANDS, "fail", _command("fail", fail))
    assert main(["fail"]) == 3
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "longpole: ERROR: the disk is on fire\n"


def test_unexpected_exception_is_logged_with_the_stack(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Anything else returns 70 and leaves a traceback on stderr for the bug report."""

    def explode() -> int:
        raise ZeroDivisionError("boom")

    monkeypatch.setitem(COMMANDS, "explode", _command("explode", explode))
    assert main(["explode"]) == 70
    err = capsys.readouterr().err
    assert "unexpected error" in err
    assert "ZeroDivisionError: boom" in err


def test_log_level_controls_what_reaches_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Debug records show up only when asked for; the handler follows the current stderr."""
    log = logging.getLogger("longpole.test")

    def chatty() -> int:
        log.debug("whisper")
        log.warning("shout")
        return 0

    monkeypatch.setitem(COMMANDS, "chatty", _command("chatty", chatty))
    assert main(["chatty"]) == 0
    assert capsys.readouterr().err == "longpole: WARNING: shout\n"
    assert main(["--log-level", "debug", "chatty"]) == 0
    assert capsys.readouterr().err == "longpole: DEBUG: whisper\nlongpole: WARNING: shout\n"


def test_options_rejects_another_commands_object() -> None:
    """Asking for the wrong options class is a programming error, reported as TypeError."""
    config = parse_cli(["echo", "hi"], commands={"echo": ECHO})
    with context.activate(config, load_env()):
        assert context.options(EchoOptions).word == "hi"
        with pytest.raises(TypeError, match="EchoOptions, not Path"):
            context.options(Path)


def test_activate_restores_the_previous_configuration() -> None:
    """Nested activations unwind in order, like any context manager should."""
    outer = parse_cli(["echo", "outer"], commands={"echo": ECHO})
    inner = parse_cli(["echo", "inner"], commands={"echo": ECHO})
    env = load_env()
    with context.activate(outer, env):
        with context.activate(inner, env):
            assert context.options(EchoOptions).word == "inner"
        assert context.options(EchoOptions).word == "outer"


def test_load_env_snapshots_variables(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The snapshot carries the CI run id and CMake's config dir; empty values count as unset."""
    monkeypatch.setenv("GITHUB_RUN_ID", "4242")
    monkeypatch.setenv("CMAKE_CONFIG_DIR", str(tmp_path))
    env = load_env(cwd=tmp_path)
    assert env.cwd == tmp_path
    assert env.ci_run_id == "4242"
    assert env.cmake_config_dir == tmp_path
    assert env.environ["GITHUB_RUN_ID"] == "4242"

    bare = load_env({"GITHUB_RUN_ID": ""}, cwd=tmp_path)
    assert bare.ci_run_id is None
    assert bare.cmake_config_dir is None
