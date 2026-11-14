"""Read the target dependency graph and toolchains through CMake's File API.

Instrumentation snippets say how long each step took but not what depended
on what; the File API does. A client drops a query file into
``<build>/.cmake/api/v1/query/<client>/`` and CMake answers at generate time
under ``.cmake/api/v1/reply/``. longpole asks for ``codemodel`` version 2
(targets, their dependencies, sources and artifacts) and ``toolchains``
version 1 (which compiler serves which language).

The reply exists only for a tree generated after the query was written. A
missing reply is a normal state, reported as ``None``, so that the caller can
plant the query for the next configure and carry on without a graph.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from longpole.errors import FileApiError
from longpole.jsonfields import FieldError, Fields, Json, load_object
from longpole.model import Graph, TargetNode, Toolchain

CLIENT = "client-longpole"
"""Our client directory name under ``.cmake/api/v1/query/``."""

QUERY: Json = {
    "requests": [
        {"kind": "codemodel", "version": 2},
        {"kind": "toolchains", "version": 1},
    ]
}
"""Shared stateful query: codemodel for the graph, toolchains for the compilers."""


@dataclass(frozen=True, kw_only=True)
class Reply:
    """The parts of a File API reply longpole reads.

    Attributes:
        reply_dir: ``<build>/.cmake/api/v1/reply``; target files are relative to it.
        codemodel: The ``codemodel`` version 2 object.
        toolchains: The ``toolchains`` version 1 object, or ``None`` when CMake
            did not answer that request.
    """

    reply_dir: Path
    codemodel: Json
    toolchains: Json | None


def query_path(build_dir: Path) -> Path:
    """Where our query file lives in a build tree."""
    return build_dir / ".cmake" / "api" / "v1" / "query" / CLIENT / "query.json"


def write_query(build_dir: Path) -> Path:
    """Ask CMake for the codemodel and toolchains on its next generate.

    Creates the query directory if needed and writes the same content every
    time, so calling it twice is harmless. The build tree need not exist yet:
    a query placed before the first configure is answered by that configure.
    """
    path = query_path(build_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(QUERY, indent=2) + "\n", encoding="utf-8")
    return path


def reply_dir(build_dir: Path) -> Path:
    """Where CMake answers File API queries in a build tree."""
    return build_dir / ".cmake" / "api" / "v1" / "reply"


def read_reply(reply_dir: Path) -> Reply | None:
    """Load the newest reply to our query from a reply directory, if CMake answered it.

    The directory is either the tree's own (:func:`reply_dir`) or a copy of
    it, as a run dump keeps.

    Raises:
        FileApiError: A reply file exists but is not the JSON the File API
            promises.
    """
    indexes = sorted(reply_dir.glob("index-*.json")) if reply_dir.is_dir() else []
    if not indexes:
        return None
    try:
        index = Fields(load_object(indexes[-1]))
        responses = index.object("reply").optional_object(CLIENT)
        if responses is None:
            return None
        answers = Fields(responses).object("query.json").objects("responses")
        files = {answer.string("kind"): answer.string("jsonFile") for answer in answers}
        if "codemodel" not in files:
            return None
        toolchains = files.get("toolchains")
        return Reply(
            reply_dir=reply_dir,
            codemodel=load_object(reply_dir / files["codemodel"]),
            toolchains=load_object(reply_dir / toolchains) if toolchains else None,
        )
    except FieldError as exc:
        raise FileApiError(f"File API reply in {reply_dir}: {exc}") from exc


def graph_from_reply(reply: Reply, *, configuration: str | None = None) -> Graph:
    """Build the target graph from a codemodel reply.

    Single-configuration generators have exactly one configuration; for a
    multi-configuration generator the first is used unless one is named.
    Edges follow the codemodel's ``dependencies``, which list what the
    generator makes a target wait for, transitive link dependencies included.

    Raises:
        FileApiError: The codemodel or a target file lacks a promised field,
            or the named configuration does not exist.
    """
    try:
        configurations = Fields(reply.codemodel).objects("configurations")
        chosen = _configuration(configurations, configuration)
        targets: list[TargetNode] = []
        edges: list[tuple[str, str]] = []
        for entry in chosen.objects("targets"):
            target = Fields(load_object(reply.reply_dir / entry.string("jsonFile")))
            target_id = target.string("id")
            targets.append(
                TargetNode(
                    id=target_id,
                    name=target.string("name"),
                    type=target.string("type"),
                    artifacts=tuple(a.string("path") for a in target.objects("artifacts")),
                    sources=tuple(s.string("path") for s in target.objects("sources")),
                )
            )
            edges.extend((target_id, dep.string("id")) for dep in target.objects("dependencies"))
    except FieldError as exc:
        raise FileApiError(f"File API codemodel in {reply.reply_dir}: {exc}") from exc
    return Graph(source="file-api", targets=tuple(targets), edges=tuple(edges))


def toolchains_from_reply(reply: Reply) -> tuple[Toolchain, ...]:
    """Read which compiler CMake chose for each language.

    Raises:
        FileApiError: The toolchains object lacks a promised field.
    """
    if reply.toolchains is None:
        return ()
    try:
        toolchains: list[Toolchain] = []
        for entry in Fields(reply.toolchains).objects("toolchains"):
            compiler = entry.optional_object("compiler")
            about = Fields(compiler) if compiler is not None else Fields({})
            toolchains.append(
                Toolchain(
                    language=entry.string("language"),
                    compiler_id=about.optional_string("id"),
                    compiler_path=about.optional_string("path"),
                    compiler_version=about.optional_string("version"),
                )
            )
    except FieldError as exc:
        raise FileApiError(f"File API toolchains in {reply.reply_dir}: {exc}") from exc
    return tuple(toolchains)


def _configuration(configurations: tuple[Fields, ...], name: str | None) -> Fields:
    """Pick the configuration to read.

    Raises:
        FieldError: There are no configurations, or none has the wanted name.
    """
    if not configurations:
        raise FieldError("codemodel has no configurations")
    if name is None:
        return configurations[0]
    for configuration in configurations:
        if configuration.optional_string("name") == name:
            return configuration
    raise FieldError(f"codemodel has no configuration named {name!r}")
