"""``longpole export``: write a run out in a format another tool reads.

The report lives in the store as data; this command renders it. Each
format is a flag with a destination path, so one invocation can write
several files. A destination of ``-`` sends that format to standard output
instead, which only makes sense for one format at a time.
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from pathlib import Path

from longpole import context, output
from longpole.analyzers.report import build_report
from longpole.cmake import tooling
from longpole.command import Command
from longpole.commands.analyze import load_run
from longpole.errors import UsageError
from longpole.exporters import perfetto
from longpole.store import RunStore, resolve_store

log = logging.getLogger(__name__)

STDOUT = "-"


@dataclass(frozen=True, kw_only=True)
class ExportOptions:
    """Arguments of ``longpole export``.

    Attributes:
        run: Run reference, or ``None`` for the default choice.
        perfetto: Destination of the Perfetto trace, or ``None`` to skip it.
    """

    run: str | None
    perfetto: str | None


def register(parser: argparse.ArgumentParser) -> None:
    """Declare the arguments of ``export``.

    Args:
        parser: The command's subparser.
    """
    parser.add_argument(
        "run",
        nargs="?",
        metavar="RUN",
        help="run id or prefix, latest, latest~N, sha:<prefix>, label:<name>, or a dump path; "
        "default: the newest run of the build tree you are in, else the newest in the store",
    )
    parser.add_argument(
        "--perfetto",
        metavar="PATH",
        help="write a Chrome JSON trace for ui.perfetto.dev to PATH ('-' for standard output)",
    )


def options_from(namespace: argparse.Namespace) -> ExportOptions:
    """Type the parsed arguments.

    Args:
        namespace: argparse's result for this command.

    Returns:
        The options.
    """
    return ExportOptions(run=namespace.run, perfetto=namespace.perfetto)


def run() -> int:
    """Load the run and write every requested format.

    Returns:
        0 on success.

    Raises:
        UsageError: No format was requested.
        UnknownRunError: The reference names no run, or there is no run to default to.
        DumpFormatError: The stored run is malformed.
        OSError: A destination cannot be written.
    """
    options = context.options(ExportOptions)
    if options.perfetto is None:
        raise UsageError("export: choose at least one format, e.g. --perfetto trace.json")
    cli = context.cli()
    env = context.env()
    tree = tooling.find_build_tree(env.cwd)
    store = RunStore(resolve_store(cli.store, build_dir=tree, environ=env.environ))
    loaded = load_run(store, options.run, tree)
    report = build_report(loaded)
    _write(options.perfetto, perfetto.format_trace(loaded, report), "Perfetto trace")
    return 0


def _write(destination: str, text: str, what: str) -> None:
    """Put rendered text where the user asked.

    Args:
        destination: A path, or ``-`` for standard output.
        text: The rendered document.
        what: Name of the format, for the log line.
    """
    if destination == STDOUT:
        output.stdout(text)
        return
    Path(destination).write_text(text, encoding="utf-8")
    log.info("wrote %s to %s", what, destination)


COMMAND = Command(
    name="export",
    help="write a run as a Perfetto trace",
    description=(
        "Render one stored run for another tool. --perfetto writes a Chrome JSON trace that "
        "ui.perfetto.dev opens directly. Defaults to the newest run of the build tree you are "
        "in, or the newest run in the store."
    ),
    register=register,
    options_from=options_from,
    run=run,
)
