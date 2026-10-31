"""usage: longpole [-h] [--version] [--log-level LEVEL] [--store PATH] [--json] <command> ...

Find the long pole in your CMake build. longpole records the instrumentation data CMake 4.3 and
later can write for every configure, compile, link, custom command and test step, stores each build
as a run, and shows what actually gated it.

options:
  -h, --help         show this help message and exit
  --version          show program's version number and exit
  --log-level LEVEL  how much to say on stderr: debug, info, warning (default) or error
  --store PATH       run store to use instead of the one resolved from the build tree and
                     environment
  --json             print results as JSON instead of tables

commands:
  <command>
    enable           switch CMake instrumentation on for a build tree, or for every build with
                     --global
    capture          store the run an instrumentation index describes; CMake runs this at each hook
    doctor           check whether a build tree records its builds, and say what to fix if not
    analyze          report on a run: totals, phases, slowest items, critical path, parallelism,
                     sizes
    export           write a run as a Perfetto trace or a Markdown snapshot
    runs             list stored runs; 'runs show' prints one, 'runs prune' deletes old ones
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Sequence

from longpole import context, output
from longpole.cli_config import HelpPrinted, parse_cli
from longpole.commands import COMMANDS
from longpole.env_config import load_env
from longpole.errors import LongpoleError, UsageError

log = logging.getLogger(__name__)

EXIT_UNEXPECTED = 70
"""Exit code for an exception nothing expected; ``EX_SOFTWARE`` in ``sysexits.h``."""

EXIT_INTERRUPTED = 130
"""Exit code after Ctrl-C, the shell convention of 128 plus SIGINT."""

LOGGER_NAME = "longpole"
HANDLER_NAME = "longpole-stderr"


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line and turn every outcome into an exit code.

    Parses ``argv``, configures logging, snapshots the environment, publishes
    both configurations through :mod:`longpole.context` and runs the chosen
    command. The process never ends with a traceback: expected failures are
    logged and mapped to their exit codes, unexpected ones are logged with
    the stack and return :data:`EXIT_UNEXPECTED`.

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
        cli = parse_cli(args)
        configure_logging(cli.log_level)
        env = load_env()
        with context.activate(cli, env):
            return COMMANDS[cli.command].run()
    except HelpPrinted:
        return 0
    except UsageError as exc:
        output.stderr(str(exc))
        return exc.exit_code
    except LongpoleError as exc:
        log.error("%s", exc)
        return exc.exit_code
    except KeyboardInterrupt:
        return EXIT_INTERRUPTED
    except Exception:
        log.exception("unexpected error")
        return EXIT_UNEXPECTED


def configure_logging(level: str) -> None:
    """Send the ``longpole`` logger hierarchy to standard error at the given level.

    Called once per :func:`main`. The handler is rebuilt each time so it
    writes to whatever ``sys.stderr`` is right now: pytest swaps the stream
    per test, and a handler created for an earlier test would otherwise keep
    writing into a closed capture. Other loggers are left alone.

    Args:
        level: One of :data:`longpole.cli_config.LOG_LEVELS`, any case.
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
