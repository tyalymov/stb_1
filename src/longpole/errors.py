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
