"""Usage: longpole COMMAND [OPTIONS]

Find the long pole in your CMake build. longpole records the instrumentation data CMake 4.3 and
later can write for every configure, compile, link, custom command and test step, stores each build
as a run, and shows what actually gated it.

╭─ Commands ───────────────────────────────────────────────────────────────────────────────────────╮
│ analyze      Report on a run: totals, phases, slowest items, critical path, parallelism, sizes.  │
│ capture      Store the run an instrumentation index describes; CMake runs this at each hook.     │
│ doctor       Check whether a build tree records its builds, and say what to fix if not.          │
│ enable       Switch CMake instrumentation on for a build tree, or for every build with --global. │
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

import logging
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated

from cyclopts import App, Parameter
from rich.console import Console

from longpole import __version__, context
from longpole.cli_config import CliConfig, LogLevel
from longpole.commands import analyze, capture, doctor, enable, export, runs
from longpole.env_config import load_env
from longpole.errors import UsageError

log = logging.getLogger(__name__)


app = App(
    name="longpole",
    help=(
        "Find the long pole in your CMake build. longpole records the instrumentation data "
        "CMake 4.3 and later can write for every configure, compile, link, custom command and "
        "test step, stores each build as a run, and shows what actually gated it."
    ),
    version=f"longpole {__version__}",
    # Fixed width, so the help text does not depend on the terminal it is printed to.
    console=Console(width=100),
    help_format="plaintext",
    result_action="return_int_as_exit_code_else_zero",
    default_parameter=Parameter(negative="", show_default=False, show_choices=False),
)
"""The commands. Nothing here exits the process or prints errors; :func:`main` does both."""
app.command(enable.enable)
app.command(capture.capture)
app.command(doctor.doctor)
app.command(analyze.analyze)
app.command(export.export)
app.command(runs.app)


@app.meta.default
def launcher(
    *tokens: Annotated[str, Parameter(show=False, allow_leading_hyphen=True)],
    log_level: LogLevel = "warning",
    store: Path | None = None,
    json: bool = False,
) -> int:
    """Parse the global options, publish them and run the command.

    This is the meta app in front of :data:`app`: it owns the options that
    come before the command word, publishes them through
    :mod:`longpole.context` and hands the remaining tokens to the command.
    That is how ``longpole --json analyze`` gets its flag before the command,
    the way git does it.

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
        raise UsageError("no command given; see --help")
    configure_logging(log_level)
    cli = CliConfig(log_level=log_level, store=store, json_output=json)
    with context.activate(cli, load_env()):
        return app(tokens, exit_on_error=False, print_error=False)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line and turn every outcome into an exit code.

    Parses ``argv`` and runs the chosen command. Any failure, a bad command
    line, an expected error or a bug alike, is logged as one line and becomes
    exit code 1; ``--log-level debug`` adds the stack. Ctrl-C is left to
    Python.

    Args:
        argv: Arguments without the program name. ``None`` means
            ``sys.argv[1:]``; that branch exists for the console script entry
            point, which calls ``main()`` bare. Tests pass a list.

    Returns:
        0 on success and after ``--help`` or ``--version``; the command's
        return value otherwise; 1 on any exception.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    configure_logging("warning")  # the launcher reconfigures once --log-level is known
    try:
        return app.meta(args, exit_on_error=False, print_error=False)
    except Exception as exc:
        log.error("%s", exc, exc_info=log.isEnabledFor(logging.DEBUG))
        return 1


def configure_logging(level: str) -> None:
    """Send the ``longpole`` logger hierarchy to standard error at the given level.

    Called more than once per process: by :func:`main` before parsing and by
    :func:`launcher` once ``--log-level`` is known, and by every ``main([...])``
    a test makes. The handler is rebuilt each time so it writes to whatever
    ``sys.stderr`` is right now: pytest swaps the stream per test, and a
    handler created for an earlier test would otherwise keep writing into a
    closed capture. Only the ``longpole`` logger is touched; the libraries'
    loggers keep whatever they have.

    Args:
        level: One of :data:`longpole.cli_config.LogLevel`, any case.
    """
    logger = logging.getLogger("longpole")
    logger.setLevel(level.upper())
    logger.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    logger.addHandler(handler)
