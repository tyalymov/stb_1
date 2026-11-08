"""Registry of the commands ``longpole`` offers.

Each command lives in its own module here; this file only lists them, in
the order ``--help`` shows them. A command is a function whose signature is
its command line, or a cyclopts ``App`` when it has actions of its own.
"""

from __future__ import annotations

from collections.abc import Callable

from cyclopts import App

from longpole.commands import analyze, capture, doctor, enable, export, runs

COMMANDS: tuple[Callable[..., int] | App, ...] = (
    enable.enable,
    capture.capture,
    doctor.doctor,
    analyze.analyze,
    export.export,
    runs.app,
)
