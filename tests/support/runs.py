"""Ready-made runs for tests, and a way to assemble one from what a real build captured."""

from __future__ import annotations

from pathlib import Path

from longpole.cmake import fileapi, snippets
from longpole.env import collect
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
    SystemInfo,
    TargetNode,
    Toolchain,
)
from longpole.store import new_run_id
from tests.support.cmake import CapturedIndex


def run_from_capture(capture: CapturedIndex, build_dir: Path) -> Run:
    """Assemble a run from an index the callback stub saved, the way ``capture`` would.

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
            data_version=index.version,
            environment=collect(build_dir, reply=reply),
        ),
        steps=snippets.read_steps(index),
        graph=fileapi.graph_from_reply(reply) if reply is not None else None,
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
    round trip through the dump format covers them all.

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
    system = SystemInfo(
        os_name="macOS",
        os_version="25.6.0",
        hostname="box",
        processor_name="Apple M3",
        logical_cpus=12,
        physical_cpus=12,
        total_memory_mib=36864,
        raw={"OSName": "macOS", "numberOfLogicalCPU": 12},
    )
    manifest = Manifest(
        run_id=run_id,
        created_at=created_at,
        tool_version="0.0.1.dev0",
        hook="postCMakeBuild",
        build_dir=build_dir,
        data_version=DataVersion(major=1, minor=2),
        environment=Environment(
            cmake_version="4.5.0-rc2",
            generator="Ninja",
            cpu_count=12,
            git=GitInfo(sha=sha, branch="main", dirty=False) if sha is not None else None,
            toolchains=(
                Toolchain(
                    language="C",
                    compiler_id="AppleClang",
                    compiler_path="/usr/bin/cc",
                    compiler_version="21.0.0",
                ),
            ),
            ccache={"cache_hit_direct": "12"},
        ),
        labels=labels,
    )
    return Run(
        manifest=manifest,
        steps=tuple(steps),
        graph=graph if with_graph else None,
        system=system if with_system else None,
    )
