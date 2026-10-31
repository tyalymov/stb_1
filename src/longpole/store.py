"""Where run dumps live and how they are found again.

A store is a directory with ``runs/<run_id>/`` dumps, a ``runs.jsonl`` of
one-line summaries for fast listing, and a ``latest`` file naming the newest
run. There are two kinds of store: one inside a build tree
(``<build>/.longpole``) and one central per user; :func:`resolve_store`
picks between them by a fixed rule so every command and the capture callback
agree without the path being written into any query file.

Runs are addressed by references: a full or prefix id, ``latest``,
``latest~N``, ``sha:<prefix>`` of the captured commit, ``label:<name>``, or
a path to a dump directory.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import platformdirs

from longpole import dump
from longpole.errors import UnknownRunError
from longpole.jsonfields import FieldError, Fields, Json
from longpole.model import Run

STORE_ENV = "LONGPOLE_STORE"
"""Environment variable naming the store, second in precedence after an explicit path."""

IN_TREE = ".longpole"
"""Store directory name inside a build tree."""

_LATEST_PATTERN = re.compile(r"^latest(?:~(\d+))?$")


@dataclass(frozen=True, kw_only=True)
class RunSummary:
    """The line ``runs.jsonl`` keeps per run, enough to list and resolve without opening dumps.

    Attributes:
        run_id: Store id of the run.
        created_at: Capture time, ISO 8601 UTC.
        hook: Hook that produced the run.
        build_dir: Build tree the run came from.
        git_sha: Commit of the sources, or ``None``.
        labels: The run's labels.
        step_count: Number of steps.
        span_ms: Wall-clock span of the steps.
    """

    run_id: str
    created_at: str
    hook: str
    build_dir: str
    git_sha: str | None
    labels: tuple[str, ...]
    step_count: int
    span_ms: int

    @classmethod
    def of(cls, run: Run) -> RunSummary:
        """Summarise a run.

        Args:
            run: The run.

        Returns:
            Its summary.
        """
        git = run.manifest.environment.git
        return cls(
            run_id=run.manifest.run_id,
            created_at=run.manifest.created_at,
            hook=run.manifest.hook,
            build_dir=run.manifest.build_dir,
            git_sha=git.sha if git is not None else None,
            labels=run.manifest.labels,
            step_count=len(run.steps),
            span_ms=run.span_ms,
        )


def new_run_id(now: datetime | None = None) -> str:
    """Make a run id that sorts by capture time.

    Args:
        now: Capture time; defaults to the current UTC time.

    Returns:
        ``YYYYMMDDTHHMMSSZ-xxxxxx`` with six random hex digits.
    """
    moment = now if now is not None else datetime.now(UTC)
    return f"{moment.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(3)}"


def resolve_store(
    explicit: Path | None = None,
    *,
    build_dir: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Decide which store a command or callback uses.

    The rule, in order: an explicit path; the ``LONGPOLE_STORE`` variable; a
    ``.longpole`` directory inside the given build tree; the user's central
    data directory (``~/.local/share/longpole`` on Linux,
    ``~/Library/Application Support/longpole`` on macOS). The directory need
    not exist yet.

    Args:
        explicit: Path from ``--store``, if given.
        build_dir: Build tree whose in-tree store applies, if it has one.
        environ: Environment to read; defaults to the process environment.

    Returns:
        The store directory.
    """
    env = os.environ if environ is None else environ
    if explicit is not None:
        return explicit
    configured = env.get(STORE_ENV)
    if configured:
        return Path(configured)
    if build_dir is not None and (build_dir / IN_TREE).is_dir():
        return build_dir / IN_TREE
    return platformdirs.user_data_path("longpole")


