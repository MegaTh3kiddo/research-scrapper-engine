from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import sessionmaker

from research_engine.application.ingestion import IngestionService
from research_engine.application.monitoring import ScheduledMonitoringService
from research_engine.application.projects import ProjectService
from research_engine.application.provider_registry import ProviderRegistry
from research_engine.application.recovery import RecoveryService
from research_engine.application.searches import ManualSearchService
from research_engine.domain.value_objects import SearchFilters
from research_engine.infrastructure.persistence import models as m
from research_engine.infrastructure.persistence.base import Base
from research_engine.infrastructure.persistence.database import create_database_engine
from research_engine.infrastructure.persistence.ingestion import SqlAlchemyIngestionStore
from research_engine.infrastructure.persistence.projects import SqlAlchemyProjectStore
from research_engine.infrastructure.persistence.recovery import SqlAlchemyRecoveryStore
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


class AuditProvider:
    name = "audit-fixture"
    parser_version = "audit-fixture-v1"

    def __init__(self, *, unsupported_filters=(), fail=False, next_cursor=None):
        self.unsupported_filters = tuple(unsupported_filters)
        self.fail = fail
        self.next_cursor = next_cursor
        self.calls: list[str | None] = []

    def plan(self, request: SearchRequest) -> ProviderQueryPlan:
        return ProviderQueryPlan(
            provider=self.name,
            translated_query=request.expression,
            translation_version="audit-plan-v1",
            unsupported_filters=self.unsupported_filters,
        )

    async def search(self, plan: ProviderQueryPlan, cursor: str | None = None) -> SearchPage:
        self.calls.append(cursor)
        if self.fail:
            raise RuntimeError("private provider exception text must not be persisted")
        return SearchPage(
            records=(ProviderRecord(
                source_id=f"audit-record-{len(self.calls)}",
                title="Audit paper",
                source_database=self.name,
            ),),
            raw_responses=(RawResponse(
                body=f"audit:{len(self.calls)}".encode(),
                media_type="text/plain",
                fetched_at=datetime.now(timezone.utc),
                safe_request_metadata={},
            ),),
            next_cursor=self.next_cursor,
        )

    async def aclose(self) -> None:
        pass


class RecentAuditProvider(AuditProvider):
    @property
    def capabilities(self):
        return ProviderCapabilities(
            supported_filters=frozenset({"published_from", "published_to"}),
            supports_boolean_queries=True,
        )


class RetryAuditProvider(AuditProvider):
    """Fixture that leaves a cursor after the first request, then completes."""

    def __init__(self):
        super().__init__()

    async def search(self, plan: ProviderQueryPlan, cursor: str | None = None) -> SearchPage:
        self.calls.append(cursor)
        next_cursor = "page-2" if len(self.calls) == 1 else None
        return SearchPage(
            records=(ProviderRecord(
                source_id=f"retry-record-{len(self.calls)}",
                title="Retry audit paper",
                source_database=self.name,
            ),),
            raw_responses=(RawResponse(
                body=f"retry:{len(self.calls)}".encode(),
                media_type="text/plain",
                fetched_at=datetime.now(timezone.utc),
                safe_request_metadata={},
            ),),
            next_cursor=next_cursor,
        )


@pytest.fixture
def audit_harness(tmp_path: Path):
    path = tmp_path / "execution-audit.sqlite3"
    engine = create_database_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    projects = ProjectService(SqlAlchemyProjectStore(factory))
    project = projects.create_project("Execution audit")
    query = projects.create_query(
        project.id, "audit query", enabled_providers=(AuditProvider.name,),
    )
    store = SqlAlchemyIngestionStore(factory)
    recovery = RecoveryService(SqlAlchemyRecoveryStore(factory))
    schedules = SqlAlchemyScheduleStore(factory)
    try:
        yield path, factory, projects, str(query.id), store, recovery, schedules, tmp_path
    finally:
        engine.dispose()


def _service(factory, store, data_directory: Path, provider: AuditProvider, *, clock=None):
    return IngestionService(
        ProviderRegistry((provider,)),
        store,
        LocalSnapshotStorage(data_directory / "snapshots"),
        **({"clock": clock} if clock is not None else {}),
    )


def _attempts(factory):
    with factory() as session:
        return tuple(session.scalars(
            select(m.ExecutionAttempt).order_by(m.ExecutionAttempt.started_at, m.ExecutionAttempt.id)
        ).all())


def _runs(factory):
    with factory() as session:
        return tuple(session.scalars(
            select(m.SearchRun).order_by(m.SearchRun.started_at, m.SearchRun.id)
        ).all())


