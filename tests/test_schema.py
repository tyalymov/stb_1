"""Instrumentation files pass the vendored CMake schemas, and bad ones are named precisely."""

from __future__ import annotations

import pytest

from longpole.cmake.schema import Json, validate
from longpole.errors import SchemaError
from tests.conftest import BuiltChain
from tests.support.cmake import InstrumentationQuery


def _snippet(**overrides: object) -> Json:
    """A minimal valid data-1.0 compile snippet, with fields overridden on request.

    Args:
        **overrides: Fields to replace or add.

    Returns:
        The snippet as CMake would write it.
    """
    snippet: Json = {
        "version": {"major": 1, "minor": 0},
        "role": "compile",
        "command": "cc -c a.c",
        "workingDir": "/build",
        "result": 0,
        "timeStart": 1700000000000,
        "duration": 12,
        "target": "a",
        "language": "C",
        "source": "/src/a.c",
        "outputs": ["a.o"],
        "outputSizes": [100],
        "config": "",
    }
    snippet.update(overrides)
    return snippet


def test_minimal_snippet_is_valid() -> None:
    """The smallest compile snippet the schema allows passes."""
    validate("snippet", _snippet())


def test_missing_required_field_is_reported_with_its_name() -> None:
    """A snippet without ``duration`` fails, and the message says which field is missing."""
    snippet = _snippet()
    del snippet["duration"]
    with pytest.raises(SchemaError, match="duration"):
        validate("snippet", snippet, source="snippet-x.json")


def test_error_message_points_into_the_document() -> None:
    """A bad nested value is located by its JSON path."""
    with pytest.raises(SchemaError, match=r"at version/minor"):
        validate("snippet", _snippet(version={"major": 1, "minor": "two"}))


def test_index_minor_version_outside_v1_is_rejected() -> None:
    """The index schema only knows minors 0 to 2."""
    index: Json = {
        "version": {"major": 1, "minor": 3},
        "buildDir": "/build",
        "dataDir": "/build/.cmake/instrumentation/v1/data",
        "hook": "postCMakeBuild",
        "snippets": [],
    }
    with pytest.raises(SchemaError):
        validate("index", index)


def test_query_we_write_is_valid() -> None:
    """The query layout the test support (and later ``enable``) writes passes the query schema."""
    query = InstrumentationQuery(
        hooks=("postCMakeBuild",), callbacks=("longpole capture",), options=("processMetrics",)
    )
    validate("query", query.to_json())


@pytest.mark.cmake
@pytest.mark.slow
def test_everything_cmake_writes_passes_its_own_schema(built_chain: BuiltChain) -> None:
    """Indexes and snippets from every installed CMake conform to the vendored schemas."""
    for capture in built_chain.captures:
        validate("index", capture.index, source=str(capture.directory / "index.json"))
        for position, snippet in enumerate(capture.snippets):
            validate("snippet", snippet, source=f"{capture.directory}/snippets/{position}")
