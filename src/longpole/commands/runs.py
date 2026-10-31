"""``longpole runs``: what the store holds.

Without an action the command lists runs, newest last so the latest is
where the eye lands at the bottom of a terminal. Inside a build tree it
lists that tree's runs; ``--all`` lifts the filter. ``show`` prints one
run's manifest, ``prune`` deletes old runs by count or by age.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from longpole import context, output, units
from longpole.cmake import tooling
from longpole.command import Command
from longpole.errors import UsageError
from longpole.exporters import terminal
from longpole.model import Manifest
from longpole.store import RunStore, RunSummary, resolve_store

log = logging.getLogger(__name__)

_DURATION = re.compile(r"^(\d+)([mhd])$")
_UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400}


@dataclass(frozen=True, kw_only=True)
class RunsOptions:
    """Arguments of ``longpole runs`` and its actions.

    Attributes:
        action: ``show``, ``prune`` or ``None`` for the listing.
        all_trees: Whether to list every tree's runs from inside a build tree.
        since: Only runs captured within this long ago.
        label: Only runs carrying this label.
        run: Reference for ``show``.
        keep: ``prune --keep``: how many newest runs survive.
        older_than: ``prune --older-than``: runs older than this go.
    """

    action: str | None
    all_trees: bool
    since: timedelta | None
    label: str | None
    run: str | None
    keep: int | None
    older_than: timedelta | None


def register(parser: argparse.ArgumentParser) -> None:
    """Declare the arguments of ``runs``.

    Args:
        parser: The command's subparser.
    """
    parser.add_argument(
        "--all",
        dest="all_trees",
        action="store_true",
        help="inside a build tree, list every tree's runs instead of this tree's",
    )
    parser.add_argument(
        "--since",
        type=duration,
        metavar="AGE",
        help="only runs captured within AGE, e.g. 90m, 12h or 7d",
    )
    parser.add_argument("--label", metavar="NAME", help="only runs carrying this label")
    actions = parser.add_subparsers(dest="action", metavar="<action>", required=False)
    show = actions.add_parser("show", help="print one run's manifest")
    show.add_argument(
        "run",
        metavar="RUN",
        help="run id or prefix, latest, latest~N, sha:<prefix>, label:<name>, or a dump path",
    )
    prune = actions.add_parser("prune", help="delete old runs by count or by age")
    prune.add_argument("--keep", type=int, metavar="N", help="keep only the N newest runs")
    prune.add_argument(
        "--older-than", type=duration, metavar="AGE", help="delete runs older than AGE, e.g. 90d"
    )


def duration(text: str) -> timedelta:
    """Parse an age such as ``90m``, ``12h`` or ``7d``.

    Args:
        text: The argument as typed.

    Returns:
        The duration.

    Raises:
        argparse.ArgumentTypeError: The text is not a number followed by m, h or d.
    """
    match = _DURATION.match(text.strip())
    if match is None:
        raise argparse.ArgumentTypeError(f"{text!r} is not an age like 90m, 12h or 7d")
    return timedelta(seconds=int(match[1]) * _UNIT_SECONDS[match[2]])


def options_from(namespace: argparse.Namespace) -> RunsOptions:
    """Type the parsed arguments.

    Args:
        namespace: argparse's result for this command.

    Returns:
        The options; fields of actions that were not chosen are ``None``.
    """
    return RunsOptions(
        action=namespace.action,
        all_trees=bool(namespace.all_trees),
        since=namespace.since,
        label=namespace.label,
        run=getattr(namespace, "run", None),
        keep=getattr(namespace, "keep", None),
        older_than=getattr(namespace, "older_than", None),
    )


def run() -> int:
    """List, show or prune, as asked.

    Returns:
        0 on success.

    Raises:
        UsageError: ``prune`` without ``--keep`` or ``--older-than``.
        UnknownRunError: ``show`` names no run.
        DumpFormatError: A stored run is malformed.
    """
    options = context.options(RunsOptions)
    cli = context.cli()
    env = context.env()
    tree = tooling.find_build_tree(env.cwd)
    store = RunStore(resolve_store(cli.store, build_dir=tree, environ=env.environ))
    if options.action == "show":
        return _show(store, options.run or "latest", json_output=cli.json_output)
    if options.action == "prune":
        return _prune(store, options, json_output=cli.json_output)
    return _list(store, options, tree=tree, json_output=cli.json_output)


def _list(store: RunStore, options: RunsOptions, *, tree: Path | None, json_output: bool) -> int:
    """Print the catalogue.

    Args:
        store: The store.
        options: Filters.
        tree: The build tree the working directory is in, or ``None``.
        json_output: JSON instead of a table.

    Returns:
        0.
    """
    build_dir = tree.resolve() if tree is not None and not options.all_trees else None
    since = datetime.now(UTC) - options.since if options.since is not None else None
    summaries = store.list(build_dir=build_dir, label=options.label, since=since)
    if json_output:
        output.stdout(json.dumps([asdict(summary) for summary in summaries], indent=2))
        return 0
    if not summaries:
        scope = f" from {build_dir}" if build_dir is not None else ""
        output.stdout(f"no runs{scope} in {store.root}")
        return 0
    output.stdout(_catalogue(summaries, build_dir is not None))
    return 0


def _catalogue(summaries: list[RunSummary], one_tree: bool) -> str:
    """The listing as aligned lines.

    A table with eight columns does not fit a terminal without truncating
    ids or paths, so each run is one line of fixed-width fields, and the
    build directory, when the listing spans several trees, follows on an
    indented line of its own where it can be as long as it needs to be.

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
            units.duration(summary.span_ms).rjust(7),
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


