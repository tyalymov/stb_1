"""``longpole analyze``: the report of one stored run.

Without a run reference the command picks the newest run in the store. A
reference selects any other run: an id or prefix, ``latest``, ``latest~N``,
``sha:<prefix>``, ``label:<name>`` or the path of a dump.
"""

from __future__ import annotations

from longpole import output
from longpole.analyzers.report import build_report, to_json
from longpole.commands.options import DEFAULT_STORE, Output, Store
from longpole.errors import UnknownRunError
from longpole.exporters import terminal
from longpole.model import Run
from longpole.store import RunStore

DEFAULT_TOP = 10


def analyze(
    run: str | None = None,
    /,
    *,
    store: Store = DEFAULT_STORE,
    top: int = DEFAULT_TOP,
    output_format: Output = "text",
) -> int:
    """Report on a run: totals, phases, slowest items, critical path, parallelism, sizes.

    Print the report of one stored run: totals and phases, the slowest
    targets, sources, custom commands and tests, the critical path, the
    parallelism over time and the artifact sizes. Defaults to the newest run
    in the store.

    Args:
        run: Run id or prefix, latest, latest~N, sha:<prefix>, label:<name>,
            or a dump path.
        store: Run store to read.
        top: Rows per table.
        output_format: text, or json for a program.

    Returns:
        0 on success.

    Raises:
        UnknownRunError: The reference names no run, or there is no run to default to.
        DumpFormatError: The stored run is malformed.
    """
    report = build_report(load_run(RunStore(store), run), top=top)
    output.result(report, output_format, text=terminal.format_report, data=to_json)
    return 0


def load_run(store: RunStore, ref: str | None) -> Run:
    """Pick the run to analyse.

    Args:
        store: The store to read from.
        ref: An explicit reference, or ``None``.

    Returns:
        The referenced run, or the newest in the store.

    Raises:
        UnknownRunError: The reference matches nothing, or the store is empty.
        DumpFormatError: The run on disk is malformed.
    """
    if ref is not None:
        return store.read(ref)
    latest = store.latest()
    if latest is None:
        raise UnknownRunError(
            f"no runs in {store.root}; build with instrumentation enabled first (longpole enable)"
        )
    return latest
