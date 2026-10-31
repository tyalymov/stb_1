"""Tables for the terminal, rendered to plain text.

The command layer writes whatever comes back from here to standard output.
Rendering goes through a ``rich`` console that writes into a string with a
fixed width and no colour, so the same text appears in a terminal, in a
pipe and in a test. Colour would be nice in a real terminal; it can come
later without changing any caller, because callers only see text.
"""

from __future__ import annotations

import io
import os
from collections.abc import Iterable, Sequence
from pathlib import Path

from rich.console import Console, Group, RenderableType
from rich.table import Table
from rich.text import Text

from longpole import units
from longpole.analyzers.critical_path import CriticalPath, Unavailable
from longpole.analyzers.report import Report
from longpole.analyzers.sizes import ArtifactSizes
from longpole.analyzers.summary import Entry, Summary
from longpole.analyzers.utilization import Utilization

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
    show_header: bool = True,
) -> RenderableType:
    """Build a table, optionally captioned.

    The caption is a line of its own above the table rather than rich's
    table title, which wraps to the table's width and breaks long paths
    across lines.

    Args:
        title: Caption above the table, or ``None``.
        columns: Header cells.
        rows: Body rows; each must have one cell per column.
        right_aligned: Indexes of columns holding numbers, which read better
            right-aligned.
        show_header: Whether to print the header row.

    Returns:
        The table, ready for :func:`render`.
    """
    aligned = set(right_aligned)
    built = Table(show_edge=False, pad_edge=False, show_header=show_header)
    for index, column in enumerate(columns):
        built.add_column(column, justify="right" if index in aligned else "left", no_wrap=False)
    for row in rows:
        built.add_row(*row)
    if title is None:
        return built
    return Group(Text(title), built)


def format_summary(summary: Summary) -> str:
    """Render a run summary: header, totals, phases and the slowest-items tables.

    Args:
        summary: The summary.

    Returns:
        The text.
    """
    totals = summary.totals
    header = (
        f"run {summary.run_id}  {summary.hook}  {summary.created_at}\n"
        f"build dir: {summary.build_dir}\n"
    )
    failed = f", {totals.failed_count} failed" if totals.failed_count else ""
    renderables: list[RenderableType] = [
        table(
            None,
            ("", ""),
            [
                ("span", units.duration(totals.span_ms)),
                ("busy", f"{units.duration(totals.busy_ms)} across the leaf steps"),
                ("steps", f"{totals.step_count}{failed}"),
            ],
            show_header=False,
        )
    ]
    if summary.phases:
        busy = max(totals.busy_ms, 1)
        renderables.append(Text(""))
        renderables.append(
            table(
                "phases",
                ("phase", "steps", "total", "share"),
                [
                    (
                        p.role,
                        str(p.count),
                        units.duration(p.total_ms),
                        f"{100 * p.total_ms / busy:.0f}%",
                    )
                    for p in summary.phases
                ],
                right_aligned=(1, 2, 3),
            )
        )
    for title, entries in (
        ("slowest targets", summary.slowest_targets),
        ("slowest sources", summary.slowest_sources),
        ("slowest custom commands", summary.slowest_custom),
        ("slowest tests", summary.slowest_tests),
    ):
        if entries:
            renderables.append(Text(""))
            renderables.append(_entries_table(title, entries))
    return header + render(*renderables)


def _entries_table(title: str, entries: Sequence[Entry]) -> RenderableType:
    """A slowest-items table with long common path prefixes folded into the title.

    Args:
        title: Table caption.
        entries: Rows, already ranked.

    Returns:
        The table.
    """
    prefix, names = _strip_common_dir([entry.name for entry in entries])
    caption = f"{title} (under {prefix})" if prefix else title
    return table(
        caption,
        ("name", "time", "steps", "detail"),
        [
            (name, units.duration(entry.total_ms), str(entry.count), entry.detail)
            for name, entry in zip(names, entries, strict=True)
        ],
        right_aligned=(1, 2),
    )


