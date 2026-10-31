# Contributing to longpole

Thanks for considering a contribution. This page explains how the code is
written so that a change fits in without a round of style comments.

## Setup

```sh
uv sync          # creates .venv with Python 3.11 and installs everything
make check       # lint, typecheck, test; what CI runs
```

The tests build small CMake projects, so they also need CMake 4.3 or newer
and a C and C++ toolchain. Either install those, or open the repository in
its dev container (`.devcontainer/`), which has them and runs `uv sync` on
creation; Codespaces uses the same definition. `LONGPOLE_CMAKE` points the
tests at a `cmake` that is not first on `PATH`.

`make format` applies ruff's formatter and auto-fixes. `pre-commit install`
runs the same checks before every commit.

## Types everywhere

Every parameter, return value, class attribute and module-level variable has a
type annotation. Each module starts with `from __future__ import annotations`.
pyright runs in strict mode and CI fails on any error. Use `Any` only with a
comment that says why nothing narrower works. Interfaces are
`typing.Protocol`; configuration and model objects are
`dataclass(frozen=True, kw_only=True)`.

## Docstrings with a contract

Every function and class has a docstring, public or private. It covers three
things:

1. What the function does, in one or two sentences.
2. The contract: what is guaranteed on success, and under which conditions
   which exception is raised and what has already happened by then (for
   example whether anything was written to disk).
3. One line per argument and for the return value, plus a list of the
   exceptions it raises.

The format is Google style (`Args:`, `Returns:`, `Raises:`). Tooling enforces
it: ruff checks presence and shape, pydoclint checks that every argument,
the return value and every raised exception are documented.

```python
def read_index(path: Path) -> Index:
    """Read a CMake instrumentation index file.

    Succeeds only if the file is a well-formed v1 index; nothing is cached or
    modified on disk. Snippet paths inside are returned as written, relative
    to ``index.data_dir``.

    Args:
        path: Location of ``index-<timestamp>.json`` as CMake passed it to
            the callback.

    Returns:
        Parsed index with the hook name and the list of snippet paths.

    Raises:
        IndexFormatError: The file is not JSON, or its ``version`` is not a
            supported v1 minor.
        FileNotFoundError: ``path`` does not exist. CMake deletes the index
            after callbacks finish, so a late read is a real failure.
    """
```

## Command-line programs

Every executable module has the same shape so the whole program can run
inside pytest by importing `main` and passing a list of arguments.

```python
def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line and turn every outcome into an exit code."""
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        cli = parse_cli(args)
        configure_logging(cli.log_level)
        env = load_env()
        with context.activate(cli, env):
            return COMMANDS[cli.command].run()
    except HelpPrinted:
        return 0
    except UsageError as exc:
        output.stderr(str(exc))
        return exc.exit_code
    except LongpoleError as exc:
        log.error("%s", exc)
        return exc.exit_code
    except KeyboardInterrupt:
        return EXIT_INTERRUPTED
    except Exception:
        log.exception("unexpected error")
        return EXIT_UNEXPECTED


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

This is `longpole/cli.py` verbatim, minus the full docstring.

The rules behind that shape:

- `argv` never contains the program name. `__main__` passes `sys.argv[1:]`
  explicitly; tests pass a list. The `None` branch exists only for the
  console script entry point, which calls `main()` without arguments.
- `parse_cli` uses argparse with a git-like layout: global flags first
  (`--log-level`, `--store`, `--json`), then a command and its flags. The
  parser never exits the process. Parse errors become `UsageError` (exit
  code 2); `--help` prints the help and raises `HelpPrinted`, which `main`
  turns into exit code 0. Otherwise `main` could not be called from a test.
- `load_env` reads everything from the environment in one place:
  environment variables, XDG paths, tool versions.
- `CliConfig` and `EnvConfig` are frozen dataclasses. They are published
  through `contextvars` in `longpole.context`: `context.activate(cli, env)`
  sets them for the duration of a block and restores the previous values
  on exit; `context.cli()` and `context.env()` read them from anywhere.
  Only `main` and tests call `activate`. The reset on exit is what lets a
  test call `main([...])` many times in one process without state leaking
  between calls.
- Each command lives in its own file under `longpole/commands/` and exposes
  one `Command` record (`longpole/command.py`): its `name`, one-line `help`,
  a `description` paragraph and three functions. `register(parser)` adds the
  command's own arguments to the subparser the entry point created for it.
  `options_from(namespace)` turns the parsed arguments into the command's
  own frozen options dataclass, so nothing downstream touches the untyped
  `argparse.Namespace`. `run() -> int` does the work, reading
  `context.cli()`, `context.env()` and its options back with
  `context.options(Options)`, which checks the type. `commands/__init__.py`
  holds the registry; the entry module only builds the parser and
  dispatches.
- Errors are a small hierarchy under `LongpoleError`, each with an
  `exit_code`. Low-level exceptions are wrapped at the layer boundary with
  a message a user can act on.
- The module docstring of an entry module is the `--help` output, verbatim.
  A test compares the two, with the help formatter pinned to a fixed width
  so the text does not depend on the terminal.

## Logging and output

Use the standard `logging` module with one logger per module
(`log = logging.getLogger(__name__)`). `main` configures logging once from
`--log-level`; all log output goes to stderr. Standard output carries only
the result of the command (tables, JSON) because tests and pipes parse it.
The output layer is `longpole/output.py`: `output.stdout(text)` for
results, `output.stderr(text)` for messages to the person at the terminal.
ruff's `T20` rule flags `print` anywhere in the package.

## Tests

- Integration tests go through `main`: call `main([...])` with `tmp_path`
  and `capsys`, then assert on the exit code and the captured output. No
  subprocesses for our own code.
- Unit tests cover the pure functions: analyzers and parsers take a model
  and return a result, without touching the file system.
- CMake fixtures are real projects under `tests/fixtures/`, configured,
  built and tested with real CMake versions. Tests that need CMake carry
  the `cmake` marker; tests that run whole builds carry `slow`.
- Behaviour of CMake that the documentation does not spell out is pinned
  by a test that records what we observed, with the date and version.
- Mock only at the boundary with the outside world (network, GitHub API).
  Our own modules are cheap to call for real.

## Comments and text

A comment explains why, not what; the what is visible in the code and the
docstring. All text in the repository is in English: code, docstrings,
documentation, commit messages, issues.

## Commits

One change per commit, small enough to review in one sitting, and green on
`make check` before it lands. Messages follow Conventional Commits
(`feat:`, `fix:`, `test:`, `docs:`, `ci:`, `chore:`); the body says what
changed and why, not how.