def test_unknown_provider_and_missing_query_are_audited_before_lookup(audit_harness):
    _, factory, _, query_id, store, _, _, data_directory = audit_harness
    provider = AuditProvider()
    service = _service(factory, store, data_directory, provider)

    with pytest.raises(KeyError):
        asyncio.run(service.run(query_id, "missing-provider"))
    missing_query = "00000000-0000-0000-0000-000000000000"
    with pytest.raises(ValueError, match="saved query"):
        asyncio.run(service.run(missing_query, provider.name))

    attempts = _attempts(factory)
    assert len(attempts) == 2
    assert [(item.requested_query_id, item.requested_provider) for item in attempts] == [
        (query_id, "missing-provider"),
        (missing_query, provider.name),
    ]
    assert [item.state for item in attempts] == ["failed", "failed"]
    assert [item.error_code for item in attempts] == ["KeyError", "ValueError"]
    assert all(item.run_id is None and item.ended_at is not None for item in attempts)
    assert _runs(factory) == ()


def test_unsupported_filters_and_legacy_database_guard_create_no_fake_search_runs(
    audit_harness,
):
    _, factory, projects, query_id, store, _, _, data_directory = audit_harness
    projects.update_query(
        query_id,
        filters=SearchFilters(published_from=date(2026, 1, 1)),
    )
    provider = AuditProvider(unsupported_filters=("published_from",))
    service = _service(factory, store, data_directory, provider)
    with pytest.raises(ValueError, match="does not support"):
        asyncio.run(service.run(query_id, provider.name))

    # An old parser record triggers the production safety guard during the
    # subsequent request lookup. Its rejected attempt is still auditable.
    with factory.begin() as session:
        article = m.Article(title="Legacy parser paper")
        session.add(article)
        session.flush()
        snapshot = m.SourceSnapshot(
            provider="pubmed",
            storage_key="legacy-snapshot",
            sha256="a" * 64,
            content_type="application/xml",
            byte_size=1,
            captured_at=datetime.now(timezone.utc),
            request_metadata={},
        )
        session.add(snapshot)
        session.flush()
        session.add(m.SourceRecord(
            provider="pubmed",
            provider_record_id="legacy-record",
            article_id=article.id,
            snapshot_id=snapshot.id,
            parser_version="pubmed-parser-v1",
            fetched_at=datetime.now(timezone.utc),
        ))
    with pytest.raises(ValueError, match="legacy PubMed"):
        asyncio.run(service.run(query_id, provider.name))

    attempts = _attempts(factory)
    assert len(attempts) == 2
    assert [item.error_code for item in attempts] == [
        "UnsupportedFiltersError", "ValueError",
    ]
    assert all(item.state == "failed" and item.run_id is None for item in attempts)
    assert _runs(factory) == ()


@pytest.mark.parametrize(
    ("provider", "max_pages", "expected_state"),
    [
        (AuditProvider(), 1, "completed"),
        (AuditProvider(next_cursor="page-2"), 1, "partial"),
        (AuditProvider(fail=True), 1, "failed"),
    ],
)
def test_actual_runs_and_attempts_finish_together(
    audit_harness, provider: AuditProvider, max_pages: int, expected_state: str,
):
    _, factory, _, query_id, store, _, _, data_directory = audit_harness
    service = _service(factory, store, data_directory, provider)

    if expected_state == "failed":
        with pytest.raises(RuntimeError):
            asyncio.run(service.run(query_id, provider.name, max_pages=max_pages))
        result = None
    else:
        result = asyncio.run(service.run(query_id, provider.name, max_pages=max_pages))

    attempts = _attempts(factory)
    runs = _runs(factory)
    assert len(attempts) == len(runs) == 1
    assert attempts[0].run_id == runs[0].id
    assert attempts[0].state == runs[0].state == expected_state
    assert attempts[0].ended_at == runs[0].ended_at
    if expected_state == "failed":
        assert attempts[0].error_code == "RuntimeError"
        assert "private provider exception" not in str(attempts[0].error_code)
    else:
        assert result is not None and result.run_id == runs[0].id
        assert attempts[0].error_code is None


def test_cancellation_finishes_the_linked_attempt_as_failed(audit_harness):
    _, factory, _, query_id, store, _, _, data_directory = audit_harness
    entered = asyncio.Event()

    class CancellableProvider(AuditProvider):
        async def search(self, plan, cursor=None):
            entered.set()
            await asyncio.Future()

    provider = CancellableProvider()
    service = _service(factory, store, data_directory, provider)

    async def cancel_request():
        task = asyncio.create_task(service.run(query_id, provider.name))
        await asyncio.wait_for(entered.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel_request())
    attempts, runs = _attempts(factory), _runs(factory)
    assert len(attempts) == len(runs) == 1
    assert attempts[0].run_id == runs[0].id
    assert attempts[0].state == runs[0].state == "failed"
    assert attempts[0].error_code == "CancelledError"
    assert runs[0].error == "CancelledError"


