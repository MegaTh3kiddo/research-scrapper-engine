"""Transactional ingestion port; the application never imports ORM types."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, date
from typing import Protocol

from research_engine.ports.providers import ProviderQueryPlan, SearchPage, SearchRequest
from research_engine.ports.snapshots import StoredSnapshot


@dataclass(frozen=True, slots=True)
class PageOutcome:
    observed: int = 0
    newly_discovered: int = 0
    new_to_query: int = 0
    newly_published: int = 0
    metadata_updated: int = 0
    conflicts: int = 0


@dataclass(frozen=True, slots=True)
class LoadedQuery:
    request: SearchRequest
    revision_id: str


@dataclass(frozen=True, slots=True)
class CoverageCheckpoint:
    id: str
    query_id: str
    provider: str
    lane: str
    revision_id: str
    signature: str
    cursor: str | None
    complete: bool
    published_from: date | None
    published_to: date | None
    last_completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class CoverageContext:
    lane: str
    signature: str
    cursor: str | None = None
    published_from: date | None = None
    published_to: date | None = None
    previous_id: str | None = None


@dataclass(frozen=True, slots=True)
class RetryRun:
    id: str
    query_id: str
    revision_id: str
    provider: str
    cursor: str | None
    plan: ProviderQueryPlan
    parser_version: str | None


class IngestionStore(Protocol):
    def load_coverage(self, query_id: str, provider: str, lane: str) -> CoverageCheckpoint | None: ...
    def load_retry(self, run_id: str) -> RetryRun: ...
    def begin_attempt(
        self,
        requested_query_id: str,
        requested_provider: str,
        started_at: datetime,
        *,
        request_reference: str | None = None,
    ) -> str: ...
    def update_attempt_request(
        self, attempt_id: str, requested_query_id: str, requested_provider: str,
    ) -> None: ...
    def finish_attempt(
        self, attempt_id: str, *, state: str, ended_at: datetime,
        error_code: str | None = None,
    ) -> None: ...
    def load_request(
        self, query_id: str, provider_name: str, page_size: int
    ) -> LoadedQuery: ...

    def begin_run(
        self,
        query_id: str,
        revision_id: str,
        plan: ProviderQueryPlan,
        started_at: datetime,
        recent_window_days: int,
        scheduled_execution_id: str | None = None,
        coverage: CoverageContext | None = None,
        start_cursor: str | None = None,
        retry_of_run_id: str | None = None,
        attempt_id: str | None = None,
    ) -> str: ...

    def persist_page(
        self,
        run_id: str,
        plan: ProviderQueryPlan,
        page: SearchPage,
        stored_snapshots: tuple[StoredSnapshot, ...],
        parser_version: str,
        observed_at: datetime,
    ) -> PageOutcome: ...

    def finish_run(
        self,
        run_id: str,
        *,
        state: str,
        cursor: str | None,
        ended_at: datetime,
        error: str | None = None,
    ) -> None: ...
