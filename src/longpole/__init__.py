"""longpole: build telemetry for CMake.

CMake 4.3 and later can record every configure, compile, link, custom
command and test step of a build. longpole captures those records into a
self-contained run dump and answers the question the raw data cannot:
what actually gated this build.
"""

from __future__ import annotations

__version__ = "0.0.1.dev0"

__all__ = ["__version__"]
