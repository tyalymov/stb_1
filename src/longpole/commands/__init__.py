"""Registry of the commands ``longpole`` offers.

Each command lives in its own module here; this file only lists them. The
entry point builds the argument parser from this mapping and dispatches to
``COMMANDS[name].run()`` once the configuration is published through
:mod:`longpole.context`.
"""

from __future__ import annotations

from longpole.command import Command
from longpole.commands import analyze, capture, doctor, enable, export, runs

COMMANDS: dict[str, Command] = {
    command.name: command
    for command in (
        enable.COMMAND,
        capture.COMMAND,
        doctor.COMMAND,
        analyze.COMMAND,
        export.COMMAND,
        runs.COMMAND,
    )
}
"""Commands by name, in the order ``--help`` lists them."""
