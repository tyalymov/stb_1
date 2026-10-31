"""Exceptions longpole raises on purpose.

Every error a user can hit derives from :class:`LongpoleError` and carries the
exit code ``main`` should return for it, so the command layer can map failures
to exit codes without a table of its own. Programming errors stay ordinary
Python exceptions.
"""

from __future__ import annotations


class LongpoleError(Exception):
    """Base of every expected failure.

    Attributes:
        exit_code: Process exit code that represents this failure; 1 unless a
            subclass says otherwise.
    """

    exit_code: int = 1


class UsageError(LongpoleError):
    """The command line could not be parsed.

    The message is argparse's usage text plus the complaint, ready to print
    to standard error as is.

    Attributes:
        exit_code: 2, the convention for misuse of a command-line program.
    """

    exit_code = 2


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


class UnsupportedCMakeError(LongpoleError):
    """The ``cmake`` found is older than 4.3 and has no instrumentation API.

    The message names the executable and its version so the user can point
    ``--cmake`` at another one.
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

    Raised for an id prefix that matches nothing or several runs, a ``sha:``
    or ``label:`` with no match, or ``latest`` in an empty store.
    """
