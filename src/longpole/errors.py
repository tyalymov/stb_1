"""Exceptions longpole raises on purpose.

Every error a user can hit derives from :class:`LongpoleError`, with a message
that says what happened and what to do about it. ``main`` logs the message and
exits with 1, whatever the class; the classes exist so callers can catch one
kind of failure and let the rest through.
"""

from __future__ import annotations


class LongpoleError(Exception):
    """Base of every expected failure."""


class UsageError(LongpoleError):
    """The arguments parsed but do not make sense together, e.g. ``runs prune`` without a limit."""


class SchemaError(LongpoleError):
    """A CMake instrumentation file does not match its published JSON schema.

    The message names the file kind, the offending location inside the
    document and the validator's complaint, so it can be shown as is.
    """


class IndexFormatError(LongpoleError):
    """An instrumentation index file cannot be read as v1 data.

    Raised for a file that is not JSON or not an object, or whose fields do
    not have the types the v1 format promises. Schema violations proper come
    as :class:`SchemaError`.
    """


class SnippetFormatError(LongpoleError):
    """An instrumentation snippet file cannot be read as v1 data.

    Same contract as :class:`IndexFormatError`, for snippets.
    """


class CMakeToolError(LongpoleError):
    """Running or reading the ``cmake`` executable failed.

    Raised when the binary cannot be started, exits non-zero, times out or
    prints something that is not the expected JSON or text.
    """


class FileApiError(LongpoleError):
    """A CMake File API reply cannot be read.

    Raised when a reply file is not JSON, is not an object, or lacks a field
    the codemodel or toolchains object kind promises.
    """


class DumpFormatError(LongpoleError):
    """A run dump on disk does not have the layout ``docs/dump-format.md`` describes.

    Raised for a missing or malformed ``manifest.json`` or ``steps.jsonl``,
    or a field of the wrong type.
    """


class UnknownRunError(LongpoleError):
    """A run reference does not name exactly one stored run.

    Raised for an id prefix that matches nothing or several runs, a
    ``label:`` with no match, or ``latest`` in an empty store.
    """
