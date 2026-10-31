"""Command line of ``longpole``: global options first, then one command.

The layout follows git: ``longpole [global options] <command> [its options]``.
The global options are the ones every command may need (verbosity, which run
store, JSON output); each command declares its own in its module under
:mod:`longpole.commands`.

Parsing never ends the process. argparse calls ``sys.exit`` after printing
help and on every error; here those paths raise :class:`HelpPrinted` and
:class:`~longpole.errors.UsageError` instead, so ``main`` can be called from
a test with a list of arguments and return an exit code.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

from longpole import __version__
from longpole.command import Command
from longpole.commands import COMMANDS
from longpole.errors import UsageError

PROGRAM = "longpole"

LOG_LEVELS: tuple[str, ...] = ("debug", "info", "warning", "error")
"""Accepted values of ``--log-level``, quietest last but one."""

HELP_WIDTH = 100
"""Column the help text wraps at. Fixed so the text does not depend on the terminal."""

DESCRIPTION = (
    "Find the long pole in your CMake build. longpole records the instrumentation data "
    "CMake 4.3 and later can write for every configure, compile, link, custom command and "
    "test step, stores each build as a run, and shows what actually gated it."
)


class HelpPrinted(Exception):  # noqa: N818 - not an error, so no Error suffix
    """argparse has printed ``--help`` or ``--version``; there is nothing left to do.

    argparse ends the process at that point; this exception carries the same
    signal up to ``main``, which turns it into exit code 0.
    """


@dataclass(frozen=True, kw_only=True)
class CliConfig:
    """The parsed command line.

    Attributes:
        log_level: One of :data:`LOG_LEVELS`.
        store: Run store from ``--store``, or ``None`` to resolve it by the
            usual rule.
        json_output: Whether results go out as JSON instead of tables.
        command: Name of the chosen command.
        options: The command's own options object, built by its
            ``options_from``; read it back with :func:`longpole.context.options`.
    """

    log_level: str
    store: Path | None
    json_output: bool
    command: str
    options: object


class _Formatter(argparse.HelpFormatter):
    """Help formatter pinned to :data:`HELP_WIDTH`."""

    def __init__(self, prog: str) -> None:
        """Set up the formatter.

        Args:
            prog: Program name argparse passes in.
        """
        super().__init__(prog, width=HELP_WIDTH, max_help_position=28)


class _Parser(argparse.ArgumentParser):
    """An argument parser that reports instead of exiting the process."""

    def error(self, message: str) -> NoReturn:
        """Report a parse error.

        Args:
            message: argparse's complaint.

        Raises:
            UsageError: Always; the message carries the usage line and the
                complaint, formatted like argparse would print them.
        """
        raise UsageError(f"{self.format_usage()}{self.prog}: error: {message}")

    def exit(self, status: int = 0, message: str | None = None) -> NoReturn:
        """Finish early after ``--help`` or ``--version``.

        Args:
            status: Exit status argparse wanted; 0 after printing help.
            message: Text argparse wanted on standard error, if any.

        Raises:
            HelpPrinted: ``status`` is 0 and there is no message.
            UsageError: Anything else.
        """
        if status == 0 and not message:
            raise HelpPrinted
        raise UsageError(message or f"{self.prog}: exit {status}")


def build_parser(commands: Mapping[str, Command]) -> argparse.ArgumentParser:
    """Build the parser for the global options and the given commands.

    Args:
        commands: Commands to offer, in the order ``--help`` should list them.

    Returns:
        A parser whose ``parse_args`` raises :class:`HelpPrinted` or
        :class:`~longpole.errors.UsageError` instead of exiting.
    """
    parser = _Parser(
        prog=PROGRAM, description=DESCRIPTION, formatter_class=_Formatter, allow_abbrev=False
    )
    parser.add_argument("--version", action="version", version=f"{PROGRAM} {__version__}")
    parser.add_argument(
        "--log-level",
        choices=LOG_LEVELS,
        default="warning",
        metavar="LEVEL",
        help="how much to say on stderr: debug, info, warning (default) or error",
    )
    parser.add_argument(
        "--store",
        type=Path,
        metavar="PATH",
        help="run store to use instead of the one resolved from the build tree and environment",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="print results as JSON instead of tables",
    )
    subparsers = parser.add_subparsers(
        title="commands", dest="command", metavar="<command>", required=True
    )
    for command in commands.values():
        subparser = subparsers.add_parser(
            command.name,
            help=command.help,
            description=command.description,
            formatter_class=_Formatter,
        )
        command.register(subparser)
    return parser


def parse_cli(args: Sequence[str], commands: Mapping[str, Command] = COMMANDS) -> CliConfig:
    """Parse a command line without the program name.

    Args:
        args: Arguments as ``sys.argv[1:]`` would hold them.
        commands: Commands to recognise; the registry by default.

    Returns:
        The parsed configuration, with the chosen command's options already
        converted to that command's options object.

    Raises:
        HelpPrinted: ``--help`` or ``--version`` was handled; the text is
            already on standard output.
        UsageError: The arguments do not parse; the message is ready to print.
    """
    namespace = build_parser(commands).parse_args(list(args))
    command = commands[str(namespace.command)]
    return CliConfig(
        log_level=str(namespace.log_level),
        store=namespace.store,
        json_output=bool(namespace.json_output),
        command=command.name,
        options=command.options_from(namespace),
    )
