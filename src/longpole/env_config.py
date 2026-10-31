"""Everything ``longpole`` takes from its environment, read once at startup.

Environment variables, the working directory and similar process-level facts
are gathered into one frozen :class:`EnvConfig` by :func:`load_env` and
published through :mod:`longpole.context`. Code elsewhere reads the config
instead of ``os.environ``, so a test can hand ``main`` a different
environment by setting variables before the call and nothing deep in the
call stack needs to know.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

CI_RUN_ID_VARIABLE = "GITHUB_RUN_ID"
"""Set by GitHub Actions; a run captured under it is labelled ``ci:<id>``."""

CMAKE_CONFIG_DIR_VARIABLE = "CMAKE_CONFIG_DIR"
"""CMake's own override of its user-level configuration directory."""


@dataclass(frozen=True, kw_only=True)
class EnvConfig:
    """Process environment as seen when ``main`` started.

    Attributes:
        cwd: Working directory of the process.
        environ: Read-only snapshot of the environment variables. Passed on
            to code that resolves paths from variables, such as the run store.
        ci_run_id: Value of ``GITHUB_RUN_ID``, or ``None`` outside CI.
        cmake_config_dir: Value of ``CMAKE_CONFIG_DIR``, or ``None`` when
            CMake uses its platform default.
    """

    cwd: Path
    environ: Mapping[str, str]
    ci_run_id: str | None
    cmake_config_dir: Path | None


def load_env(environ: Mapping[str, str] | None = None, *, cwd: Path | None = None) -> EnvConfig:
    """Snapshot the environment.

    Args:
        environ: Variables to read; defaults to the process environment.
        cwd: Working directory; defaults to the process's current directory.

    Returns:
        The configuration. Empty variable values count as unset.
    """
    variables = MappingProxyType(dict(os.environ if environ is None else environ))
    config_dir = variables.get(CMAKE_CONFIG_DIR_VARIABLE)
    return EnvConfig(
        cwd=cwd if cwd is not None else Path.cwd(),
        environ=variables,
        ci_run_id=variables.get(CI_RUN_ID_VARIABLE) or None,
        cmake_config_dir=Path(config_dir) if config_dir else None,
    )
