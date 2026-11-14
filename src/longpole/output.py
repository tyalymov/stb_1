"""The one place that writes to standard output and standard error.

Standard output carries results: tables, JSON, paths. Tests and pipes parse
it, so nothing else may end up there. Standard error carries messages for a
person: the one-line status of a callback. Log records also go to standard
error, through :mod:`logging`, which ``longpole.cli`` configures.

A command hands its result to :func:`result` together with the
``--output-format`` the user chose; the choice of representation is made
here, once, and a new format is one more branch in this file.

Everything looks up the stream when called rather than at import time, so
output lands in whatever ``sys.stdout`` and ``sys.stderr`` are at that
moment; pytest's ``capsys`` swaps them per test.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Literal, TypeVar

OutputFormat = Literal["text", "json"]
"""What a command prints: ``text`` for people, ``json`` for other programs."""

T = TypeVar("T")


def result(
    value: T,
    output_format: OutputFormat,
    *,
    text: Callable[[T], str],
    data: Callable[[T], object] | None = None,
) -> None:
    """Print a command's result in the format the user asked for."""
    if output_format == "json":
        document = value if data is None else data(value)
        stdout(json.dumps(document, indent=2, default=_encode))
    else:
        rendered = text(value)
        if rendered:
            stdout(rendered)


def _encode(value: object) -> object:
    """Tell ``json.dumps`` how to write the types our results contain.

    Raises:
        TypeError: Anything else; the result is not meant for JSON.
    """
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"{type(value).__name__} is not JSON serialisable")


def stdout(text: str) -> None:
    """Write a result to standard output, ending it with a newline if it lacks one."""
    sys.stdout.write(text if text.endswith("\n") else text + "\n")


def stderr(text: str) -> None:
    """Write a message for a person to standard error, ending it with a newline if it lacks one."""
    sys.stderr.write(text if text.endswith("\n") else text + "\n")
