"""Options several commands share, declared once so their help and names agree."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from cyclopts import Parameter

from longpole.output import OutputFormat

DEFAULT_STORE = Path(".longpole")
"""Where runs go for local work when ``--store`` is not given; CI passes the path explicitly."""

Store = Annotated[
    Path,
    Parameter(name="--store", help="run store to read from and write to (default: ./.longpole)"),
]
"""``--store PATH``; the commands default it to :data:`DEFAULT_STORE`, nothing else is guessed."""

Output = Annotated[
    OutputFormat,
    Parameter(name="--output-format", help="text (the default) for a terminal, json for a program"),
]
"""``--output-format FORMAT``."""
