from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from research_engine.application.ingestion import IngestionService
from research_engine.application.monitoring import ScheduledMonitoringService
from research_engine.application.projects import ProjectService
from research_engine.application.provider_registry import ProviderRegistry
from research_engine.application.recovery import RecoveryService
from research_engine.application.searches import ManualSearchService
from research_engine.infrastructure.persistence import models as m
from research_engine.infrastructure.persistence.base import Base
from research_engine.infrastructure.persistence.database import create_database_engine
from research_engine.infrastructure.persistence.ingestion import SqlAlchemyIngestionStore
from research_engine.infrastructure.persistence.projects import SqlAlchemyProjectStore
from research_engine.infrastructure.persistence.recovery import SqlAlchemyRecoveryStore
from research_engine.infrastructure.persistence.scheduling import SqlAlchemyScheduleStore
from research_engine.infrastructure.snapshots import LocalSnapshotStorage
from research_engine.ports.providers import (
    ProviderQueryPlan,
    ProviderRecord,
    RawResponse,
    SearchPage,
    SearchRequest,
)


class ResumeProvider:
    name = "recovery-fixture"
    parser_version = "recovery-fixture-v1"

    def __init__(self) -> None:
        self.cursors: list[str | None] = []

    def plan(self, request: SearchRequest) -> ProviderQueryPlan:
        return ProviderQueryPlan(
            provider=self.name,
            translated_query=request.expression,
            translation_version="recovery-test-v1",
        )

    async def search(self, plan: ProviderQueryPlan, cursor: str | None = None) -> SearchPage:
        self.cursors.append(cursor)
        suffix = "first" if cursor is None else "second"
        return SearchPage(
            records=(ProviderRecord(
                source_id=f"recovery-paper-{suffix}",
                title=f"Recovery paper {suffix}",
                source_database=self.name,
            ),),
            raw_responses=(RawResponse(
                body=f"recovery:{suffix}".encode(),
                media_type="text/plain",
                fetched_at=datetime.now(timezone.utc),
                safe_request_metadata={},
            ),),
            next_cursor="resume-2" if cursor is None else None,
        )

    async def aclose(self) -> None:
        pass


