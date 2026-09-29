"""UI-independent operations for safely recovering interrupted monitoring."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable
from uuid import UUID

from research_engine.ports.ingestion import CoverageCheckpoint
from research_engine.ports.recovery import (
    RecoveryStore, RunningExecutionAttempt, RunningProviderRun,
)
from research_engine.ports.scheduling import QuerySchedule


class RecoveryService:
    def __init__(
        self, store: RecoveryStore, *, clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def list_running_runs(self) -> tuple[RunningProviderRun, ...]:
        return self._store.list_running_runs()

    def list_running_attempts(self) -> tuple[RunningExecutionAttempt, ...]:
        return self._store.list_running_attempts()

    def list_attempts(self, query_id: str | None = None, *, limit: int = 200, offset: int = 0):
        """Newest-first audit history; paging does not discard retained attempts."""
        if not 1 <= limit <= 5000 or offset < 0:
            raise ValueError("attempt history requires limit 1–5000 and non-negative offset")
        query_id = str(UUID(str(query_id))) if query_id is not None else None
        return self._store.list_attempts(query_id, limit=limit, offset=offset)

    def list_coverage(self, query_id: str) -> tuple[CoverageCheckpoint, ...]:
        return self._store.list_coverage(str(query_id))

    def recover_run(
        self, run_id: str, *, confirm_worker_stopped: bool = False,
    ) -> None:
        if not confirm_worker_stopped:
            raise ValueError("confirm that the provider worker has stopped before recovery")
        self._store.recover_run(str(run_id), ended_at=self._now())

    def recover_attempt(
        self, attempt_id: str, *, confirm_worker_stopped: bool = False,
    ) -> None:
        if not confirm_worker_stopped:
            raise ValueError("confirm that the request worker has stopped before recovery")
        self._store.recover_attempt(str(attempt_id), ended_at=self._now())

    def restart_coverage(
        self, query_id: str, provider: str, lane: str, *,
        confirm_worker_stopped: bool = False,
    ) -> None:
        if not confirm_worker_stopped:
            raise ValueError("confirm that all workers have stopped before restarting coverage")
        if lane not in {"full", "recent"}:
            raise ValueError("unknown monitoring coverage lane")
        self._store.restart_coverage(
            str(query_id), provider, lane, requested_at=self._now(),
        )

    def queue_retry(self, query_id: str) -> QuerySchedule:
        return self._store.queue_retry(str(query_id), requested_at=self._now())

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("recovery clock must return a timezone-aware datetime")
        return value
