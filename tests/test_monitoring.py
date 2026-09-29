from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from research_engine.application.ingestion import IngestionService
from research_engine.application.local_monitor import LocalMonitoringRunner
from research_engine.application.monitoring import ScheduledMonitoringService
from research_engine.application.projects import ProjectService
from research_engine.application.provider_registry import ProviderRegistry
from research_engine.application.searches import ManualSearchService
from research_engine.infrastructure.persistence import models as m
from research_engine.infrastructure.persistence.base import Base
from research_engine.infrastructure.persistence.database import create_database_engine
from research_engine.infrastructure.persistence.ingestion import SqlAlchemyIngestionStore
from research_engine.infrastructure.persistence.projects import SqlAlchemyProjectStore
from research_engine.infrastructure.persistence.scheduling import SqlAlchemyScheduleStore
from research_engine.infrastructure.snapshots import LocalSnapshotStorage
from research_engine.ports.providers import (
    ProviderQueryPlan, ProviderRecord, RawResponse, SearchPage, SearchRequest,
)


class ScheduledProvider:
    parser_version = "scheduled-test-v1"

    def __init__(self, name: str = "pubmed", *, fail: bool = False) -> None:
        self.name = name
        self.fail = fail

    def plan(self, request: SearchRequest) -> ProviderQueryPlan:
        return ProviderQueryPlan(
            provider=self.name, translated_query=request.expression,
            translation_version="test-v1", parameters={},
        )

    async def search(self, plan: ProviderQueryPlan, cursor=None) -> SearchPage:
        if self.fail:
            raise RuntimeError("simulated provider failure")
        return SearchPage(
            records=(ProviderRecord(
                source_id="monitor-1", title="Scheduled discovery",
                source_database=self.name,
            ),),
            raw_responses=(RawResponse(
                body=b"scheduled-test", media_type="text/plain",
                fetched_at=datetime.now(timezone.utc), safe_request_metadata={},
            ),),
        )

    async def aclose(self) -> None:
        pass


