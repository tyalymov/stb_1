"""``longpole disable``: switch the recording off again.

Deletes the query file ``longpole enable`` wrote, so CMake stops writing
snippets and calling the callback. The run store is left alone: it holds
the captured runs, which stay readable.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

from cyclopts import Parameter

from longpole import output
from longpole.cmake import fileapi
from longpole.commands.enable import query_location
from longpole.commands.options import Output
from longpole.errors import UsageError

log = logging.getLogger(__name__)


def disable(
    build_dir: Path | None = None,
    /,
    *,
    everywhere: Annotated[bool, Parameter(name="--global")] = False,
    output_format: Output = "text",
) -> int:
    """Switch CMake instrumentation off for a build tree, or for every build with --global.

    Delete the query file 'longpole enable' wrote; for a build tree, the
    File API query too. Captured runs stay in the store. Nothing happens
    when the files are already gone.

    Args:
        build_dir: Build tree to stop instrumenting.
        everywhere: Stop instrumenting every build of this user on this machine instead.
        output_format: text, or json for a program.

    Returns:
        0 on success; failures raise.

    Raises:
        UsageError: Neither a build tree nor ``--global`` was given, or both were.
        CMakeToolError: ``--global`` and there is no working ``cmake`` on PATH
            to ask for its configuration directory.
        OSError: A file exists but cannot be deleted.
    """
    if (build_dir is None) == (not everywhere):
        raise UsageError("disable: give a build tree or --global, not both and not neither")
    query = query_location(build_dir)
    query.unlink(missing_ok=True)
    if build_dir is not None:
        fileapi.query_path(build_dir).unlink(missing_ok=True)
    log.info("removed %s", query)
    scope = "global" if build_dir is None else "tree"
    output.result(
        {"scope": scope, "query": query}, output_format, text=lambda _: f"removed {query}"
    )
    return 0
