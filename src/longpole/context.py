"""Where the active configuration lives while a command runs.

``main`` parses the command line and reads the environment exactly once,
then publishes both results here. Any module can read them back with
:func:`cli` and :func:`env` instead of receiving them as parameters through
every call. A command's own options are not here: they are the parameters
of its function.

The storage is :mod:`contextvars`, not module globals. :func:`activate`
sets the values for the duration of a block and restores the previous state
on exit, so a test can call ``main([...])`` many times in one process with
different flags and nothing leaks from one call into the next. Threads and
asyncio tasks get their own copies for free.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from longpole.cli_config import CliConfig
    from longpole.env_config import EnvConfig

_cli: ContextVar[CliConfig] = ContextVar("longpole_cli")
_env: ContextVar[EnvConfig] = ContextVar("longpole_env")


@contextmanager
def activate(cli: CliConfig, env: EnvConfig) -> Generator[None, None, None]:
    """Publish a configuration for the duration of a ``with`` block.

    Only ``main`` and tests call this. On exit the previous values are
    restored, whether the block finished or raised.

    Args:
        cli: Parsed command line.
        env: Environment snapshot.

    Yields:
        Nothing; the configuration is read through :func:`cli` and :func:`env`.
    """
    cli_token = _cli.set(cli)
    env_token = _env.set(env)
    try:
        yield
    finally:
        _env.reset(env_token)
        _cli.reset(cli_token)


def cli() -> CliConfig:
    """The active command line.

    Returns:
        The configuration published by the enclosing :func:`activate`.

    Raises:
        RuntimeError: No configuration is active; the caller runs outside
            ``main``, which is a programming error, not a user error.
    """
    try:
        return _cli.get()
    except LookupError:
        raise RuntimeError("no active configuration: context.activate() was not entered") from None


def env() -> EnvConfig:
    """The active environment snapshot.

    Returns:
        The configuration published by the enclosing :func:`activate`.

    Raises:
        RuntimeError: No configuration is active.
    """
    try:
        return _env.get()
    except LookupError:
        raise RuntimeError("no active configuration: context.activate() was not entered") from None
