"""Usage: longpole COMMAND [OPTIONS]

Find the long pole in your CMake build. longpole records the instrumentation data CMake 4.3 and
later can write for every configure, compile, link, custom command and test step, stores each build
as a run, and shows what actually gated it.

╭─ Commands ───────────────────────────────────────────────────────────────────────────────────────╮
│ enable       Switch CMake instrumentation on for a build tree, or for every build with --global. │
│ capture      Store the run an instrumentation index describes; CMake runs this at each hook.     │
│ doctor       Check whether a build tree records its builds, and say what to fix if not.          │
│ analyze      Report on a run: totals, phases, slowest items, critical path, parallelism, sizes.  │
│ export       Write a run as a Perfetto trace or a Markdown snapshot.                             │
│ runs         List stored runs; 'runs show' prints one, 'runs prune' deletes old ones.            │
│ --help (-h)  Display this message and exit.                                                      │
│ --version    Display application version.                                                        │
╰──────────────────────────────────────────────────────────────────────────────────────────────────╯
╭─ Parameters ─────────────────────────────────────────────────────────────────────────────────────╮
│ --log-level CHOICE  How much to say on stderr: debug, info, warning (the default) or error.      │
│ --store PATH        Run store to use instead of the one resolved from the tree and environment.  │
│ --json              Print results as JSON instead of tables.                                     │
╰──────────────────────────────────────────────────────────────────────────────────────────────────╯
"""

from __future__ import annotations

import io
import logging
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Annotated

from cyclopts import App, CycloptsError, Parameter
from rich.console import Console

from longpole import __version__, context, output
from longpole.cli_config import CliConfig, LogLevel
from longpole.commands import COMMANDS
from longpole.env_config import load_env
from longpole.errors import LongpoleError, UsageError

log = logging.getLogger(__name__)

PROGRAM = "longpole"

DESCRIPTION = (
    "Find the long pole in your CMake build. longpole records the instrumentation data "
    "CMake 4.3 and later can write for every configure, compile, link, custom command and "
    "test step, stores each build as a run, and shows what actually gated it."
)

HELP_WIDTH = 100
"""Column the help text wraps at. Fixed so the text does not depend on the terminal."""

EXIT_UNEXPECTED = 70
"""Exit code for an exception nothing expected; ``EX_SOFTWARE`` in ``sysexits.h``."""

EXIT_INTERRUPTED = 130
"""Exit code after Ctrl-C, the shell convention of 128 plus SIGINT."""

LOGGER_NAME = "longpole"
HANDLER_NAME = "longpole-stderr"


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line and turn every outcome into an exit code.

    Builds the application from the command registry, parses ``argv`` and
    runs the chosen command. The process never ends with a traceback:
    expected failures are logged and mapped to their exit codes, unexpected
    ones are logged with the stack and return :data:`EXIT_UNEXPECTED`.

    Args:
        argv: Arguments without the program name. ``None`` means
            ``sys.argv[1:]``; that branch exists for the console script entry
            point, which calls ``main()`` bare. Tests pass a list.

    Returns:
        0 on success and after ``--help`` or ``--version``; 2 on a usage
        error; the command's own code on a known failure; 70 on an unexpected
        exception; 130 after an interrupt.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        result = build_app(COMMANDS).meta(args, exit_on_error=False, print_error=False)
        return 0 if result is None else int(result)
    except CycloptsError as exc:
        output.stderr(f"{PROGRAM}: error: {exc}")
        return UsageError.exit_code
    except UsageError as exc:
        output.stderr(f"{PROGRAM}: error: {exc}")
        return exc.exit_code
    except LongpoleError as exc:
        log.error("%s", exc)
        return exc.exit_code
    except KeyboardInterrupt:
        return EXIT_INTERRUPTED
    except Exception:
        log.exception("unexpected error")
        return EXIT_UNEXPECTED


def build_app(commands: Sequence[Callable[..., int] | App]) -> App:
    """Assemble the application: the global options in front of the given commands.

    The commands form the inner app. The global options belong to its meta
    app, whose only job is to parse them, publish the configuration through
    :mod:`longpole.context` and hand the remaining tokens to the inner app.
    That is how ``longpole --json analyze`` gets its flag before the command
    word, the way git does it.

    Args:
        commands: Command functions or sub-apps, in the order ``--help``
            should list them.

    Returns:
        The application; call its ``meta`` with the arguments.
    """
    app = App(
        name=PROGRAM,
        help=DESCRIPTION,
        version=f"{PROGRAM} {__version__}",
        console=Console(width=HELP_WIDTH),
        help_format="plaintext",
        result_action="return_value",
        default_parameter=Parameter(negative="", show_default=False, show_choices=False),
    )
    for position, command in enumerate(commands):
        if isinstance(command, App):
            command.sort_key = position
            app.command(command)
        else:
            app.command(command, sort_key=position)

    @app.meta.default
    def launcher(
        *tokens: Annotated[str, Parameter(show=False, allow_leading_hyphen=True)],
        log_level: LogLevel = "warning",
        store: Path | None = None,
        json: bool = False,
    ) -> int:
        """Parse the global options, publish them and run the command.

        Args:
            *tokens: The command and its arguments.
            log_level: How much to say on stderr: debug, info, warning (the default) or error.
            store: Run store to use instead of the one resolved from the tree and environment.
            json: Print results as JSON instead of tables.

        Returns:
            The command's exit code.

        Raises:
            UsageError: No command was given.
        """
        if not tokens:
            raise UsageError(f"no command given; see {PROGRAM} --help")
        configure_logging(log_level)
        cli = CliConfig(log_level=log_level, store=store, json_output=json)
        with context.activate(cli, load_env()):
            result = app(tokens, exit_on_error=False, print_error=False)
        return 0 if result is None else int(result)

    return app


def help_text(commands: Sequence[Callable[..., int] | App] = COMMANDS) -> str:
    """Render ``longpole --help`` as a string.

    The module docstring of this file is kept equal to it by
    ``scripts/sync-cli-help.py`` and a test.

    Args:
        commands: Commands to list; the registry by default.

    Returns:
        The help text, wrapped at :data:`HELP_WIDTH`, without the trailing
        spaces rich pads lines with.
    """
    buffer = io.StringIO()
    build_app(commands).meta(["--help"], console=Console(file=buffer, width=HELP_WIDTH))
    padded = buffer.getvalue().splitlines()
    return "".join(line.rstrip() + "\n" for line in padded)


def configure_logging(level: str) -> None:
    """Send the ``longpole`` logger hierarchy to standard error at the given level.

    Called once per :func:`main`. The handler is rebuilt each time so it
    writes to whatever ``sys.stderr`` is right now: pytest swaps the stream
    per test, and a handler created for an earlier test would otherwise keep
    writing into a closed capture. Other loggers are left alone.

    Args:
        level: One of :data:`longpole.cli_config.LogLevel`, any case.
    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level.upper())
    for handler in list(logger.handlers):
        if handler.get_name() == HANDLER_NAME:
            logger.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    handler.set_name(HANDLER_NAME)
    handler.setFormatter(logging.Formatter("longpole: %(levelname)s: %(message)s"))
    logger.addHandler(handler)
