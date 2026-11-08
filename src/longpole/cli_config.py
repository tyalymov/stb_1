"""The global options of ``longpole``, the ones every command may need.

``longpole [global options] <command> [its options]``, like git. The global
options are verbosity, which run store to use and whether to print JSON;
each command declares its own options in its function signature under
:mod:`longpole.commands`. :func:`longpole.cli.main` parses the global
options once and publishes them through :mod:`longpole.context`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

LogLevel = Literal["debug", "info", "warning", "error"]
"""Accepted values of ``--log-level``, quietest last."""


@dataclass(frozen=True, kw_only=True)
class CliConfig:
    """The parsed global options.

    Attributes:
        log_level: One of :data:`LogLevel`.
        store: Run store from ``--store``, or ``None`` to resolve it by the
            usual rule.
        json_output: Whether results go out as JSON instead of tables.
    """

    log_level: LogLevel
    store: Path | None
    json_output: bool
