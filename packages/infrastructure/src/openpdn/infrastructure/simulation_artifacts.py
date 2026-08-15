"""Filesystem artifact store with atomic publication (ADR-0011).

Layout under the configured data directory:

    data/
      boards/<digest>.json          canonical board documents (content-addressed)
      results/<job-id>/             published results (never partial)
          manifest.json
          metrics.json
          layers/<n>.npz            mesh + fields per layer, float64
          log.txt
      results/<job-id>.working/     private working area, promoted atomically

Publication is a single `os.replace` of the working directory into place: a
crash mid-write leaves a `.working` directory (cleaned up on recovery), never
a half-readable result. NumPy archives are written and read with pickle
support disabled -- result artifacts are data, never executable objects.

Job ids come from `new_job_id` (hex + fixed prefix); `_safe_job_dir` still
validates them against path traversal because artifact paths are constructed
from persisted strings, and persisted strings are input.
"""

from __future__ import annotations

import json
import re
import shutil
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from pathlib import Path

_JOB_ID_PATTERN: Final = re.compile(r"^job-[0-9a-f]{16}$")
_DIGEST_PATTERN: Final = re.compile(r"^[0-9a-zA-Z_.\-]{1,128}$")


class ArtifactSecurityError(Exception):
    """An artifact path constructed from input failed validation."""


class FilesystemArtifactStore:
    """`SimulationArtifactStore` on a local directory tree."""

    def __init__(self, root: Path) -> None:
        """Anchor the store at `root` (created on demand)."""
        self._root = root
        (root / "boards").mkdir(parents=True, exist_ok=True)
        (root / "results").mkdir(parents=True, exist_ok=True)

    # -- boards -----------------------------------------------------------------------

    def save_board_document(self, digest: str, document_json: str) -> None:
        """Persist a canonical board document, content-addressed."""
        path = self._board_path(digest)
        if path.exists():
            return
        temp = path.with_suffix(".json.tmp")
        temp.write_text(document_json)
        temp.replace(path)

    def load_board_document(self, digest: str) -> str | None:
        """Load a persisted board document."""
        path = self._board_path(digest)
        return path.read_text() if path.exists() else None

    def _board_path(self, digest: str) -> Path:
        if not _DIGEST_PATTERN.fullmatch(digest):
            raise ArtifactSecurityError(f"Invalid board digest {digest!r}")
        return self._root / "boards" / f"{digest}.json"

    # -- results ----------------------------------------------------------------------

    def working_dir(self, job_id: str) -> Path:
        """Create and return the job's private working directory."""
        path = self._safe_job_dir(job_id, working=True)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def publish(self, job_id: str) -> None:
        """Atomically promote the working directory to the published result."""
        working = self._safe_job_dir(job_id, working=True)
        final = self._safe_job_dir(job_id, working=False)
        if not working.is_dir():
            raise FileNotFoundError(f"No working artifacts for job {job_id}")
        manifest = working / "manifest.json"
        if not manifest.is_file():
            raise FileNotFoundError(f"Job {job_id} working area has no manifest; refusing publish")
        # Validate the manifest parses before the result can ever be read.
        json.loads(manifest.read_text())
        if final.exists():
            shutil.rmtree(final)
        working.replace(final)

    def discard_working(self, job_id: str) -> None:
        """Delete a job's working directory."""
        working = self._safe_job_dir(job_id, working=True)
        if working.is_dir():
            shutil.rmtree(working)

    def result_dir(self, job_id: str) -> Path | None:
        """The published result directory, or None."""
        path = self._safe_job_dir(job_id, working=False)
        return path if path.is_dir() else None

    def delete_result(self, job_id: str) -> None:
        """Remove a published result."""
        path = self._safe_job_dir(job_id, working=False)
        if path.is_dir():
            shutil.rmtree(path)

    def cleanup_stale_working(self) -> int:
        """Delete working directories left by crashed workers; returns count."""
        removed = 0
        for entry in (self._root / "results").iterdir():
            if entry.name.endswith(".working") and entry.is_dir():
                shutil.rmtree(entry)
                removed += 1
        return removed

    def _safe_job_dir(self, job_id: str, *, working: bool) -> Path:
        if not _JOB_ID_PATTERN.fullmatch(job_id):
            raise ArtifactSecurityError(f"Invalid job id {job_id!r}")
        name = f"{job_id}.working" if working else job_id
        return self._root / "results" / name
