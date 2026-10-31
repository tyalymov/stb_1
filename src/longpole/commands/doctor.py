"""``longpole doctor``: will this build tree record anything, and if not, what to do.

The instrumentation API has a few ways to be switched on and still record
nothing: a query that was written after the tree was generated, a query
with hooks but no callback, a generator without launcher support, a CMake
too old to know the API. Each check here looks at one of them and answers
with what was seen and, when something is wrong, what to do about it. The
exit code is 1 as soon as any check has an action attached, so the command
also works as a gate in a script.
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast

from longpole import context, output
from longpole.cmake import fileapi, tooling
from longpole.command import Command
from longpole.errors import CMakeToolError
from longpole.exporters import terminal
from longpole.jsonfields import FieldError, load_object
from longpole.store import RunStore, resolve_store

log = logging.getLogger(__name__)

OK = "ok"
WARNING = "warning"
PROBLEM = "problem"

SUPPORTED_GENERATOR_WORDS: tuple[str, ...] = ("Makefiles", "Ninja", "FASTBuild")
"""A generator whose name contains one of these can run the recording launcher."""


@dataclass(frozen=True, kw_only=True)
class DoctorOptions:
    """Arguments of ``longpole doctor``.

    Attributes:
        build_dir: The build tree to examine.
        cmake: ``--cmake``: executable to consult instead of the tree's or PATH's.
    """

    build_dir: Path
    cmake: Path | None


@dataclass(frozen=True, kw_only=True)
class Finding:
    """One check's verdict.

    Attributes:
        check: What was looked at, e.g. ``query`` or ``build system``.
        status: :data:`OK`, :data:`WARNING` or :data:`PROBLEM`.
        detail: What was seen, in one sentence.
        action: What to do about it, or ``None`` when nothing is needed.
    """

    check: str
    status: str
    detail: str
    action: str | None = None


@dataclass(frozen=True, kw_only=True)
class Report:
    """Every finding for one build tree.

    Attributes:
        build_dir: The tree, as given.
        findings: Checks in the order they were made.
    """

    build_dir: Path
    findings: tuple[Finding, ...]

    @property
    def actions(self) -> tuple[str, ...]:
        """Distinct actions the findings ask for, in order; empty when all is well."""
        seen: list[str] = []
        for finding in self.findings:
            if finding.action is not None and finding.action not in seen:
                seen.append(finding.action)
        return tuple(seen)


def register(parser: argparse.ArgumentParser) -> None:
    """Declare the arguments of ``doctor``.

    Args:
        parser: The command's subparser.
    """
    parser.add_argument("build_dir", type=Path, metavar="BUILD_DIR", help="build tree to examine")
    parser.add_argument(
        "--cmake",
        type=Path,
        metavar="PATH",
        help="cmake executable to consult; default: the one that configured the tree, "
        "else the first on PATH",
    )


def options_from(namespace: argparse.Namespace) -> DoctorOptions:
    """Type the parsed arguments.

    Args:
        namespace: argparse's result for this command.

    Returns:
        The options.
    """
    return DoctorOptions(build_dir=namespace.build_dir, cmake=namespace.cmake)


def run() -> int:
    """Examine the tree and print the findings.

    Returns:
        0 when no finding asks for an action, 1 otherwise.
    """
    options = context.options(DoctorOptions)
    cli = context.cli()
    report = examine(
        options.build_dir,
        cmake_override=options.cmake,
        store_override=cli.store,
        environ=context.env().environ,
    )
    if cli.json_output:
        output.stdout(json.dumps(_as_json(report), indent=2))
    else:
        output.stdout(describe(report))
    return 1 if report.actions else 0


def examine(
    build_dir: Path,
    *,
    cmake_override: Path | None,
    store_override: Path | None,
    environ: Mapping[str, str],
) -> Report:
    """Run every check against a build tree.

    Nothing is modified. A ``cmake`` that cannot be run is a finding, not an
    exception; only unexpected failures propagate.

    Args:
        build_dir: The tree; it need not exist or be configured.
        cmake_override: ``--cmake``, if given.
        store_override: ``--store``, if given.
        environ: Environment variables, for the store rule.

    Returns:
        The report.
    """
    findings: list[Finding] = []
    cache = tooling.read_cache(build_dir)
    configured = cache.generator is not None

    info = _describe_cmake(_pick_cmake(cmake_override, cache), findings)
    config_dir = _config_dir(info.executable) if info is not None else None

    queries = tooling.query_files(build_dir, config_dir=config_dir)
    if configured:
        findings.append(
            Finding(check="build tree", status=OK, detail=f"configured, {cache.generator}")
        )
        findings.append(_generator_finding(cache.generator or ""))
    else:
        findings.append(
            Finding(
                check="build tree",
                status=OK if queries else WARNING,
                detail="not configured yet; a query applies from the first configure on",
            )
        )
    findings.append(_query_finding(build_dir, queries, config_dir))
    if configured and queries:
        findings.append(_freshness_finding(build_dir, queries))
    findings.append(_graph_finding(build_dir, configured))
    findings.append(_pending_data_finding(build_dir))
    findings.append(_store_finding(build_dir, store_override, environ))
    return Report(build_dir=build_dir, findings=tuple(findings))


def describe(report: Report) -> str:
    """Render a report as a table plus the list of things to do.

    Args:
        report: The report.

    Returns:
        The text.
    """
    rows = [(f.check, f.status, f.detail) for f in report.findings]
    text = terminal.render(
        terminal.table(f"longpole doctor: {report.build_dir}", ("check", "status", "detail"), rows)
    )
    if report.actions:
        steps = "\n".join(f"  {n}. {action}" for n, action in enumerate(report.actions, start=1))
        return f"{text}\nto do:\n{steps}"
    return f"{text}\nall good: this tree records every build"


def generator_supported(generator: str) -> bool:
    """Whether a generator can run the recording launcher.

    Args:
        generator: ``CMAKE_GENERATOR`` as the cache spells it.

    Returns:
        ``True`` for the Makefile, Ninja and FASTBuild families; ``False`` for
        Visual Studio, Xcode and anything else.
    """
    return any(word in generator for word in SUPPORTED_GENERATOR_WORDS)


def _pick_cmake(override: Path | None, cache: tooling.CacheInfo) -> Path | None:
    """Choose the executable to consult: ``--cmake``, the tree's, then PATH.

    Args:
        override: ``--cmake``, if given.
        cache: The tree's cache facts.

    Returns:
        The executable, or ``None`` when none is available.
    """
    if override is not None:
        return override
    if cache.cmake_command is not None and cache.cmake_command.is_file():
        return cache.cmake_command
    found = shutil.which("cmake")
    return Path(found) if found else None


def _describe_cmake(cmake: Path | None, findings: list[Finding]) -> tooling.CMakeInfo | None:
    """Append the ``cmake`` finding and return what the executable reported.

    Args:
        cmake: The executable, or ``None``.
        findings: The list being built.

    Returns:
        The executable's facts, or ``None`` when it is missing, broken or too old.
    """
    if cmake is None:
        findings.append(
            Finding(
                check="cmake",
                status=PROBLEM,
                detail="no cmake executable found",
                action="pass --cmake or put cmake on PATH",
            )
        )
        return None
    try:
        info = tooling.describe(cmake)
    except CMakeToolError as exc:
        findings.append(
            Finding(
                check="cmake",
                status=PROBLEM,
                detail=f"cannot run {cmake}: {exc}",
                action="point --cmake at a working executable",
            )
        )
        return None
    if info.data_version is None:
        findings.append(
            Finding(
                check="cmake",
                status=PROBLEM,
                detail=f"CMake {info.version} predates the instrumentation API",
                action="use CMake 4.3 or newer",
            )
        )
        return None
    findings.append(
        Finding(
            check="cmake",
            status=OK,
            detail=f"CMake {info.version}, data version {info.data_version}",
        )
    )
    return info


def _config_dir(cmake: Path) -> Path | None:
    """The user-level configuration directory, or ``None`` when ``cmake`` cannot say.

    Args:
        cmake: A working executable.

    Returns:
        The directory, or ``None``.
    """
    try:
        return tooling.config_dir(cmake)
    except CMakeToolError as exc:
        log.debug("cannot find the user-level query directory: %s", exc)
        return None


def _generator_finding(generator: str) -> Finding:
    """Judge the tree's generator.

    Args:
        generator: ``CMAKE_GENERATOR`` from the cache.

    Returns:
        The finding.
    """
    if generator_supported(generator):
        return Finding(check="generator", status=OK, detail=f"{generator} runs the launcher")
    return Finding(
        check="generator",
        status=PROBLEM,
        detail=f"{generator} has no instrumentation support",
        action="configure the tree with Ninja or a Makefiles generator",
    )


def _query_finding(build_dir: Path, queries: tuple[Path, ...], config_dir: Path | None) -> Finding:
    """Judge the query files that apply to the tree.

    Args:
        build_dir: The tree.
        queries: Query files found, tree first.
        config_dir: The user-level configuration directory, if known.

    Returns:
        The finding.
    """
    if not queries:
        return Finding(
            check="query",
            status=PROBLEM,
            detail="no instrumentation query applies to this tree",
            action=f"run `longpole enable {build_dir}` or `longpole enable --global`",
        )
    places: list[str] = []
    for query in queries:
        scope = "user" if config_dir is not None and query.is_relative_to(config_dir) else "tree"
        places.append(f"{scope}: {query.name}")
    if not any(_names_a_callback(query) for query in queries):
        return Finding(
            check="query",
            status=PROBLEM,
            detail=f"{', '.join(places)}; none names a callback, so CMake deletes the data unread",
            action=f"run `longpole enable {build_dir}` to register the capture callback",
        )
    return Finding(check="query", status=OK, detail=", ".join(places))


def _names_a_callback(query: Path) -> bool:
    """Whether a query file registers at least one callback.

    Args:
        query: The file.

    Returns:
        ``True`` when its ``callbacks`` list is non-empty; ``False`` for an
        unreadable file too, which is then reported through the same finding.
    """
    try:
        document = load_object(query)
    except (OSError, ValueError, FieldError):
        return False
    callbacks = document.get("callbacks")
    return isinstance(callbacks, list) and len(cast(list[object], callbacks)) > 0


def _freshness_finding(build_dir: Path, queries: tuple[Path, ...]) -> Finding:
    """Judge whether the build system was generated after the queries.

    Args:
        build_dir: The configured tree.
        queries: Query files that apply to it.

    Returns:
        The finding.
    """
    if tooling.generated_before(build_dir, queries):
        return Finding(
            check="build system",
            status=WARNING,
            detail="generated before the query; compile, link and custom steps are not recorded",
            action=f"run cmake again in {build_dir}",
        )
    return Finding(check="build system", status=OK, detail="generated after the query")


def _graph_finding(build_dir: Path, configured: bool) -> Finding:
    """Judge whether runs from this tree will carry a target graph.

    Args:
        build_dir: The tree.
        configured: Whether it has a cache.

    Returns:
        The finding.
    """
    if not fileapi.query_path(build_dir).is_file():
        return Finding(
            check="target graph",
            status=WARNING,
            detail="no File API query yet; the first capture plants one and the next configure "
            "answers it",
        )
    if fileapi.read_reply(build_dir) is None:
        return Finding(
            check="target graph",
            status=WARNING,
            detail="File API query present, reply pending",
            action=f"run cmake again in {build_dir}" if configured else None,
        )
    return Finding(check="target graph", status=OK, detail="File API reply available")


def _pending_data_finding(build_dir: Path) -> Finding:
    """Report snippets lying in the data directory that no hook has indexed.

    Args:
        build_dir: The tree.

    Returns:
        The finding; informational only.
    """
    data_dir = build_dir / ".cmake" / "instrumentation" / "v1" / "data"
    count = sum(1 for _ in data_dir.glob("*.json")) if data_dir.is_dir() else 0
    detail = f"{count} snippets waiting for a hook" if count else "no unindexed data"
    return Finding(check="data", status=OK, detail=detail)


def _store_finding(
    build_dir: Path, store_override: Path | None, environ: Mapping[str, str]
) -> Finding:
    """Report which store this tree's runs go to and how many are there.

    Args:
        build_dir: The tree.
        store_override: ``--store``, if given.
        environ: Environment variables, for the store rule.

    Returns:
        The finding; informational only.
    """
    resolved = build_dir.resolve()
    root = resolve_store(store_override, build_dir=resolved, environ=environ)
    count = len(RunStore(root).list(build_dir=resolved)) if root.is_dir() else 0
    return Finding(
        check="store",
        status=OK,
        detail=f"{root}: {count} run{'s' if count != 1 else ''} from this tree",
    )


def _as_json(report: Report) -> dict[str, object]:
    """Serialise a report.

    Args:
        report: The report.

    Returns:
        A JSON-ready object with ``build_dir``, ``needs_action``, ``actions``
        and ``findings``.
    """
    return {
        "build_dir": str(report.build_dir),
        "needs_action": bool(report.actions),
        "actions": list(report.actions),
        "findings": [asdict(finding) for finding in report.findings],
    }


COMMAND = Command(
    name="doctor",
    help="check whether a build tree records its builds, and say what to fix if not",
    description=(
        "Look at the cmake executable, the generator, the query files, the build system's "
        "age relative to them, the File API reply and the run store, and list what needs "
        "doing. Exit code 1 when something does."
    ),
    register=register,
    options_from=options_from,
    run=run,
)