def _strip_common_dir(names: Sequence[str]) -> tuple[str | None, list[str]]:
    """Remove the directory every absolute path in a list shares.

    Args:
        names: Row names; only a list of two or more absolute paths is shortened.

    Returns:
        The removed prefix (or ``None`` when nothing was removed) and the names
        to display.
    """
    if len(names) < 2 or not all(Path(name).is_absolute() for name in names):
        return None, list(names)
    common = os.path.commonpath(names)
    if common in ("", os.sep):
        return None, list(names)
    return common, [os.path.relpath(name, common) for name in names]


def format_report(report: Report) -> str:
    """Render the whole report: summary, then critical path, parallelism and artifact sizes.

    Args:
        report: The report.

    Returns:
        The text.
    """
    return (
        format_summary(report.summary)
        + "\n"
        + render(
            critical_path_table(report.critical_path),
            Text(""),
            utilization_lines(report.utilization),
            Text(""),
            *sizes_tables(report.sizes),
        )
    )


def critical_path_table(path: CriticalPath | Unavailable) -> RenderableType:
    """The critical path as a table of targets, or the reason there is none.

    Args:
        path: The analyzer's result.

    Returns:
        The table, captioned with the lower bound and its share of the span,
        followed by the unattributed custom commands when there are any; or
        one line with the reason.
    """
    if isinstance(path, Unavailable):
        return Text(f"critical path: {path.reason}")
    caption = (
        f"critical path: {units.duration(path.lower_bound_ms)} lower bound, "
        f"{path.share:.0%} of the {units.duration(path.span_ms)} span"
    )
    chain = table(
        caption,
        ("target", "total", "custom", "compile", "link"),
        [
            (
                target.name,
                units.duration(target.total_ms),
                units.duration(target.custom_ms),
                units.duration(target.compile_ms),
                units.duration(target.link_ms),
            )
            for target in path.chain
        ],
        right_aligned=(1, 2, 3, 4),
    )
    if not path.unattributed:
        return chain
    orphans = table(
        "custom commands outside any target (not on the path)",
        ("outputs", "time"),
        [(item.name, units.duration(item.duration_ms)) for item in path.unattributed],
        right_aligned=(1,),
    )
    return Group(chain, Text(""), orphans)


def utilization_lines(parallelism: Utilization) -> RenderableType:
    """Parallelism in three lines.

    Args:
        parallelism: The analyzer's result.

    Returns:
        The measured numbers and the recommendation.
    """
    cores = f" of {parallelism.cpu_count} cores" if parallelism.cpu_count is not None else ""
    return Text(
        f"parallelism: peak {parallelism.peak_parallelism}{cores}, "
        f"average {parallelism.average_parallelism:.1f}\n"
        f"active {units.duration(parallelism.active_ms)}, idle "
        f"{units.duration(parallelism.idle_ms)}, single-step tail "
        f"{units.duration(parallelism.tail_ms)} ({parallelism.tail_share:.0%})\n"
        f"{parallelism.recommendation}"
    )


def sizes_tables(sizes: ArtifactSizes) -> list[RenderableType]:
    """Artifact sizes: a total line, the kinds, and the largest files.

    Args:
        sizes: The analyzer's result.

    Returns:
        Renderables; a single line when the run recorded no artifacts.
    """
    if not sizes.by_kind:
        return [Text("artifacts: none recorded")]
    count = sum(total.count for total in sizes.by_kind)
    renderables: list[RenderableType] = [
        table(
            f"artifacts: {units.size(sizes.total_bytes)} in {count} files",
            ("kind", "files", "size"),
            [
                (total.kind, str(total.count), units.size(total.total_bytes))
                for total in sizes.by_kind
            ],
            right_aligned=(1, 2),
        )
    ]
    if sizes.largest:
        renderables.append(Text(""))
        renderables.append(
            table(
                "largest artifacts",
                ("path", "size", "kind", "target"),
                [
                    (
                        artifact.path,
                        units.size(artifact.size_bytes),
                        artifact.kind,
                        artifact.target or "",
                    )
                    for artifact in sizes.largest
                ],
                right_aligned=(1,),
            )
        )
    return renderables
