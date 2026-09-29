"""Immutable byte storage contract for auditable provider responses."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class StoredSnapshot:
    storage_key: str
    sha256: str
    compression: str
    byte_size: int


class SnapshotStorage(Protocol):
    def save(self, body: bytes) -> StoredSnapshot:
        """Persist bytes immutably and return an integrity-checked reference."""
        ...

    def load(self, snapshot: StoredSnapshot) -> bytes:
        """Return original bytes after checking the stored content hash."""
        ...
