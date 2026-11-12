"""``longpole runs``: what the store holds.

Without an action the command lists the runs in the store, newest last so
the latest is where the eye lands at the bottom of a terminal. ``show``
prints one run's manifest, ``prune`` deletes old runs by count or by age.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Annotated

from cyclopts import App, Parameter, Token

from longpole import output
from longpole.commands.options import DEFAULT_STORE, Output, Store
from longpole.errors import UsageError
from longpole.exporters import terminal
from longpole.model import Manifest, Run
from longpole.output import OutputFormat
from longpole.store import RunStore, RunSummary

log = logging.getLogger(__name__)

_DURATION = re.compile(r"^(\d+)([mhd])$")
_UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400}


def duration(text: str) -> timedelta:
    """Parse an age such as ``90m``, ``12h`` or ``7d``.

    Args:
        text: The argument as typed.

    Returns:
        The duration.

    Raises:
        ValueError: The text is not a number followed by m, h or d.
    """
    match = _DURATION.match(text.strip())
    if match is None:
        raise ValueError(f"{text!r} is not an age like 90m, 12h or 7d")
    return timedelta(seconds=int(match[1]) * _UNIT_SECONDS[match[2]])


def _age(_type: type, tokens: list[Token]) -> timedelta:
    """Adapt :func:`duration` to the converter signature cyclopts calls.

    Args:
        _type: The annotated type; unused.
        tokens: The command-line tokens for the option; exactly one.

    Returns:
        The duration.

    Raises:
        ValueError: The token is not an age.
    """
    return duration(tokens[0].value)


Age = Annotated[timedelta | None, Parameter(converter=_age, metavar="AGE")]
"""An option such as ``--since 7d``."""


app = App(
    name="runs",
    help="List stored runs; 'runs show' prints one, 'runs prune' deletes old ones.",
)


@app.default
def list_runs(
    *,
    store: Store = DEFAULT_STORE,
    since: Age = None,
    label: str | None = None,
    output_format: Output = "text",
) -> int:
    """List the runs in the store, newest last.

    Args:
        store: Run store to list.
        since: Only runs captured within AGE, e.g. 90m, 12h or 7d.
        label: Only runs carrying this label.
        output_format: text, or json for a program.

    Returns:
        0.
    """
    return _list(RunStore(store), since=since, label=label, output_format=output_format)


@app.command
def show(
    run: str = "latest", /, *, store: Store = DEFAULT_STORE, output_format: Output = "text"
) -> int:
    """Print one run's manifest.

    Args:
        run: Run id or prefix, latest, latest~N, sha:<prefix>, label:<name>,
            or a dump path.
        store: Run store to read.
        output_format: text, or json for a program.

    Returns:
        0.

    Raises:
        UnknownRunError: The reference names no run.
        DumpFormatError: The run on disk is malformed.
    """
    return _show(RunStore(store), run, output_format=output_format)


@app.command
def prune(
    *,
    store: Store = DEFAULT_STORE,
    keep: int | None = None,
    older_than: Age = None,
    output_format: Output = "text",
) -> int:
    """Delete old runs by count or by age.

    Args:
        store: Run store to prune.
        keep: Keep only the N newest runs.
        older_than: Delete runs older than AGE, e.g. 90d.
        output_format: text, or json for a program.

    Returns:
        0.

    Raises:
        UsageError: Neither limit was given.
    """
    if keep is None and older_than is None:
        raise UsageError("runs prune: give --keep N, --older-than AGE, or both")
    return _prune(RunStore(store), keep=keep, older_than=older_than, output_format=output_format)


def _list(
    store: RunStore,
    *,
    since: timedelta | None,
    label: str | None,
    output_format: OutputFormat,
) -> int:
    """Print the catalogue.

    Args:
        store: The store.
        since: Only runs captured within this long ago, or ``None``.
        label: Only runs carrying this label, or ``None``.
        output_format: Aligned lines, or JSON.

    Returns:
        0.
    """
    cutoff = datetime.now(UTC) - since if since is not None else None
    summaries = store.list(label=label, since=cutoff)

    def text(found: list[RunSummary]) -> str:
        if not found:
            return f"no runs in {store.root}"
        trees = {summary.build_dir for summary in found}
        return _catalogue(found, len(trees) == 1)

    output.result(summaries, output_format, text=text)
    return 0


def _catalogue(summaries: list[RunSummary], one_tree: bool) -> str:
    """The listing as aligned lines.

    A table with seven columns does not fit a terminal without truncating
    ids or paths, so each run is one line of fixed-width fields, and the
    build directory, when the listing spans several trees, follows on an
    indented line of its own where it can be as long as it needs to be.
    Durations are not listed: the summary knows only what the callback knew,
    and it did not parse the snippets.

    Args:
        summaries: Runs, oldest first.
        one_tree: Whether every row is from the same tree, so its line can go.

    Returns:
        The text, one or two lines per run.
    """
    hook_width = max(len(summary.hook) for summary in summaries)
    lines: list[str] = []
    for summary in summaries:
        fields = [
            summary.run_id,
            _when(summary.created_at),
            summary.hook.ljust(hook_width),
            (summary.git_sha[:7] if summary.git_sha else "-").ljust(7),
            f"{summary.step_count:>5} steps",
        ]
        if summary.labels:
            fields.append(", ".join(summary.labels))
        lines.append("  ".join(fields))
        if not one_tree:
            lines.append(f"    {summary.build_dir}")
    return "\n".join(lines)


def _when(created_at: str) -> str:
    """Shorten an ISO timestamp to minute precision in UTC.

    Args:
        created_at: ISO 8601 timestamp.

    Returns:
        ``YYYY-MM-DD HH:MM``, or the input unchanged if it does not parse.
    """
    try:
        moment = datetime.fromisoformat(created_at)
    except ValueError:
        return created_at
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M")


def _show(store: RunStore, ref: str, *, output_format: OutputFormat) -> int:
    """Print one run's manifest.

    Args:
        store: The store.
        ref: Run reference.
        output_format: A key/value table, or JSON.

    Returns:
        0.

    Raises:
        UnknownRunError: The reference names no run.
        DumpFormatError: The run on disk is malformed.
    """
    loaded = store.read(ref)
    output.result(loaded, output_format, text=_manifest_table, data=_manifest_document)
    return 0


def _manifest_document(loaded: Run) -> dict[str, object]:
    """The manifest plus two counts, for JSON.

    Args:
        loaded: The run.

    Returns:
        A JSON-ready object.
    """
    document: dict[str, object] = asdict(loaded.manifest)
    document["data_version"] = asdict(loaded.data_version)
    document["steps"] = len(loaded.steps)
    document["has_graph"] = loaded.graph is not None
    return document


def _manifest_table(loaded: Run) -> str:
    """The manifest as a key/value table.

    Args:
        loaded: The run.

    Returns:
        The text.
    """
    manifest = loaded.manifest
    environment = manifest.environment
    rows = [
        ("run", manifest.run_id),
        ("captured", manifest.created_at),
        ("hook", manifest.hook),
        ("build dir", manifest.build_dir),
        ("data version", str(loaded.data_version)),
        ("cmake", environment.cmake_version or ""),
        ("generator", environment.generator or ""),
        ("cores", str(environment.cpu_count) if environment.cpu_count is not None else ""),
        ("git", _git_line(manifest)),
        ("labels", ", ".join(manifest.labels)),
        ("steps", str(len(loaded.steps))),
        ("graph", "yes" if loaded.graph is not None else "no"),
        ("longpole", manifest.tool_version),
    ]
    return terminal.render(terminal.table(None, ("", ""), rows, show_header=False))


def _git_line(manifest: Manifest) -> str:
    """Describe the checkout of a run in one cell.

    Args:
        manifest: The run's manifest.

    Returns:
        ``sha branch`` with ``(dirty)`` when applicable; empty outside git.
    """
    git = manifest.environment.git
    if git is None:
        return ""
    text = git.sha[:12]
    if git.branch:
        text += f" {git.branch}"
    if git.dirty:
        text += " (dirty)"
    return text


def _prune(
    store: RunStore, *, keep: int | None, older_than: timedelta | None, output_format: OutputFormat
) -> int:
    """Delete runs and say which.

    Args:
        store: The store.
        keep: How many newest runs survive, or ``None``.
        older_than: Runs older than this go, or ``None``.
        output_format: One id per line, or a JSON list.

    Returns:
        0.
    """
    deleted = store.prune(keep=keep, older_than=older_than)
    output.result(deleted, output_format, text="\n".join)
    log.info("deleted %d run(s) from %s", len(deleted), store.root)
    return 0
