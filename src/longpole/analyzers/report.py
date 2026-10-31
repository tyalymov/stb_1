"""Every analysis of one run in a single object.

The exporters and ``analyze --json`` all want the same four results: the
summary, the critical path, the utilization and the artifact sizes. Bundling
them here means each exporter takes one argument, and the JSON shape is
decided in one place.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from longpole.analyzers.critical_path import CriticalPath, Unavailable, critical_path
from longpole.analyzers.sizes import ArtifactSizes, artifact_sizes
from longpole.analyzers.summary import Summary, summarize
from longpole.analyzers.utilization import Utilization, utilization
from longpole.model import Run


@dataclass(frozen=True, kw_only=True)
class Report:
    """All analyses of one run.

    Attributes:
        summary: Totals, phases and the slowest items.
        critical_path: The longest chain of targets, or why there is none.
        utilization: Parallelism over time.
        sizes: What the build produced, by size.
    """

    summary: Summary
    critical_path: CriticalPath | Unavailable
    utilization: Utilization
    sizes: ArtifactSizes


def build_report(run: Run, *, top: int = 10) -> Report:
    """Run every analyzer on a run.

    Args:
        run: The run.
        top: Rows per ranked table.

    Returns:
        The report.
    """
    return Report(
        summary=summarize(run, top=top),
        critical_path=critical_path(run),
        utilization=utilization(run),
        sizes=artifact_sizes(run, top=top),
    )


def to_json(report: Report) -> dict[str, object]:
    """Lay the report out for ``json.dumps``.

    The dataclasses become dictionaries as they are, plus the derived numbers
    a reader would otherwise have to compute: the critical path's ``share``
    and an ``available`` flag that tells a path from a reason, and the
    utilization's ``tail_share``.

    Args:
        report: The report.

    Returns:
        A JSON-ready dictionary.
    """
    data: dict[str, object] = asdict(report)
    path = dict(asdict(report.critical_path))
    path["available"] = isinstance(report.critical_path, CriticalPath)
    if isinstance(report.critical_path, CriticalPath):
        path["share"] = report.critical_path.share
    data["critical_path"] = path
    parallelism = dict(asdict(report.utilization))
    parallelism["tail_share"] = report.utilization.tail_share
    data["utilization"] = parallelism
    return data
