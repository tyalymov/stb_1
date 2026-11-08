"""Rewrite the module docstring of ``longpole/cli.py`` from the ``--help`` text.

The docstring doubles as the program's header and a test keeps it equal to
``longpole --help``. Run this after adding a command or changing a help
string, then review the diff:

    uv run python scripts/sync-cli-help.py
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

from rich.console import Console

from longpole import cli

CLI_MODULE = Path(__file__).resolve().parents[1] / "src" / "longpole" / "cli.py"


def main() -> int:
    """Replace the docstring at the top of the module with the current help text.

    Returns:
        0 when the file was rewritten or already current; 1 when the module
        does not start with a docstring.
    """
    text = CLI_MODULE.read_text(encoding="utf-8")
    if not text.startswith('"""'):
        sys.stderr.write(f"{CLI_MODULE}: expected a module docstring first\n")
        return 1
    end = text.index('"""', 3)
    updated = '"""' + help_text() + text[end:]
    if updated != text:
        CLI_MODULE.write_text(updated, encoding="utf-8")
    return 0


def help_text() -> str:
    """Render ``longpole --help`` at the app's width, without the trailing spaces rich pads with.

    Returns:
        The help text.
    """
    buffer = io.StringIO()
    cli.app.meta(["--help"], console=Console(file=buffer, width=cli.app.console.width))
    return "".join(line.rstrip() + "\n" for line in buffer.getvalue().splitlines())


if __name__ == "__main__":
    sys.exit(main())