def test_no_recent_intersection_is_skipped_and_has_no_search_run(audit_harness):
    _, factory, projects, query_id, store, _, schedules, data_directory = audit_harness
    projects.update_query(
        query_id,
        filters=SearchFilters(
            published_from=date(2026, 2, 1), published_to=date(2026, 3, 1),
        ),
        enabled_providers=(RecentAuditProvider.name,),
    )
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    schedules.configure(query_id, interval_minutes=10, enabled=True, next_due_at=now)
    claim = schedules.claim_due(now)
    assert claim is not None
    provider = RecentAuditProvider()
    service = _service(factory, store, data_directory, provider, clock=lambda: now)

    with pytest.raises(Exception, match="no recent publication window"):
        asyncio.run(service.run(
            query_id,
            provider.name,
            scheduled_execution_id=claim.execution_id,
            coverage_lane="recent",
        ))

    attempts = _attempts(factory)
    assert len(attempts) == 1
    assert attempts[0].state == "skipped"
    assert attempts[0].error_code == "NoRecentWindow"
    assert attempts[0].run_id is None
    assert _runs(factory) == ()


def test_pre_run_attempt_recovery_is_confirmed_audited_and_rejects_linked_attempt(
    audit_harness,
):
    _, factory, _, query_id, store, recovery, _, _ = audit_harness
    now = datetime.now(timezone.utc)
    attempt_id = store.begin_attempt(query_id, AuditProvider.name, now)
    running = recovery.list_running_attempts()
    assert [item.id for item in running] == [attempt_id]
    assert running[0].run_id is None

    with pytest.raises(ValueError, match="confirm"):
        recovery.recover_attempt(attempt_id)
    assert recovery.list_running_attempts()[0].id == attempt_id

    recovery.recover_attempt(attempt_id, confirm_worker_stopped=True)
    assert recovery.list_running_attempts() == ()
    with factory() as session:
        recovered = session.get(m.ExecutionAttempt, attempt_id)
        event = session.scalar(select(m.OutboxEvent).where(
            m.OutboxEvent.type == "ExecutionAttemptRecovered"
        ))
        assert recovered.state == "failed"
        assert recovered.error_code == "InterruptedProcess"
        assert recovered.ended_at is not None
        assert event.aggregate_id == attempt_id
        assert event.payload == {"attempt_id": attempt_id, "action": "marked_failed"}


def test_manual_and_scheduled_run_recovery_finalize_linked_attempts_in_same_transaction(
    audit_harness,
):
    _, factory, _, query_id, store, recovery, schedules, _ = audit_harness
    now = datetime.now(timezone.utc)
    manual_attempt = store.begin_attempt(query_id, AuditProvider.name, now)
    manual_plan = AuditProvider().plan(SearchRequest("audit query", page_size=10))
    manual_run = store.begin_run(
        query_id, _revision(factory, query_id), manual_plan, now, 30,
        attempt_id=manual_attempt,
    )
    with pytest.raises(ValueError, match="provider run"):
        recovery.recover_attempt(manual_attempt, confirm_worker_stopped=True)
    recovery.recover_run(manual_run, confirm_worker_stopped=True)

    schedules.configure(query_id, interval_minutes=10, enabled=True, next_due_at=now)
    claim = schedules.claim_due(now)
    assert claim is not None
    scheduled_attempt = store.begin_attempt(query_id, AuditProvider.name, now)
    scheduled_plan = AuditProvider().plan(SearchRequest("audit query", page_size=10))
    scheduled_run = store.begin_run(
        query_id, _revision(factory, query_id), scheduled_plan, now, 30,
        scheduled_execution_id=claim.execution_id,
        attempt_id=scheduled_attempt,
    )
    schedules.recover(claim.execution_id, ended_at=now)

    with factory() as session:
        manual = session.get(m.ExecutionAttempt, manual_attempt)
        scheduled = session.get(m.ExecutionAttempt, scheduled_attempt)
        assert (manual.state, manual.error_code, manual.run_id) == (
            "failed", "InterruptedProcess", manual_run,
        )
        assert (scheduled.state, scheduled.error_code, scheduled.run_id) == (
            "failed", "InterruptedProcess", scheduled_run,
        )
        assert session.get(m.SearchRun, manual_run).state == "failed"
        assert session.get(m.SearchRun, scheduled_run).state == "failed"


