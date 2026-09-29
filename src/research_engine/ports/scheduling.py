"""UI- and database-neutral contracts for scheduled monitoring."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class QuerySchedule:
    id: str
    query_id: str
    interval_minutes: int
    next_due_at: datetime
    enabled: bool


@dataclass(frozen=True, slots=True)
class ScheduledClaim:
    execution_id: str
    query_id: str
    providers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ScheduledExecution:
    id: str
    query_id: str
    state: str
    started_at: datetime
    ended_at: datetime | None
    error_code: str | None
    search_run_ids: tuple[str, ...]


class ScheduleStore(Protocol):
    def configure(
        self, query_id: str, *, interval_minutes: int,
        enabled: bool, next_due_at: datetime,
    ) -> QuerySchedule: ...

    def get_schedule(self, query_id: str) -> QuerySchedule | None: ...

    def claim_due(self, now: datetime) -> ScheduledClaim | None: ...

    def finish(
        self, execution_id: str, *, state: str,
        ended_at: datetime, error_code: str | None = None,
    ) -> ScheduledExecution: ...

    def recover(self, execution_id: str, *, ended_at: datetime) -> ScheduledExecution: ...

    def list_executions(self, query_id: str) -> tuple[ScheduledExecution, ...]: ...

    def list_running(self) -> tuple[ScheduledExecution, ...]: ...
