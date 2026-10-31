"""What a subcommand looks like to the entry point.

Each command is a module under :mod:`longpole.commands` that exposes one
:class:`Command`. The entry point only needs three things from it: how to
declare its arguments, how to turn the parsed arguments into the command's
own typed options object, and how to run. Keeping the three together in one
record lets the registry in ``commands/__init__.py`` stay a plain mapping.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, kw_only=True)
class Command:
    """One subcommand of the ``longpole`` program.

    Attributes:
        name: The word on the command line, e.g. ``enable``.
        help: One line for the command list in ``longpole --help``.
        description: A paragraph for ``longpole <name> --help``.
        register: Adds the command's own arguments to its subparser.
        options_from: Turns the parsed namespace into the command's frozen
            options object. The command's ``run`` reads it back with
            :func:`longpole.context.options`.
        run: Does the work, reading :func:`longpole.context.cli` and
            :func:`longpole.context.env`. Returns the process exit code.
    """

    name: str
    help: str
    description: str
    register: Callable[[argparse.ArgumentParser], None]
    options_from: Callable[[argparse.Namespace], object]
    run: Callable[[], int]
