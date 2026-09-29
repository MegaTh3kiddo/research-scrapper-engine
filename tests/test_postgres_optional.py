"""Opt-in migration smoke test against an empty, disposable PostgreSQL database."""

from __future__ import annotations

import os
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
import pytest
from sqlalchemy import inspect, text, select
from sqlalchemy.orm import sessionmaker

from research_engine.infrastructure.persistence.base import Base
from research_engine.infrastructure.persistence.database import create_database_engine
from research_engine.infrastructure.persistence import models as m


def test_postgres_fresh_migrations_match_models(tmp_path):
    url = os.environ.get("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("set TEST_POSTGRES_URL to an empty disposable PostgreSQL database")
    engine = create_database_engine(url)
    try:
        if engine.dialect.name != "postgresql":
            pytest.fail("TEST_POSTGRES_URL must identify PostgreSQL")
        with engine.connect() as connection:
            if inspect(connection).get_table_names():
                pytest.fail("TEST_POSTGRES_URL must identify an empty disposable database")
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert "research_projects" in inspect(connection).get_table_names()
            assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
        _verify_existing_database_upgrade(engine, config)
        _verify_audited_workflow(engine, tmp_path)
    finally:
        engine.dispose()


def _verify_existing_database_upgrade(engine, config):
    """Isolate legacy-data upgrade in a new, exclusively test-owned schema."""
    schema = "research_upgrade_" + uuid4().hex
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    upgrade_url = engine.url.update_query_dict({"options": f"-csearch_path={schema}"})
    upgrade_engine = create_database_engine(upgrade_url.render_as_string(hide_password=False))
    upgrade_config = Config(config.config_file_name)
    upgrade_config.set_main_option("sqlalchemy.url", upgrade_url.render_as_string(hide_password=False).replace("%", "%%"))
    try:
        command.upgrade(upgrade_config, "0005_metadata_provenance")
        with upgrade_engine.begin() as connection:
            connection.execute(text("INSERT INTO research_projects (id,name,created_at,updated_at) VALUES ('upgrade-project','Retained project',now(),now())"))
            connection.execute(text("INSERT INTO saved_queries (id,project_id,expression,filters,enabled_providers,is_active,created_at,updated_at) VALUES ('upgrade-query','upgrade-project','breast cancer','{}','[\"pubmed\"]',true,now(),now())"))
            connection.execute(text("INSERT INTO articles (id,title,publication_year,created_at,updated_at) VALUES ('upgrade-paper','Retained paper',2026,now(),now())"))
            connection.execute(text("INSERT INTO search_runs (id,query_id,provider,state,started_at,ended_at) VALUES ('upgrade-run','upgrade-query','pubmed','completed',now(),now())"))
            connection.execute(text("INSERT INTO query_schedules (id,query_id,interval_minutes,next_due_at,enabled,created_at,updated_at) VALUES ('upgrade-schedule','upgrade-query',60,now(),true,now(),now())"))
        command.upgrade(upgrade_config, "head")
        with upgrade_engine.connect() as connection:
            assert connection.execute(text("SELECT name FROM research_projects WHERE id='upgrade-project'")).scalar_one() == "Retained project"
            assert connection.execute(text("SELECT expression FROM saved_queries WHERE id='upgrade-query'")).scalar_one() == "breast cancer"
            assert connection.execute(text("SELECT title FROM articles WHERE id='upgrade-paper'")).scalar_one() == "Retained paper"
            assert connection.execute(text("SELECT state FROM search_runs WHERE id='upgrade-run'")).scalar_one() == "completed"
            assert connection.execute(text("SELECT enabled FROM query_schedules WHERE id='upgrade-schedule'")).scalar_one() is True
            assert connection.execute(text("SELECT count(*) FROM execution_attempts")).scalar_one() == 0
            assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    finally:
        upgrade_engine.dispose()
        # Only this freshly-created random schema is removed, never public/user data.
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


def _verify_audited_workflow(engine, tmp_path):
    """Exercise JSON, evidence, dedupe and identity-decision replay on PostgreSQL."""
    from research_engine.application.projects import ProjectService
    from research_engine.application.provider_registry import ProviderRegistry
    from research_engine.application.ingestion import IngestionService
    from research_engine.application.identity_review import IdentityReviewService
    from research_engine.application.monitoring import ScheduledMonitoringService
    from research_engine.application.searches import ManualSearchService
    from contextlib import asynccontextmanager
    from research_engine.domain.value_objects import Identifier, PartialDate
    from research_engine.infrastructure.persistence.projects import SqlAlchemyProjectStore
    from research_engine.infrastructure.persistence.ingestion import SqlAlchemyIngestionStore
    from research_engine.infrastructure.persistence.identity_review import SqlAlchemyIdentityReviewStore
    from research_engine.infrastructure.persistence.scheduling import SqlAlchemyScheduleStore
    from research_engine.infrastructure.snapshots import LocalSnapshotStorage
    from research_engine.ports.providers import ProviderRecord, ProviderQueryPlan, RawResponse, SearchPage

    sessions = sessionmaker(engine, expire_on_commit=False)
    projects = ProjectService(SqlAlchemyProjectStore(sessions))
    project = projects.create_project("PostgreSQL audited workflow")
    query = projects.create_query(project.id, "breast cancer", enabled_providers=("pg-fixture",))
    title = "A systematic review of breast cancer immunotherapy clinical outcomes"
    def record(source_id, pmid, doi):
        return ProviderRecord(source_id, title, "PG fixture", identifiers=(
            Identifier("pmid", pmid), Identifier("doi", doi),
        ), publication_date=PartialDate(2026), url="https://example.org/paper",
            citation_count=0, publication_types=("Review",))

    class Provider:
        name = "pg-fixture"
        parser_version = "pg-fixture-v1"
        records = ()
        sequence = 0
        next_cursor = None
        cursors = []
        def plan(self, request):
            return ProviderQueryPlan(self.name, request.expression, "pg-plan-v1")
        async def search(self, plan, cursor=None):
            self.sequence += 1
            self.cursors.append(cursor)
            return SearchPage(self.records, (RawResponse(
                f"pg-page-{self.sequence}".encode(), "text/plain", datetime.now(timezone.utc), {},
            ),), self.next_cursor)

    provider = Provider()
    ingestion = IngestionService(ProviderRegistry((provider,)), SqlAlchemyIngestionStore(sessions),
                                 LocalSnapshotStorage(tmp_path / "pg-snapshots"))
    def run(records):
        provider.records = records
        return asyncio.run(ingestion.run(str(query.id), provider.name))

    first = run((record("a", "910001", "10.1000/pg-a"), record("b", "910002", "10.1000/pg-b")))
    assert first.outcome.newly_discovered == 2
    reviews = IdentityReviewService(SqlAlchemyIdentityReviewStore(sessions))
    assert len(reviews.list_candidates(str(project.id))) == 1
    conflicted = record("conflict", "910001", "10.1000/pg-b")
    assert run((conflicted,)).outcome.conflicts == 1
    conflict = reviews.list_conflicts(str(project.id))[0]
    chosen = next(candidate for candidate in conflict.candidates
                  if Identifier("pmid", "910001") in candidate.identifiers)
    reviews.review_conflict(conflict.id, action="select_article", reason="Verified fixture IDs",
                            article_id=chosen.article_id, rejected_identifiers=chosen.rejected_identifiers)
    accepted = run((conflicted,))
    assert accepted.outcome.conflicts == accepted.outcome.newly_discovered == 0
    assert run((conflicted,)).outcome.new_to_query == 0
    rejected = record("reject", "910001", "10.1000/pg-b")
    assert run((rejected,)).outcome.conflicts == 1
    conflict = reviews.list_conflicts(str(project.id))[0]
    reviews.review_conflict(conflict.id, action="reject_source", reason="Incorrect fixture assertion")
    rejected_run = run((rejected,))
    assert rejected_run.outcome.observed == 1 and rejected_run.outcome.conflicts == 0
    with sessions() as session:
        assert len(session.scalars(select(m.Article)).all()) == 2
        assert len(session.scalars(select(m.ExecutionAttempt)).all()) == 6
        assert session.scalar(select(m.OutboxEvent.id).where(
            m.OutboxEvent.type == "SourceAssertionRejected",
            m.OutboxEvent.payload["run_id"].as_string() == rejected_run.run_id,
        )) is not None
        assert all(row.snapshot_id for row in session.scalars(select(m.CitationObservation)))
    @asynccontextmanager
    async def searches():
        yield ManualSearchService(ingestion, max_pages=1)
    monitoring = ScheduledMonitoringService(SqlAlchemyScheduleStore(sessions), searches)
    monitoring.configure(str(query.id), interval_minutes=1, next_due_at=datetime.now(timezone.utc))
    provider.records = (record("a", "910001", "10.1000/pg-a"),)
    provider.next_cursor = "pg-next-page"
    assert asyncio.run(monitoring.run_due_once(max_claims=1))[0].state == "partial"
    monitoring.configure(str(query.id), interval_minutes=1, next_due_at=datetime.now(timezone.utc))
    provider.records = (record("b", "910002", "10.1000/pg-b"),)
    provider.next_cursor = None
    assert asyncio.run(monitoring.run_due_once(max_claims=1))[0].state == "completed"
    assert provider.cursors[-1] == "pg-next-page"
    with sessions() as session:
        coverage = session.scalar(select(m.MonitoringCoverage))
        assert coverage.complete is True
        assert coverage.seen_cursors == [None, "pg-next-page"]
