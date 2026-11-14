"""Where run dumps live and how they are found again.

A store is a directory with ``runs/<run_id>/`` dumps, a ``runs.jsonl`` of
one-line summaries for fast listing, and a ``latest`` file naming the newest
run. Every command gets its path explicitly through ``--store``; nothing
looks for it in the environment or the working directory.

A dump holds raw files and is parsed when read. The target graph of a build
run comes from the configure before it: a ``postGenerate`` run keeps a copy
of the File API reply, and :meth:`RunStore.read` finds the newest such copy
for the same tree captured no later than the run being read.

Runs are addressed by references: a full or prefix id, ``latest``,
``latest~N``, ``label:<name>``, or
a path to a dump directory.
"""

from __future__ import annotations

import json
import re
import secrets
import shutil
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from longpole import dump
from longpole.errors import UnknownRunError
from longpole.jsonfields import FieldError, Fields, Json
from longpole.model import Manifest, Run

_LATEST_PATTERN = re.compile(r"^latest(?:~(\d+))?$")


@dataclass(frozen=True, kw_only=True)
class RunSummary:
    """The line ``runs.jsonl`` keeps per run, enough to list and resolve without opening dumps.

    Attributes:
        run_id: Store id of the run.
        created_at: Capture time, ISO 8601 UTC.
        hook: Hook that produced the run.
        build_dir: Build tree the run came from.
        labels: The run's labels.
        step_count: Number of snippets the index listed.
    """

    run_id: str
    created_at: str
    hook: str
    build_dir: str
    labels: tuple[str, ...]
    step_count: int

    @classmethod
    def of(cls, manifest: Manifest, step_count: int) -> RunSummary:
        """Summarise a run from what the callback knows without parsing snippets.

        Args:
            manifest: The run's manifest.
            step_count: How many snippets its index lists.

        Returns:
            Its summary.
        """
        return cls(
            run_id=manifest.run_id,
            created_at=manifest.created_at,
            hook=manifest.hook,
            build_dir=manifest.build_dir,
            labels=manifest.labels,
            step_count=step_count,
        )

    @classmethod
    def read(cls, directory: Path) -> RunSummary:
        """Summarise a dump on disk.

        Args:
            directory: A dump directory.

        Returns:
            Its summary.

        Raises:
            DumpFormatError: The manifest is malformed.
            IndexFormatError: The copied index is not v1 data.
        """
        manifest = dump.read_manifest(directory)
        return cls.of(manifest, len(dump.read_index(directory).snippet_paths))


def new_run_id(now: datetime | None = None) -> str:
    """Make a run id that sorts by capture time.

    Args:
        now: Capture time; defaults to the current UTC time.

    Returns:
        ``YYYYMMDDTHHMMSSZ-xxxxxx`` with six random hex digits.
    """
    moment = now if now is not None else datetime.now(UTC)
    return f"{moment.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(3)}"


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
        manifest: Manifest,
        *,
        index: Path,
        snippets: Iterable[Path],
        reply_dir: Path | None = None,
    ) -> Path:
        """Store a run from CMake's files and a manifest.

        The dump is written to a temporary directory and renamed into place,
        so a crash mid-write never leaves a half dump under ``runs/``. The
        summary line and the ``latest`` pointer are updated afterwards.

        Args:
            manifest: What is known about the run; ``run_id`` names the directory.
            index: CMake's index file.
            snippets: The snippet files it lists.
            reply_dir: File API reply directory to keep with the run, if any.

        Returns:
            The run's directory.

        Raises:
            FileExistsError: A run with that id is already stored.
            OSError: A file cannot be copied or the store cannot be written.
        """
        run_id = manifest.run_id
        final = self.runs_dir / run_id
        if final.exists():
            raise FileExistsError(f"run {run_id} already exists in {self.root}")
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        staging = self.runs_dir / f".tmp-{run_id}"
        if staging.exists():
            shutil.rmtree(staging)
        listed = tuple(snippets)
        dump.write_dump(staging, manifest, index=index, snippets=listed, reply_dir=reply_dir)
        staging.rename(final)
        self._append_summary(RunSummary.of(manifest, len(listed)))
        self._write_latest(run_id)
        return final

    def summaries(self) -> list[RunSummary]:
        """Every stored run, oldest first.

        ``runs.jsonl`` is the source; if it is missing or unreadable the
        manifests under ``runs/`` are scanned instead and the file rebuilt,
        so a damaged index costs time, not data.

        Returns:
            Summaries sorted by capture time. Run ids carry whole seconds
            only, so ``created_at`` decides between runs captured within the
            same second, as a build and the ctest run right after it are.
        """
        try:
            summaries = self._read_summaries()
        except (OSError, ValueError, FieldError):
            # ValueError covers json.JSONDecodeError: a half-written line.
            summaries = None
        if summaries is None:
            summaries = [RunSummary.read(path) for path in self._run_dirs()]
            self._rewrite_summaries(summaries)
        return sorted(summaries, key=lambda summary: (summary.created_at, summary.run_id))

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
            ref: ``latest``, ``latest~N``, ``label:<name>``,
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
        """Load and parse the run a reference names, graph included when one applies.

        Args:
            ref: Any reference :meth:`resolve` accepts.

        Returns:
            The run.

        Raises:
            UnknownRunError: The reference names no single run.
            DumpFormatError: The manifest on disk is malformed.
            IndexFormatError: The copied index is not v1 data.
            SnippetFormatError: A copied snippet is not v1 data.
            FileApiError: The File API reply that applies cannot be read.
        """
        directory = self.resolve(ref)
        manifest = dump.read_manifest(directory)
        return dump.read_run(directory, reply_from=self.reply_for(manifest))

    def reply_for(self, manifest: Manifest) -> Path | None:
        """The File API reply describing the tree as it was for a run.

        A configure copies the reply into its own dump; a build does not,
        because the launcher that records its steps was written into the
        build system by that configure, and the graph did not change since.
        So the newest dump of the same tree captured no later than the run,
        holding a reply, is the one.

        Args:
            manifest: The run's manifest.

        Returns:
            The reply directory, or ``None`` when no configure of the tree
            was recorded before the run (it was generated before recording
            was enabled; the next ``cmake`` run fixes that).
        """
        for summary in reversed(self.summaries()):
            same_tree = summary.build_dir == manifest.build_dir
            not_later = summary.created_at <= manifest.created_at
            candidate = dump.reply_dir(self.runs_dir / summary.run_id)
            if same_tree and not_later and candidate.is_dir():
                return candidate
        return None

    def latest(self) -> Run | None:
        """The newest stored run.

        Returns:
            The run, or ``None`` for an empty store.
        """
        summaries = self.summaries()
        if not summaries:
            return None
        return self.read(summaries[-1].run_id)

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
        self._append_summary(RunSummary.read(directory))
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
                    labels=fields.strings("labels"),
                    step_count=fields.integer("step_count"),
                )
            )
        return summaries

    def _append_summary(self, summary: RunSummary) -> None:
        """Add one line to ``runs.jsonl``.

        Args:
            summary: The line.
        """
        with self._summaries_path.open("a", encoding="utf-8") as summaries:
            summaries.write(json.dumps(asdict(summary), sort_keys=True) + "\n")

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
