"""Event values; durable delivery is an application/infrastructure concern."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping
from uuid import UUID, uuid4

from research_engine.domain.models import _require_aware, utc_now


@dataclass(frozen=True, slots=True)
class DomainEvent:
    type: str
    aggregate_type: str
    aggregate_id: UUID
    payload: Mapping[str, Any]
    id: UUID = field(default_factory=uuid4)
    schema_version: int = 1
    occurred_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.type.strip() or not self.aggregate_type.strip():
            raise ValueError("event type and aggregate type must be non-empty")
        if self.schema_version < 1:
            raise ValueError("schema_version must be positive")
        _require_aware(self.occurred_at, "occurred_at")
