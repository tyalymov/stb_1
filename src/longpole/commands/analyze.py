"""``longpole analyze``: the summary of one stored run.

Without a run reference the command picks the newest run of the build tree
the working directory is in, so ``cd build && longpole analyze`` shows the
build that just finished; outside any tree it shows the newest run in the
store. A reference selects any other run: an id or prefix, ``latest``,
``latest~N``, ``sha:<prefix>``, ``label:<name>`` or the path of a dump.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from longpole import context, output
from longpole.analyzers.summary import summarize
from longpole.cmake import tooling
from longpole.command import Command
from longpole.errors import UnknownRunError
from longpole.exporters import terminal
from longpole.model import Run
from longpole.store import RunStore, resolve_store

DEFAULT_TOP = 10


@dataclass(frozen=True, kw_only=True)
class AnalyzeOptions:
    """Arguments of ``longpole analyze``.

    Attributes:
        run: Run reference, or ``None`` for the default choice.
        top: Rows per slowest-items table.
    """

    run: str | None
    top: int


def register(parser: argparse.ArgumentParser) -> None:
    """Declare the arguments of ``analyze``.

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
        "--top",
        type=int,
        default=DEFAULT_TOP,
        metavar="N",
        help=f"rows per table (default {DEFAULT_TOP})",
    )


def options_from(namespace: argparse.Namespace) -> AnalyzeOptions:
    """Type the parsed arguments.

    Args:
        namespace: argparse's result for this command.

    Returns:
        The options.
    """
    return AnalyzeOptions(run=namespace.run, top=int(namespace.top))


def run() -> int:
    """Load the run, summarise it and print the summary.

    Returns:
        0 on success.

    Raises:
        UnknownRunError: The reference names no run, or there is no run to default to.
        DumpFormatError: The stored run is malformed.
    """
    options = context.options(AnalyzeOptions)
    cli = context.cli()
    env = context.env()
    tree = tooling.find_build_tree(env.cwd)
    store = RunStore(resolve_store(cli.store, build_dir=tree, environ=env.environ))
    summary = summarize(load_run(store, options.run, tree), top=options.top)
    if cli.json_output:
        output.stdout(json.dumps(asdict(summary), indent=2))
    else:
        output.stdout(terminal.format_summary(summary))
    return 0


def load_run(store: RunStore, ref: str | None, tree: Path | None) -> Run:
    """Pick the run to analyse.

    Args:
        store: The store to read from.
        ref: An explicit reference, or ``None``.
        tree: The build tree the working directory is in, or ``None``.

    Returns:
        The run: the referenced one; else the newest of the tree; else the
        newest in the store.

    Raises:
        UnknownRunError: The reference matches nothing, the tree has no runs
            in this store, or the store is empty.
        DumpFormatError: The run on disk is malformed.
    """
    if ref is not None:
        return store.read(ref)
    if tree is not None:
        resolved = tree.resolve()
        runs = store.list(build_dir=resolved)
        if not runs:
            raise UnknownRunError(
                f"no runs from {resolved} in {store.root}; build with instrumentation "
                "enabled first (longpole enable), or name a run"
            )
        return store.read(runs[-1].run_id)
    latest = store.latest()
    if latest is None:
        raise UnknownRunError(
            f"no runs in {store.root}; build with instrumentation enabled first (longpole enable)"
        )
    return latest


COMMAND = Command(
    name="analyze",
    help="summarise a run: totals, phases, slowest targets, sources, custom commands and tests",
    description=(
        "Print the summary of one stored run. Defaults to the newest run of the build tree "
        "you are in, or the newest run in the store."
    ),
    register=register,
    options_from=options_from,
    run=run,
)
