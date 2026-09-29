"""Manual retries create new audited runs and preserve their predecessors."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from research_engine.application.ingestion import IngestionService
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
    ProviderQueryPlan,
    ProviderRecord,
    RawResponse,
    SearchPage,
    SearchRequest,
)


class RetryProvider:
    name = "retry-fixture"
    parser_version = "retry-fixture-parser-v1"
    translation_version = "retry-fixture-plan-v1"

    def __init__(self, *, pages=None, fail_once_at=None, fail_first=False):
        self.pages = pages or {None: ("page-2", "first"), "page-2": (None, "second")}
        self.fail_once_at = fail_once_at
        self.fail_first = fail_first
        self.failed = False
        self.calls: list[str | None] = []

    def plan(self, request: SearchRequest) -> ProviderQueryPlan:
        return ProviderQueryPlan(
            provider=self.name,
            translated_query=request.expression,
            translation_version=self.translation_version,
            parameters={"page_size": str(request.page_size)},
        )

    async def search(self, plan: ProviderQueryPlan, cursor: str | None = None) -> SearchPage:
        self.calls.append(cursor)
        if self.fail_first and not self.failed:
            self.failed = True
            raise RuntimeError("fixture failed before first page")
        if self.fail_once_at is not None and cursor == self.fail_once_at and not self.failed:
            self.failed = True
            raise RuntimeError("fixture failed on later page")
        try:
            next_cursor, label = self.pages[cursor]
        except KeyError as exc:
            raise AssertionError(f"unexpected provider cursor {cursor!r}") from exc
        fetched_at = datetime.now(timezone.utc)
        record = ProviderRecord(
            source_id=f"retry-{label}",
            title=f"Retry record {label}",
            source_database=self.name,
        )
        raw = RawResponse(
            body=f"{self.name}:{label}:{plan.translated_query}".encode(),
            media_type="application/json",
            fetched_at=fetched_at,
            safe_request_metadata={"query": plan.translated_query},
        )
        return SearchPage(records=(record,), raw_responses=(raw,), next_cursor=next_cursor)

    async def aclose(self) -> None:
        pass


@pytest.fixture
def retry_harness(tmp_path: Path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'manual-retry.sqlite3'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    projects = ProjectService(SqlAlchemyProjectStore(factory))
    project = projects.create_project("Retry tests")
    query = projects.create_query(
        project.id, "retryable search", filters=SearchFilters(),
        enabled_providers=(RetryProvider.name,),
    )
    store = SqlAlchemyIngestionStore(factory)
    snapshots = LocalSnapshotStorage(tmp_path / "snapshots")
    schedules = SqlAlchemyScheduleStore(factory)
    try:
        yield factory, projects, str(query.id), store, snapshots, schedules
    finally:
        engine.dispose()


def _service(factory, store, snapshots, provider, *, page_size=100, max_pages=1):
    ingestion = IngestionService(
        ProviderRegistry((provider,)), store, snapshots,
    )
    return ManualSearchService(ingestion, page_size=page_size, max_pages=max_pages)


def _run(service, query_id, provider=RetryProvider.name, *, max_pages=None):
    return asyncio.run(service.run_query(query_id, provider, max_pages=max_pages))


def _retry(service, run_id, *, max_pages=None):
    return asyncio.run(service.retry_run(run_id, max_pages=max_pages))


def test_partial_run_retry_resumes_new_run_and_links_outbox_without_mutating_history(retry_harness):
    factory, _, query_id, store, snapshots, _ = retry_harness
    provider = RetryProvider()
    service = _service(factory, store, snapshots, provider, page_size=40, max_pages=1)

    original = _run(service, query_id)
    assert original.state == "partial"
    assert original.next_cursor == "page-2"
    with factory() as session:
        old_row = session.get(m.SearchRun, original.run_id)
        old_history = (
            old_row.state, old_row.cursor, old_row.start_cursor, old_row.started_at,
            old_row.ended_at, old_row.result_count, old_row.new_count,
        )
        old_snapshot_count = session.scalar(select(func.count(m.SourceSnapshot.id)))
        old_observation_count = session.scalar(select(func.count(m.SearchRunObservation.id)))

    retried = _retry(service, original.run_id)

    assert retried.state == "completed"
    assert provider.calls == [None, "page-2"]
    with factory() as session:
        old_row = session.get(m.SearchRun, original.run_id)
        assert (
            old_row.state, old_row.cursor, old_row.start_cursor, old_row.started_at,
            old_row.ended_at, old_row.result_count, old_row.new_count,
        ) == old_history
        new_row = session.get(m.SearchRun, retried.run_id)
        assert new_row.id != old_row.id
        assert new_row.start_cursor == "page-2"
        assert new_row.query_revision_id == old_row.query_revision_id
        assert new_row.plan_id != old_row.plan_id
        assert session.scalar(select(func.count(m.SourceSnapshot.id))) == old_snapshot_count + 1
        assert session.scalar(select(func.count(m.SearchRunObservation.id))) == old_observation_count + 1
        events = session.scalars(select(m.OutboxEvent).where(
            m.OutboxEvent.type == "SearchRunRetryStarted",
        )).all()
        assert len(events) == 1
        assert events[0].payload == {
            "previous_run_id": original.run_id,
            "run_id": retried.run_id,
        }
        assert events[0].aggregate_id == query_id


def test_failed_second_page_retry_does_not_replay_the_first_page(retry_harness):
    factory, _, query_id, store, snapshots, _ = retry_harness
    provider = RetryProvider(fail_once_at="page-2")
    service = _service(factory, store, snapshots, provider, max_pages=2)

    with pytest.raises(RuntimeError, match="later page"):
        _run(service, query_id, max_pages=2)
    with factory() as session:
        failed = session.scalar(select(m.SearchRun).where(m.SearchRun.state == "failed"))
        assert failed is not None
        assert failed.cursor == "page-2"
        first_run_id = failed.id
        first_result_count = failed.result_count
        first_observations = session.scalar(select(func.count(m.SearchRunObservation.id)))
    assert provider.calls == [None, "page-2"]

    retried = _retry(service, first_run_id, max_pages=1)

    assert retried.state == "completed"
    assert provider.calls == [None, "page-2", "page-2"]
    with factory() as session:
        failed = session.get(m.SearchRun, first_run_id)
        assert failed.state == "failed"
        assert failed.cursor == "page-2"
        assert failed.result_count == first_result_count == 1
        assert session.scalar(select(func.count(m.SearchRunObservation.id))) == first_observations + 1
        continued = session.get(m.SearchRun, retried.run_id)
        assert continued.start_cursor == "page-2"
        assert continued.result_count == 1


@pytest.mark.parametrize("change", ["revision", "page_size", "translation", "parser"])
def test_retry_rejects_query_or_provider_contract_changes(retry_harness, change):
    factory, projects, query_id, store, snapshots, _ = retry_harness
    provider = RetryProvider()
    original_service = _service(factory, store, snapshots, provider, page_size=40)
    original = _run(original_service, query_id)
    assert original.state == "partial"

    retry_service = original_service
    if change == "revision":
        projects.update_query(query_id, expression="changed query")
    elif change == "page_size":
        retry_service = _service(factory, store, snapshots, provider, page_size=41)
    elif change == "translation":
        provider.translation_version = "retry-fixture-plan-v2"
    elif change == "parser":
        provider.parser_version = "retry-fixture-parser-v2"

    with pytest.raises(ValueError, match="changed|start a new search"):
        _retry(retry_service, original.run_id)
    assert provider.calls == [None]


def test_run_that_failed_before_any_snapshot_can_be_retried_from_first_page(retry_harness):
    factory, _, query_id, store, snapshots, _ = retry_harness
    provider = RetryProvider(fail_first=True)
    service = _service(factory, store, snapshots, provider, max_pages=1)

    with pytest.raises(RuntimeError, match="before first page"):
        _run(service, query_id)
    with factory() as session:
        failed = session.scalar(select(m.SearchRun).where(m.SearchRun.state == "failed"))
        assert failed is not None
        assert failed.cursor is None
        assert session.scalar(select(func.count(m.SourceSnapshot.id))) == 0

    retried = _retry(service, failed.id)
    assert retried.state == "partial"
    assert provider.calls == [None, None]


def test_scheduled_run_cannot_be_retried_through_manual_api(retry_harness):
    factory, _, query_id, store, snapshots, schedules = retry_harness
    provider = RetryProvider()
    service = _service(factory, store, snapshots, provider)
    now = datetime.now(timezone.utc)
    schedules.configure(query_id, interval_minutes=60, enabled=True, next_due_at=now)
    claim = schedules.claim_due(now)
    assert claim is not None

    scheduled = asyncio.run(service.run_query(
        query_id, RetryProvider.name, scheduled_execution_id=claim.execution_id,
    ))
    assert scheduled.state == "partial"
    with pytest.raises(ValueError, match="scheduled runs"):
        _retry(service, scheduled.run_id)


@pytest.mark.parametrize("state", ["completed", "running"])
def test_completed_and_running_runs_cannot_be_retried(retry_harness, state):
    factory, _, query_id, store, snapshots, _ = retry_harness
    provider = RetryProvider(pages={None: (None, "only")})
    service = _service(factory, store, snapshots, provider)
    if state == "completed":
        run = _run(service, query_id)
        assert run.state == "completed"
        run_id = run.run_id
    else:
        loaded = store.load_request(query_id, provider.name, 100)
        plan = provider.plan(loaded.request)
        run_id = store.begin_run(
            query_id, loaded.revision_id, plan, datetime.now(timezone.utc), 30,
        )

    with pytest.raises(ValueError, match="only failed or partial"):
        _retry(service, run_id)
