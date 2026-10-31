"""Renderers that turn analysis results into text for people, machines or other tools.

Each module here takes model or analysis objects and returns text; nothing
in this package writes to a stream itself. The command layer decides where
the text goes.
"""

from __future__ import annotations
