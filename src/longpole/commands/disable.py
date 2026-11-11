"""``longpole disable``: switch the recording off again.

Deletes the query files ``longpole enable`` wrote, so CMake stops writing
snippets and calling the callback. The run store is left alone: it holds
the captured runs, which stay readable.
"""

from __future__ import annotations

import logging
from pathlib import Path

from longpole import output
from longpole.cmake import fileapi
from longpole.commands.enable import query_location
from longpole.commands.options import Output

log = logging.getLogger(__name__)


def disable(build_dir: Path, /, *, output_format: Output = "text") -> int:
    """Switch CMake instrumentation off for a build tree.

    Delete the query files 'longpole enable' wrote, the instrumentation one
    and the File API one. Captured runs stay in the store. Nothing happens
    when the files are already gone.

    Args:
        build_dir: Build tree to stop instrumenting.
        output_format: text, or json for a program.

    Returns:
        0 on success; failures raise.

    Raises:
        OSError: A file exists but cannot be deleted.
    """
    query = query_location(build_dir)
    query.unlink(missing_ok=True)
    fileapi.query_path(build_dir).unlink(missing_ok=True)
    log.info("removed %s", query)
    output.result({"query": query}, output_format, text=lambda _: f"removed {query}")
    return 0