class RunStore:
    """Dumps on disk, addressable by reference.

    Attributes:
        root: The store directory.
    """

    root: Path

    def __init__(self, root: Path) -> None:
        """Open a store; nothing is created until the first write.

        Args:
            root: The store directory.
        """
        self.root = root

    @property
    def runs_dir(self) -> Path:
        """Directory holding one subdirectory per run."""
        return self.root / "runs"

    @property
    def _summaries_path(self) -> Path:
        """``runs.jsonl``."""
        return self.root / "runs.jsonl"

    @property
    def _latest_path(self) -> Path:
        """File naming the newest run id."""
        return self.root / "latest"

    def write(
        self,
        run: Run,
        *,
        raw_index: Path | None = None,
        raw_snippets: Iterable[Path] = (),
    ) -> Path:
        """Store a run.

        The dump is written to a temporary directory and renamed into place,
        so a crash mid-write never leaves a half dump under ``runs/``. The
        summary line and the ``latest`` pointer are updated afterwards.

        Args:
            run: The run; its ``manifest.run_id`` becomes the directory name.
            raw_index: CMake's index file to copy into the dump.
            raw_snippets: CMake's snippet files to copy into the dump.

        Returns:
            The run's directory.

        Raises:
            FileExistsError: A run with that id is already stored.
            OSError: The store cannot be written.
        """
        run_id = run.manifest.run_id
        final = self.runs_dir / run_id
        if final.exists():
            raise FileExistsError(f"run {run_id} already exists in {self.root}")
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        staging = self.runs_dir / f".tmp-{run_id}"
        if staging.exists():
            shutil.rmtree(staging)
        dump.write_run(run, staging, raw_index=raw_index, raw_snippets=raw_snippets)
        staging.rename(final)
        with self._summaries_path.open("a", encoding="utf-8") as summaries:
            summaries.write(json.dumps(asdict(RunSummary.of(run)), sort_keys=True) + "\n")
        self._write_latest(run_id)
        return final

    def summaries(self) -> list[RunSummary]:
        """Every stored run, oldest first.

        ``runs.jsonl`` is the source; if it is missing or unreadable the
        manifests under ``runs/`` are scanned instead and the file rebuilt,
        so a damaged index costs time, not data.

        Returns:
            Summaries sorted by run id, which is capture order.
        """
        try:
            summaries = self._read_summaries()
        except (OSError, ValueError, FieldError):
            # ValueError covers json.JSONDecodeError: a half-written line.
            summaries = None
        if summaries is None:
            summaries = [RunSummary.of(dump.read_run(path)) for path in self._run_dirs()]
            self._rewrite_summaries(summaries)
        return sorted(summaries, key=lambda summary: summary.run_id)

    def list(
        self,
        *,
        build_dir: Path | None = None,
        label: str | None = None,
        since: datetime | None = None,
    ) -> list[RunSummary]:
        """Select stored runs.

        Args:
            build_dir: Keep runs from this build tree only.
            label: Keep runs carrying this label only.
            since: Keep runs captured at or after this time only.

        Returns:
            Matching summaries, oldest first.
        """
        selected = self.summaries()
        if build_dir is not None:
            wanted = str(build_dir)
            selected = [summary for summary in selected if summary.build_dir == wanted]
        if label is not None:
            selected = [summary for summary in selected if label in summary.labels]
        if since is not None:
            floor = since.astimezone(UTC).isoformat()
            selected = [summary for summary in selected if summary.created_at >= floor]
        return selected

    def resolve(self, ref: str) -> Path:
        """Turn a run reference into the dump directory it names.

        Args:
            ref: ``latest``, ``latest~N``, ``sha:<prefix>``, ``label:<name>``,
                a path to a dump directory, or a run id or unique prefix of one.

        Returns:
            The dump directory.

        Raises:
            UnknownRunError: Nothing matches, or an id prefix matches more
                than one run.
        """
        as_path = Path(ref)
        if as_path.is_dir() and (as_path / dump.MANIFEST).is_file():
            return as_path
        summaries = self.summaries()
        newest_first = list(reversed(summaries))

        latest = _LATEST_PATTERN.match(ref)
        if latest:
            back = int(latest.group(1) or 0)
            if back >= len(summaries):
                raise UnknownRunError(f"{ref}: the store has only {len(summaries)} run(s)")
            return self.runs_dir / newest_first[back].run_id
        if ref.startswith("sha:"):
            prefix = ref[len("sha:") :]
            for summary in newest_first:
                if summary.git_sha and summary.git_sha.startswith(prefix):
                    return self.runs_dir / summary.run_id
            raise UnknownRunError(f"no run captured at a commit starting with {prefix!r}")
        if ref.startswith("label:"):
            wanted = ref[len("label:") :]
            for summary in newest_first:
                if wanted in summary.labels:
                    return self.runs_dir / summary.run_id
            raise UnknownRunError(f"no run labelled {wanted!r}")
        matches = [summary for summary in summaries if summary.run_id.startswith(ref)]
        if len(matches) == 1:
            return self.runs_dir / matches[0].run_id
        if not matches:
            raise UnknownRunError(f"no run matches {ref!r}")
        raise UnknownRunError(f"{ref!r} is ambiguous: {', '.join(m.run_id for m in matches)}")

    def read(self, ref: str) -> Run:
        """Load the run a reference names.

        Args:
            ref: Any reference :meth:`resolve` accepts.

        Returns:
            The run.

        Raises:
            UnknownRunError: The reference names no single run.
            DumpFormatError: The dump on disk is malformed.
        """
        return dump.read_run(self.resolve(ref))

    def latest(self) -> Run | None:
        """The newest stored run.

        Returns:
            The run, or ``None`` for an empty store.
        """
        summaries = self.summaries()
        if not summaries:
            return None
        return dump.read_run(self.runs_dir / summaries[-1].run_id)

    def pack(self, ref: str, into: Path) -> Path:
        """Archive one run as ``<into>/<run_id>.tar.zst``.

        Args:
            ref: Any reference :meth:`resolve` accepts.
            into: Directory for the archive; created if needed.

        Returns:
            The archive path.

        Raises:
            UnknownRunError: The reference names no single run.
        """
        directory = self.resolve(ref)
        return dump.pack(directory, into / f"{directory.name}{dump.ARCHIVE_SUFFIX}")

    def import_archive(self, archive: Path) -> Path:
        """Add a run from an archive made by :meth:`pack`, possibly on another machine.

        This is how a CI job brings in the baseline run it downloaded. The
        dump is extracted under ``runs/`` and indexed like a freshly written
        run.

        Args:
            archive: The ``.tar.zst`` file.

        Returns:
            The imported run's directory.

        Raises:
            FileExistsError: A run with the same id is already stored.
            DumpFormatError: The archive is not a single valid run dump.
        """
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        directory = dump.unpack(archive, self.runs_dir)
        run = dump.read_run(directory)
        with self._summaries_path.open("a", encoding="utf-8") as summaries:
            summaries.write(json.dumps(asdict(RunSummary.of(run)), sort_keys=True) + "\n")
        newest = self.summaries()[-1].run_id
        self._write_latest(newest)
        return directory

    def prune(self, *, keep: int | None = None, older_than: timedelta | None = None) -> list[str]:
        """Delete old runs.

        Args:
            keep: Keep only this many newest runs.
            older_than: Delete runs captured longer ago than this.

        Returns:
            Ids of the deleted runs, oldest first.
        """
        summaries = self.summaries()
        doomed: set[str] = set()
        if keep is not None and keep < len(summaries):
            doomed.update(summary.run_id for summary in summaries[: len(summaries) - keep])
        if older_than is not None:
            floor = (datetime.now(UTC) - older_than).isoformat()
            doomed.update(s.run_id for s in summaries if s.created_at < floor)
        for run_id in doomed:
            shutil.rmtree(self.runs_dir / run_id, ignore_errors=True)
        remaining = [summary for summary in summaries if summary.run_id not in doomed]
        self._rewrite_summaries(remaining)
        if remaining:
            self._write_latest(remaining[-1].run_id)
        elif self._latest_path.exists():
            self._latest_path.unlink()
        return sorted(doomed)

    def _run_dirs(self) -> list[Path]:
        """Dump directories under ``runs/``, ignoring staging leftovers.

        Returns:
            Directories that contain a manifest, sorted by name.
        """
        if not self.runs_dir.is_dir():
            return []
        return sorted(
            path
            for path in self.runs_dir.iterdir()
            if path.is_dir() and not path.name.startswith(".") and (path / dump.MANIFEST).is_file()
        )

    def _read_summaries(self) -> list[RunSummary] | None:
        """Parse ``runs.jsonl``.

        Returns:
            The summaries, or ``None`` when the file does not exist.

        Raises:
            FieldError: A line is a JSON value but not a summary object.
            ValueError: A line is not JSON.
            OSError: The file cannot be read.
        """
        if not self._summaries_path.is_file():
            return None
        summaries: list[RunSummary] = []
        for line in self._summaries_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            document: object = json.loads(line)
            if not isinstance(document, dict):
                raise FieldError("runs.jsonl line is not a JSON object")
            # JSON object keys are always strings; the cast only tells the type checker so.
            fields = Fields(cast(Json, document))
            summaries.append(
                RunSummary(
                    run_id=fields.string("run_id"),
                    created_at=fields.string("created_at"),
                    hook=fields.string("hook"),
                    build_dir=fields.string("build_dir"),
                    git_sha=fields.optional_string("git_sha"),
                    labels=fields.strings("labels"),
                    step_count=fields.integer("step_count"),
                    span_ms=fields.integer("span_ms"),
                )
            )
        return summaries

    def _rewrite_summaries(self, summaries: Iterable[RunSummary]) -> None:
        """Replace ``runs.jsonl`` atomically.

        Args:
            summaries: The lines to keep, in order.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        staging = self._summaries_path.with_suffix(".jsonl.tmp")
        staging.write_text(
            "".join(json.dumps(asdict(s), sort_keys=True) + "\n" for s in summaries),
            encoding="utf-8",
        )
        staging.replace(self._summaries_path)

    def _write_latest(self, run_id: str) -> None:
        """Point ``latest`` at a run, atomically.

        Args:
            run_id: The newest run.
        """
        staging = self._latest_path.with_suffix(".tmp")
        staging.write_text(run_id + "\n", encoding="utf-8")
        staging.replace(self._latest_path)
