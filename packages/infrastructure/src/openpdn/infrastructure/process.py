"""Running external tools.

ElmerFEM is a separate solver binary, and some future importers wrap external
tools: openPDN drives them as subprocesses. Every such call goes through
`run_tool`, which exists to make one class of bug impossible -- a filename or a
study name reaching a shell.

Rules enforced here:

* argument lists only, never a command string, never `shell=True`;
* a mandatory timeout, so a wedged backend cannot hang a request forever;
* an explicit working directory, normally an isolated workspace;
* a minimal, explicit environment rather than the server's own.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_logger = logging.getLogger(__name__)

#: Environment variables an external tool may inherit. Anything else -- tokens,
#: registry credentials, cloud metadata -- is withheld.
_INHERITED_ENVIRONMENT_KEYS: Final = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")

DEFAULT_TIMEOUT_SECONDS: Final = 600.0

#: Numerical-library thread-count variables pinned on worker subprocesses so
#: concurrent jobs cannot oversubscribe the host.
_THREAD_COUNT_KEYS: Final = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
)


def worker_environment(threads: int) -> dict[str, str]:
    """Environment for a solver worker subprocess.

    Inherits the parent environment (workers must see the same OPENPDN_*
    configuration as the orchestrator) with numerical thread counts pinned.
    This module is the one sanctioned place that touches `os.environ`.
    """
    environment = dict(os.environ)
    for key in _THREAD_COUNT_KEYS:
        environment[key] = str(max(1, threads))
    return environment


class ExternalToolError(Exception):
    """An external tool failed, timed out or is not installed."""


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Outcome of one external tool invocation."""

    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float


def resolve_tool(executable: str) -> Path:
    """Locate `executable` on PATH.

    Raises:
        ExternalToolError: If the tool is not installed. Adapters turn this
            into an "unavailable" descriptor rather than a crash, so the UI can
            explain why a backend cannot be selected.
    """
    located = shutil.which(executable)
    if located is None:
        raise ExternalToolError(f"Executable {executable!r} was not found on PATH")
    return Path(located)


def run_tool(
    argv: Sequence[str],
    *,
    cwd: Path,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    extra_environment: Mapping[str, str] | None = None,
    check: bool = True,
) -> ToolResult:
    """Run an external tool and return its result.

    Args:
        argv: Program and arguments as separate items. Never a shell string.
        cwd: Working directory, normally an isolated workspace.
        timeout_seconds: Wall-clock limit; the process is killed on expiry.
        extra_environment: Additional variables for the child. Keep this to
            tool configuration -- never pass credentials through it.
        check: Raise on a non-zero exit status.

    Raises:
        ExternalToolError: On a missing executable, a timeout, or (with
            `check`) a non-zero exit status.
    """
    if not argv:
        raise ExternalToolError("run_tool requires a non-empty argument list")

    environment = {key: os.environ[key] for key in _INHERITED_ENVIRONMENT_KEYS if key in os.environ}
    if extra_environment:
        environment.update(extra_environment)

    started = time.perf_counter()
    try:
        completed = subprocess.run(  # noqa: S603 - argv list, shell=False, fixed cwd
            list(argv),
            cwd=cwd,
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
            shell=False,
        )
    except FileNotFoundError as exc:
        raise ExternalToolError(f"Executable {argv[0]!r} was not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise ExternalToolError(f"{argv[0]!r} exceeded its {timeout_seconds:g} s timeout") from exc

    duration_seconds = time.perf_counter() - started
    result = ToolResult(
        argv=tuple(argv),
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        duration_seconds=duration_seconds,
    )
    _logger.debug(
        "external.tool.finished",
        extra={
            "event": "external.tool.finished",
            "tool": argv[0],
            "returncode": result.returncode,
            "duration_seconds": round(duration_seconds, 6),
        },
    )
    if check and completed.returncode != 0:
        raise ExternalToolError(
            f"{argv[0]!r} exited with status {completed.returncode}: "
            f"{completed.stderr.strip()[:500]}"
        )
    return result
