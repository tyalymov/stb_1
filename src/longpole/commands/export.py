"""``longpole export``: write a run out in a format another tool reads.

The report lives in the store as data; this command renders it. Each
format is a flag with a destination path, so one invocation can write
several files. A destination of ``-`` sends that format to standard output
instead, which only makes sense for one format at a time.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

from cyclopts import Parameter

from longpole import output
from longpole.analyzers.report import build_report
from longpole.commands.analyze import load_run
from longpole.commands.options import Store
from longpole.errors import UsageError
from longpole.exporters import markdown as markdown_snapshot
from longpole.exporters import perfetto as perfetto_trace
from longpole.store import RunStore

log = logging.getLogger(__name__)

STDOUT = "-"


def export(
    run: str | None = None,
    /,
    *,
    store: Store,
    perfetto: Annotated[str | None, Parameter(metavar="PATH", allow_leading_hyphen=True)] = None,
    markdown: Annotated[str | None, Parameter(metavar="PATH", allow_leading_hyphen=True)] = None,
) -> int:
    """Write a run as a Perfetto trace or a Markdown snapshot.

    Render one stored run for another tool. --perfetto writes a Chrome JSON
    trace that ui.perfetto.dev opens directly; --markdown writes a snapshot
    for a CI job summary or check run. Defaults to the newest run in the
    store.

    Args:
        run: Run id or prefix, latest, latest~N, sha:<prefix>, label:<name>,
            or a dump path.
        store: Run store to read.
        perfetto: Write the Perfetto trace to PATH ('-' for standard output).
        markdown: Write the Markdown snapshot to PATH ('-' for standard output).

    Returns:
        0 on success.

    Raises:
        UsageError: No format was requested.
        UnknownRunError: The reference names no run, or there is no run to default to.
        DumpFormatError: The stored run is malformed.
        OSError: A destination cannot be written.
    """
    if perfetto is None and markdown is None:
        raise UsageError(
            "export: choose at least one format, e.g. --perfetto trace.json or --markdown run.md"
        )
    loaded = load_run(RunStore(store), run)
    report = build_report(loaded)
    if perfetto is not None:
        _write(perfetto, perfetto_trace.format_trace(loaded, report), "Perfetto trace")
    if markdown is not None:
        _write(markdown, markdown_snapshot.format_markdown(report), "Markdown snapshot")
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
