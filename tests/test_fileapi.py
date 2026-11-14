"""The File API gives us the target graph and the compilers, or tells us it has not answered yet."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from longpole.cmake.fileapi import (
    CLIENT,
    graph_from_reply,
    query_path,
    read_reply,
    reply_dir,
    toolchains_from_reply,
    write_query,
)
from longpole.errors import FileApiError
from longpole.jsonfields import Json
from tests.conftest import BuiltChain
from tests.support.cmake import CMakeProject, InstrumentationQuery


def test_write_query_is_idempotent_and_asks_for_codemodel_and_toolchains(tmp_path: Path) -> None:
    """The query file lands under our client directory and rewriting it changes nothing."""
    first = write_query(tmp_path / "build")
    second = write_query(tmp_path / "build")
    assert first == second == query_path(tmp_path / "build")
    requests = json.loads(first.read_text(encoding="utf-8"))["requests"]
    assert {(r["kind"], r["version"]) for r in requests} == {("codemodel", 2), ("toolchains", 1)}


def test_no_reply_directory_means_no_reply(tmp_path: Path) -> None:
    """A tree that was never generated with our query has nothing to read."""
    assert read_reply(reply_dir(tmp_path)) is None


def _write_reply(build_dir: Path, *, with_client: bool = True) -> Path:
    """Write a hand-made File API reply with two targets, ``app`` depending on ``lib``."""
    reply = build_dir / ".cmake" / "api" / "v1" / "reply"
    reply.mkdir(parents=True)
    lib: Json = {
        "id": "lib::@1",
        "name": "lib",
        "type": "STATIC_LIBRARY",
        "artifacts": [{"path": "liblib.a"}],
        "sources": [{"path": "/src/lib.c"}],
        "dependencies": [],
    }
    app: Json = {
        "id": "app::@1",
        "name": "app",
        "type": "EXECUTABLE",
        "artifacts": [{"path": "app"}],
        "sources": [{"path": "/src/main.c"}],
        "dependencies": [{"id": "lib::@1"}],
    }
    codemodel: Json = {
        "kind": "codemodel",
        "configurations": [
            {
                "name": "",
                "targets": [
                    {"id": "lib::@1", "name": "lib", "jsonFile": "target-lib.json"},
                    {"id": "app::@1", "name": "app", "jsonFile": "target-app.json"},
                ],
            }
        ],
    }
    toolchains: Json = {
        "kind": "toolchains",
        "toolchains": [
            {"language": "C", "compiler": {"id": "GNU", "path": "/usr/bin/cc", "version": "14.2"}}
        ],
    }
    answers: Json = {
        "query.json": {
            "responses": [
                {"kind": "codemodel", "jsonFile": "codemodel-v2.json"},
                {"kind": "toolchains", "jsonFile": "toolchains-v1.json"},
            ]
        }
    }
    index: Json = {"reply": {CLIENT: answers} if with_client else {}}
    for name, document in [
        ("target-lib.json", lib),
        ("target-app.json", app),
        ("codemodel-v2.json", codemodel),
        ("toolchains-v1.json", toolchains),
        ("index-2026-10-31T00-00-00-0000.json", index),
    ]:
        (reply / name).write_text(json.dumps(document), encoding="utf-8")
    return reply


def test_graph_and_toolchains_from_a_reply(tmp_path: Path) -> None:
    """Targets, edges and compilers come out of the reply files as the model expects."""
    _write_reply(tmp_path)
    reply = read_reply(reply_dir(tmp_path))
    assert reply is not None

    graph = graph_from_reply(reply)
    assert graph.source == "file-api"
    assert [t.name for t in graph.targets] == ["lib", "app"]
    assert graph.target("app::@1").artifacts == ("app",)
    assert graph.target("lib::@1").sources == ("/src/lib.c",)
    assert graph.edges == (("app::@1", "lib::@1"),)
    assert graph.dependencies_of("app::@1") == ("lib::@1",)

    (toolchain,) = toolchains_from_reply(reply)
    assert toolchain.language == "C"
    assert toolchain.compiler_id == "GNU"
    assert toolchain.compiler_path == "/usr/bin/cc"
    assert toolchain.compiler_version == "14.2"


def test_reply_for_another_client_only_is_no_reply(tmp_path: Path) -> None:
    """An index that does not mention our client means CMake never saw our query."""
    _write_reply(tmp_path, with_client=False)
    assert read_reply(reply_dir(tmp_path)) is None


def test_unknown_configuration_is_an_error(tmp_path: Path) -> None:
    """Asking for a configuration the codemodel does not have fails by name."""
    _write_reply(tmp_path)
    reply = read_reply(reply_dir(tmp_path))
    assert reply is not None
    with pytest.raises(FileApiError, match="Release"):
        graph_from_reply(reply, configuration="Release")


def test_broken_reply_file_is_a_file_api_error(tmp_path: Path) -> None:
    """A target file that is not JSON is reported as a File API problem, with the location."""
    reply = _write_reply(tmp_path)
    (reply / "target-app.json").write_text("{not json", encoding="utf-8")
    loaded = read_reply(reply_dir(tmp_path))
    assert loaded is not None
    with pytest.raises(FileApiError, match="codemodel"):
        graph_from_reply(loaded)


@pytest.mark.cmake
@pytest.mark.slow
def test_real_chain_graph_has_the_dependency_chain(built_chain: BuiltChain) -> None:
    """For the chain fixture, every installed CMake reports a <- b <- c and a C compiler."""
    reply = read_reply(reply_dir(built_chain.project.build_dir))
    assert reply is not None

    graph = graph_from_reply(reply)
    by_name = {target.name: target for target in graph.targets}
    assert set(by_name) == {"a", "b", "c"}
    assert by_name["a"].type == by_name["b"].type == "SHARED_LIBRARY"
    assert by_name["c"].type == "EXECUTABLE"
    assert any(path.endswith("gen_b.c") for path in by_name["b"].sources)
    assert by_name["c"].artifacts == ("c",)

    edges_by_name = {
        (graph.target(dependent).name, graph.target(dependency).name)
        for dependent, dependency in graph.edges
    }
    assert {("b", "a"), ("c", "b")} <= edges_by_name

    (toolchain,) = toolchains_from_reply(reply)
    assert toolchain.language == "C"
    assert toolchain.compiler_id
    assert toolchain.compiler_path


@pytest.mark.cmake
@pytest.mark.slow
def test_query_written_after_generate_is_answered_by_the_next_configure(
    chain: CMakeProject,
) -> None:
    """The reply appears only once CMake has generated with the query in place."""
    chain.write_query(InstrumentationQuery(hooks=("postCMakeBuild",), callbacks=(chain.callback,)))
    chain.configure()
    assert read_reply(reply_dir(chain.build_dir)) is None

    write_query(chain.build_dir)
    assert read_reply(reply_dir(chain.build_dir)) is None

    chain.configure()
    assert read_reply(reply_dir(chain.build_dir)) is not None