@pytest.fixture
def recovery_harness(tmp_path: Path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'recovery.sqlite3'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    projects = ProjectService(SqlAlchemyProjectStore(factory))
    project = projects.create_project("Recovery research")
    query = projects.create_query(
        project.id, "recovery coverage", enabled_providers=("pubmed",),
    )
    recovery_store = SqlAlchemyRecoveryStore(factory)
    schedules = SqlAlchemyScheduleStore(factory)
    try:
        yield factory, projects, str(query.id), recovery_store, schedules, tmp_path
    finally:
        engine.dispose()


def _coverage(factory, query_id: str, *, provider: str = ResumeProvider.name) -> str:
    with factory.begin() as session:
        query = session.get(m.SavedQuery, query_id)
        revision = session.scalar(select(m.SavedQueryRevision).where(
            m.SavedQueryRevision.query_id == query_id,
            m.SavedQueryRevision.revision_number == query.revision_number,
        ))
        row = m.MonitoringCoverage(
            query_id=query_id,
            provider=provider,
            lane="full",
            revision_id=revision.id,
            signature="a" * 64,
            cursor="page-2",
            complete=False,
            pages_completed=1,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(row)
        session.flush()
        return row.id


def _running_run(factory, query_id: str, *, scheduled_execution_id: str | None = None) -> str:
    with factory.begin() as session:
        row = m.SearchRun(
            query_id=query_id,
            scheduled_execution_id=scheduled_execution_id,
            provider="pubmed",
            state="running",
            cursor="provider-cursor",
            result_count=3,
            started_at=datetime.now(timezone.utc),
        )
        session.add(row)
        session.flush()
        return row.id


def _events(factory, event_type: str):
    with factory() as session:
        return tuple(session.scalars(select(m.OutboxEvent).where(
            m.OutboxEvent.type == event_type
        ).order_by(m.OutboxEvent.occurred_at, m.OutboxEvent.id)).all())


def test_manual_running_run_requires_confirmation_and_records_recovery_event(recovery_harness):
    factory, _, query_id, store, _, _ = recovery_harness
    now = datetime(2026, 9, 26, 12, tzinfo=timezone.utc)
    run_id = _running_run(factory, query_id)
    service = RecoveryService(store, clock=lambda: now)

    running = service.list_running_runs()
    assert len(running) == 1
    assert running[0].id == run_id
    assert running[0].query_id == query_id
    assert running[0].scheduled_execution_id is None
    assert running[0].cursor == "provider-cursor"
    assert running[0].result_count == 3

    with pytest.raises(ValueError, match="confirm"):
        service.recover_run(run_id)
    assert service.list_running_runs()[0].id == run_id
    assert _events(factory, "SearchRunRecovered") == ()

    service.recover_run(run_id, confirm_worker_stopped=True)
    assert service.list_running_runs() == ()
    with factory() as session:
        run = session.get(m.SearchRun, run_id)
        assert run.state == "failed"
        assert run.error == "InterruptedProcess"
        assert run.ended_at == now
        event = session.scalar(select(m.OutboxEvent).where(
            m.OutboxEvent.type == "SearchRunRecovered"
        ))
        assert event.aggregate_type == "SavedQuery"
        assert event.aggregate_id == query_id
        assert event.payload == {"run_id": run_id, "action": "marked_failed"}
        assert event.schema_version == 1
        assert event.processed_at is None


def test_scheduled_child_run_must_be_recovered_through_execution(recovery_harness):
    factory, _, query_id, store, schedules, _ = recovery_harness
    now = datetime.now(timezone.utc)
    schedules.configure(query_id, interval_minutes=20, enabled=True, next_due_at=now)
    claim = schedules.claim_due(now)
    assert claim is not None
    run_id = _running_run(factory, query_id, scheduled_execution_id=claim.execution_id)
    service = RecoveryService(store)

    with pytest.raises(ValueError, match="recover that execution"):
        service.recover_run(run_id, confirm_worker_stopped=True)
    assert service.list_running_runs()[0].id == run_id
    assert schedules.list_running()[0].id == claim.execution_id
    assert _events(factory, "SearchRunRecovered") == ()


def test_coverage_restart_is_confirmed_audited_and_preserves_old_checkpoint(recovery_harness):
    factory, _, query_id, store, _, _ = recovery_harness
    coverage_id = _coverage(factory, query_id)
    now = datetime(2026, 9, 26, 13, tzinfo=timezone.utc)
    service = RecoveryService(store, clock=lambda: now)

    active = service.list_coverage(query_id)
    assert len(active) == 1
    assert active[0].id == coverage_id
    with pytest.raises(ValueError, match="confirm"):
        service.restart_coverage(query_id, ResumeProvider.name, "full")
    assert len(service.list_coverage(query_id)) == 1
    assert _events(factory, "CoverageRestartRequested") == ()

    service.restart_coverage(
        query_id, ResumeProvider.name, "full", confirm_worker_stopped=True,
    )
    assert service.list_coverage(query_id) == ()
    with factory() as session:
        old = session.get(m.MonitoringCoverage, coverage_id)
        assert old is not None
        assert old.is_active is False
        assert old.cursor == "page-2"
        assert old.pages_completed == 1
        event = session.scalar(select(m.OutboxEvent).where(
            m.OutboxEvent.type == "CoverageRestartRequested"
        ))
        assert event.aggregate_id == query_id
        assert event.payload == {
            "coverage_id": coverage_id,
            "provider": ResumeProvider.name,
            "lane": "full",
            "action": "restart_requested",
        }


@pytest.mark.parametrize("running_kind", ["provider_run", "scheduled_execution"])
def test_coverage_restart_rejects_any_live_query_work_even_with_confirmation(
    recovery_harness, running_kind: str,
):
    factory, _, query_id, store, schedules, _ = recovery_harness
    coverage_id = _coverage(factory, query_id)
    now = datetime.now(timezone.utc)
    if running_kind == "provider_run":
        _running_run(factory, query_id)
    else:
        schedules.configure(query_id, interval_minutes=20, enabled=True, next_due_at=now)
        assert schedules.claim_due(now) is not None

    service = RecoveryService(store)
    with pytest.raises(ValueError, match="worker is running"):
        service.restart_coverage(
            query_id, ResumeProvider.name, "full", confirm_worker_stopped=True,
        )
    assert service.list_coverage(query_id)[0].id == coverage_id
    assert _events(factory, "CoverageRestartRequested") == ()


def test_queue_retry_keeps_schedule_and_query_settings_and_records_outbox_event(
    recovery_harness,
):
    factory, projects, query_id, store, schedules, _ = recovery_harness
    projects.update_query(query_id, expression="preserve this query")
    now = datetime(2026, 9, 26, 14, tzinfo=timezone.utc)
    configured = schedules.configure(
        query_id, interval_minutes=37, enabled=True,
        next_due_at=now + timedelta(days=2),
    )
    service = RecoveryService(store, clock=lambda: now)

    queued = service.queue_retry(query_id)
    assert queued.id == configured.id
    assert queued.next_due_at == now
    assert queued.enabled is True
    assert queued.interval_minutes == 37
    assert schedules.get_schedule(query_id).next_due_at == now
    with factory() as session:
        query = session.get(m.SavedQuery, query_id)
        assert query.expression == "preserve this query"
        assert query.revision_number == 2
        event = session.scalar(select(m.OutboxEvent).where(
            m.OutboxEvent.type == "ScheduledRetryRequested"
        ))
        assert event.aggregate_type == "SavedQuery"
        assert event.aggregate_id == query_id
        assert event.payload == {"schedule_id": configured.id, "action": "retry_requested"}
        assert event.occurred_at == now


def test_queue_retry_rejects_disabled_missing_archived_and_running_work(recovery_harness):
    factory, projects, query_id, store, schedules, _ = recovery_harness
    service = RecoveryService(store)
    with pytest.raises(ValueError, match="enabled schedule"):
        service.queue_retry(query_id)

    now = datetime.now(timezone.utc)
    schedules.configure(query_id, interval_minutes=20, enabled=False, next_due_at=now)
    with pytest.raises(ValueError, match="enabled schedule"):
        service.queue_retry(query_id)

    schedules.configure(query_id, interval_minutes=20, enabled=True, next_due_at=now)
    _running_run(factory, query_id)
    with pytest.raises(ValueError, match="worker is running"):
        service.queue_retry(query_id)
    assert _events(factory, "ScheduledRetryRequested") == ()

    with factory.begin() as session:
        session.execute(
            m.SearchRun.__table__.delete().where(m.SearchRun.query_id == query_id)
        )
    claim = schedules.claim_due(now)
    assert claim is not None
    with pytest.raises(ValueError, match="worker is running"):
        service.queue_retry(query_id)
    assert _events(factory, "ScheduledRetryRequested") == ()
    schedules.recover(claim.execution_id, ended_at=now + timedelta(seconds=1))

    projects.archive_project(projects.get_query(query_id).project_id)
    with pytest.raises(ValueError, match="archived"):
        service.queue_retry(query_id)


def test_queued_retry_resumes_the_existing_coverage_cursor(recovery_harness):
    factory, projects, query_id, store, schedules, data_directory = recovery_harness
    projects.update_query(query_id, enabled_providers=(ResumeProvider.name,))
    provider = ResumeProvider()
    snapshots = LocalSnapshotStorage(data_directory / "snapshots")

    @asynccontextmanager
    async def search_factory():
        yield ManualSearchService(
            IngestionService(
                ProviderRegistry((provider,)),
                SqlAlchemyIngestionStore(factory),
                snapshots,
            ),
            max_pages=1,
        )

    monitoring = ScheduledMonitoringService(schedules, search_factory)
    recovery = RecoveryService(store)
    start = datetime.now(timezone.utc).replace(microsecond=0)
    schedules.configure(query_id, interval_minutes=60, enabled=True, next_due_at=start)
    first = asyncio.run(monitoring.run_due_once(now=start))[0]
    assert first.state == "partial"
    checkpoint = recovery.list_coverage(query_id)[0]
    assert checkpoint.cursor == "resume-2"

    retry_at = start + timedelta(minutes=1)
    recovery = RecoveryService(store, clock=lambda: retry_at)
    queued = recovery.queue_retry(query_id)
    assert queued.next_due_at == retry_at
    second = asyncio.run(monitoring.run_due_once(now=retry_at))[0]

    assert second.state == "completed"
    assert provider.cursors == [None, "resume-2"]
    assert len(recovery.list_coverage(query_id)) == 1
    assert recovery.list_coverage(query_id)[0].id == checkpoint.id
    with factory() as session:
        runs = session.scalars(select(m.SearchRun).order_by(m.SearchRun.started_at)).all()
        assert [run.start_cursor for run in runs] == [None, "resume-2"]
        assert {run.coverage_id for run in runs} == {checkpoint.id}
