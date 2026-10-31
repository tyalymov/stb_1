"""Everything that talks to CMake's own files and executables.

The instrumentation query, index and snippet files, the File API reply, the
cache and the ``cmake`` binary's self-description all live here. Nothing in
this package knows about longpole's store or commands; it turns CMake's
formats into :mod:`longpole.model` objects and back.
"""
