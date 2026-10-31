"""Instrumentation callback used by the tests: copy an index and its snippets aside.

CMake deletes the index file and every snippet it lists as soon as all
callbacks have returned, so a test cannot inspect them afterwards. This
script is registered as the callback instead; it copies everything into a
numbered directory under the capture root, and the test reads the copies.

Usage::

    capture_stub.py <capture-root> <index-path>

CMake appends ``<index-path>`` itself; the test supplies ``<capture-root>``.
"""

from __future__ import annotations

import json
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path


def main(argv: Sequence[str] | None = None) -> int:
    """Copy one index file and the snippets it references into the capture root.

    Each invocation creates the next numbered directory (``000``, ``001``, ...)
    containing ``index.json``, ``argv.json`` with the exact arguments CMake
    passed, and ``snippets/`` with a copy of every listed snippet. Nothing in
    the build tree is modified; CMake's own cleanup runs after we return.

    Args:
        argv: ``[capture_root, index_path]``; ``None`` means ``sys.argv[1:]``.

    Returns:
        0 on success, 2 when the arguments are missing.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        sys.stderr.write(f"usage: {Path(__file__).name} <capture-root> <index-path>\n")
        return 2
    capture_root, index_path = Path(args[0]), Path(args[1])

    capture_root.mkdir(parents=True, exist_ok=True)
    entry = capture_root / f"{len(list(capture_root.iterdir())):03d}"
    snippets_dir = entry / "snippets"
    snippets_dir.mkdir(parents=True)

    index = json.loads(index_path.read_text(encoding="utf-8"))
    shutil.copy2(index_path, entry / "index.json")
    (entry / "argv.json").write_text(json.dumps(args), encoding="utf-8")

    data_dir = Path(index["dataDir"])
    for relative in index.get("snippets", []):
        shutil.copy2(data_dir / relative, snippets_dir / Path(relative).name)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
