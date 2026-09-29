"""Audited, UI-independent provider ingestion orchestration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from typing import Callable

from research_engine.application.discovery import PaginationError, UnsupportedFiltersError
from research_engine.application.provider_registry import ProviderRegistry
from research_engine.application.coverage import NoRecentWindow, plan_coverage
from research_engine.ports.ingestion import IngestionStore, PageOutcome
from research_engine.ports.snapshots import SnapshotStorage


_LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class IngestionResult:
    run_id: str
    state: str
    next_cursor: str | None
    pages: int
    outcome: PageOutcome


class IngestionService:
    """Coordinate provider I/O, immutable blobs, and page-level transactions."""

    def __init__(
        self,
        registry: ProviderRegistry,
        store: IngestionStore,
        snapshots: SnapshotStorage,
        *, clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._registry = registry
        self._store = store
        self._snapshots = snapshots
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def supports_recent(self, provider_name: str) -> bool:
        capabilities = getattr(self._registry.get(provider_name), "capabilities", None)
        return capabilities is not None and {"published_from", "published_to"}.issubset(capabilities.supported_filters)

    async def retry(self, run_id: str, *, page_size: int = 100, max_pages: int = 1) -> IngestionResult:
        attempt_id = self._store.begin_attempt(
            "unknown", "unknown", self._clock(), request_reference=run_id,
        )
        try:
            previous = self._store.load_retry(run_id)
            self._store.update_attempt_request(
                attempt_id, previous.query_id, previous.provider,
            )
        except (Exception, asyncio.CancelledError) as exc:
            self._finish_attempt_safely(
                attempt_id,
                state="failed",
                ended_at=self._clock(),
                error_code=type(exc).__name__,
            )
            raise
        return await self._execute_attempt(
            previous.query_id,
            previous.provider,
            page_size=page_size,
            max_pages=max_pages,
            cursor=previous.cursor,
            retry_of_run_id=run_id,
            attempt_id=attempt_id,
        )

    def record_rejection(
        self, query_id: str, provider_name: str, error_code: str,
    ) -> str:
        """Record a workflow-level rejection that occurs before ``run``."""

        attempt_id = self._store.begin_attempt(
            query_id, provider_name, self._clock(),
        )
        self._store.finish_attempt(
            attempt_id,
            state="failed",
            ended_at=self._clock(),
            error_code=error_code,
        )
        return attempt_id

    async def run(
        self,
        query_id: str,
        provider_name: str,
        *,
        page_size: int = 100,
        max_pages: int = 1,
        recent_window_days: int = 30,
        cursor: str | None = None,
        scheduled_execution_id: str | None = None,
        coverage_lane: str | None = None,
        retry_of_run_id: str | None = None,
    ) -> IngestionResult:
        attempt_id = self._store.begin_attempt(
            query_id, provider_name, self._clock(),
        )
        return await self._execute_attempt(
            query_id,
            provider_name,
            page_size=page_size,
            max_pages=max_pages,
            recent_window_days=recent_window_days,
            cursor=cursor,
            scheduled_execution_id=scheduled_execution_id,
            coverage_lane=coverage_lane,
            retry_of_run_id=retry_of_run_id,
            attempt_id=attempt_id,
        )

    async def _execute_attempt(
        self,
        query_id: str,
        provider_name: str,
        *,
        page_size: int,
        max_pages: int,
        recent_window_days: int = 30,
        cursor: str | None = None,
        scheduled_execution_id: str | None = None,
        coverage_lane: str | None = None,
        retry_of_run_id: str | None = None,
        attempt_id: str,
    ) -> IngestionResult:
        try:
            result = await self._run(
                query_id,
                provider_name,
                page_size=page_size,
                max_pages=max_pages,
                recent_window_days=recent_window_days,
                cursor=cursor,
                scheduled_execution_id=scheduled_execution_id,
                coverage_lane=coverage_lane,
                retry_of_run_id=retry_of_run_id,
                attempt_id=attempt_id,
            )
        except NoRecentWindow:
            self._finish_attempt_safely(
                attempt_id,
                state="skipped",
                ended_at=self._clock(),
                error_code="NoRecentWindow",
            )
            raise
        except (Exception, asyncio.CancelledError) as exc:
            self._finish_attempt_safely(
                attempt_id,
                state="failed",
                ended_at=self._clock(),
                error_code=type(exc).__name__,
            )
            raise
        return result

    def _finish_attempt_safely(
        self, attempt_id: str, *, state: str, ended_at: datetime,
        error_code: str | None,
    ) -> None:
        try:
            self._store.finish_attempt(
                attempt_id, state=state, ended_at=ended_at, error_code=error_code,
            )
        except Exception as exc:
            # Keep the original provider/validation failure visible. A still
            # running attempt remains available to the explicit recovery path.
            _LOG.warning(
                "execution_attempt_finalize_failed attempt_id=%s error_code=%s",
                attempt_id, type(exc).__name__,
            )

    async def _run(
        self,
        query_id: str,
        provider_name: str,
        *,
        page_size: int,
        max_pages: int,
        recent_window_days: int,
        cursor: str | None,
        scheduled_execution_id: str | None,
        coverage_lane: str | None,
        retry_of_run_id: str | None,
        attempt_id: str,
    ) -> IngestionResult:
        if max_pages < 1 or page_size < 1 or recent_window_days < 0:
            raise ValueError("max_pages and page_size must be positive; window non-negative")
        provider = self._registry.get(provider_name)
        loaded = self._store.load_request(query_id, provider_name, page_size)
        plan = provider.plan(loaded.request)
        if plan.provider != provider_name:
            raise ValueError("provider returned a plan for another source")
        if plan.unsupported_filters:
            raise UnsupportedFiltersError(
                f"{provider_name} does not support: {', '.join(plan.unsupported_filters)}"
            )
        parser_version = provider.parser_version
        if not parser_version.strip():
            raise ValueError("provider parser_version must be non-empty")
        if retry_of_run_id:
            previous = self._store.load_retry(retry_of_run_id)
            if (scheduled_execution_id or coverage_lane or previous.query_id != query_id
                or previous.provider != provider_name or previous.revision_id != loaded.revision_id
                or previous.plan != plan or previous.cursor != cursor
                or (previous.parser_version is not None and previous.parser_version != parser_version)):
                raise ValueError("query or provider changed; start a new search instead of resuming")
        started_at = self._clock()
        if started_at.tzinfo is None or started_at.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        coverage = None
        if coverage_lane is not None:
            if scheduled_execution_id is None or cursor is not None:
                raise ValueError("monitoring coverage requires a scheduled execution and owns its cursor")
            if coverage_lane == "recent" and not self.supports_recent(provider_name):
                raise UnsupportedFiltersError(f"{provider_name} does not support recent date windows")
            request, coverage = plan_coverage(
                loaded, plan, parser_version, coverage_lane,
                self._store.load_coverage(query_id, provider_name, coverage_lane), started_at,
            )
            plan = provider.plan(request)
            if plan.provider != provider_name or plan.unsupported_filters:
                raise UnsupportedFiltersError("provider cannot honor the monitoring window")
            cursor = coverage.cursor
        run_id = self._store.begin_run(
            query_id, loaded.revision_id, plan, started_at, recent_window_days,
            scheduled_execution_id=scheduled_execution_id,
            coverage=coverage, start_cursor=cursor,
            retry_of_run_id=retry_of_run_id,
            attempt_id=attempt_id,
        )
        _LOG.info(
            "ingestion_started run_id=%s query_id=%s provider=%s scheduled_execution_id=%s",
            run_id, query_id, provider_name, scheduled_execution_id or "none",
        )
        pages = 0
        totals = PageOutcome()
        seen_cursors: set[str | None] = set()
        try:
            for _ in range(max_pages):
                if cursor in seen_cursors:
                    raise PaginationError(f"{provider_name} repeated a search cursor")
                seen_cursors.add(cursor)
                page = await provider.search(plan, cursor=cursor)
                if page.next_cursor is not None and page.next_cursor in seen_cursors:
                    raise PaginationError(f"{provider_name} repeated a search cursor")
                stored = tuple(
                    self._snapshots.save(raw.body) for raw in page.raw_responses
                )
                outcome = self._store.persist_page(
                    run_id,
                    plan,
                    page,
                    stored,
                    parser_version,
                    self._clock(),
                )
                totals = PageOutcome(
                    observed=totals.observed + outcome.observed,
                    newly_discovered=totals.newly_discovered + outcome.newly_discovered,
                    new_to_query=totals.new_to_query + outcome.new_to_query,
                    newly_published=totals.newly_published + outcome.newly_published,
                    metadata_updated=totals.metadata_updated + outcome.metadata_updated,
                    conflicts=totals.conflicts + outcome.conflicts,
                )
                pages += 1
                cursor = page.next_cursor
                if cursor is None:
                    break
        except (Exception, asyncio.CancelledError) as exc:
            # Do not record exception text: HTTP stack details can contain secrets.
            _LOG.warning(
                "ingestion_failed run_id=%s provider=%s error_code=%s pages=%d",
                run_id, provider_name, type(exc).__name__, pages,
            )
            self._store.finish_run(
                run_id,
                state="failed",
                cursor=cursor,
                ended_at=self._clock(),
                error=type(exc).__name__,
            )
            raise
        state = "completed" if cursor is None else "partial"
        self._store.finish_run(
            run_id,
            state=state,
            cursor=cursor,
            ended_at=self._clock(),
        )
        _LOG.info(
            "ingestion_finished run_id=%s provider=%s state=%s pages=%d observed=%d",
            run_id, provider_name, state, pages, totals.observed,
        )
        return IngestionResult(run_id, state, cursor, pages, totals)
