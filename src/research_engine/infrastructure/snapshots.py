"""Content-addressed local storage for immutable response snapshots."""

from __future__ import annotations

import gzip
import hashlib
import io
import os
import re
import tempfile
import zlib
from pathlib import Path
from typing import Literal

from research_engine.ports.snapshots import StoredSnapshot


Compression = Literal["gzip", "none"]
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


class SnapshotIntegrityError(OSError):
    """Raised when a stored snapshot is missing, corrupt, or misidentified."""


class LocalSnapshotStorage:
    """Persist immutable snapshots in a local, content-addressed directory.

    The returned key is derived only from the raw body's SHA-256 digest and
    the configured encoding. Writes are staged in the destination directory
    and installed with a hard link, which publishes a complete file atomically
    and never replaces an existing snapshot. ``gzip`` output has a fixed
    timestamp and filename so identical inputs have identical stored bytes.
    """

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        compression: Compression = "gzip",
        compression_level: int = 6,
    ) -> None:
        if compression not in ("gzip", "none"):
            raise ValueError("compression must be 'gzip' or 'none'")
        if not 0 <= compression_level <= 9:
            raise ValueError("compression_level must be between 0 and 9")

        self._root = Path(root).expanduser().resolve()
        self._mkdir_durable(self._root)
        self._compression = compression
        self._compression_level = compression_level

    def save(self, body: bytes) -> StoredSnapshot:
        """Persist ``body`` once and return its content-addressed reference."""
        if not isinstance(body, bytes):
            raise TypeError("snapshot body must be bytes")

        digest = hashlib.sha256(body).hexdigest()
        snapshot = StoredSnapshot(
            storage_key=self._storage_key(digest),
            sha256=digest,
            compression=self._compression,
            byte_size=len(body),
        )
        target = self._path_for(snapshot)
        parent = target.parent
        self._ensure_safe_parent(parent)
        self._mkdir_durable(parent)
        self._ensure_safe_parent(parent)

        encoded = self._encode(body)
        temp_path: Path | None = None
        try:
            descriptor, temp_name = tempfile.mkstemp(
                prefix=f".{digest}.", suffix=".tmp", dir=parent
            )
            temp_path = Path(temp_name)
            with os.fdopen(descriptor, "wb") as temporary:
                temporary.write(encoded)
                temporary.flush()
                os.fsync(temporary.fileno())

            try:
                # Hard-link creation is atomic and fails if target already
                # exists, avoiding the overwrite window of os.replace().
                os.link(temp_path, target)
                self._fsync_directory(parent)
            except FileExistsError:
                existing = self.load(snapshot)
                if existing != body:
                    raise SnapshotIntegrityError(
                        f"existing snapshot does not match content at {snapshot.storage_key}"
                    )
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink()
                except FileNotFoundError:
                    pass

        return snapshot

    def load(self, snapshot: StoredSnapshot) -> bytes:
        """Load and verify raw bytes for a previously stored snapshot."""
        path = self._path_for(snapshot)
        self._ensure_safe_parent(path.parent)
        try:
            if path.is_symlink():
                raise SnapshotIntegrityError("snapshot path must not be a symlink")
            encoded = path.read_bytes()
        except FileNotFoundError as error:
            raise SnapshotIntegrityError(
                f"snapshot not found: {snapshot.storage_key}"
            ) from error
        except OSError as error:
            if isinstance(error, SnapshotIntegrityError):
                raise
            raise SnapshotIntegrityError(
                f"unable to read snapshot: {snapshot.storage_key}"
            ) from error

        try:
            body = self._decode(encoded, snapshot.compression)
        except (OSError, EOFError, zlib.error) as error:
            raise SnapshotIntegrityError(
                f"snapshot encoding is corrupt: {snapshot.storage_key}"
            ) from error

        actual_digest = hashlib.sha256(body).hexdigest()
        if actual_digest != snapshot.sha256:
            raise SnapshotIntegrityError(
                f"snapshot hash mismatch: {snapshot.storage_key}"
            )
        if len(body) != snapshot.byte_size:
            raise SnapshotIntegrityError(
                f"snapshot size mismatch: {snapshot.storage_key}"
            )
        return body

    def _storage_key(self, digest: str) -> str:
        return f"sha256/{self._compression}/{digest[:2]}/{digest}"

    def _path_for(self, snapshot: StoredSnapshot) -> Path:
        if not isinstance(snapshot, StoredSnapshot):
            raise TypeError("snapshot must be a StoredSnapshot")
        if not isinstance(snapshot.sha256, str) or not _SHA256_RE.fullmatch(
            snapshot.sha256
        ):
            raise ValueError("snapshot sha256 must be 64 lowercase hexadecimal characters")
        if snapshot.compression != self._compression:
            raise ValueError("snapshot compression does not match this storage")
        expected_key = self._storage_key(snapshot.sha256)
        if snapshot.storage_key != expected_key:
            raise ValueError("snapshot storage_key is invalid")
        if not isinstance(snapshot.byte_size, int) or snapshot.byte_size < 0:
            raise ValueError("snapshot byte_size must be a non-negative integer")

        # Construct from validated digest components, never from the supplied
        # key, so path traversal strings cannot reach filesystem operations.
        return (
            self._root
            / "sha256"
            / self._compression
            / snapshot.sha256[:2]
            / snapshot.sha256
        )

    def _ensure_safe_parent(self, parent: Path) -> None:
        """Reject symlinked storage components that could escape the root."""
        if parent == self._root:
            return
        try:
            relative = parent.relative_to(self._root)
        except ValueError as error:
            raise ValueError("snapshot path escapes storage root") from error
        current = self._root
        for component in relative.parts:
            current = current / component
            if current.is_symlink():
                raise SnapshotIntegrityError("snapshot storage directories must not be symlinks")
        if current.exists() and current.resolve() != parent.resolve():
            raise SnapshotIntegrityError("snapshot path escapes storage root")

    def _encode(self, body: bytes) -> bytes:
        if self._compression == "none":
            return body
        output = io.BytesIO()
        with gzip.GzipFile(
            fileobj=output,
            mode="wb",
            filename="",
            compresslevel=self._compression_level,
            mtime=0,
        ) as compressed:
            compressed.write(body)
        return output.getvalue()

    @staticmethod
    def _decode(encoded: bytes, compression: str) -> bytes:
        if compression == "none":
            return encoded
        if compression == "gzip":
            return gzip.decompress(encoded)
        raise ValueError(f"unsupported snapshot compression: {compression}")

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        if os.name != "posix":
            return
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @classmethod
    def _mkdir_durable(cls, directory: Path) -> None:
        missing: list[Path] = []
        current = directory
        while not current.exists():
            missing.append(current)
            current = current.parent
        for path in reversed(missing):
            path.mkdir(exist_ok=True)
            cls._fsync_directory(path)
            cls._fsync_directory(path.parent)
