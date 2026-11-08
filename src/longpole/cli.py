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
╰──────────────────────────────────────────────────────────────────────────────────────────────────╯
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Sequence
from typing import Annotated, Literal

from cyclopts import App, Parameter
from rich.console import Console

from longpole import __version__
from longpole.commands import analyze, capture, doctor, enable, export, runs
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
    log_level: Literal["debug", "info", "warning", "error"] = "warning",
) -> int:
    """Apply the option that comes before the command word, then run the command.

    This is the meta app in front of :data:`app`. ``--log-level`` is the one
    option every command shares and the one thing that has to happen before
    any command runs, so it lives here; everything else a command needs is
    in its own signature.

    Args:
        *tokens: The command and its arguments.
        log_level: How much to say on stderr: debug, info, warning (the default) or error.

    Returns:
        The command's exit code.

    Raises:
        UsageError: No command was given.
    """
    if not tokens:
        raise UsageError("no command given; see --help")
    logging.getLogger("longpole").setLevel(log_level.upper())
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
    init_logging()
    try:
        return app.meta(args, exit_on_error=False, print_error=False)
    except Exception as exc:
        log.error("%s", exc, exc_info=log.isEnabledFor(logging.DEBUG))
        return 1


def init_logging() -> None:
    """Put one stderr handler on the ``longpole`` logger, at level WARNING.

    Our own handler, because the one Python falls back to prints bare text
    and nothing below WARNING. Called on every :func:`main` rather than at
    import: pytest swaps ``sys.stderr`` per test, and a handler keeps the
    stream it was created with. :func:`launcher` applies ``--log-level``
    afterwards.
    """
    logger = logging.getLogger("longpole")
    logger.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
