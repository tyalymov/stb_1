"""``longpole capture``: the callback CMake runs at each hook.

CMake calls it with the path of an index file and deletes that index and
every snippet it lists as soon as all callbacks have returned. So this
command copies: the index, the snippets and, after a configure, the File
API reply, plus a manifest with what is only knowable now (time, labels,
git state, ccache counters). From the index it takes only what the copy
needs: which files, which tree, which hook; the vendored schema is not
consulted, since a CMake newer than it writes a data minor it forbids and
the data must be stored all the same. It runs no ``cmake``; everything else
is derived when a run is read. One line on standard error says what was
stored.

An index with no snippets, which the ``postCMakeWorkflow`` hook produces
because the per-step hooks consumed everything already, records nothing.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from cyclopts import Parameter

from longpole import __version__, env, output
from longpole.cmake import fileapi
from longpole.errors import IndexFormatError
from longpole.jsonfields import FieldError, Fields, load_object
from longpole.model import Manifest
from longpole.store import RunStore, new_run_id

log = logging.getLogger(__name__)


def capture(
    index: Path,
    /,
    *,
    store: Annotated[Path, Parameter(name="--store", help="run store to write the run to")],
    label: tuple[str, ...] = (),
) -> int:
    """Store the run an instrumentation index describes; CMake runs this at each hook.

    Copy a CMake instrumentation index, its snippets and, after a configure,
    the File API reply into the store before CMake deletes the data.
    Registered as the callback by 'longpole enable', which puts the store
    and the labels on its command line; rarely run by hand. One status line
    goes to standard error.

    Args:
        index: Index file; CMake appends it to the callback.
        store: Run store to write the run to.
        label: Label the run; may be repeated.

    Returns:
        0; a skipped empty index is a success too.

    Raises:
        FileNotFoundError: The index or a snippet is already gone.
        IndexFormatError: The index does not say which files to copy.
        OSError: The store cannot be written.
    """
    try:
        document = load_object(index)
        fields = Fields(document)
        hook = fields.string("hook")
        build_dir = Path(fields.string("buildDir")).resolve()
        data_dir = Path(fields.string("dataDir"))
        snippets = [data_dir / name for name in fields.strings("snippets")]
    except FieldError as exc:
        raise IndexFormatError(f"index file {index}: {exc}; nothing to copy") from exc

    if not snippets:
        output.stderr(f"longpole: {hook}: empty index, nothing recorded")
        return 0

    now = datetime.now(UTC)
    manifest = Manifest(
        run_id=new_run_id(now),
        created_at=now.isoformat(),
        tool_version=__version__,
        hook=hook,
        build_dir=str(build_dir),
        environment=env.collect(build_dir),
        labels=label,
    )
    # The reply is rewritten by every generate, so the copy taken right after
    # one describes the tree every build until the next configure runs on.
    reply = fileapi.reply_dir(build_dir)
    directory = RunStore(store).write(
        manifest,
        index=index,
        snippets=snippets,
        reply_dir=reply if hook == "postGenerate" and reply.is_dir() else None,
    )
    log.info("stored %s as %s", hook, directory)
    count = len(snippets)
    output.stderr(
        f"longpole: {hook} recorded as {manifest.run_id}: "
        f"{count} snippet{'s' if count != 1 else ''} (longpole analyze)"
    )
    return 0
