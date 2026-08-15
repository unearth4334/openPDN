"""Untrusted archive handling.

PCB deliveries arrive as archives from outside the trust boundary -- zipped
IPC-2581 packages today, ODB++ jobs when that importer lands. These tests
encode the attacks openPDN must refuse; deleting one is a security regression,
not a cleanup.
"""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from openpdn.infrastructure.archives import (
    ExtractionLimits,
    UnsafeArchiveError,
    safe_extract_tar,
    safe_extract_zip,
    safe_relative_path,
)
from openpdn.infrastructure.workspace import TemporaryWorkspace, sanitise_label

pytestmark = pytest.mark.integration


def _zip_with(entries: dict[str, bytes], path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return path


class TestPathTraversal:
    @pytest.mark.parametrize(
        "member",
        [
            "../escaped.txt",
            "steps/../../escaped.txt",
            "/absolute.txt",
            "..\\windows-escape.txt",
            "C:\\windows\\system32\\evil.txt",
        ],
    )
    def test_escaping_members_are_rejected(self, member: str, tmp_path: Path):
        with pytest.raises(UnsafeArchiveError):
            safe_relative_path(member, tmp_path / "dest")

    def test_a_traversing_zip_writes_nothing_outside_the_destination(self, tmp_path: Path):
        archive = _zip_with({"../pwned.txt": b"x"}, tmp_path / "evil.zip")
        destination = tmp_path / "dest"
        with pytest.raises(UnsafeArchiveError):
            safe_extract_zip(archive, destination)
        assert not (tmp_path / "pwned.txt").exists()

    def test_a_traversing_tar_is_rejected(self, tmp_path: Path):
        archive_path = tmp_path / "evil.tar"
        with tarfile.open(archive_path, "w") as archive:
            payload = b"x"
            info = tarfile.TarInfo("../pwned.txt")
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
        with pytest.raises(UnsafeArchiveError):
            safe_extract_tar(archive_path, tmp_path / "dest")
        assert not (tmp_path / "pwned.txt").exists()

    def test_a_tar_symlink_member_is_rejected(self, tmp_path: Path):
        # A symlink into /etc lets a later, innocent-looking write escape.
        archive_path = tmp_path / "link.tar"
        with tarfile.open(archive_path, "w") as archive:
            info = tarfile.TarInfo("link")
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            archive.addfile(info)
        with pytest.raises(UnsafeArchiveError, match="link"):
            safe_extract_tar(archive_path, tmp_path / "dest")


class TestResourceLimits:
    def test_a_decompression_bomb_hits_the_total_limit(self, tmp_path: Path):
        archive = _zip_with({"big.bin": b"0" * 2_000_000}, tmp_path / "bomb.zip")
        with pytest.raises(UnsafeArchiveError, match="limit"):
            safe_extract_zip(
                archive,
                tmp_path / "dest",
                ExtractionLimits(max_total_bytes=1024, max_member_bytes=1024),
            )

    def test_too_many_members_are_refused(self, tmp_path: Path):
        archive = _zip_with({f"f{i}.txt": b"x" for i in range(20)}, tmp_path / "many.zip")
        with pytest.raises(UnsafeArchiveError, match="members"):
            safe_extract_zip(archive, tmp_path / "dest", ExtractionLimits(max_members=5))


class TestHappyPath:
    def test_a_normal_archive_extracts(self, tmp_path: Path):
        archive = _zip_with(
            {"steps/pcb/layers/top/features": b"copper", "matrix/matrix": b"rows"},
            tmp_path / "board.zip",
        )
        report = safe_extract_zip(archive, tmp_path / "dest")
        assert report.member_count == 2
        assert (tmp_path / "dest" / "steps/pcb/layers/top/features").read_bytes() == b"copper"


class TestWorkspaceIsolation:
    @pytest.mark.parametrize(
        ("supplied", "forbidden"),
        [
            ("../../etc/passwd", "/"),
            ("board;rm -rf /.zip", ";"),
            ("nul\x00byte", "\x00"),
        ],
    )
    def test_an_upload_filename_cannot_choose_a_location(self, supplied: str, forbidden: str):
        assert forbidden not in sanitise_label(supplied)

    def test_a_workspace_is_created_and_removed(self, tmp_path: Path):
        with TemporaryWorkspace(tmp_path / "root", label="upload.zip") as workspace:
            created = workspace.path
            assert created.is_dir()
            assert created.is_relative_to(tmp_path / "root")
        assert not created.exists()

    def test_two_workspaces_with_the_same_label_do_not_collide(self, tmp_path: Path):
        root = tmp_path / "root"
        with (
            TemporaryWorkspace(root, label="board") as first,
            TemporaryWorkspace(root, label="board") as second,
        ):
            assert first.path != second.path
