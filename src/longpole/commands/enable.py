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

import argparse
import json
import logging
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from longpole import context, output
from longpole.cmake import fileapi, tooling
from longpole.command import Command
from longpole.errors import CMakeToolError, UnsupportedCMakeError
from longpole.model import DataVersion
from longpole.store import IN_TREE

log = logging.getLogger(__name__)

QUERY_FILE_NAME = "longpole.json"
"""Name of our query file in either query directory."""

HOOKS: tuple[str, ...] = (
    "postGenerate",
    "postCMakeBuild",
    "postCMakeInstall",
    "postCMakeWorkflow",
    "postCTest",
)
"""Every hook CMake offers for work done through ``cmake`` and ``ctest`` themselves.

``preBuild`` and ``postBuild`` fire when the generator's tool (``ninja``,
``make``) is run directly; longpole treats the generator as an
implementation detail of CMake and leaves them out.
"""

BASE_OPTIONS: tuple[str, ...] = ("staticSystemInformation", "dynamicSystemInformation")
"""Options every supported CMake accepts."""

PROCESS_METRICS_MINOR = 2
"""Data minor from which ``processMetrics`` exists; older CMakes ignore a query that asks for it."""


@dataclass(frozen=True, kw_only=True)
class EnableOptions:
    """Arguments of ``longpole enable``.

    Attributes:
        build_dir: Build tree to instrument; ``None`` with ``--global``.
        everywhere: ``--global``: write the query to the user-level directory.
        remove: ``--remove``: delete the query instead of writing it.
        cmake: ``--cmake``: executable to ask about its version and paths.
    """

    build_dir: Path | None
    everywhere: bool
    remove: bool
    cmake: Path | None


@dataclass(frozen=True, kw_only=True)
class EnableResult:
    """What the command did, for the text and JSON reports.

    Attributes:
        scope: ``tree`` or ``global``.
        query: Path of the query file written or removed.
        removed: Whether this was a removal.
        callback: Command line CMake will run, or ``None`` on removal.
        cmake_version: Version of the CMake consulted.
        data_version: Data version requested in the query, as ``major.minor``.
        file_api_query: File API query file written for a build tree, or ``None``.
        regenerate_needed: The tree is already configured, so the query takes
            full effect only after the next ``cmake`` run.
    """

    scope: str
    query: Path
    removed: bool
    callback: str | None
    cmake_version: str | None
    data_version: str | None
    file_api_query: Path | None
    regenerate_needed: bool


def register(parser: argparse.ArgumentParser) -> None:
    """Declare the arguments of ``enable``.

    Args:
        parser: The command's subparser.
    """
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument(
        "build_dir",
        nargs="?",
        type=Path,
        metavar="BUILD_DIR",
        help="build tree to instrument; may not exist yet",
    )
    scope.add_argument(
        "--global",
        dest="everywhere",
        action="store_true",
        help="instrument every build of this user on this machine instead",
    )
    parser.add_argument(
        "--remove", action="store_true", help="delete the query file written earlier"
    )
    parser.add_argument(
        "--cmake",
        type=Path,
        metavar="PATH",
        help="cmake executable to consult; default: the one that configured the tree, "
        "else the first on PATH",
    )


def options_from(namespace: argparse.Namespace) -> EnableOptions:
    """Type the parsed arguments.

    Args:
        namespace: argparse's result for this command.

    Returns:
        The options.
    """
    return EnableOptions(
        build_dir=namespace.build_dir,
        everywhere=bool(namespace.everywhere),
        remove=bool(namespace.remove),
        cmake=namespace.cmake,
    )


def run() -> int:
    """Write or remove the query file and report what was done.

    Returns:
        0 on success; failures raise.

    Raises:
        CMakeToolError: No usable ``cmake`` executable could be found or run.
        UnsupportedCMakeError: The executable predates CMake 4.3.
        OSError: A query directory cannot be created or written.
    """
    options = context.options(EnableOptions)
    cmake = find_cmake(options.cmake, options.build_dir)
    if options.remove:
        result = disable(cmake, options.build_dir)
    else:
        result = enable(cmake, options.build_dir)
    if context.cli().json_output:
        output.stdout(json.dumps(_as_json(result), indent=2))
    else:
        output.stdout(_describe(result))
    return 0


