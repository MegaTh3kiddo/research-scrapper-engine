from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.autogenerate import compare_metadata
from sqlalchemy import create_engine, inspect, insert, text
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session

from research_engine.infrastructure.persistence.base import Base
from research_engine.infrastructure.persistence.database import create_database_engine
from research_engine.infrastructure.persistence.models import (
    Article,
    ArticleIdentifier,
    ProviderQueryPlan,
    ProjectArticle,
    ProjectArticleTag,
    QueryMatch,
    ResearchProject,
    SavedQuery,
    SearchRun,
    SourceRecord,
    SourceRecordSnapshot,
    SourceSnapshot,
    Tag,
)
from research_engine.infrastructure.persistence import models as _models  # noqa: F401


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _alembic_config(database_path: Path) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    return config


def _migrated_engine(database_path: Path):
    command.upgrade(_alembic_config(database_path), "head")
    return create_database_engine(f"sqlite:///{database_path}")


@pytest.fixture
def migrated_engine(tmp_path: Path):
    engine = _migrated_engine(tmp_path / "stage1.sqlite3")
    try:
        yield engine
    finally:
        engine.dispose()


def test_fresh_sqlite_database_upgrades_and_downgrades(tmp_path: Path):
    database_path = tmp_path / "fresh.sqlite3"
    config = _alembic_config(database_path)

    command.upgrade(config, "head")
    with create_engine(f"sqlite:///{database_path}").connect() as connection:
        table_names = set(inspect(connection).get_table_names())
        assert "research_projects" in table_names
        assert "articles" in table_names
        assert "project_article_tags" in table_names
        assert "outbox_events" in table_names
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()

    command.downgrade(config, "base")
    with create_engine(f"sqlite:///{database_path}").connect() as connection:
        remaining = set(inspect(connection).get_table_names()) - {"alembic_version"}
        assert remaining == set()
        if "alembic_version" in inspect(connection).get_table_names():
            assert connection.execute(text("SELECT COUNT(*) FROM alembic_version")).scalar_one() == 0


def test_migrated_sqlite_schema_matches_orm_metadata(migrated_engine):
    with migrated_engine.connect() as connection:
        context = MigrationContext.configure(connection)
        differences = compare_metadata(context, Base.metadata)

    assert differences == []


def test_sqlite_engine_enables_foreign_key_enforcement(migrated_engine):
    with migrated_engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1


