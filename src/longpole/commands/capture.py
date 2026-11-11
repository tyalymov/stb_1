"""``longpole capture``: the callback CMake runs at each hook.

CMake calls it with the path of an index file and deletes that index and
every snippet it lists as soon as all callbacks have returned. So this
command copies: the index, the snippets and, after a configure, the File
API reply, plus a manifest with what is only knowable now (time, labels,
git state, ccache counters). It parses nothing beyond the index's own
fields and runs no ``cmake``; everything else is derived when a run is
read. One line on standard error says what was stored.

An index with no snippets, which the ``postCMakeWorkflow`` hook produces
because the per-step hooks consumed everything already, records nothing.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from longpole import __version__, env, output
from longpole.cmake import fileapi, snippets
from longpole.commands.options import Store
from longpole.model import Manifest
from longpole.store import RunStore, new_run_id, resolve_store

log = logging.getLogger(__name__)


def capture(index: Path, /, *, label: tuple[str, ...] = (), store: Store = None) -> int:
    """Store the run an instrumentation index describes; CMake runs this at each hook.

    Copy a CMake instrumentation index, its snippets and, after a configure,
    the File API reply into the store before CMake deletes the data.
    Registered as the callback by 'longpole enable'; rarely run by hand.
    One status line goes to standard error.

    Args:
        index: Index file; CMake appends it to the callback.
        label: Label the run; may be repeated.
        store: Run store to write to instead of the resolved one.

    Returns:
        0; a skipped empty index is a success too.

    Raises:
        FileNotFoundError: The index or a snippet is already gone.
        IndexFormatError: The index is not v1 data.
        OSError: The store cannot be written.
    """
    parsed = snippets.read_index(index, validate_schema=False)
    if not parsed.snippet_paths:
        output.stderr(f"longpole: {parsed.hook}: empty index, nothing recorded")
        return 0

    build_dir = parsed.build_dir.resolve()
    now = datetime.now(UTC)
    manifest = Manifest(
        run_id=new_run_id(now),
        created_at=now.isoformat(),
        tool_version=__version__,
        hook=parsed.hook,
        build_dir=str(build_dir),
        data_version=parsed.version,
        environment=env.collect(build_dir),
        labels=label,
    )
    # The reply is rewritten by every generate, so the copy taken right after
    # one describes the tree every build until the next configure runs on.
    reply = fileapi.reply_dir(build_dir)
    directory = RunStore(resolve_store(store, build_dir=build_dir)).write(
        manifest,
        index=index,
        snippets=parsed.snippet_paths,
        reply_dir=reply if parsed.hook == "postGenerate" and reply.is_dir() else None,
    )
    log.info("stored %s as %s", parsed.hook, directory)
    count = len(parsed.snippet_paths)
    output.stderr(
        f"longpole: {parsed.hook} recorded as {manifest.run_id}: "
        f"{count} snippet{'s' if count != 1 else ''} (longpole analyze)"
    )
    return 0
