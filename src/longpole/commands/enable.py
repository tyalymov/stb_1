"""``longpole enable``: switch CMake's instrumentation on, for one build tree or for everything.

CMake records a build when a query file sits in one of two places: inside
the build tree (``<build>/.cmake/instrumentation/v1/query/``) or in the
user's CMake configuration directory, which applies to every build on the
machine. This command writes one query file there, naming ``longpole
capture`` as the callback, and nothing else: no change to the project, no
wrapper around the build. CMake calls the callback after each configure,
build, install, workflow and ctest run, and the callback stores the run.

For a build tree the command also plants a File API query, so the next
configure answers with the target graph the critical path needs, and marks
the tree as having its own run store. A tree that was configured before the
query existed has to be configured once more: the launcher that records
each step is written into the build system at generate time.
"""

from __future__ import annotations

import json
import logging
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Annotated

from cyclopts import Parameter

from longpole import output
from longpole.cmake import fileapi, tooling
from longpole.commands.options import Output
from longpole.errors import CMakeToolError, UsageError
from longpole.model import DataVersion
from longpole.store import IN_TREE

log = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class EnableResult:
    """What the command did, for the text and JSON reports.

    Attributes:
        scope: ``tree`` or ``global``.
        query: Path of the query file written.
        callback: Command line CMake will run.
        cmake_version: Version of the ``cmake`` on PATH, or ``None`` when there was none.
        data_version: Data version requested in the query, as ``major.minor``.
        file_api_query: File API query file written for a build tree, or ``None``.
        regenerate_needed: The tree is already configured, so the query takes
            full effect only after the next ``cmake`` run.
    """

    scope: str
    query: Path
    callback: str
    cmake_version: str | None
    data_version: str
    file_api_query: Path | None
    regenerate_needed: bool


def enable(
    build_dir: Path | None = None,
    /,
    *,
    everywhere: Annotated[bool, Parameter(name="--global")] = False,
    output_format: Output = "text",
) -> int:
    """Switch CMake instrumentation on for a build tree, or for every build with --global.

    Write the query file that makes CMake record every step and call
    'longpole capture' afterwards. With a build tree, also ask the File API
    for the target graph and mark the tree as having its own run store.
    Nothing in the project changes. 'longpole disable' undoes it.

    Args:
        build_dir: Build tree to instrument; may not exist yet.
        everywhere: Instrument every build of this user on this machine instead.
        output_format: text, or json for a program.

    Returns:
        0 on success; failures raise.

    Raises:
        UsageError: Neither a build tree nor ``--global`` was given, or both were.
        CMakeToolError: ``--global`` and there is no working ``cmake`` on PATH
            to ask for its configuration directory.
        OSError: A query directory cannot be created or written.
    """
    if (build_dir is None) == (not everywhere):
        raise UsageError("enable: give a build tree or --global, not both and not neither")
    result = write_query(build_dir)
    if output_format == "json":
        output.stdout(json.dumps(_as_json(result), indent=2))
    else:
        output.stdout(_describe(result))
    return 0


def write_query(build_dir: Path | None) -> EnableResult:
    """Write the query file for a build tree, or for the user when ``build_dir`` is ``None``.

    The ``cmake`` on PATH is asked which data version it writes, so the query
    asks for exactly that (a newer minor than the reader knows is ignored
    whole). No ``cmake``, or one older than 4.3, is a warning, not an error:
    the file is written for data version 1.0 and starts working as soon as a
    CMake that knows the API builds the tree. Writing the same file again is
    harmless. For a build tree the File API query and the in-tree store
    directory are created as well.

    Args:
        build_dir: The tree, or ``None`` for the user-level directory.

    Returns:
        What was written.

    Raises:
        CMakeToolError: ``build_dir`` is ``None`` and no ``cmake`` on PATH can
            say where the user-level directory is.
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
    callback = callback_command()
    query = query_location(build_dir)
    query.parent.mkdir(parents=True, exist_ok=True)
    document = query_document(data_version, callback)
    query.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    log.info("wrote %s", query)

    file_api_query: Path | None = None
    regenerate_needed = False
    if build_dir is not None:
        file_api_query = fileapi.write_query(build_dir)
        (build_dir / IN_TREE).mkdir(parents=True, exist_ok=True)
        regenerate_needed = (build_dir / "CMakeCache.txt").is_file()
    return EnableResult(
        scope="global" if build_dir is None else "tree",
        query=query,
        callback=callback,
        cmake_version=info.version if info is not None else None,
        data_version=str(data_version),
        file_api_query=file_api_query,
        regenerate_needed=regenerate_needed,
    )


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


def query_location(build_dir: Path | None) -> Path:
    """Where our query file goes.

    Args:
        build_dir: The tree, or ``None`` for the user-level directory.

    Returns:
        ``<build>/.cmake/instrumentation/v1/query/longpole.json`` for a tree;
        ``<config dir>/instrumentation/v1/query/longpole.json`` otherwise,
        where the configuration directory is what ``cmake --print-config-dir``
        of the ``cmake`` on PATH says.

    Raises:
        CMakeToolError: Global mode and there is no ``cmake`` on PATH, or it
            cannot say.
    """
    if build_dir is not None:
        root = build_dir / ".cmake"
    else:
        found = shutil.which("cmake")
        if found is None:
            raise CMakeToolError(
                "no cmake on PATH to ask for the user-level configuration directory"
            )
        root = tooling.config_dir(Path(found))
    return root / "instrumentation" / "v1" / "query" / "longpole.json"


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


def callback_command() -> str:
    """The command line CMake runs at each hook.

    IDEs and CI runners often start ``cmake`` with a different ``PATH`` than
    the shell that ran ``enable``, so the command names the executable by
    absolute path: the installed ``longpole`` script when there is one,
    otherwise this interpreter with ``-m longpole``.

    Returns:
        ``<path> capture`` or ``<python> -m longpole capture``, with paths
        double-quoted when they contain whitespace.
    """
    script = shutil.which("longpole")
    if script is not None:
        return f"{_quoted(script)} capture"
    return f"{_quoted(sys.executable)} -m longpole capture"


def _quoted(path: str) -> str:
    """Double-quote a path if it contains whitespace.

    Args:
        path: The path.

    Returns:
        The path, quoted when needed.
    """
    return f'"{path}"' if any(character.isspace() for character in path) else path


def _as_json(result: EnableResult) -> dict[str, object]:
    """Serialise a result with paths as strings.

    Args:
        result: The result.

    Returns:
        A JSON-ready object.
    """
    document = asdict(result)
    for key in ("query", "file_api_query"):
        if document[key] is not None:
            document[key] = str(document[key])
    return document


def _describe(result: EnableResult) -> str:
    """Render a result as a few lines for a person.

    Args:
        result: The result.

    Returns:
        The text.
    """
    lines = [
        f"enabled ({result.scope}): {result.query}",
        f"callback: {result.callback}",
        f"data version: {result.data_version} (cmake {result.cmake_version or 'not found'})",
    ]
    if result.file_api_query is not None:
        lines.append(f"target graph: File API query at {result.file_api_query}")
    if result.regenerate_needed:
        lines.append(
            "next: this tree is already configured; run cmake again so the recording "
            "launcher is written into the build system"
        )
    return "\n".join(lines)
