"""Drive a fixture project through a specific CMake binary and collect what it recorded.

The tests configure and build small projects under ``tests/fixtures`` with the
instrumentation API switched on, then look at the indexes and snippets CMake
produced. :class:`CMakeProject` wraps that cycle; :class:`InstrumentationQuery`
builds the query file; :class:`CapturedIndex` is what the callback stub saved.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CAPTURE_STUB = Path(__file__).with_name("capture_stub.py")


def instrumentation_options(version: tuple[int, int, int]) -> tuple[str, ...]:
    """Query options a given CMake accepts, richest first.

    ``processMetrics`` exists from 4.5 on; asking an older CMake for it would
    make it ignore the whole query, so the set depends on the version.
    """
    options = ["staticSystemInformation", "dynamicSystemInformation"]
    if version[:2] >= (4, 5):
        options.append("processMetrics")
    return tuple(options)


Json = dict[str, Any]  # a parsed JSON object; the shape is CMake's, not ours


@dataclass(frozen=True, kw_only=True)
class InstrumentationQuery:
    """Contents of one ``.cmake/instrumentation/v1/query/*.json`` file.

    Attributes:
        hooks: Hook names CMake should trigger indexing on, e.g. ``postCMakeBuild``.
        callbacks: Command lines to run at each hook; CMake appends the index path.
        options: Extra data to collect, e.g. ``dynamicSystemInformation``.
        minor: Requested data minor version; CMake always writes the newest it knows.
    """

    hooks: tuple[str, ...] = ()
    callbacks: tuple[str, ...] = ()
    options: tuple[str, ...] = ()
    minor: int = 0

    def to_json(self) -> Json:
        """Render the query in the layout CMake reads."""
        query: Json = {"version": {"major": 1, "minor": self.minor}}
        if self.hooks:
            query["hooks"] = list(self.hooks)
        if self.callbacks:
            query["callbacks"] = list(self.callbacks)
        if self.options:
            query["options"] = list(self.options)
        return query


@dataclass(frozen=True, kw_only=True)
class CapturedIndex:
    """One index file plus its snippets, as copied aside by the callback stub.

    Attributes:
        directory: The numbered capture directory the stub created.
        index: Parsed ``index.json``.
        snippets: Parsed snippet files, in the order the index lists them.
        argv: Arguments CMake passed to the callback, last one the index path.
    """

    directory: Path
    index: Json
    snippets: tuple[Json, ...]
    argv: tuple[str, ...]

    @property
    def hook(self) -> str:
        """Hook that triggered this indexing, as CMake recorded it."""
        return str(self.index["hook"])

    def roles(self) -> dict[str, int]:
        """Count snippets per role."""
        counts: dict[str, int] = {}
        for snippet in self.snippets:
            role = str(snippet["role"])
            counts[role] = counts.get(role, 0) + 1
        return counts

    def with_role(self, role: str) -> list[Json]:
        """Select snippets of one role."""
        return [snippet for snippet in self.snippets if snippet["role"] == role]


@dataclass(kw_only=True)
class CMakeProject:
    """One build tree of a fixture project, driven through a chosen CMake binary.

    Every method shells out to CMake and raises ``CalledProcessError`` on a
    non-zero exit, so a failing fixture build fails the test loudly instead
    of producing confusing empty data.

    Attributes:
        source_dir: Fixture project directory containing ``CMakeLists.txt``.
        build_dir: Build tree; created on first configure.
        cmake: Path to the ``cmake`` executable to use.
        generator: CMake generator name, e.g. ``Ninja`` or ``Unix Makefiles``.
        capture_root: Where the callback stub saves indexes and snippets.
    """

    source_dir: Path
    build_dir: Path
    cmake: Path
    generator: str
    capture_root: Path
    _env: dict[str, str] = field(default_factory=dict[str, str], repr=False)

    @property
    def ctest(self) -> Path:
        """The ``ctest`` executable next to ``cmake``."""
        return self.cmake.with_name("ctest")

    @property
    def callback(self) -> str:
        """Callback command line that saves each index into ``capture_root``."""
        return f"{sys.executable} {CAPTURE_STUB} {self.capture_root}"

    @property
    def instrumentation_dir(self) -> Path:
        """``<build>/.cmake/instrumentation/v1``."""
        return self.build_dir / ".cmake" / "instrumentation" / "v1"

    @property
    def data_dir(self) -> Path:
        """Where CMake accumulates snippets until a hook indexes them."""
        return self.instrumentation_dir / "data"

    def set_env(self, **variables: str) -> None:
        """Add environment variables to every subsequent CMake invocation."""
        self._env.update(variables)

    def write_query(self, query: InstrumentationQuery, name: str = "test") -> Path:
        """Write a query file into the build tree, creating the directory if needed."""
        query_dir = self.instrumentation_dir / "query"
        query_dir.mkdir(parents=True, exist_ok=True)
        path = query_dir / f"{name}.json"
        path.write_text(json.dumps(query.to_json(), indent=2), encoding="utf-8")
        return path

    def snippet_files(self) -> list[Path]:
        """List snippet files currently sitting in the data directory."""
        if not self.data_dir.is_dir():
            return []
        return sorted(p for p in self.data_dir.iterdir() if p.suffix == ".json")

    def configure(self, *extra_args: str) -> subprocess.CompletedProcess[str]:
        """Run ``cmake -S <source> -B <build> -G <generator>``.

        Raises:
            subprocess.CalledProcessError: Configure or generate failed.
        """
        return self._run(
            self.cmake,
            "-S",
            str(self.source_dir),
            "-B",
            str(self.build_dir),
            "-G",
            self.generator,
            *extra_args,
        )

    def build(self, *extra_args: str) -> subprocess.CompletedProcess[str]:
        """Run ``cmake --build <build>``.

        Raises:
            subprocess.CalledProcessError: The build failed.
        """
        return self._run(self.cmake, "--build", str(self.build_dir), *extra_args)

    def run_ctest(self, *extra_args: str) -> subprocess.CompletedProcess[str]:
        """Run ``ctest --test-dir <build>`` without failing on failing tests.

        A fixture may contain a test that is meant to fail; ctest's non-zero
        exit in that case is data, not an error.
        """
        return self._run(self.ctest, "--test-dir", str(self.build_dir), *extra_args, check=False)

    def install(self, prefix: Path) -> subprocess.CompletedProcess[str]:
        """Run ``cmake --install <build> --prefix <prefix>``.

        Raises:
            subprocess.CalledProcessError: Installation failed.
        """
        return self._run(self.cmake, "--install", str(self.build_dir), "--prefix", str(prefix))

    def workflow(self, preset: str) -> subprocess.CompletedProcess[str]:
        """Run ``cmake --workflow --preset <preset>`` from the source directory.

        Raises:
            subprocess.CalledProcessError: Any workflow step failed.
        """
        return self._run(self.cmake, "--workflow", "--preset", preset, cwd=self.source_dir)

    def captured(self) -> list[CapturedIndex]:
        """Load everything the callback stub saved so far."""
        if not self.capture_root.is_dir():
            return []
        captures: list[CapturedIndex] = []
        for entry in sorted(self.capture_root.iterdir()):
            index: Json = json.loads((entry / "index.json").read_text(encoding="utf-8"))
            argv: list[str] = json.loads((entry / "argv.json").read_text(encoding="utf-8"))
            snippets = tuple(
                json.loads((entry / "snippets" / Path(rel).name).read_text(encoding="utf-8"))
                for rel in index.get("snippets", [])
            )
            captures.append(
                CapturedIndex(directory=entry, index=index, snippets=snippets, argv=tuple(argv))
            )
        return captures

    def _run(
        self, *command: str | Path, check: bool = True, cwd: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Run one subprocess with the project's environment, capturing text output.

        Raises:
            subprocess.CalledProcessError: Non-zero exit and ``check`` is true.
        """
        return subprocess.run(
            [str(part) for part in command],
            cwd=cwd or self.build_dir.parent,
            env={**os.environ, **self._env},
            capture_output=True,
            text=True,
            check=check,
        )
