"""Validate instrumentation files against the schemas CMake publishes.

The three v1 schemas (query, index, snippet) are vendored under ``schemas/``
at a pinned CMake tag; see the README there. Validation is the first thing
the reader does with a file, so a CMake that changes its format fails here
with a precise message instead of somewhere deep in normalisation.
"""

# jsonschema ships partial type information. The three calls below are the
# whole surface this module uses, so the Unknowns are contained here.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false

from __future__ import annotations

import json
from functools import cache
from importlib import resources
from typing import Any, Literal, cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError, best_match

from longpole.errors import SchemaError

Kind = Literal["query", "index", "snippet"]

Json = dict[str, object]
"""A parsed JSON object. The shape is CMake's, so values stay ``object``."""


@cache
def _validator(kind: Kind) -> Draft202012Validator:
    """Load one vendored schema and build a validator for it, once per process.

    Args:
        kind: Which schema to load.

    Returns:
        A validator for the 2020-12 draft the schemas declare.
    """
    text = resources.files("longpole.cmake.schemas").joinpath(f"{kind}-v1-schema.json")
    schema = json.loads(text.read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


def validate(kind: Kind, document: Json, *, source: str = "<memory>") -> None:
    """Check one document against the schema of its kind.

    Succeeds silently when the document conforms. Nothing is modified.

    Args:
        kind: ``query``, ``index`` or ``snippet``.
        document: The parsed JSON object to check.
        source: Where the document came from, for the error message; usually
            a file path.

    Raises:
        SchemaError: The document violates the schema. The message carries
            the kind, ``source``, the JSON path of the worst violation and the
            validator's description of it.
    """
    # jsonschema types its input as a recursive JSON alias our plain dict does
    # not satisfy; the document is a parsed JSON object either way.
    instance = cast(Any, document)
    errors: list[ValidationError] = list(_validator(kind).iter_errors(instance))
    if not errors:
        return
    worst = _most_specific(errors, document)
    location = "/".join(str(part) for part in worst.absolute_path) or "<root>"
    raise SchemaError(f"{kind} file {source} is not valid v1 data at {location}: {worst.message}")


def _most_specific(errors: list[ValidationError], document: Json) -> ValidationError:
    """Pick the error worth showing.

    The snippet schema is a ``oneOf`` over the data versions 1.0, 1.1 and 1.2,
    so any violation surfaces as "not valid under any of the given schemas"
    with the whole document in the message. The useful error is one level
    down, in the branch that matches the document's own ``version.minor``;
    when that minor is itself broken, any branch tells the same story.

    Args:
        errors: Every violation the validator produced; non-empty.
        document: The document, to read its declared minor version.

    Returns:
        The violation to report.
    """
    worst: ValidationError = best_match(errors)
    if worst.validator != "oneOf" or not worst.context:
        return worst
    branch_errors: list[ValidationError] = list(worst.context)
    minor = _declared_minor(document)
    if minor is not None:
        same_branch = [error for error in branch_errors if error.relative_schema_path[0] == minor]
        if same_branch:
            branch_errors = same_branch
    return best_match(branch_errors)


def _declared_minor(document: Json) -> int | None:
    """Read ``version.minor`` from a document, if it is there and is an integer.

    Args:
        document: Parsed index or snippet.

    Returns:
        The minor version, or ``None`` when absent or malformed.
    """
    version = document.get("version")
    if not isinstance(version, dict):
        return None
    minor = cast(dict[str, object], version).get("minor")
    return minor if isinstance(minor, int) else None
