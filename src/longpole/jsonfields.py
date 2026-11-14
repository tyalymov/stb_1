"""Typed access to parsed JSON documents whose shape is someone else's.

CMake's instrumentation and File API files are plain JSON. ``json.loads``
gives back ``object``-shaped data; these helpers check each field's runtime
type and raise :class:`FieldError` naming the field, so a malformed file is
reported as such instead of surfacing as a ``TypeError`` three calls later.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

Json = dict[str, object]
"""A parsed JSON object. The shape is CMake's, so values stay ``object``."""


def load_object(path: Path) -> Json:
    """Read a file as a JSON object.

    Raises:
        FileNotFoundError: ``path`` does not exist.
        FieldError: The content is not JSON or not a JSON object.
    """
    text = path.read_text(encoding="utf-8")
    try:
        document: object = json.loads(text)
    except json.JSONDecodeError as exc:
        raise FieldError(f"not JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise FieldError("not a JSON object")
    # JSON object keys are always strings; the cast only tells the type checker so.
    return cast(Json, document)


class FieldError(Exception):
    """A field is missing or has the wrong type.

    Readers catch it at their public boundary and re-raise their own error
    type (``IndexFormatError``, ``SnippetFormatError``, ``FileApiError``)
    with the file the document came from, so callers never see this one.
    """


class Fields:
    """Typed access to the fields of a parsed JSON object.

    Each accessor checks the runtime type and raises :class:`FieldError`
    naming the field, so a malformed file is reported as such rather than as
    a stray ``TypeError`` later.
    """

    def __init__(self, document: Json) -> None:
        """Wrap a document."""
        self._document = document

    def string(self, key: str) -> str:
        """A required string field.

        Raises:
            FieldError: When the field is missing or not a string.
        """
        value = self._document.get(key)
        if not isinstance(value, str):
            raise FieldError(f"field {key!r} must be a string, got {value!r}")
        return value

    def optional_string(self, key: str) -> str | None:
        """A string field that may be absent or ``null``.

        Raises:
            FieldError: When the field is present but not a string.
        """
        value = self._document.get(key)
        if value is None:
            return None
        if not isinstance(value, str):
            raise FieldError(f"field {key!r} must be a string, got {value!r}")
        return value

    def integer(self, key: str) -> int:
        """A required integer field.

        Raises:
            FieldError: When the field is missing or not an integer.
        """
        value = self._document.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            raise FieldError(f"field {key!r} must be an integer, got {value!r}")
        return value

    def optional_int(self, key: str) -> int | None:
        """An integer field that may be absent or ``null``.

        Raises:
            FieldError: When the field is present but not an integer.
        """
        value = self._document.get(key)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise FieldError(f"field {key!r} must be an integer, got {value!r}")
        return value

    def optional_bool(self, key: str) -> bool | None:
        """A boolean field that may be absent.

        Raises:
            FieldError: When the field is present but not a boolean.
        """
        value = self._document.get(key)
        if value is None:
            return None
        if not isinstance(value, bool):
            raise FieldError(f"field {key!r} must be a boolean, got {value!r}")
        return value

    def number(self, key: str) -> float:
        """A required numeric field; integers are accepted and widened.

        Raises:
            FieldError: When the field is missing or not a number.
        """
        value = self._document.get(key)
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise FieldError(f"field {key!r} must be a number, got {value!r}")
        return float(value)

    def strings(self, key: str) -> tuple[str, ...]:
        """An optional array of strings; absent means empty.

        Raises:
            FieldError: When the field is not an array of strings.
        """
        items = self._optional_list(key)
        if not all(isinstance(item, str) for item in items):
            raise FieldError(f"field {key!r} must be an array of strings")
        return tuple(str(item) for item in items)

    def integers(self, key: str) -> tuple[int, ...]:
        """An optional array of integers; absent means empty.

        Raises:
            FieldError: When the field is not an array of integers.
        """
        items = self._optional_list(key)
        if not all(isinstance(item, int) and not isinstance(item, bool) for item in items):
            raise FieldError(f"field {key!r} must be an array of integers")
        return tuple(int(cast(int, item)) for item in items)

    def object(self, key: str) -> Fields:
        """A required nested object, wrapped for further typed access.

        Raises:
            FieldError: When the field is missing or not an object.
        """
        nested = self.optional_object(key)
        if nested is None:
            raise FieldError(f"field {key!r} must be an object, got null")
        return Fields(nested)

    def optional_object(self, key: str) -> Json | None:
        """A nested object that may be absent or ``null``.

        Raises:
            FieldError: When the field is present but not an object.
        """
        value = self._document.get(key)
        if value is None:
            return None
        if not isinstance(value, dict):
            raise FieldError(f"field {key!r} must be an object, got {value!r}")
        return cast(Json, value)

    def _optional_list(self, key: str) -> list[object]:
        """An array field that may be absent; absent means empty.

        Raises:
            FieldError: When the field is present but not an array.
        """
        value = self._document.get(key)
        if value is None:
            return []
        if not isinstance(value, list):
            raise FieldError(f"field {key!r} must be an array, got {value!r}")
        return cast(list[object], value)

    def value(self, key: str) -> object | None:
        """The raw value of a field, for shapes the typed accessors do not cover."""
        return self._document.get(key)

    def objects(self, key: str) -> tuple[Fields, ...]:
        """An optional array of objects, each wrapped; absent means empty.

        Raises:
            FieldError: When the field is not an array of objects.
        """
        items = self._optional_list(key)
        if not all(isinstance(item, dict) for item in items):
            raise FieldError(f"field {key!r} must be an array of objects")
        return tuple(Fields(cast(Json, item)) for item in items)
