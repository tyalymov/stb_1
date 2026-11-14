"""Sizes of what the build produced: object files, libraries, executables.

CMake records the size of every output a step wrote, so a run already
contains a complete picture of the build's artifacts at no extra cost. This
module arranges those numbers by kind of artifact, by target and by size,
so that a report can say what is large and a diff can say what grew.

Artifact kinds come from the target graph when it is present: the File API
knows whether a target is an executable or a library, which is more
reliable than reading file extensions. Without a graph the extension
decides.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import PurePosixPath

from longpole.model import Graph, Run, Step

OBJECT = "object"
LIBRARY = "library"
EXECUTABLE = "executable"
GENERATED = "generated"
KIND_ORDER: tuple[str, ...] = (EXECUTABLE, LIBRARY, OBJECT, GENERATED)
"""Display order of artifact kinds, the ones people ship first."""

_TARGET_TYPE_KINDS = {
    "EXECUTABLE": EXECUTABLE,
    "SHARED_LIBRARY": LIBRARY,
    "STATIC_LIBRARY": LIBRARY,
    "MODULE_LIBRARY": LIBRARY,
}
_LIBRARY_SUFFIXES = frozenset({".a", ".lib", ".dylib", ".dll", ".so"})


@dataclass(frozen=True, kw_only=True)
class Artifact:
    """One output file with its size.

    Attributes:
        path: Path as CMake recorded it, relative to the step's working directory.
        size_bytes: Size when the step finished.
        kind: One of :data:`KIND_ORDER`.
        target: Target the producing step belonged to, if CMake recorded one.
    """

    path: str
    size_bytes: int
    kind: str
    target: str | None


@dataclass(frozen=True, kw_only=True)
class KindTotal:
    """Artifacts of one kind added up.

    Attributes:
        kind: The kind.
        count: How many artifacts.
        total_bytes: Their sizes added up.
    """

    kind: str
    count: int
    total_bytes: int


@dataclass(frozen=True, kw_only=True)
class TargetTotal:
    """Artifacts of one target added up, objects included.

    Attributes:
        target: Target name.
        count: How many artifacts.
        total_bytes: Their sizes added up.
    """

    target: str
    count: int
    total_bytes: int


@dataclass(frozen=True, kw_only=True)
class ArtifactSizes:
    """Everything a run produced, by size.

    Attributes:
        total_bytes: Every artifact added up, each counted once.
        by_kind: Totals per kind in :data:`KIND_ORDER`; only kinds present.
        by_target: Totals per target, largest first, limited to the top rows.
        largest: The biggest artifacts, largest first, limited to the top rows.
    """

    total_bytes: int
    by_kind: tuple[KindTotal, ...]
    by_target: tuple[TargetTotal, ...]
    largest: tuple[Artifact, ...]


def artifact_sizes(run: Run, *, top: int = 10) -> ArtifactSizes:
    """Collect the artifacts of a run and rank them.

    Pure: reads the run, touches nothing else. An output written by several
    steps, as a static library is by ``ar`` and then ``ranlib``, counts once,
    with the size the last step left it at.
    """
    artifacts = _artifacts(run.steps, run.graph)
    kind_counts: Counter[str] = Counter()
    kind_bytes: Counter[str] = Counter()
    target_counts: Counter[str] = Counter()
    target_bytes: Counter[str] = Counter()
    for artifact in artifacts:
        kind_counts[artifact.kind] += 1
        kind_bytes[artifact.kind] += artifact.size_bytes
        if artifact.target is not None:
            target_counts[artifact.target] += 1
            target_bytes[artifact.target] += artifact.size_bytes
    by_target = sorted(
        (
            TargetTotal(target=name, count=target_counts[name], total_bytes=target_bytes[name])
            for name in target_counts
        ),
        key=lambda total: (-total.total_bytes, total.target),
    )
    largest = sorted(artifacts, key=lambda artifact: (-artifact.size_bytes, artifact.path))
    return ArtifactSizes(
        total_bytes=sum(artifact.size_bytes for artifact in artifacts),
        by_kind=tuple(
            KindTotal(kind=kind, count=kind_counts[kind], total_bytes=kind_bytes[kind])
            for kind in KIND_ORDER
            if kind in kind_counts
        ),
        by_target=tuple(by_target[:top]),
        largest=tuple(largest[:top]),
    )


def _artifacts(steps: tuple[Step, ...], graph: Graph | None) -> list[Artifact]:
    """Collect one artifact per distinct output path."""
    target_types = {target.name: target.type for target in graph.targets} if graph else {}
    seen: dict[tuple[str, str], Artifact] = {}
    for step in steps:
        for path, size in zip(step.outputs, step.output_sizes, strict=False):
            key = (step.working_dir, path)
            seen[key] = Artifact(
                path=path,
                size_bytes=size,
                kind=_kind(step, path, target_types),
                target=step.target,
            )
    return list(seen.values())


def _kind(step: Step, path: str, target_types: dict[str, str]) -> str:
    """Classify an output."""
    if step.role == "compile":
        return OBJECT
    if step.role != "link":
        return GENERATED
    if step.target is not None and step.target in target_types:
        return _TARGET_TYPE_KINDS.get(target_types[step.target], LIBRARY)
    suffixes = PurePosixPath(path).suffixes
    if any(suffix in _LIBRARY_SUFFIXES for suffix in suffixes):
        return LIBRARY
    return EXECUTABLE
