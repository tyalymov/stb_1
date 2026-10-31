"""Tables for the terminal, rendered to plain text.

The command layer writes whatever comes back from here to standard output.
Rendering goes through a ``rich`` console that writes into a string with a
fixed width and no colour, so the same text appears in a terminal, in a
pipe and in a test. Colour would be nice in a real terminal; it can come
later without changing any caller, because callers only see text.
"""

from __future__ import annotations

import io
from collections.abc import Iterable, Sequence

from rich.console import Console, RenderableType
from rich.table import Table

WIDTH = 100
"""Columns the output is wrapped to."""


def render(*renderables: RenderableType) -> str:
    """Render ``rich`` objects to plain text.

    Args:
        *renderables: Tables or other renderables, printed one after another.

    Returns:
        The text, without colour codes, ending with a newline.
    """
    buffer = io.StringIO()
    console = Console(file=buffer, width=WIDTH, force_terminal=False, color_system=None)
    for renderable in renderables:
        console.print(renderable)
    return buffer.getvalue()


def table(
    title: str | None,
    columns: Sequence[str],
    rows: Iterable[Sequence[str]],
    *,
    right_aligned: Iterable[int] = (),
) -> Table:
    """Build a table with a header row.

    Args:
        title: Caption above the table, or ``None``.
        columns: Header cells.
        rows: Body rows; each must have one cell per column.
        right_aligned: Indexes of columns holding numbers, which read better
            right-aligned.

    Returns:
        The table, ready for :func:`render`.
    """
    aligned = set(right_aligned)
    built = Table(title=title, title_justify="left", show_edge=False, pad_edge=False)
    for index, column in enumerate(columns):
        built.add_column(column, justify="right" if index in aligned else "left", no_wrap=False)
    for row in rows:
        built.add_row(*row)
    return built
