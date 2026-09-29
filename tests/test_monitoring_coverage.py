"""Regression coverage for resumable scheduled literature sweeps.

These tests use real application services and the SQLAlchemy persistence adapter;
only the remote provider is replaced with a deterministic cursor fixture.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from research_engine.application.ingestion import IngestionService
from research_engine.application.monitoring import ScheduledMonitoringService
from research_engine.application.projects import ProjectService
from research_engine.application.provider_registry import ProviderRegistry
from research_engine.application.searches import ManualSearchService
from research_engine.domain.value_objects import SearchFilters
from research_engine.infrastructure.persistence import models as m
from research_engine.infrastructure.persistence.base import Base
from research_engine.infrastructure.persistence.database import create_database_engine
from research_engine.infrastructure.persistence.ingestion import SqlAlchemyIngestionStore
from research_engine.infrastructure.persistence.projects import SqlAlchemyProjectStore
from research_engine.infrastructure.persistence.scheduling import SqlAlchemyScheduleStore
from research_engine.infrastructure.snapshots import LocalSnapshotStorage
from research_engine.ports.providers import (
    ProviderCapabilities,
    ProviderQueryPlan,
    ProviderRecord,
    RawResponse,
    SearchPage,
    SearchRequest,
)


class CursorProvider:
    """Provider fixture that exposes cursor order and can fail a chosen page."""

    name = "coverage-fixture"
    parser_version = "coverage-fixture-v1"

    def __init__(
        self,
        *,
        next_by_cursor: dict[str | None, str | None] | None = None,
        fail_once_at: str | None = None,
        supports_dates: bool = False,
    ) -> None:
        self.next_by_cursor = next_by_cursor or {None: "page-2", "page-2": "page-3", "page-3": None}
        self.fail_once_at = fail_once_at
        self.failed = False
        self._supports_dates = supports_dates
        self.calls: list[tuple[str | None, dict[str, str]]] = []
        self.planned_filters: list[SearchFilters] = []

    @property
    def capabilities(self):
        if not self._supports_dates:
            # Older providers without a capabilities declaration participate in
            # the full sweep, while the optional date-window lane is skipped.
            raise AttributeError("capabilities")
        return ProviderCapabilities(
            supported_filters=frozenset({"published_from", "published_to"}),
            supports_boolean_queries=True,
        )

    def plan(self, request: SearchRequest) -> ProviderQueryPlan:
        self.planned_filters.append(request.filters)
        parameters: dict[str, str] = {}
        if request.filters.published_from is not None:
            parameters["published_from"] = request.filters.published_from.isoformat()
        if request.filters.published_to is not None:
            parameters["published_to"] = request.filters.published_to.isoformat()
        return ProviderQueryPlan(
            provider=self.name,
            translated_query=request.expression,
            translation_version="coverage-test-v1",
            parameters=parameters,
        )

    async def search(self, plan: ProviderQueryPlan, cursor: str | None = None) -> SearchPage:
        self.calls.append((cursor, dict(plan.parameters)))
        if self.fail_once_at is not None and cursor == self.fail_once_at and not self.failed:
            self.failed = True
            raise RuntimeError("fixture page failure")
        token = cursor or "first"
        record = ProviderRecord(
            source_id=f"{plan.parameters.get('published_to', 'full')}-{token}",
            title=f"Coverage record {token}",
            source_database=self.name,
        )
        return SearchPage(
            records=(record,),
            raw_responses=(RawResponse(
                body=f"fixture:{plan.parameters}:{token}".encode(),
                media_type="text/plain",
                fetched_at=datetime.now(timezone.utc),
                safe_request_metadata={},
            ),),
            next_cursor=self.next_by_cursor.get(cursor),
        )

    async def aclose(self) -> None:
        pass


@pytest.fixture
def coverage_harness(tmp_path: Path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'coverage.sqlite3'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    project_service = ProjectService(SqlAlchemyProjectStore(factory))
    project = project_service.create_project("Coverage")
    query = project_service.create_query(
        project.id, "long running query", enabled_providers=(CursorProvider.name,),
    )
    schedules = SqlAlchemyScheduleStore(factory)
    snapshots = LocalSnapshotStorage(tmp_path / "snapshots")
    try:
        yield factory, project_service, str(query.id), schedules, snapshots, tmp_path
    finally:
        engine.dispose()


def _services(factory, schedules, snapshots, root, provider, *, clock=None, page_size=100):
    @asynccontextmanager
    async def search_context():
        ingestion = IngestionService(
            ProviderRegistry((provider,)),
            SqlAlchemyIngestionStore(factory),
            snapshots,
            **({"clock": clock} if clock is not None else {}),
        )
        yield ManualSearchService(ingestion, page_size=page_size, max_pages=1)

    return ScheduledMonitoringService(schedules, search_context)


def _due(service, query_id: str, now: datetime, *, max_pages: int = 1):
    service.configure(query_id, interval_minutes=1, next_due_at=now)
    return asyncio.run(service.run_due_once(now=now, max_pages=max_pages))[0]


def test_scheduled_sweeps_continue_to_later_pages_then_restart_full_cycle(coverage_harness):
    factory, _, query_id, schedules, snapshots, root = coverage_harness
    provider = CursorProvider()
    service = _services(factory, schedules, snapshots, root, provider)
    start = datetime.now(timezone.utc).replace(microsecond=0)

    attempts = [
        _due(service, query_id, start + timedelta(minutes=index))
        for index in range(4)
    ]

    assert [item.state for item in attempts] == ["partial", "partial", "completed", "partial"]
    # The fourth attempt is a new reconciliation cycle, so it starts from the
    # provider's first page rather than reusing the exhausted cycle cursor.
    assert [cursor for cursor, _ in provider.calls] == [None, "page-2", "page-3", None]
    with factory() as session:
        runs = session.scalars(select(m.SearchRun).order_by(m.SearchRun.started_at)).all()
        checkpoints = session.scalars(select(m.MonitoringCoverage)).all()
        assert [run.state for run in runs] == ["partial", "partial", "completed", "partial"]
        assert [run.start_cursor for run in runs] == [None, "page-2", "page-3", None]
        assert len({run.coverage_id for run in runs[:3]}) == 1
        assert runs[3].coverage_id != runs[2].coverage_id
        assert any(row.complete and not row.is_active for row in checkpoints)
        assert sum(row.is_active for row in checkpoints) == 1


def test_failed_later_page_keeps_committed_checkpoint_and_retry_resumes_there(coverage_harness):
    factory, _, query_id, schedules, snapshots, root = coverage_harness
    provider = CursorProvider(fail_once_at="page-2")
    service = _services(factory, schedules, snapshots, root, provider)
    now = datetime.now(timezone.utc).replace(microsecond=0)

    failed = _due(service, query_id, now, max_pages=2)
    assert failed.state == "failed"
    with factory() as session:
        checkpoint = session.scalar(select(m.MonitoringCoverage).where(
            m.MonitoringCoverage.is_active.is_(True)
        ))
        assert checkpoint.cursor == "page-2"
        assert checkpoint.complete is False
        first_run = session.get(m.SearchRun, failed.search_run_ids[0])
        assert first_run.state == "failed"

    recovered = _due(service, query_id, now + timedelta(minutes=1), max_pages=1)
    assert recovered.state == "partial"
    finished = _due(service, query_id, now + timedelta(minutes=2), max_pages=1)
    assert finished.state == "completed"
    assert [cursor for cursor, _ in provider.calls] == [
        None, "page-2", "page-2", "page-3",
    ]
    with factory() as session:
        resumed = session.get(m.SearchRun, recovered.search_run_ids[0])
        assert resumed.start_cursor == "page-2"
        resumed_again = session.get(m.SearchRun, finished.search_run_ids[0])
        assert resumed_again.start_cursor == "page-3"
        checkpoint = session.get(m.MonitoringCoverage, resumed_again.coverage_id)
        assert checkpoint.complete is True
        assert checkpoint.cursor is None


def test_cursor_loop_across_scheduled_runs_is_rejected_without_advancing_checkpoint(coverage_harness):
    factory, _, query_id, schedules, snapshots, root = coverage_harness
    provider = CursorProvider(next_by_cursor={
        None: "next-b",
        "next-b": "initial-a",
        "initial-a": "next-b",
    })
    service = _services(factory, schedules, snapshots, root, provider)
    now = datetime.now(timezone.utc).replace(microsecond=0)

    first = _due(service, query_id, now)
    second = _due(service, query_id, now + timedelta(minutes=1))
    loop = _due(service, query_id, now + timedelta(minutes=2))

    assert [attempt.state for attempt in (first, second, loop)] == [
        "partial", "partial", "failed",
    ]
    assert [cursor for cursor, _ in provider.calls] == [None, "next-b", "initial-a"]
    with factory() as session:
        checkpoint = session.scalar(select(m.MonitoringCoverage).where(
            m.MonitoringCoverage.is_active.is_(True)
        ))
        assert checkpoint.cursor == "initial-a"
        assert checkpoint.seen_cursors == [None, "next-b"]
        assert checkpoint.complete is False
        failed_run = session.get(m.SearchRun, loop.search_run_ids[0])
        assert failed_run.state == "failed"
        # The looping page and its observations roll back together with the
        # checkpoint update; only the first two committed pages are present.
        assert session.scalar(select(func.count(m.Article.id))) == 2


def test_revision_and_page_size_changes_start_new_cursor_signatures(coverage_harness):
    factory, projects, query_id, schedules, snapshots, root = coverage_harness
    provider = CursorProvider()
    first_service = _services(factory, schedules, snapshots, root, provider, page_size=25)
    start = datetime.now(timezone.utc).replace(microsecond=0)
    first = _due(first_service, query_id, start)
    assert first.state == "partial"

    projects.update_query(query_id, expression="edited query")
    second_service = _services(factory, schedules, snapshots, root, provider, page_size=25)
    second = _due(second_service, query_id, start + timedelta(minutes=1))
    assert second.state == "partial"

    third_service = _services(factory, schedules, snapshots, root, provider, page_size=50)
    third = _due(third_service, query_id, start + timedelta(minutes=2))
    assert third.state == "partial"
    assert [cursor for cursor, _ in provider.calls] == [None, None, None]

    with factory() as session:
        runs = session.scalars(select(m.SearchRun).order_by(m.SearchRun.started_at)).all()
        assert [run.start_cursor for run in runs] == [None, None, None]
        assert len({run.coverage_id for run in runs}) == 3
        assert len({run.query_revision_id for run in runs}) == 2
        checkpoints = session.scalars(select(m.MonitoringCoverage)).all()
        assert len(checkpoints) == 3
        assert sum(checkpoint.is_active for checkpoint in checkpoints) == 1


def test_recent_window_stays_frozen_while_partial_then_overlaps_seven_days(coverage_harness):
    factory, projects, query_id, schedules, snapshots, root = coverage_harness
    projects.update_query(
        query_id,
        filters=SearchFilters(
            published_from=date(2026, 5, 1), published_to=date(2026, 8, 1),
        ),
    )
    clock_value = [datetime(2026, 6, 15, 12, tzinfo=timezone.utc)]
    provider = CursorProvider(
        next_by_cursor={None: "recent-page-2", "recent-page-2": None},
        supports_dates=True,
    )

    @asynccontextmanager
    async def search_context():
        ingestion = IngestionService(
            ProviderRegistry((provider,)), SqlAlchemyIngestionStore(factory), snapshots,
            clock=lambda: clock_value[0],
        )
        yield ManualSearchService(ingestion, max_pages=1)

    service = ScheduledMonitoringService(schedules, search_context)

    def run_at(instant: datetime):
        schedules.configure(
            query_id, interval_minutes=1, enabled=True, next_due_at=instant,
        )
        # The scheduled service uses its explicit run time for claiming. The
        # injected ingestion clock controls the stable publication window.
        return asyncio.run(service.run_due_once(now=instant, max_pages=1))[0]

    first_time = datetime(2026, 6, 15, 12, tzinfo=timezone.utc)
    second_time = datetime(2026, 6, 20, 12, tzinfo=timezone.utc)
    third_time = datetime(2026, 6, 21, 12, tzinfo=timezone.utc)

    first = run_at(first_time)
    assert first.state == "partial"
    clock_value[0] = second_time
    second = run_at(second_time)
    assert second.state == "completed"
    clock_value[0] = third_time
    third = run_at(third_time)
    assert third.state in {"partial", "completed"}

    recent_calls = [
        (cursor, params) for cursor, params in provider.calls
        if params.get("published_to") not in {None, "2026-08-01"}
    ]
    assert [cursor for cursor, _ in recent_calls] == [None, "recent-page-2", None]
    assert [(p["published_from"], p["published_to"]) for _, p in recent_calls] == [
        ("2026-05-16", "2026-06-15"),
        ("2026-05-16", "2026-06-15"),
        ("2026-06-08", "2026-06-21"),
    ]

    with factory() as session:
        recent_checkpoints = session.scalars(select(m.MonitoringCoverage).where(
            m.MonitoringCoverage.lane == "recent"
        ).order_by(m.MonitoringCoverage.created_at)).all()
        assert len(recent_checkpoints) == 2
        assert recent_checkpoints[0].complete is True
        assert recent_checkpoints[0].published_to == date(2026, 6, 15)
        assert recent_checkpoints[1].published_from == date(2026, 6, 8)
        assert recent_checkpoints[1].published_to == date(2026, 6, 21)
