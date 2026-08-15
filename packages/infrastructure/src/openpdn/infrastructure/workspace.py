"""Isolated working directories.

Untrusted archives are expanded, and external tools are run, inside a workspace
created under the configured data directory -- never in the process's own
working directory and never in a path derived from a user-supplied filename.
"""

from __future__ import annotations

import re
import shutil
import tempfile
import uuid
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Final

#: Characters allowed in a workspace label. Anything else is replaced, so a
#: hostile "filename" cannot influence where files land.
_LABEL_ALLOWED: Final = re.compile(r"[^A-Za-z0-9._-]")
_LABEL_MAX_LENGTH: Final = 48


def sanitise_label(label: str) -> str:
    """Reduce `label` to a safe directory-name fragment.

    Filenames from uploads are untrusted: they may contain separators, `..`,
    null bytes or control characters. This keeps them usable as a *hint* while
    stripping any ability to choose a location.
    """
    cleaned = _LABEL_ALLOWED.sub("_", label.strip()).strip("._-")
    return (cleaned or "unnamed")[:_LABEL_MAX_LENGTH]


@dataclass(frozen=True, slots=True)
class Workspace:
    """A directory owned by one unit of work."""

    path: Path
    label: str

    def subdirectory(self, name: str) -> Path:
        """Create and return a child directory with a sanitised name."""
        child = self.path / sanitise_label(name)
        child.mkdir(parents=True, exist_ok=True)
        return child


class TemporaryWorkspace(AbstractContextManager["Workspace"]):
    """Creates an isolated workspace and removes it on exit.

    Example:
        >>> with TemporaryWorkspace(root, label="upload.zip") as workspace:  # doctest: +SKIP
        ...     staged = workspace.path / "archive.zip"
    """

    def __init__(self, root: Path, label: str = "work", keep: bool = False) -> None:
        """Prepare a workspace under `root`; `keep` retains it for debugging."""
        self._root = root
        self._label = sanitise_label(label)
        self._keep = keep
        self._workspace: Workspace | None = None

    def __enter__(self) -> Workspace:
        """Create the workspace directory."""
        self._root.mkdir(parents=True, exist_ok=True)
        # The random suffix, not the caller's label, guarantees uniqueness.
        path = Path(
            tempfile.mkdtemp(
                prefix=f"openpdn-{self._label}-{uuid.uuid4().hex[:8]}-", dir=self._root
            )
        )
        self._workspace = Workspace(path=path, label=self._label)
        return self._workspace

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Remove the workspace unless it was created with `keep=True`."""
        if self._workspace is not None and not self._keep:
            shutil.rmtree(self._workspace.path, ignore_errors=True)
