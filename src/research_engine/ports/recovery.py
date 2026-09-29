"""UI- and database-neutral contracts for operational recovery actions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from research_engine.ports.ingestion import CoverageCheckpoint
from research_engine.ports.scheduling import QuerySchedule


@dataclass(frozen=True, slots=True)
class RunningProviderRun:
    id: str
    query_id: str
    provider: str
    started_at: datetime
    scheduled_execution_id: str | None
    cursor: str | None
    result_count: int


@dataclass(frozen=True, slots=True)
class RunningExecutionAttempt:
    id: str
    requested_query_id: str
    requested_provider: str
    request_reference: str | None
    started_at: datetime
    run_id: str | None


@dataclass(frozen=True, slots=True)
class ExecutionAttemptSummary:
    id: str
    requested_query_id: str
    requested_provider: str
    request_reference: str | None
    started_at: datetime
    ended_at: datetime | None
    state: str
    error_code: str | None
    run_id: str | None


class RecoveryStore(Protocol):
    """Transactional operations for recovering runs and retrying schedules."""

    def list_running_runs(self) -> tuple[RunningProviderRun, ...]: ...

    def list_coverage(self, query_id: str) -> tuple[CoverageCheckpoint, ...]: ...

    def list_running_attempts(self) -> tuple[RunningExecutionAttempt, ...]: ...

    def list_attempts(self, query_id: str | None = None, *, limit: int = 200, offset: int = 0) -> tuple[ExecutionAttemptSummary, ...]: ...

    def recover_attempt(self, attempt_id: str, *, ended_at: datetime) -> None: ...

    def recover_run(self, run_id: str, *, ended_at: datetime) -> None: ...

    def restart_coverage(
        self, query_id: str, provider: str, lane: str, *, requested_at: datetime
    ) -> None: ...

    def queue_retry(self, query_id: str, *, requested_at: datetime) -> QuerySchedule: ...
