"""A run as a Markdown snapshot for a CI job summary or a check run.

GitHub renders a job summary and a check run's text as Markdown with
tables, collapsible ``<details>`` blocks and Mermaid diagrams, and caps
each at about a megabyte. The snapshot leads with the numbers that decide
whether to look further, draws the critical path as a Gantt chart, and
folds the long tables away. When the whole thing would not fit, the tables
shrink until it does, and a note says so.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from longpole import units
from longpole.analyzers.critical_path import CriticalPath, Unavailable
from longpole.analyzers.report import Report
from longpole.analyzers.summary import Entry

JOB_SUMMARY_LIMIT = 1_000_000
"""Bytes GitHub accepts in one step's job summary."""

DEFAULT_LIMIT = 900_000
"""Bytes the snapshot stays under by default, leaving room for whatever surrounds it."""

_ROW_BUDGETS: tuple[int | None, ...] = (None, 10, 5, 3, 1, 0)
"""Rows per table to try until the document fits the limit: all of them first, then fewer."""


def format_markdown(report: Report, *, limit_bytes: int = DEFAULT_LIMIT) -> str:
    """Render the snapshot of one run.

    Args:
        report: The report.
        limit_bytes: Size the UTF-8 text must stay under; tables lose rows
            until it does.

    Returns:
        The Markdown text, ending with a newline.
    """
    for rows in _ROW_BUDGETS[:-1]:
        text = _render(report, rows=rows, truncated=rows is not None)
        if len(text.encode("utf-8")) <= limit_bytes:
            return text
    return _render(report, rows=_ROW_BUDGETS[-1], truncated=True)


def _render(report: Report, *, rows: int | None, truncated: bool) -> str:
    """Render with a given number of rows per table.

    Args:
        report: The report.
        rows: Rows each ranked table keeps; ``None`` keeps them all, 0 drops
            the tables.
        truncated: Whether to say that tables were shortened.

    Returns:
        The text.
    """
    summary = report.summary
    totals = summary.totals
    parts: list[str] = [
        f"## longpole: {summary.hook} `{summary.run_id}`",
        "",
        f"`{summary.build_dir}` at {summary.created_at}",
        "",
        _headline(report),
        "",
    ]
    if summary.phases:
        busy = max(totals.busy_ms, 1)
        parts.extend(
            _table(
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
            )
        )
        parts.append("")
    parts.extend(_critical_path_section(report.critical_path, rows))
    parts.extend(_parallelism_section(report))
    parts.extend(_sizes_section(report, rows))
    if rows != 0:
        for title, entries in (
            ("Slowest targets", summary.slowest_targets),
            ("Slowest sources", summary.slowest_sources),
            ("Slowest custom commands", summary.slowest_custom),
            ("Slowest tests", summary.slowest_tests),
        ):
            if entries:
                parts.extend(_entries_details(title, entries[:rows]))
    if truncated:
        parts.append("_Tables shortened to fit the size limit; `longpole analyze` has the rest._")
        parts.append("")
    return "\n".join(parts).rstrip("\n") + "\n"


def _headline(report: Report) -> str:
    """The one line that says whether to read on.

    Args:
        report: The report.

    Returns:
        Span, busy time, step count, critical path and peak parallelism.
    """
    totals = report.summary.totals
    pieces = [
        f"**{units.duration(totals.span_ms)}** wall clock",
        f"{units.duration(totals.busy_ms)} busy",
        f"{totals.step_count} steps"
        + (f" ({totals.failed_count} failed)" if totals.failed_count else ""),
    ]
    path = report.critical_path
    if isinstance(path, CriticalPath):
        pieces.append(
            f"critical path {units.duration(path.lower_bound_ms)} ({path.share:.0%} of the span)"
        )
    cores = report.utilization.cpu_count
    peak = report.utilization.peak_parallelism
    pieces.append(f"peak parallelism {peak}" + (f" of {cores} cores" if cores is not None else ""))
    return " · ".join(pieces)


