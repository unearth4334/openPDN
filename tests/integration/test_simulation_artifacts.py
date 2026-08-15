"""Artifact store: atomic publication and path-traversal defence."""

from __future__ import annotations

import json

import pytest

from openpdn.infrastructure.simulation_artifacts import (
    ArtifactSecurityError,
    FilesystemArtifactStore,
)

pytestmark = pytest.mark.integration

JOB = "job-0123456789abcdef"


@pytest.fixture
def store(tmp_path) -> FilesystemArtifactStore:
    return FilesystemArtifactStore(tmp_path)


class TestAtomicPublication:
    def test_unpublished_working_area_is_not_a_result(self, store):
        working = store.working_dir(JOB)
        (working / "manifest.json").write_text("{}")
        assert store.result_dir(JOB) is None  # nothing published yet

    def test_publish_promotes_atomically(self, store):
        working = store.working_dir(JOB)
        (working / "manifest.json").write_text('{"schema": 1}')
        (working / "metrics.json").write_text("{}")
        store.publish(JOB)
        published = store.result_dir(JOB)
        assert published is not None
        assert json.loads((published / "manifest.json").read_text()) == {"schema": 1}
        # The working area is gone: no half-state exists.
        assert store.working_dir(JOB).name.endswith(".working")

    def test_publish_without_manifest_is_refused(self, store):
        working = store.working_dir(JOB)
        (working / "metrics.json").write_text("{}")
        with pytest.raises(FileNotFoundError):
            store.publish(JOB)
        assert store.result_dir(JOB) is None

    def test_publish_with_corrupt_manifest_is_refused(self, store):
        working = store.working_dir(JOB)
        (working / "manifest.json").write_text("{not json")
        with pytest.raises(json.JSONDecodeError):
            store.publish(JOB)
        assert store.result_dir(JOB) is None

    def test_discard_working_cleans_up(self, store):
        working = store.working_dir(JOB)
        (working / "partial.npz").write_bytes(b"x")
        store.discard_working(JOB)
        assert not working.exists()

    def test_stale_working_cleanup(self, store):
        store.working_dir(JOB)
        assert store.cleanup_stale_working() == 1
        assert store.cleanup_stale_working() == 0


class TestPathSafety:
    @pytest.mark.parametrize(
        "bad_id",
        ["../etc", "job-../../x", "job-XYZ", "", "job-0123456789abcdef/../..", "a" * 200],
    )
    def test_traversal_shaped_job_ids_are_refused(self, store, bad_id):
        with pytest.raises(ArtifactSecurityError):
            store.working_dir(bad_id)
        with pytest.raises(ArtifactSecurityError):
            store.result_dir(bad_id)

    def test_bad_board_digest_is_refused(self, store):
        with pytest.raises(ArtifactSecurityError):
            store.save_board_document("../../secrets", "{}")
