"""Ready-made runs for tests, and the raw files a store needs to hold one.

A dump keeps what CMake wrote and is parsed when read, so a test that wants
a run with known numbers in a store has to produce CMake-shaped files for
it. :func:`raw_files` is the inverse of the readers in ``longpole.cmake``:
an index, one snippet per step and a File API reply for the graph.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

from longpole.cmake import fileapi, snippets
from longpole.env import collect
from longpole.jsonfields import Json
from longpole.model import (
    DataVersion,
    Environment,
    GitInfo,
    Graph,
    HostLoad,
    Manifest,
    ProcessMetrics,
    Run,
    Step,
    TargetNode,
    Toolchain,
)
from longpole.store import RunStore, new_run_id
from tests.support.cmake import CapturedIndex


@dataclass(frozen=True, kw_only=True)
class RawFiles:
    """What :func:`raw_files` wrote: the arguments ``RunStore.write`` takes.

    Attributes:
        index: The index file.
        snippets: The snippet files, in index order.
        reply_dir: A File API reply directory, or ``None`` for a run without a graph.
    """

    index: Path
    snippets: tuple[Path, ...]
    reply_dir: Path | None


def raw_files(run: Run, directory: Path) -> RawFiles:
    """Write a run back out as the files CMake would have produced for it.

    Args:
        run: The run; its steps become snippets, its graph and toolchains a
            File API reply, its system information the index's static block.
        directory: Where to write; created if needed.

    Returns:
        The paths.
    """
    snippet_dir = directory / "snippets"
    snippet_dir.mkdir(parents=True, exist_ok=True)
    version = run.data_version
    paths: list[Path] = []
    for position, step in enumerate(run.steps):
        path = snippet_dir / f"{step.role}-{position:03d}.json"
        path.write_text(json.dumps(_snippet(step, version)), encoding="utf-8")
        paths.append(path)
    index: Json = {
        "version": {"major": version.major, "minor": version.minor},
        "buildDir": run.manifest.build_dir,
        "dataDir": str(snippet_dir),
        "hook": run.manifest.hook,
        "snippets": [path.name for path in paths],
    }
    if run.system is not None:
        index["staticSystemInformation"] = dict(run.system.raw)
    index_path = directory / "index.json"
    index_path.write_text(json.dumps(index), encoding="utf-8")
    reply_dir = None
    if run.graph is not None:
        reply_dir = directory / "reply"
        _write_reply(reply_dir, run.graph, run.toolchains)
    return RawFiles(index=index_path, snippets=tuple(paths), reply_dir=reply_dir)


def write_run(store: RunStore, run: Run) -> Path:
    """Put a run into a store the way the callback would, from synthesised raw files.

    Args:
        store: The store.
        run: The run to store.

    Returns:
        The run's directory in the store.
    """
    with tempfile.TemporaryDirectory() as scratch:
        files = raw_files(run, Path(scratch))
        return store.write(
            run.manifest, index=files.index, snippets=files.snippets, reply_dir=files.reply_dir
        )


def _snippet(step: Step, version: DataVersion) -> Json:
    """One snippet as CMake writes it, the inverse of ``snippets.step_from_snippet``.

    Args:
        step: The step.
        version: Data version to stamp on it.

    Returns:
        The document.
    """
    document: Json = {
        "version": {"major": version.major, "minor": version.minor},
        "role": step.role,
        "workingDir": step.working_dir,
        "result": step.result,
        "timeStart": step.time_start_ms,
        "duration": step.duration_ms,
    }
    optional: dict[str, object] = {
        "command": step.command,
        "target": step.target,
        "source": step.source,
        "language": step.language,
        "config": step.config,
        "testName": step.test_name,
        "showOnly": step.show_only,
        "cmakeContent": step.cmake_content,
        "traceFile": step.trace_file,
        "stdout": step.stdout,
        "stderr": step.stderr,
        "interruptSignal": step.interrupt_signal,
    }
    document.update({key: value for key, value in optional.items() if value is not None})
    if step.target_labels:
        document["targetLabels"] = list(step.target_labels)
    if step.outputs:
        document["outputs"] = list(step.outputs)
    if step.output_sizes:
        document["outputSizes"] = list(step.output_sizes)
    if step.process_metrics is not None:
        document["processMetrics"] = {
            "maxRSS": step.process_metrics.max_rss_kib,
            "userTime": step.process_metrics.user_time_us,
            "systemTime": step.process_metrics.system_time_us,
        }
    if step.host_load is not None:
        document["dynamicSystemInformation"] = {
            "beforeHostMemoryUsed": step.host_load.memory_used_before_kib,
            "afterHostMemoryUsed": step.host_load.memory_used_after_kib,
            "beforeCPULoadAverage": step.host_load.load_average_before,
            "afterCPULoadAverage": step.host_load.load_average_after,
        }
    document.update(step.extra)
    return document


def _write_reply(reply_dir: Path, graph: Graph, toolchains: tuple[Toolchain, ...]) -> None:
    """A File API reply directory answering our client with the given graph.

    Args:
        reply_dir: Directory to create.
        graph: Targets and edges to describe.
        toolchains: Compilers to list.
    """
    reply_dir.mkdir(parents=True, exist_ok=True)
    entries: list[Json] = []
    for position, target in enumerate(graph.targets):
        name = f"target-{position}.json"
        entries.append({"id": target.id, "name": target.name, "jsonFile": name})
        (reply_dir / name).write_text(
            json.dumps(
                {
                    "id": target.id,
                    "name": target.name,
                    "type": target.type,
                    "artifacts": [{"path": path} for path in target.artifacts],
                    "sources": [{"path": path} for path in target.sources],
                    "dependencies": [
                        {"id": dependency}
                        for dependent, dependency in graph.edges
                        if dependent == target.id
                    ],
                }
            ),
            encoding="utf-8",
        )
    codemodel: Json = {
        "kind": "codemodel",
        "configurations": [{"name": "", "targets": entries}],
    }
    listed: list[Json] = []
    for toolchain in toolchains:
        compiler = {
            "id": toolchain.compiler_id,
            "path": toolchain.compiler_path,
            "version": toolchain.compiler_version,
        }
        listed.append(
            {
                "language": toolchain.language,
                "compiler": {key: value for key, value in compiler.items() if value is not None},
            }
        )
    index: Json = {
        "reply": {
            fileapi.CLIENT: {
                "query.json": {
                    "responses": [
                        {"kind": "codemodel", "jsonFile": "codemodel-v2.json"},
                        {"kind": "toolchains", "jsonFile": "toolchains-v1.json"},
                    ]
                }
            }
        }
    }
    for name, document in [
        ("codemodel-v2.json", codemodel),
        ("toolchains-v1.json", {"kind": "toolchains", "toolchains": listed}),
        ("index-2026-10-31T00-00-00-0000.json", index),
    ]:
        (reply_dir / name).write_text(json.dumps(document), encoding="utf-8")


def run_from_capture(capture: CapturedIndex, build_dir: Path) -> Run:
    """Assemble a run from an index the callback stub saved, the way a store read would.

    Args:
        capture: The saved index and snippets.
        build_dir: The build tree the index came from; its File API reply, if
            any, becomes the run's graph.

    Returns:
        The run, with a graph when the reply exists and without one otherwise.
    """
    index = snippets.read_index(
        capture.directory / "index.json", data_dir=capture.directory / "snippets"
    )
    reply = fileapi.read_reply(fileapi.reply_dir(build_dir))
    return Run(
        manifest=Manifest(
            run_id=new_run_id(),
            created_at="2026-10-31T12:00:00+00:00",
            tool_version="test",
            hook=index.hook,
            build_dir=str(index.build_dir),
            environment=collect(build_dir),
        ),
        data_version=index.version,
        steps=snippets.read_steps(index),
        graph=fileapi.graph_from_reply(reply) if reply is not None else None,
        toolchains=fileapi.toolchains_from_reply(reply) if reply is not None else (),
        system=index.system,
    )


def sample_run(
    *,
    run_id: str = "20261031T120000Z-abc123",
    created_at: str = "2026-10-31T12:00:00+00:00",
    sha: str | None = "0123456789abcdef0123456789abcdef01234567",
    labels: tuple[str, ...] = (),
    build_dir: str = "/work/project/build",
    with_graph: bool = True,
    with_system: bool = True,
) -> Run:
    """A run resembling a small chain build: configure, three compiles, three links.

    Every optional field of the model is exercised somewhere in the run so a
    round trip through raw files and the store covers them all.

    Args:
        run_id: Store id.
        created_at: Capture time.
        sha: Commit of the sources, or ``None`` for a run outside git.
        labels: Run labels.
        build_dir: Build tree path.
        with_graph: Include a target graph.
        with_system: Include static host information.

    Returns:
        The run.
    """
    metrics = ProcessMetrics(max_rss_kib=27216, user_time_us=10889, system_time_us=9964)
    load = HostLoad(
        memory_used_before_kib=27907856.0,
        memory_used_after_kib=27908112.0,
        load_average_before=3.5,
        load_average_after=3.6,
    )
    steps = [
        Step(
            role="configure",
            command="cmake -S . -B build -G Ninja",
            working_dir="/work/project",
            result=0,
            time_start_ms=1_700_000_000_000,
            duration_ms=272,
            cmake_content="content/cmake-1.json",
            host_load=load,
        )
    ]
    for offset, name in enumerate(("a", "b", "c")):
        start = 1_700_000_001_000 + offset * 400
        steps.append(
            Step(
                role="compile",
                command=f"cc -c gen_{name}.c -o {name}.o",
                working_dir=build_dir,
                result=0,
                time_start_ms=start,
                duration_ms=24,
                target=name,
                target_labels=("core",) if name == "a" else (),
                outputs=(f"CMakeFiles/{name}.dir/gen_{name}.c.o",),
                output_sizes=(552 + offset,),
                source=f"{build_dir}/gen_{name}.c",
                language="C",
                config="",
                process_metrics=metrics,
                host_load=load,
                extra={"futureField": offset} if name == "c" else {},
            )
        )
        steps.append(
            Step(
                role="link",
                command=f"cc -o lib{name}.dylib {name}.o",
                working_dir=build_dir,
                result=0,
                time_start_ms=start + 30,
                duration_ms=36,
                target=name,
                outputs=(f"lib{name}.dylib",),
                output_sizes=(33336,),
                language="C",
                config="",
                process_metrics=metrics,
            )
        )
    steps.append(
        Step(
            role="build",
            command=None,
            working_dir="/work/project",
            result=None,
            time_start_ms=1_700_000_000_900,
            duration_ms=1506,
        )
    )
    graph = Graph(
        source="file-api",
        targets=(
            TargetNode(id="a::@1", name="a", type="SHARED_LIBRARY", artifacts=("liba.dylib",)),
            TargetNode(id="b::@1", name="b", type="SHARED_LIBRARY", artifacts=("libb.dylib",)),
            TargetNode(id="c::@1", name="c", type="EXECUTABLE", artifacts=("c",)),
        ),
        edges=(("b::@1", "a::@1"), ("c::@1", "b::@1"), ("c::@1", "a::@1")),
    )
    system = snippets.system_info_from_static(
        {
            "OSName": "macOS",
            "OSVersion": "25.6.0",
            "hostname": "box",
            "processorName": "Apple M3",
            "numberOfLogicalCPU": 12,
            "numberOfPhysicalCPU": 12,
            "totalPhysicalMemory": 36864,
        }
    )
    manifest = Manifest(
        run_id=run_id,
        created_at=created_at,
        tool_version="0.0.1.dev0",
        hook="postCMakeBuild",
        build_dir=build_dir,
        environment=Environment(
            cmake_version="4.5.0",
            generator="Ninja",
            cpu_count=12,
            git=GitInfo(sha=sha, branch="main", dirty=False) if sha is not None else None,
            ccache={"cache_hit_direct": "12"},
        ),
        labels=labels,
    )
    return Run(
        manifest=manifest,
        data_version=DataVersion(major=1, minor=2),
        steps=tuple(steps),
        graph=graph if with_graph else None,
        toolchains=(
            Toolchain(
                language="C",
                compiler_id="AppleClang",
                compiler_path="/usr/bin/cc",
                compiler_version="21.0.0",
            ),
        )
        if with_graph
        else (),
        system=system if with_system else None,
    )