def _critical_path_section(path: CriticalPath | Unavailable, rows: int | None) -> list[str]:
    """The critical path as a Gantt chart plus its table, or the reason there is none.

    Args:
        path: The analyzer's result.
        rows: Rows the unattributed list keeps; ``None`` all, 0 drops it.

    Returns:
        Lines of Markdown.
    """
    lines = ["### Critical path", ""]
    if isinstance(path, Unavailable):
        lines.extend([path.reason[:1].upper() + path.reason[1:] + ".", ""])
        return lines
    lines.append(
        f"{units.duration(path.lower_bound_ms)} lower bound, {path.share:.0%} of the "
        f"{units.duration(path.span_ms)} span. The build cannot finish faster than this chain "
        "without changing what depends on what."
    )
    lines.append("")
    lines.extend(_gantt(path))
    lines.append("")
    lines.extend(
        _table(
            ("target", "step", "time", "what"),
            [
                (f"`{s.target}`", s.kind, units.duration(s.duration_ms), f"`{s.detail}`")
                for s in path.chain
            ],
        )
    )
    lines.append("")
    if path.unattributed and rows != 0:
        lines.append("<details><summary>Custom commands outside any target</summary>")
        lines.append("")
        lines.extend(
            _table(
                ("outputs", "time"),
                [(f"`{u.name}`", units.duration(u.duration_ms)) for u in path.unattributed[:rows]],
            )
        )
        lines.extend(["", "</details>", ""])
    return lines


def _gantt(path: CriticalPath) -> list[str]:
    """Draw the chain as a Mermaid Gantt chart, one bar per segment, end to end.

    Mermaid reads ``dateFormat x`` as milliseconds since the epoch; the bars
    sit at the segments' model times, which start at zero, and the axis
    shows minutes and seconds.

    Args:
        path: The critical path.

    Returns:
        The fenced code block.
    """
    lines = ["```mermaid", "gantt", "    dateFormat x", "    axisFormat %M:%S", "    section chain"]
    for index, segment in enumerate(path.chain):
        label = f"{segment.target} {segment.kind} ({units.duration(segment.duration_ms)})"
        label = label.replace(":", " ")
        lines.append(f"    {label} :t{index}, {segment.start_ms}, {segment.end_ms}")
    lines.append("```")
    return lines


def _parallelism_section(report: Report) -> list[str]:
    """Three sentences on parallelism.

    Args:
        report: The report.

    Returns:
        Lines of Markdown.
    """
    u = report.utilization
    cores = f" of {u.cpu_count} cores" if u.cpu_count is not None else ""
    return [
        "### Parallelism",
        "",
        f"Peak {u.peak_parallelism}{cores}, average {u.average_parallelism:.1f}. Active "
        f"{units.duration(u.active_ms)}, idle {units.duration(u.idle_ms)}, single-step tail "
        f"{units.duration(u.tail_ms)} ({u.tail_share:.0%}). {u.recommendation}",
        "",
    ]


def _sizes_section(report: Report, rows: int | None) -> list[str]:
    """Artifact sizes by kind, with the largest files folded away.

    Args:
        report: The report.
        rows: Rows the largest-artifacts table keeps; ``None`` all, 0 drops it.

    Returns:
        Lines of Markdown.
    """
    sizes = report.sizes
    lines = ["### Artifacts", ""]
    if not sizes.by_kind:
        lines.extend(["No artifact sizes were recorded.", ""])
        return lines
    count = sum(total.count for total in sizes.by_kind)
    lines.extend([f"{units.size(sizes.total_bytes)} in {count} files.", ""])
    lines.extend(
        _table(
            ("kind", "files", "size"),
            [(t.kind, str(t.count), units.size(t.total_bytes)) for t in sizes.by_kind],
        )
    )
    lines.append("")
    if sizes.largest and rows != 0:
        lines.append("<details><summary>Largest artifacts</summary>")
        lines.append("")
        lines.extend(
            _table(
                ("path", "size", "kind", "target"),
                [
                    (
                        f"`{a.path}`",
                        units.size(a.size_bytes),
                        a.kind,
                        f"`{a.target}`" if a.target else "",
                    )
                    for a in sizes.largest[:rows]
                ],
            )
        )
        lines.extend(["", "</details>", ""])
    return lines


def _entries_details(title: str, entries: Sequence[Entry]) -> list[str]:
    """A ranked table inside a collapsible block.

    Args:
        title: The block's summary line.
        entries: Rows, already ranked and cut.

    Returns:
        Lines of Markdown.
    """
    lines = [f"<details><summary>{title}</summary>", ""]
    lines.extend(
        _table(
            ("name", "time", "steps", "detail"),
            [(f"`{e.name}`", units.duration(e.total_ms), str(e.count), e.detail) for e in entries],
        )
    )
    lines.extend(["", "</details>", ""])
    return lines


def _table(columns: Sequence[str], rows: Iterable[Sequence[str]]) -> list[str]:
    """A GitHub-flavoured Markdown table.

    Pipes inside cells are escaped so a path with one cannot break the row.

    Args:
        columns: Header cells.
        rows: Body rows, one cell per column.

    Returns:
        The table's lines.
    """
    lines = ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
    for row in rows:
        lines.append("| " + " | ".join(cell.replace("|", "\\|") for cell in row) + " |")
    return lines
