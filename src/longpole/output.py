"""The one place that writes to standard output and standard error.

Standard output carries results: tables, JSON, paths. Tests and pipes parse
it, so nothing else may end up there. Standard error carries messages for a
person: usage errors, the one-line status of a callback. Log records also go
to standard error, through :mod:`logging`, which ``longpole.cli`` configures.

Both functions look up the stream when called rather than at import time,
so output lands in whatever ``sys.stdout`` and ``sys.stderr`` are at that
moment; pytest's ``capsys`` swaps them per test.
"""

from __future__ import annotations

import sys


def stdout(text: str) -> None:
    """Write a result to standard output, ending it with a newline if it lacks one.

    Args:
        text: The text; an empty string writes a bare newline.
    """
    sys.stdout.write(text if text.endswith("\n") else text + "\n")


def stderr(text: str) -> None:
    """Write a message for a person to standard error, ending it with a newline if it lacks one.

    Args:
        text: The text; an empty string writes a bare newline.
    """
    sys.stderr.write(text if text.endswith("\n") else text + "\n")