def _show(store: RunStore, ref: str, *, json_output: bool) -> int:
    """Print one run's manifest.

    Args:
        store: The store.
        ref: Run reference.
        json_output: JSON instead of a key/value table.

    Returns:
        0.

    Raises:
        UnknownRunError: The reference names no run.
        DumpFormatError: The run on disk is malformed.
    """
    loaded = store.read(ref)
    manifest = loaded.manifest
    if json_output:
        document = asdict(manifest)
        document["steps"] = len(loaded.steps)
        document["has_graph"] = loaded.graph is not None
        output.stdout(json.dumps(document, indent=2))
        return 0
    environment = manifest.environment
    rows = [
        ("run", manifest.run_id),
        ("captured", manifest.created_at),
        ("hook", manifest.hook),
        ("build dir", manifest.build_dir),
        ("data version", str(manifest.data_version)),
        ("cmake", environment.cmake_version or ""),
        ("generator", environment.generator or ""),
        ("cores", str(environment.cpu_count) if environment.cpu_count is not None else ""),
        ("git", _git_line(manifest)),
        ("labels", ", ".join(manifest.labels)),
        ("steps", str(len(loaded.steps))),
        ("graph", "yes" if loaded.graph is not None else "no"),
        ("longpole", manifest.tool_version),
    ]
    output.stdout(terminal.render(terminal.table(None, ("", ""), rows, show_header=False)))
    return 0


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


def _prune(store: RunStore, options: RunsOptions, *, json_output: bool) -> int:
    """Delete runs and say which.

    Args:
        store: The store.
        options: ``keep`` and ``older_than``; at least one must be set.
        json_output: JSON list instead of one id per line.

    Returns:
        0.

    Raises:
        UsageError: Neither limit was given.
    """
    if options.keep is None and options.older_than is None:
        raise UsageError("runs prune: give --keep N, --older-than AGE, or both")
    deleted = store.prune(keep=options.keep, older_than=options.older_than)
    if json_output:
        output.stdout(json.dumps(deleted))
    elif deleted:
        output.stdout("\n".join(deleted))
    log.info("deleted %d run(s) from %s", len(deleted), store.root)
    return 0


COMMAND = Command(
    name="runs",
    help="list stored runs; 'runs show' prints one, 'runs prune' deletes old ones",
    description=(
        "List the runs in the store, newest last. Inside a build tree only that tree's runs "
        "are listed unless --all is given. 'runs show RUN' prints a run's manifest; "
        "'runs prune' deletes runs beyond a count or an age."
    ),
    register=register,
    options_from=options_from,
    run=run,
)
