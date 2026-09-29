from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import gzip
import hashlib

import pytest

from research_engine.infrastructure.snapshots import (
    LocalSnapshotStorage,
    SnapshotIntegrityError,
)


def test_snapshot_storage_round_trips_raw_bytes_across_instances(tmp_path):
    body = b'{"provider":"pubmed","items":[1,2,3]}\x00'
    first_storage = LocalSnapshotStorage(tmp_path)

    snapshot = first_storage.save(body)

    assert snapshot.sha256 == hashlib.sha256(body).hexdigest()
    assert snapshot.byte_size == len(body)
    assert snapshot.compression == "gzip"
    assert first_storage.load(snapshot) == body
    assert LocalSnapshotStorage(tmp_path).load(snapshot) == body


def test_gzip_encoding_is_deterministic_and_duplicate_save_does_not_change_file(tmp_path):
    body = b"same provider response" * 50
    storage = LocalSnapshotStorage(tmp_path)

    snapshot = storage.save(body)
    stored_path = tmp_path / snapshot.storage_key
    first_encoding = stored_path.read_bytes()
    duplicate = storage.save(body)

    assert duplicate == snapshot
    assert stored_path.read_bytes() == first_encoding
    assert gzip.decompress(first_encoding) == body


def test_none_compression_is_supported(tmp_path):
    body = b"uncompressed response"
    storage = LocalSnapshotStorage(tmp_path, compression="none")

    snapshot = storage.save(body)

    assert snapshot.compression == "none"
    assert (tmp_path / snapshot.storage_key).read_bytes() == body
    assert storage.load(snapshot) == body


def test_load_rejects_modified_snapshot_and_save_never_repairs_it(tmp_path):
    body = b"original response"
    storage = LocalSnapshotStorage(tmp_path)
    snapshot = storage.save(body)
    stored_path = tmp_path / snapshot.storage_key
    original_encoding = stored_path.read_bytes()
    stored_path.write_bytes(b"corrupted")

    with pytest.raises(SnapshotIntegrityError):
        storage.load(snapshot)
    with pytest.raises(SnapshotIntegrityError):
        storage.save(body)

    assert stored_path.read_bytes() == b"corrupted"
    assert stored_path.read_bytes() != original_encoding


def test_load_rejects_path_traversal_keys(tmp_path):
    storage = LocalSnapshotStorage(tmp_path)
    snapshot = storage.save(b"response")
    forged_snapshot = replace(snapshot, storage_key="../../outside")

    with pytest.raises(ValueError, match="storage_key"):
        storage.load(forged_snapshot)


def test_concurrent_identical_writes_publish_one_immutable_snapshot(tmp_path):
    body = b"one response shared by concurrent workers" * 100
    storage = LocalSnapshotStorage(tmp_path)

    with ThreadPoolExecutor(max_workers=12) as workers:
        snapshots = list(workers.map(storage.save, [body] * 36))

    assert all(snapshot == snapshots[0] for snapshot in snapshots)
    assert storage.load(snapshots[0]) == body
    assert len(list(tmp_path.glob("sha256/gzip/*/*"))) == 1
