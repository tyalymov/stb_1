"""``longpole capture``: the callback CMake runs at each hook.

CMake calls it with the path of an index file and deletes that index and
every snippet it lists as soon as all callbacks have returned. So this
command does one thing, quickly: it reads the index and the snippets, adds
the target graph from the File API reply, the environment and a few labels,
writes the whole as a run into the store, and prints one line on standard
error saying what it stored. Analysis happens later, from the stored run.

Two situations get special treatment. An index with no snippets, which the
``postCMakeWorkflow`` hook produces because the per-step hooks consumed
everything already, records nothing. An index from a tree that was generated
before the query existed carries only the ``cmakeBuild`` wrapper step; the
run is stored but labelled, and the status line asks for a reconfigure.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from longpole import __version__, env, output, units
from longpole.analyzers.critical_path import CriticalPath, critical_path
from longpole.cmake import fileapi, snippets, tooling
from longpole.commands.options import Store
from longpole.errors import CMakeToolError
from longpole.model import Graph, Manifest, Run
from longpole.store import RunStore, new_run_id, resolve_store

log = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class Outcome:
    """What one callback invocation did.

    Attributes:
        hook: Hook that fired.
        run: The stored run, or ``None`` when nothing was recorded.
        directory: The run's directory in the store, or ``None``.
        graph_pending: No File API reply existed; the query was planted for
            the next configure.
        needs_reconfigure: The build system predates the query, so compile,
            link and custom steps are missing from this run.
    """

    hook: str
    run: Run | None
    directory: Path | None
    graph_pending: bool
    needs_reconfigure: bool


def capture(index: Path, /, *, label: tuple[str, ...] = (), store: Store = None) -> int:
    """Store the run an instrumentation index describes; CMake runs this at each hook.

    Read a CMake instrumentation index and its snippets, add the target graph
    and the environment, and write the run to the store before CMake deletes
    the data. Registered as the callback by 'longpole enable'; rarely run by
    hand. One status line goes to standard error.

    Args:
        index: Index file; CMake appends it to the callback.
        label: Label the run; may be repeated (a CI run id is added automatically).
        store: Run store to write to instead of the resolved one.

    Returns:
        0; a skipped empty index is a success too.

    Raises:
        FileNotFoundError: The index or a snippet is already gone.
        IndexFormatError: The index is not v1 data.
        SnippetFormatError: A snippet is not v1 data.
        SchemaError: The index or a snippet violates the published schema.
        FileApiError: A File API reply exists but cannot be read.
        OSError: The store cannot be written.
    """
    labels = list(label)
    ci_run_id = os.environ.get("GITHUB_RUN_ID")  # set by GitHub Actions
    if ci_run_id:
        labels.append(f"ci:{ci_run_id}")
    outcome = record(index, store_override=store, environ=os.environ, labels=tuple(labels))
    output.stderr(status_line(outcome))
    return 0


def record(
    index_path: Path,
    *,
    store_override: Path | None,
    environ: Mapping[str, str],
    labels: tuple[str, ...] = (),
    now: datetime | None = None,
) -> Outcome:
    """Turn one index into a stored run.

    Reads the index and its snippets, resolves the build tree, loads the File
    API reply (or plants the query when there is none), collects the
    environment, checks whether the build system predates the query, and
    writes the run. The raw index and snippets are copied into the dump
    before this function returns, since CMake deletes them afterwards.

    Args:
        index_path: The index file.
        store_override: ``--store``, if given.
        environ: Environment variables, for the store rule.
        labels: Labels to attach besides the automatic ones.
        now: Capture time; defaults to the current UTC time.

    Returns:
        What happened. ``run`` is ``None`` for an index without snippets.

    Raises:
        FileNotFoundError: The index or a snippet is already gone.
        IndexFormatError: The index is not v1 data.
        SnippetFormatError: A snippet is not v1 data.
        SchemaError: The index or a snippet violates the published schema.
        FileApiError: A File API reply exists but cannot be read.
        OSError: The store cannot be written.
    """
    index = snippets.read_index(index_path)
    steps = snippets.read_steps(index)
    if not steps:
        log.info("%s: index %s lists no snippets; nothing to record", index.hook, index_path)
        return Outcome(
            hook=index.hook, run=None, directory=None, graph_pending=False, needs_reconfigure=False
        )

    build_dir = index.build_dir.resolve()
    reply = fileapi.read_reply(build_dir)
    graph: Graph | None = None
    if reply is None:
        fileapi.write_query(build_dir)
        log.info("no File API reply in %s; query planted for the next configure", build_dir)
    else:
        graph = fileapi.graph_from_reply(reply)
    environment = env.collect(build_dir, reply=reply)
    needs_reconfigure = _generated_before_query(build_dir)

    all_labels = list(labels)
    if graph is None:
        all_labels.append("no-graph")  # the critical path needs the graph
    if needs_reconfigure:
        all_labels.append("needs-reconfigure")  # compile and link steps are missing

    moment = now if now is not None else datetime.now(UTC)
    manifest = Manifest(
        run_id=new_run_id(moment),
        created_at=moment.isoformat(),
        tool_version=__version__,
        hook=index.hook,
        build_dir=str(build_dir),
        data_version=index.version,
        environment=environment,
        labels=tuple(all_labels),
    )
    captured = Run(manifest=manifest, steps=steps, graph=graph, system=index.system)
    store = RunStore(resolve_store(store_override, build_dir=build_dir, environ=environ))
    directory = store.write(captured, raw_index=index.path, raw_snippets=index.snippet_paths)
    log.info("stored %s as %s", index.hook, directory)
    return Outcome(
        hook=index.hook,
        run=captured,
        directory=directory,
        graph_pending=graph is None,
        needs_reconfigure=needs_reconfigure,
    )


def status_line(outcome: Outcome) -> str:
    """The one line the callback prints.

    Args:
        outcome: What :func:`record` did.

    Returns:
        ``longpole: <hook> recorded as <id>: N steps, 1.6 s, critical path
        1.2 s (74%)`` plus a hint when the graph is pending or a reconfigure
        is needed; the critical path part is left out when there is none;
        or ``longpole: <hook>: empty index, nothing recorded``.
    """
    if outcome.run is None:
        return f"longpole: {outcome.hook}: empty index, nothing recorded"
    run = outcome.run
    count = len(run.steps)
    text = (
        f"longpole: {run.manifest.hook} recorded as {run.manifest.run_id}: "
        f"{count} step{'s' if count != 1 else ''}, {units.duration(run.span_ms)}"
    )
    path = critical_path(run)
    if isinstance(path, CriticalPath):
        text += f", critical path {units.duration(path.lower_bound_ms)} ({path.share:.0%})"
    hints: list[str] = []
    if outcome.needs_reconfigure:
        hints.append("run cmake again to record compile and link steps")
    if outcome.graph_pending:
        hints.append("target graph pending next configure")
    if hints:
        text += "; " + "; ".join(hints)
    return text + " (longpole analyze)"


def _generated_before_query(build_dir: Path) -> bool:
    """Whether the tree's build system is older than the query that produced this index.

    Looks at the tree's own query files and at the user-level ones, found
    through the ``cmake`` recorded in the cache. A ``cmake`` that cannot be
    run only drops the user-level check.

    Args:
        build_dir: The resolved build tree.

    Returns:
        ``True`` when a reconfigure is needed before steps get recorded.
    """
    config_dir: Path | None = None
    cache = tooling.read_cache(build_dir)
    if cache.cmake_command is not None:
        try:
            config_dir = tooling.config_dir(cache.cmake_command)
        except CMakeToolError as exc:
            log.debug("cannot find the user-level query directory: %s", exc)
    return tooling.generated_before(
        build_dir, tooling.query_files(build_dir, config_dir=config_dir)
    )