def enable(cmake: Path, build_dir: Path | None) -> EnableResult:
    """Write the query file for a build tree, or for the user when ``build_dir`` is ``None``.

    Writing the same file again is harmless: the content is derived from the
    CMake version and the callback only. For a build tree the File API query
    and the in-tree store directory are created as well.

    Args:
        cmake: Executable to ask for its data version and configuration directory.
        build_dir: The tree, or ``None`` for the user-level directory.

    Returns:
        What was written.

    Raises:
        CMakeToolError: ``cmake`` cannot be run.
        UnsupportedCMakeError: ``cmake`` is older than 4.3.
        OSError: A directory cannot be created or a file written.
    """
    info = tooling.describe(cmake)
    if info.data_version is None:
        raise UnsupportedCMakeError(
            f"{cmake} is CMake {info.version}, which predates the instrumentation API; "
            "4.3 or newer is needed"
        )
    callback = callback_command()
    query = query_location(cmake, build_dir)
    query.parent.mkdir(parents=True, exist_ok=True)
    document = query_document(info.data_version, callback)
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
        removed=False,
        callback=callback,
        cmake_version=info.version,
        data_version=str(info.data_version),
        file_api_query=file_api_query,
        regenerate_needed=regenerate_needed,
    )


def disable(cmake: Path, build_dir: Path | None) -> EnableResult:
    """Delete the query file written by :func:`enable`, if it is there.

    The in-tree run store is left alone: it holds captured runs. For a build
    tree the File API query is removed too, since nothing else needs it.

    Args:
        cmake: Executable to ask for the configuration directory in global mode.
        build_dir: The tree, or ``None`` for the user-level directory.

    Returns:
        What was removed; ``removed`` is true even when nothing was there.

    Raises:
        CMakeToolError: ``cmake`` cannot be run (global mode only).
        OSError: A file exists but cannot be deleted.
    """
    query = query_location(cmake, build_dir)
    query.unlink(missing_ok=True)
    if build_dir is not None:
        fileapi.query_path(build_dir).unlink(missing_ok=True)
    log.info("removed %s", query)
    return EnableResult(
        scope="global" if build_dir is None else "tree",
        query=query,
        removed=True,
        callback=None,
        cmake_version=None,
        data_version=None,
        file_api_query=None,
        regenerate_needed=False,
    )


def find_cmake(explicit: Path | None, build_dir: Path | None) -> Path:
    """Pick the ``cmake`` to consult.

    Args:
        explicit: ``--cmake``, if given.
        build_dir: Build tree whose cache may name the configuring ``cmake``.

    Returns:
        The executable, in this order of preference: ``--cmake``, the tree's
        ``CMakeCache.txt``, the first ``cmake`` on ``PATH``.

    Raises:
        CMakeToolError: None of the three is available.
    """
    if explicit is not None:
        return explicit
    if build_dir is not None:
        configured = tooling.read_cache(build_dir).cmake_command
        if configured is not None and configured.is_file():
            return configured
    found = shutil.which("cmake")
    if found is None:
        raise CMakeToolError("no cmake executable found on PATH; pass --cmake")
    return Path(found)


def query_location(cmake: Path, build_dir: Path | None) -> Path:
    """Where our query file goes.

    Args:
        cmake: Executable to ask for the configuration directory in global mode.
        build_dir: The tree, or ``None`` for the user-level directory.

    Returns:
        ``<build>/.cmake/instrumentation/v1/query/longpole.json`` for a tree;
        ``<config dir>/instrumentation/v1/query/longpole.json`` otherwise.

    Raises:
        CMakeToolError: ``cmake --print-config-dir`` fails (global mode only).
    """
    root = build_dir / ".cmake" if build_dir is not None else tooling.config_dir(cmake)
    return root / "instrumentation" / "v1" / "query" / QUERY_FILE_NAME


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
    options = list(BASE_OPTIONS)
    if data_version.minor >= PROCESS_METRICS_MINOR:
        options.append("processMetrics")
    return {
        "version": {"major": data_version.major, "minor": data_version.minor},
        "hooks": list(HOOKS),
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
    if result.removed:
        return f"removed {result.query}"
    lines = [
        f"enabled ({result.scope}): {result.query}",
        f"callback: {result.callback}",
        f"data version: {result.data_version} (cmake {result.cmake_version})",
    ]
    if result.file_api_query is not None:
        lines.append(f"target graph: File API query at {result.file_api_query}")
    if result.regenerate_needed:
        lines.append(
            "next: this tree is already configured; run cmake again so the recording "
            "launcher is written into the build system"
        )
    return "\n".join(lines)


COMMAND = Command(
    name="enable",
    help="switch CMake instrumentation on for a build tree, or for every build with --global",
    description=(
        "Write the query file that makes CMake record every step and call "
        "`longpole capture` afterwards. With a build tree, also ask the File API for "
        "the target graph and mark the tree as having its own run store. "
        "Nothing in the project changes."
    ),
    register=register,
    options_from=options_from,
    run=run,
)
