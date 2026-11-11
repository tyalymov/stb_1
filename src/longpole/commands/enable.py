"""``longpole enable``: switch CMake's instrumentation on for a build tree.

CMake records a build when a query file sits in
``<build>/.cmake/instrumentation/v1/query/``. This command writes one query
file there, naming ``longpole capture`` as the callback, and nothing else:
no change to the project, no wrapper around the build. CMake calls the
callback after each configure, build, install, workflow and ctest run, and
the callback stores the run.

The command also plants a File API query, so the next configure answers
with the target graph the critical path needs, and marks the tree as having
its own run store. A tree that was configured before the query existed has
to be configured once more, since the launcher that records each step is
written into the build system at generate time; ``analyze`` and ``doctor``
say so when they meet such a run.
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from longpole import output
from longpole.cmake import fileapi, tooling
from longpole.commands.options import Output, Store
from longpole.errors import CMakeToolError
from longpole.model import DataVersion
from longpole.store import IN_TREE

log = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class EnableResult:
    """What the command did, for the text and JSON reports.

    Attributes:
        query: Path of the query file written.
        callback: Command line CMake will run.
        file_api_query: File API query file written next to it.
    """

    query: Path
    callback: str
    file_api_query: Path


def enable(
    build_dir: Path,
    /,
    *,
    label: tuple[str, ...] = (),
    store: Store = None,
    output_format: Output = "text",
) -> int:
    """Switch CMake instrumentation on for a build tree.

    Write the query file that makes CMake record every step and call
    'longpole capture' afterwards, ask the File API for the target graph,
    and mark the tree as having its own run store. Labels and a store given
    here are written into the callback, so every run it records carries
    them. Nothing in the project changes. 'longpole disable' undoes it.

    Args:
        build_dir: Build tree to instrument; may not exist yet.
        label: Label every recorded run; may be repeated.
        store: Run store the callback writes to; without it, <build>/.longpole.
        output_format: text, or json for a program.

    Returns:
        0 on success; failures raise.

    Raises:
        FileNotFoundError: No ``longpole`` on PATH to name as the callback.
        OSError: A query directory cannot be created or written.
    """
    result = write_query(build_dir, labels=label, store=store)
    output.result(result, output_format, text=lambda r: f"enabled: {r.query}")
    return 0


def write_query(
    build_dir: Path, *, labels: tuple[str, ...] = (), store: Path | None = None
) -> EnableResult:
    """Write the query file for a build tree.

    The ``cmake`` on PATH is asked which data version it writes, so the query
    asks for exactly that (a newer minor than the reader knows is ignored
    whole). No ``cmake``, or one older than 4.3, is a warning, not an error:
    the file is written for data version 1.0 and starts working as soon as a
    CMake that knows the API builds the tree. Writing the same file again is
    harmless. The File API query is planted as well, and the in-tree store
    directory unless ``store`` points elsewhere. The callback gets the store
    on its command line either way; it never has to work one out.

    Args:
        build_dir: The tree.
        labels: Labels the callback attaches to every run.
        store: Store the callback writes to, or ``None`` for ``<build>/.longpole``.

    Returns:
        What was written.

    Raises:
        FileNotFoundError: No ``longpole`` on PATH to name as the callback.
        OSError: A directory cannot be created or a file written.
    """
    info = _cmake_on_path()
    data_version = (info.data_version if info is not None else None) or DataVersion(
        major=1, minor=0
    )
    if info is None:
        log.warning("no cmake on PATH; the query asks for data version 1.0 without process metrics")
    elif info.data_version is None:
        log.warning(
            "cmake on PATH is %s, older than 4.3: it has no instrumentation API and will "
            "record nothing; the query waits for a newer cmake",
            info.version,
        )
    target = store if store is not None else build_dir / IN_TREE
    callback = callback_command(labels=labels, store=target)
    query = query_location(build_dir)
    query.parent.mkdir(parents=True, exist_ok=True)
    document = query_document(data_version, callback)
    query.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    log.info("wrote %s", query)

    file_api_query = fileapi.write_query(build_dir)
    if store is None:  # an in-tree store would shadow the one the callback writes to
        (build_dir / IN_TREE).mkdir(parents=True, exist_ok=True)
    return EnableResult(query=query, callback=callback, file_api_query=file_api_query)


def _cmake_on_path() -> tooling.CMakeInfo | None:
    """Describe the ``cmake`` on PATH, the one that will most likely run the build.

    Returns:
        Its version facts, or ``None`` when there is no ``cmake`` on PATH or
        it cannot be run; the caller warns, since the query can be written
        without it.
    """
    found = shutil.which("cmake")
    if found is None:
        return None
    try:
        return tooling.describe(Path(found))
    except CMakeToolError as exc:
        log.warning("cannot run %s: %s", found, exc)
        return None


def query_location(build_dir: Path) -> Path:
    """Where our query file goes.

    Args:
        build_dir: The tree.

    Returns:
        ``<build>/.cmake/instrumentation/v1/query/longpole.json``.
    """
    return build_dir / ".cmake" / "instrumentation" / "v1" / "query" / "longpole.json"


def query_document(data_version: DataVersion, callback: str) -> dict[str, object]:
    """The query file's content.

    The requested minor matches what the consulted CMake writes: a query
    asking for a newer minor than the CMake knows is silently ignored by it,
    and an older one still gets the newest data anyway.

    Args:
        data_version: Data version of the CMake that will read the query.
        callback: Command line to run at each hook; CMake appends the index path.

    Returns:
        A JSON-ready object with ``version``, ``hooks``, ``callbacks`` and ``options``.
    """
    options = ["staticSystemInformation", "dynamicSystemInformation"]
    if data_version.minor >= 2:  # processMetrics exists from data 1.2 (CMake 4.5)
        options.append("processMetrics")
    return {
        "version": {"major": data_version.major, "minor": data_version.minor},
        # Every hook for work done through cmake and ctest themselves. preBuild and
        # postBuild fire when ninja or make is run directly; the generator is CMake's
        # implementation detail here, so they are left out.
        "hooks": [
            "postGenerate",
            "postCMakeBuild",
            "postCMakeInstall",
            "postCMakeWorkflow",
            "postCTest",
        ],
        "callbacks": [callback],
        "options": options,
    }


def callback_command(*, store: Path, labels: tuple[str, ...] = ()) -> str:
    """The command line CMake runs at each hook.

    IDEs and CI runners often start ``cmake`` with a different ``PATH`` than
    the shell that ran ``enable``, so the command names the installed
    ``longpole`` by absolute path. The store and the labels go on the
    command line too, since the callback gets nothing else from the shell
    that enabled recording; the store is made absolute for the same reason.

    Args:
        store: ``--store`` for the callback.
        labels: ``--label`` values for the callback.

    Returns:
        ``"<path>" capture --store "PATH" [--label "NAME"]...``; every value
        is double-quoted, which CMake's argument splitting reads the same
        whether or not it contains whitespace.

    Raises:
        FileNotFoundError: No ``longpole`` on PATH to name.
    """
    script = shutil.which("longpole")
    if script is None:
        raise FileNotFoundError(
            "no longpole on PATH to name as the callback; install it so that "
            "`longpole --version` works, then run enable again"
        )
    command = f'"{script}" capture --store "{store.resolve()}"'
    for label in labels:
        command += f' --label "{label}"'
    return command