def test_retry_lookup_failure_is_audited_with_sanitized_predecessor_reference(audit_harness):
    _, factory, _, _, store, _, _, data_directory = audit_harness
    service = _service(factory, store, data_directory, AuditProvider())

    with pytest.raises(ValueError, match="only failed or partial"):
        asyncio.run(service.retry("bad/path?api_key=must-not-persist"))

    attempt = _attempts(factory)[0]
    assert attempt.requested_query_id == "unknown"
    assert attempt.requested_provider == "unknown"
    assert attempt.request_reference == "bad"
    assert attempt.state == "failed"
    assert attempt.error_code == "ValueError"
    assert "api_key" not in str(attempt.request_reference)


def test_retry_success_creates_one_attempt_per_actual_request(audit_harness):
    _, factory, _, query_id, store, _, _, data_directory = audit_harness
    provider = RetryAuditProvider()
    service = _service(factory, store, data_directory, provider)

    first = asyncio.run(service.run(query_id, provider.name))
    assert first.state == "partial"
    retried = asyncio.run(service.retry(first.run_id))
    assert retried.state == "completed"

    attempts, runs = _attempts(factory), _runs(factory)
    assert len(attempts) == len(runs) == 2
    assert [attempt.run_id for attempt in attempts] == [run.id for run in runs]
    assert [attempt.state for attempt in attempts] == ["partial", "completed"]
    assert attempts[1].request_reference == first.run_id


def test_workflow_preflight_rejections_and_recent_capability_lookup_are_audited(
    audit_harness,
):
    _, factory, _, query_id, store, _, _, data_directory = audit_harness
    provider = AuditProvider()
    service = _service(factory, store, data_directory, provider)
    disabled_source = ManualSearchService(service, allowed_sources=("pubmed",))
    with pytest.raises(ValueError, match="disabled"):
        asyncio.run(disabled_source.run_query(query_id, provider.name))

    # Capability inspection can fail before an ingestion request is created;
    # keep that rejected monitoring request in the same audit journal.
    missing_source = ManualSearchService(service)
    with pytest.raises(KeyError):
        asyncio.run(missing_source.run_monitoring_query(
            query_id, "missing-provider", scheduled_execution_id="schedule-run",
        ))

    attempts = _attempts(factory)
    assert [(attempt.requested_provider, attempt.state, attempt.error_code) for attempt in attempts] == [
        (provider.name, "failed", "SourceDisabledForWorkflow"),
        ("missing-provider", "failed", "KeyError"),
    ]
    assert all(attempt.run_id is None for attempt in attempts)
    assert _runs(factory) == ()


def _revision(factory, query_id: str) -> str:
    with factory() as session:
        return session.scalar(select(m.SavedQueryRevision.id).where(
            m.SavedQueryRevision.query_id == query_id,
        ))


def test_execution_attempt_migration_is_additive_and_matches_metadata(tmp_path: Path):
    path = tmp_path / "execution-attempt-migration.sqlite3"
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
    command.upgrade(config, "0006_monitoring_coverage")
    engine = create_database_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO research_projects (id, name, created_at, updated_at) "
            "VALUES ('project-a', 'Project A', '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
        ))
        connection.execute(text(
            "INSERT INTO saved_queries "
            "(id, project_id, expression, filters, enabled_providers, is_active, "
            "archived_at, created_at, updated_at, revision_number) "
            "VALUES ('query-a', 'project-a', 'migration query', '{}', '[\"pubmed\"]', "
            "1, NULL, '2026-01-01 00:00:00', '2026-01-01 00:00:00', 1)"
        ))
        connection.execute(text(
            "INSERT INTO search_runs (id, query_id, provider, state, started_at) "
            "VALUES ('legacy-run', 'query-a', 'pubmed', 'completed', '2026-01-01 00:00:00')"
        ))

    command.upgrade(config, "head")
    with engine.connect() as connection:
        inspector = inspect(connection)
        assert "execution_attempts" in inspector.get_table_names()
        columns = {column["name"] for column in inspector.get_columns("execution_attempts")}
        assert columns == {
            "id", "requested_query_id", "requested_provider", "request_reference",
            "started_at", "ended_at", "state", "error_code", "run_id",
        }
        foreign_keys = inspector.get_foreign_keys("execution_attempts")
        assert len(foreign_keys) == 1
        assert foreign_keys[0]["constrained_columns"] == ["run_id"]
        assert foreign_keys[0]["referred_table"] == "search_runs"
        assert connection.execute(text("SELECT count(*) FROM search_runs")).scalar_one() == 1
        assert connection.execute(text("SELECT count(*) FROM execution_attempts")).scalar_one() == 0
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        from research_engine.infrastructure.persistence.base import Base

        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []

    command.downgrade(config, "0006_monitoring_coverage")
    with engine.connect() as connection:
        assert "execution_attempts" not in inspect(connection).get_table_names()
        assert connection.execute(text("SELECT count(*) FROM search_runs")).scalar_one() == 1
    engine.dispose()
