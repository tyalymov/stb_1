"""``longpole analyze``: the report of one stored run.

Without a run reference the command picks the newest run of the build tree
the working directory is in, so ``cd build && longpole analyze`` shows the
build that just finished; outside any tree it shows the newest run in the
store. A reference selects any other run: an id or prefix, ``latest``,
``latest~N``, ``sha:<prefix>``, ``label:<name>`` or the path of a dump.
"""

from __future__ import annotations

from pathlib import Path

from longpole import output
from longpole.analyzers.report import build_report, to_json
from longpole.cmake import tooling
from longpole.commands.options import Output, Store
from longpole.errors import UnknownRunError
from longpole.exporters import terminal
from longpole.model import Run
from longpole.store import RunStore, resolve_store

DEFAULT_TOP = 10


def analyze(
    run: str | None = None,
    /,
    *,
    top: int = DEFAULT_TOP,
    store: Store = None,
    output_format: Output = "text",
) -> int:
    """Report on a run: totals, phases, slowest items, critical path, parallelism, sizes.

    Print the report of one stored run: totals and phases, the slowest
    targets, sources, custom commands and tests, the critical path, the
    parallelism over time and the artifact sizes. Defaults to the newest run
    of the build tree you are in, or the newest run in the store.

    Args:
        run: Run id or prefix, latest, latest~N, sha:<prefix>, label:<name>,
            or a dump path.
        top: Rows per table.
        store: Run store to read instead of the resolved one.
        output_format: text, or json for a program.

    Returns:
        0 on success.

    Raises:
        UnknownRunError: The reference names no run, or there is no run to default to.
        DumpFormatError: The stored run is malformed.
    """
    tree = tooling.find_build_tree(Path.cwd())
    runs = RunStore(resolve_store(store, build_dir=tree))
    report = build_report(load_run(runs, run, tree), top=top)
    output.result(report, output_format, text=terminal.format_report, data=to_json)
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
