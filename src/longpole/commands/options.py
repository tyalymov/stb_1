"""Options several commands share, declared once so their help and names agree."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from cyclopts import Parameter

from longpole.output import OutputFormat

Store = Annotated[Path, Parameter(name="--store", help="run store to read from and write to")]
"""``--store PATH``, required everywhere: no command guesses where the runs are."""

Output = Annotated[
    OutputFormat,
    Parameter(name="--output-format", help="text (the default) for a terminal, json for a program"),
]
"""``--output-format FORMAT``."""
