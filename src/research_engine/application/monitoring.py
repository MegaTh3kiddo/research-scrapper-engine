"""Reusable scheduled-monitoring use cases with no Qt dependency."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timedelta, timezone
import logging

from research_engine.application.searches import ManualSearchService
from research_engine.ports.scheduling import (
    QuerySchedule, ScheduledExecution, ScheduleStore,
)


_LOG = logging.getLogger(__name__)


class ScheduledMonitoringService:
    def __init__(
        self, store: ScheduleStore,
        search_factory: Callable[[], AbstractAsyncContextManager[ManualSearchService]],
        on_completed: Callable[[str], None] | None = None,
    ) -> None:
        self._store = store
        self._search_factory = search_factory
        self._on_completed = on_completed

    def configure(
        self, query_id: str, *, interval_minutes: int = 60,
        enabled: bool = True, next_due_at: datetime | None = None,
    ) -> QuerySchedule:
        if interval_minutes < 1:
            raise ValueError("schedule interval must be at least one minute")
        when = next_due_at or datetime.now(timezone.utc) + timedelta(minutes=interval_minutes)
        if when.tzinfo is None or when.utcoffset() is None:
            raise ValueError("next_due_at must be timezone-aware")
        return self._store.configure(
            query_id, interval_minutes=interval_minutes,
            enabled=enabled, next_due_at=when,
        )

    def get_schedule(self, query_id: str) -> QuerySchedule | None:
        return self._store.get_schedule(query_id)

    def list_executions(self, query_id: str) -> tuple[ScheduledExecution, ...]:
        return self._store.list_executions(query_id)

    def list_running(self) -> tuple[ScheduledExecution, ...]:
        return self._store.list_running()

    def recover_interrupted(
        self, execution_id: str, *, confirm_worker_stopped: bool = False,
    ) -> ScheduledExecution:
        if not confirm_worker_stopped:
            raise ValueError("confirm that the worker has stopped before recovery")
        recovered = self._store.recover(execution_id, ended_at=datetime.now(timezone.utc))
        _LOG.warning(
            "scheduled_execution_recovered execution_id=%s query_id=%s linked_runs=%d",
            recovered.id, recovered.query_id, len(recovered.search_run_ids),
        )
        return recovered

    async def run_due_once(
        self, *, now: datetime | None = None, max_claims: int = 10,
        max_pages: int | None = None,
    ) -> tuple[ScheduledExecution, ...]:
        if max_claims < 1 or (max_pages is not None and max_pages < 1):
            raise ValueError("max_claims and max_pages must be positive")
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        completed: list[ScheduledExecution] = []
        for _ in range(max_claims):
            claim = self._store.claim_due(current)
            if claim is None:
                break
            _LOG.info(
                "scheduled_execution_started execution_id=%s query_id=%s providers=%d",
                claim.execution_id, claim.query_id, len(claim.providers),
            )
            try:
                partial = False
                if not claim.providers:
                    raise ValueError("scheduled query has no enabled providers")
                async with self._search_factory() as search:
                    selected = search.selected_sources(claim.providers)
                    if not selected:
                        raise ValueError("no query sources are enabled for monitoring")
                    failures: list[Exception] = []
                    for provider in selected:
                        try:
                            options = {"scheduled_execution_id": claim.execution_id}
                            if max_pages is not None:
                                options["max_pages"] = max_pages
                            results = await search.run_monitoring_query(claim.query_id, provider, **options)
                            partial = partial or any(result.state == "partial" for result in results)
                        except Exception as exc:
                            failures.append(exc)
                    if failures:
                        raise failures[0]
            except Exception as exc:
                _LOG.warning(
                    "scheduled_execution_failed execution_id=%s query_id=%s error_code=%s",
                    claim.execution_id, claim.query_id, type(exc).__name__,
                )
                completed.append(self._store.finish(
                    claim.execution_id, state="failed",
                    ended_at=datetime.now(timezone.utc),
                    error_code=type(exc).__name__,
                ))
            else:
                _LOG.info(
                    "scheduled_execution_finished execution_id=%s query_id=%s",
                    claim.execution_id, claim.query_id,
                )
                completed.append(self._store.finish(
                    claim.execution_id, state="partial" if partial else "completed",
                    ended_at=datetime.now(timezone.utc),
                ))
                if self._on_completed is not None:
                    try:
                        self._on_completed(claim.query_id)
                    except Exception as exc:
                        _LOG.warning(
                            "scheduled_sheet_sync_failed execution_id=%s error_code=%s",
                            claim.execution_id, type(exc).__name__,
                        )
        return tuple(completed)
