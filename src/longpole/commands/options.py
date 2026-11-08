"""Options several commands share, declared once so their help and names agree."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from cyclopts import Parameter

Store = Annotated[
    Path | None,
    Parameter(
        name="--store",
        help="run store to use instead of the one resolved from the build tree and environment",
    ),
]
"""``--store PATH``; ``None`` leaves the choice to :func:`longpole.store.resolve_store`."""

Json = Annotated[bool, Parameter(name="--json", help="print results as JSON instead of tables")]
"""``--json``."""