def test_publication_day_requires_month_in_sqlite(migrated_engine):
    with Session(migrated_engine) as session:
        session.add(
            Article(
                title="Incomplete publication date",
                publication_year=2024,
                publication_day=1,
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


def test_article_identifiers_are_unique_across_canonical_articles(migrated_engine):
    with Session(migrated_engine) as session:
        first = Article(title="First article")
        second = Article(title="Second article")
        session.add_all([first, second])
        session.flush()
        session.add(ArticleIdentifier(article_id=first.id, scheme="doi", value="10.1000/test"))
        session.commit()

        session.add(ArticleIdentifier(article_id=second.id, scheme="doi", value="10.1000/test"))
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


def test_article_identifier_requires_an_existing_article(migrated_engine):
    with Session(migrated_engine) as session:
        session.add(
            ArticleIdentifier(
                article_id=str(uuid4()),
                scheme="doi",
                value="10.1000/orphan",
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


def test_project_tags_are_scoped_and_cannot_cross_project_links(migrated_engine):
    with Session(migrated_engine) as session:
        project_a = ResearchProject(name="Project A")
        project_b = ResearchProject(name="Project B")
        article = Article(title="Paper")
        session.add_all([project_a, project_b, article])
        session.flush()

        session.add_all(
            [
                ProjectArticle(project_id=project_a.id, article_id=article.id),
                ProjectArticle(project_id=project_b.id, article_id=article.id),
            ]
        )
        tag_a = Tag(
            project_id=project_a.id,
            name="Cancer",
            normalized_name="cancer",
        )
        tag_b = Tag(
            project_id=project_b.id,
            name="Cancer",
            normalized_name="cancer",
        )
        session.add_all([tag_a, tag_b])
        session.flush()
        session.add(
            ProjectArticleTag(
                project_id=project_a.id,
                article_id=article.id,
                tag_id=tag_a.id,
            )
        )
        session.commit()

        session.add(
            Tag(
                project_id=project_a.id,
                name="Cancer duplicate",
                normalized_name="cancer",
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

        session.add(
            ProjectArticleTag(
                project_id=project_a.id,
                article_id=article.id,
                tag_id=tag_b.id,
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


def test_project_article_pair_is_unique(migrated_engine):
    with Session(migrated_engine) as session:
        project = ResearchProject(name="Project")
        article = Article(title="Paper")
        session.add_all([project, article])
        session.flush()
        match = ProjectArticle(project_id=project.id, article_id=article.id)
        session.add(match)
        session.commit()

        with pytest.raises(IntegrityError):
            session.execute(
                insert(ProjectArticle.__table__).values(
                    project_id=project.id,
                    article_id=article.id,
                )
            )
        session.rollback()


def test_source_record_snapshot_history_survives_latest_pointer_update(migrated_engine):
    with Session(migrated_engine) as session:
        article = Article(title="Paper")
        original = SourceSnapshot(
            provider="PubMed",
            storage_key="snapshots/original",
            sha256="a" * 64,
            content_type="application/json",
            byte_size=10,
        )
        latest = SourceSnapshot(
            provider="PubMed",
            storage_key="snapshots/latest",
            sha256="b" * 64,
            content_type="application/json",
            byte_size=12,
        )
        session.add_all([article, original, latest])
        session.flush()

        source_record = SourceRecord(
            provider="PubMed",
            provider_record_id="12345",
            article_id=article.id,
            snapshot_id=original.id,
            parser_version="1",
        )
        session.add(source_record)
        session.flush()
        session.add(
            SourceRecordSnapshot(
                source_record_id=source_record.id,
                snapshot_id=original.id,
                provider="PubMed",
            )
        )
        session.commit()

        source_record.snapshot_id = latest.id
        session.add(
            SourceRecordSnapshot(
                source_record_id=source_record.id,
                snapshot_id=latest.id,
                provider="PubMed",
            )
        )
        session.commit()
        session.expire_all()

        assert session.get(SourceRecord, source_record.id).snapshot_id == latest.id
        assert session.get(SourceRecordSnapshot, (source_record.id, original.id)) is not None
        assert session.get(SourceRecordSnapshot, (source_record.id, latest.id)) is not None


def test_utc_timestamp_round_trip_normalizes_non_utc_aware_input(migrated_engine):
    local_timestamp = datetime(
        2024,
        4,
        3,
        17,
        45,
        tzinfo=timezone(timedelta(hours=5, minutes=30)),
    )
    expected_utc = datetime(2024, 4, 3, 12, 15, tzinfo=timezone.utc)

    with Session(migrated_engine) as session:
        project = ResearchProject(name="UTC check", created_at=local_timestamp)
        session.add(project)
        session.commit()
        project_id = project.id
        session.expunge_all()

        restored = session.get(ResearchProject, project_id)

    assert restored.created_at == expected_utc
    assert restored.created_at.utcoffset() == timedelta(0)


def test_utc_timestamp_bind_rejects_naive_input(migrated_engine):
    with Session(migrated_engine) as session:
        session.add(
            ResearchProject(
                name="Naive timestamp",
                created_at=datetime(2024, 4, 3, 12, 15),
            )
        )
        with pytest.raises(StatementError, match="timezone-aware"):
            session.flush()
        session.rollback()


def _seed_search_plan(session: Session, *, provider: str = "PubMed"):
    project = ResearchProject(name="Search project")
    session.add(project)
    session.flush()
    first_query = SavedQuery(project_id=project.id, expression="first query")
    second_query = SavedQuery(project_id=project.id, expression="second query")
    session.add_all([first_query, second_query])
    session.flush()
    plan = ProviderQueryPlan(
        query_id=first_query.id,
        provider=provider,
        translated_query="translated query",
        translation_version="v1",
    )
    session.add(plan)
    session.commit()
    return first_query, second_query, plan


def test_search_run_cannot_reference_another_queries_plan(migrated_engine):
    with Session(migrated_engine) as session:
        _first_query, second_query, plan = _seed_search_plan(session)
        session.add(
            SearchRun(
                query_id=second_query.id,
                plan_id=plan.id,
                provider=plan.provider,
                state="completed",
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


def test_search_run_plan_provider_must_match_run_provider(migrated_engine):
    with Session(migrated_engine) as session:
        first_query, _second_query, plan = _seed_search_plan(session)
        session.add(
            SearchRun(
                query_id=first_query.id,
                plan_id=plan.id,
                provider="Europe PMC",
                state="completed",
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


def test_query_match_run_must_belong_to_the_same_saved_query(migrated_engine):
    with Session(migrated_engine) as session:
        run_query, other_query, plan = _seed_search_plan(session)
        run = SearchRun(
            query_id=run_query.id,
            plan_id=plan.id,
            provider=plan.provider,
            state="completed",
        )
        article = Article(title="Run provenance check")
        session.add_all([run, article])
        session.flush()

        session.add(
            QueryMatch(
                query_id=other_query.id,
                article_id=article.id,
                first_run_id=run.id,
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


def test_snapshot_run_provider_must_match_run_provider(migrated_engine):
    with Session(migrated_engine) as session:
        first_query, _second_query, plan = _seed_search_plan(session)
        run = SearchRun(
            query_id=first_query.id,
            plan_id=plan.id,
            provider=plan.provider,
            state="completed",
        )
        session.add(run)
        session.flush()
        session.add(
            SourceSnapshot(
                provider="Europe PMC",
                search_run_id=run.id,
                storage_key="snapshots/wrong-run-provider",
                sha256="c" * 64,
                content_type="application/json",
                byte_size=1,
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


def test_source_record_snapshot_provider_must_match_source_provider(migrated_engine):
    with Session(migrated_engine) as session:
        article = Article(title="Provider link test")
        pubmed_snapshot = SourceSnapshot(
            provider="PubMed",
            storage_key="snapshots/pubmed",
            sha256="d" * 64,
            content_type="application/json",
            byte_size=1,
        )
        europe_pmc_snapshot = SourceSnapshot(
            provider="Europe PMC",
            storage_key="snapshots/europe-pmc",
            sha256="e" * 64,
            content_type="application/json",
            byte_size=1,
        )
        session.add_all([article, pubmed_snapshot, europe_pmc_snapshot])
        session.flush()

        source_record = SourceRecord(
            provider="PubMed",
            provider_record_id="12345",
            article_id=article.id,
            snapshot_id=pubmed_snapshot.id,
            parser_version="v1",
        )
        session.add(source_record)
        session.flush()
        session.add(
            SourceRecordSnapshot(
                source_record_id=source_record.id,
                snapshot_id=europe_pmc_snapshot.id,
                provider="PubMed",
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


def test_source_record_must_reference_its_providers_snapshot(migrated_engine):
    with Session(migrated_engine) as session:
        article = Article(title="Provider link test")
        snapshot = SourceSnapshot(
            provider="PubMed",
            storage_key="snapshots/pubmed",
            sha256="f" * 64,
            content_type="application/json",
            byte_size=1,
        )
        session.add_all([article, snapshot])
        session.flush()
        session.add(
            SourceRecord(
                provider="Europe PMC",
                provider_record_id="12345",
                article_id=article.id,
                snapshot_id=snapshot.id,
                parser_version="v1",
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


def test_hard_deleting_project_with_saved_query_is_restricted(migrated_engine):
    with Session(migrated_engine) as session:
        project = ResearchProject(name="Retained project")
        session.add(project)
        session.flush()
        query = SavedQuery(project_id=project.id, expression="saved query")
        session.add(query)
        session.commit()

        session.delete(project)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()
