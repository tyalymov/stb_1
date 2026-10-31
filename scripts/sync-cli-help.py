"""Rewrite the module docstring of ``longpole/cli.py`` from the parser's ``--help`` text.

The docstring doubles as the program's header and a test keeps it equal to
``longpole --help``. Run this after adding a command or changing a help
string, then review the diff:

    uv run python scripts/sync-cli-help.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from longpole.cli_config import build_parser
from longpole.commands import COMMANDS

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
    updated = '"""' + build_parser(COMMANDS).format_help() + text[end:]
    if updated != text:
        CLI_MODULE.write_text(updated, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
