"""Safe extraction of untrusted archives.

PCB deliveries arrive as ZIP or tar archives from outside the trust boundary --
zipped IPC-2581 packages today, ODB++ jobs when that importer lands.
Python's `ZipFile.extractall` and `TarFile.extractall` will happily write
outside the destination directory given a crafted entry, so openPDN never calls
them directly -- extraction goes through this module.

Defences implemented here:

* path traversal (`../../etc/cron.d/x`) and absolute member paths;
* symlinks and hard links, which can redirect a later write outside the target;
* device/FIFO members;
* decompression bombs, via per-member and total uncompressed size limits;
* member-count limits.

See SECURITY.md.
"""

from __future__ import annotations

import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import IO, Final

#: Conservative defaults; callers with a legitimately larger board raise them
#: explicitly rather than the limits being absent.
DEFAULT_MAX_TOTAL_BYTES: Final = 2 * 1024**3
DEFAULT_MAX_MEMBER_BYTES: Final = 512 * 1024**2
DEFAULT_MAX_MEMBERS: Final = 200_000


class UnsafeArchiveError(Exception):
    """The archive attempts something extraction must not allow."""


@dataclass(frozen=True, slots=True)
class ExtractionLimits:
    """Bounds applied while extracting an untrusted archive."""

    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES
    max_member_bytes: int = DEFAULT_MAX_MEMBER_BYTES
    max_members: int = DEFAULT_MAX_MEMBERS


@dataclass(frozen=True, slots=True)
class ExtractionReport:
    """What an extraction actually wrote."""

    member_count: int
    total_bytes: int
    destination: Path


def safe_relative_path(member_name: str, destination: Path) -> Path:
    """Resolve `member_name` inside `destination`, or reject it.

    Raises:
        UnsafeArchiveError: If the member is absolute, contains a drive letter,
            or escapes `destination` via `..`.
    """
    normalised = member_name.replace("\\", "/")
    pure = PurePosixPath(normalised)
    has_drive_letter = len(normalised) > 1 and normalised[1] == ":"
    if pure.is_absolute() or has_drive_letter:
        raise UnsafeArchiveError(f"Archive member is not a relative path: {member_name!r}")
    if not pure.parts:
        raise UnsafeArchiveError("Archive member has an empty path")
    if any(part == ".." for part in pure.parts):
        raise UnsafeArchiveError(f"Archive member escapes the destination: {member_name!r}")

    target = (destination / Path(*pure.parts)).resolve()
    root = destination.resolve()
    if not target.is_relative_to(root):
        raise UnsafeArchiveError(f"Archive member escapes the destination: {member_name!r}")
    return target


def safe_extract_zip(
    archive_path: Path,
    destination: Path,
    limits: ExtractionLimits | None = None,
) -> ExtractionReport:
    """Extract a ZIP archive into `destination` under `limits`.

    Only regular files and directories are written; every other member type is
    rejected rather than skipped, so a hostile archive fails loudly.
    """
    applied = limits or ExtractionLimits()
    destination.mkdir(parents=True, exist_ok=True)
    written_bytes = 0
    written_members = 0

    with zipfile.ZipFile(archive_path) as archive:
        infos = archive.infolist()
        if len(infos) > applied.max_members:
            raise UnsafeArchiveError(
                f"Archive holds {len(infos)} members, above the {applied.max_members} limit"
            )
        for info in infos:
            target = safe_relative_path(info.filename, destination)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            _reject_zip_special_member(info)
            if info.file_size > applied.max_member_bytes:
                raise UnsafeArchiveError(
                    f"Member {info.filename!r} declares {info.file_size} bytes, "
                    f"above the {applied.max_member_bytes} limit"
                )
            written_bytes += info.file_size
            if written_bytes > applied.max_total_bytes:
                raise UnsafeArchiveError(
                    f"Archive exceeds the {applied.max_total_bytes} byte extraction limit"
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as sink:
                # Copy in bounded chunks: the declared size is attacker-controlled,
                # so it is checked again against what actually arrives.
                actual = _copy_bounded(source, sink, applied.max_member_bytes)
            if actual != info.file_size:
                raise UnsafeArchiveError(
                    f"Member {info.filename!r} expanded to {actual} bytes, "
                    f"not the declared {info.file_size}"
                )
            written_members += 1

    return ExtractionReport(written_members, written_bytes, destination)


def safe_extract_tar(
    archive_path: Path,
    destination: Path,
    limits: ExtractionLimits | None = None,
) -> ExtractionReport:
    """Extract a tar archive into `destination` under `limits`."""
    applied = limits or ExtractionLimits()
    destination.mkdir(parents=True, exist_ok=True)
    written_bytes = 0
    written_members = 0

    with tarfile.open(archive_path) as archive:
        for member in archive:
            target = safe_relative_path(member.name, destination)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                raise UnsafeArchiveError(
                    f"Archive member {member.name!r} is a link or special file"
                )
            if member.size > applied.max_member_bytes:
                raise UnsafeArchiveError(
                    f"Member {member.name!r} declares {member.size} bytes, "
                    f"above the {applied.max_member_bytes} limit"
                )
            written_bytes += member.size
            written_members += 1
            if written_bytes > applied.max_total_bytes:
                raise UnsafeArchiveError(
                    f"Archive exceeds the {applied.max_total_bytes} byte extraction limit"
                )
            if written_members > applied.max_members:
                raise UnsafeArchiveError(f"Archive holds more than {applied.max_members} members")
            source = archive.extractfile(member)
            if source is None:  # pragma: no cover - defensive
                raise UnsafeArchiveError(f"Member {member.name!r} could not be read")
            target.parent.mkdir(parents=True, exist_ok=True)
            with source, target.open("wb") as sink:
                _copy_bounded(source, sink, applied.max_member_bytes)

    return ExtractionReport(written_members, written_bytes, destination)


def _reject_zip_special_member(info: zipfile.ZipInfo) -> None:
    """Reject symlinks and other non-regular ZIP members."""
    unix_mode = info.external_attr >> 16
    file_type = unix_mode & 0o170000
    if unix_mode and file_type not in (0o100000, 0o040000, 0):
        raise UnsafeArchiveError(f"Archive member {info.filename!r} is not a regular file")


def _copy_bounded(source: IO[bytes], sink: IO[bytes], limit_bytes: int) -> int:
    """Copy at most `limit_bytes` from `source` to `sink`, returning the count."""
    chunk_size = 1024 * 1024
    total = 0
    while True:
        chunk = source.read(chunk_size)
        if not chunk:
            return total
        total += len(chunk)
        if total > limit_bytes:
            raise UnsafeArchiveError(
                f"Member expanded past the {limit_bytes} byte limit (decompression bomb?)"
            )
        sink.write(chunk)