@pytest.fixture
def harness(tmp_path: Path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'monitor.sqlite3'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    projects = ProjectService(SqlAlchemyProjectStore(factory))
    project = projects.create_project("Monitoring")
    query = projects.create_query(project.id, "stroke prevention", enabled_providers=("pubmed",))
    store = SqlAlchemyScheduleStore(factory)
    try:
        yield factory, projects, str(query.id), store, tmp_path
    finally:
        engine.dispose()


def _service(factory, store, tmp_path, *, fail=False, provider_names=("pubmed",)):
    @asynccontextmanager
    async def searches():
        providers = tuple(ScheduledProvider(name, fail=fail) for name in provider_names)
        try:
            yield ManualSearchService(IngestionService(
                ProviderRegistry(providers),
                SqlAlchemyIngestionStore(factory),
                LocalSnapshotStorage(tmp_path / "snapshots"),
            ))
        finally:
            for provider in providers:
                await provider.aclose()

    return ScheduledMonitoringService(store, searches)


def test_scheduled_run_links_ingestion_and_does_not_repeat_before_next_due(harness):
    factory, projects, query_id, store, tmp_path = harness
    service = _service(factory, store, tmp_path)
    now = datetime.now(timezone.utc)
    service.configure(
        query_id, next_due_at=now - timedelta(minutes=1),
    )
    assert service.get_schedule(query_id).interval_minutes == 60
    first = asyncio.run(service.run_due_once(now=now))
    assert len(first) == 1
    assert first[0].state == "completed"
    assert len(first[0].search_run_ids) == 1
    assert asyncio.run(service.run_due_once(now=now)) == ()
    assert store.get_schedule(query_id).next_due_at == now + timedelta(hours=1)
    assert len(projects.list_papers(projects.get_query(query_id).project_id)) == 1
    with factory() as session:
        run = session.get(m.SearchRun, first[0].search_run_ids[0])
        assert run.scheduled_execution_id == first[0].id
        assert run.state == "completed"


def test_failure_is_persisted_with_failed_search_run_and_safe_error_code(harness):
    factory, _, query_id, store, tmp_path = harness
    service = _service(factory, store, tmp_path, fail=True)
    now = datetime.now(timezone.utc)
    service.configure(query_id, next_due_at=now - timedelta(minutes=1))
    attempts = asyncio.run(service.run_due_once(now=now))
    assert len(attempts) == 1
    assert attempts[0].state == "failed"
    assert attempts[0].error_code == "RuntimeError"
    assert len(attempts[0].search_run_ids) == 1
    with factory() as session:
        run = session.get(m.SearchRun, attempts[0].search_run_ids[0])
        assert run.state == "failed"
        assert run.error == "RuntimeError"


def test_schedule_runs_every_enabled_provider_under_one_execution(harness):
    factory, projects, query_id, store, tmp_path = harness
    projects.update_query(query_id, enabled_providers=("pubmed", "europe_pmc"))
    service = _service(
        factory, store, tmp_path, provider_names=("pubmed", "europe_pmc"),
    )
    now = datetime.now(timezone.utc)
    service.configure(query_id, next_due_at=now)
    attempt = asyncio.run(service.run_due_once(now=now))[0]
    assert attempt.state == "completed"
    assert len(attempt.search_run_ids) == 2
    with factory() as session:
        providers = session.scalars(select(m.SearchRun.provider).where(
            m.SearchRun.scheduled_execution_id == attempt.id
        )).all()
        assert set(providers) == {"pubmed", "europe_pmc"}


def test_running_claim_blocks_overlap_until_explicit_recovery(harness):
    factory, _, query_id, store, _ = harness
    now = datetime.now(timezone.utc)
    store.configure(query_id, interval_minutes=1, enabled=True, next_due_at=now)
    claim = store.claim_due(now)
    assert claim is not None
    store.configure(query_id, interval_minutes=1, enabled=True, next_due_at=now)
    assert store.claim_due(now) is None
    with factory.begin() as session:
        schedule = session.scalar(select(m.QuerySchedule).where(m.QuerySchedule.query_id == query_id))
        session.add(m.ScheduledExecution(
            schedule_id=schedule.id, query_id=query_id,
            state="running", started_at=now,
        ))
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()
    recovered = store.recover(claim.execution_id, ended_at=now + timedelta(minutes=1))
    assert recovered.state == "failed"
    assert recovered.error_code == "InterruptedProcess"
    assert store.claim_due(now) is not None


def test_local_monitor_runs_without_a_qt_event_loop():
    called = Event()

    class StubMonitoring:
        async def run_due_once(self):
            called.set()

    runner = LocalMonitoringRunner(StubMonitoring(), poll_seconds=0.05)
    runner.start()
    try:
        assert called.wait(2)
        assert runner.last_error_code is None
    finally:
        runner.stop()


def test_disabled_schedule_is_not_claimed(harness):
    _, _, query_id, store, _ = harness
    now = datetime.now(timezone.utc)
    store.configure(query_id, interval_minutes=60, enabled=False, next_due_at=now)
    assert store.claim_due(now) is None
    assert store.list_executions(query_id) == ()


def test_confirmed_recovery_atomically_fails_linked_running_provider_run(harness):
    factory, _, query_id, store, tmp_path = harness
    now = datetime.now(timezone.utc)
    store.configure(query_id, interval_minutes=60, enabled=True, next_due_at=now)
    claim = store.claim_due(now)
    with factory.begin() as session:
        run = m.SearchRun(
            query_id=query_id, scheduled_execution_id=claim.execution_id,
            provider="pubmed", state="running", started_at=now,
        )
        session.add(run)
        session.flush()
        run_id = run.id

    service = _service(factory, store, tmp_path)
    assert service.list_running()[0].id == claim.execution_id
    with pytest.raises(ValueError, match="confirm"):
        service.recover_interrupted(claim.execution_id)
    assert store.list_running()[0].state == "running"
    recovered = service.recover_interrupted(
        claim.execution_id, confirm_worker_stopped=True,
    )
    assert recovered.state == "failed"
    assert recovered.error_code == "InterruptedProcess"
    assert recovered.search_run_ids == (run_id,)
    assert service.list_running() == ()
    with factory() as session:
        linked = session.get(m.SearchRun, run_id)
        assert linked.state == "failed"
        assert linked.error == "InterruptedProcess"
        assert linked.ended_at is not None
