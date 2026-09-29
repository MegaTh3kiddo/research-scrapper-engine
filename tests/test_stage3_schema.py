from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from research_engine.infrastructure.persistence.base import Base
from research_engine.infrastructure.persistence.database import create_database_engine
from research_engine.infrastructure.persistence.models import (
    Article,
    IdentityConflict,
    ResearchProject,
    SavedQuery,
    SearchRun,
    SearchRunObservation,
    SourceRecord,
    SourceSnapshot,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _alembic_config(database_path: Path) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    return config


def test_stage3_migration_backfills_and_round_trips(tmp_path: Path):
    database_path = tmp_path / "stage3.sqlite3"
    config = _alembic_config(database_path)
    command.upgrade(config, "0001_initial_schema")

    # Seed legacy rows using the Stage 1 layout, before its columns are changed.
    seed_engine = create_database_engine(f"sqlite:///{database_path}")
    with seed_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO research_projects (id, name, created_at, updated_at) "
                "VALUES ('legacy-project', 'Legacy project', '2025-01-01 00:00:00', '2025-01-01 00:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO saved_queries "
                "(id, project_id, expression, filters, enabled_providers, is_active, created_at, updated_at) "
                "VALUES ('legacy-query', 'legacy-project', 'cancer', '{}', '[]', 1, "
                "'2025-01-01 00:00:00', '2025-01-01 00:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO provider_query_plans "
                "(id, query_id, provider, translated_query, parameters, unsupported_filters, translation_version, created_at) "
                "VALUES ('legacy-plan', 'legacy-query', 'PubMed', 'cancer', '{}', '[]', 'v1', "
                "'2025-01-01 00:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO search_runs (id, query_id, plan_id, provider, state, started_at) "
                "VALUES ('legacy-run', 'legacy-query', 'legacy-plan', 'PubMed', 'completed', "
                "'2025-01-01 00:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO articles (id, title, created_at, updated_at) "
                "VALUES ('legacy-article', 'Legacy paper', '2025-01-01 00:00:00', '2025-01-01 00:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO source_snapshots "
                "(id, provider, search_run_id, storage_key, sha256, content_type, compression, byte_size, captured_at, request_metadata) "
                "VALUES ('legacy-snapshot', 'PubMed', 'legacy-run', 'legacy/raw.json', :sha, 'application/json', NULL, 8, "
                "'2025-01-01 00:00:00', '{}')"
            ),
            {"sha": "a" * 64},
        )
        connection.execute(
            text(
                "INSERT INTO source_records "
                "(id, provider, provider_record_id, article_id, snapshot_id, source_updated_at, fetched_at, parser_version, rights_note) "
                "VALUES ('legacy-source', 'PubMed', '12345', 'legacy-article', 'legacy-snapshot', NULL, "
                "'2025-01-01 00:00:00', 'old-parser', NULL)"
            )
        )
    seed_engine.dispose()

    command.upgrade(config, "head")
    with create_engine(f"sqlite:///{database_path}").connect() as connection:
        snapshot = connection.execute(
            text("SELECT parser_version FROM source_snapshots WHERE id = 'legacy-snapshot'")
        ).scalar_one()
        identifiers = connection.execute(
            text("SELECT identifiers FROM source_records WHERE id = 'legacy-source'")
        ).scalar_one()
        assert snapshot == "unknown"
        assert identifiers == "[]"

        context = MigrationContext.configure(connection)
        assert compare_metadata(context, Base.metadata) == []

    with create_database_engine(f"sqlite:///{database_path}").connect() as connection:
        inspector = inspect(connection)
        assert inspector.get_columns("source_snapshots")[
            [column["name"] for column in inspector.get_columns("source_snapshots")].index("parser_version")
        ]["nullable"] is False
        scheme = next(
            column for column in inspector.get_columns("article_identifiers") if column["name"] == "scheme"
        )
        assert scheme["type"].length == 160
        assert {"search_run_observations", "identity_conflicts"}.issubset(
            set(inspector.get_table_names())
        )

    command.downgrade(config, "0001_initial_schema")
    with create_engine(f"sqlite:///{database_path}").connect() as connection:
        inspector = inspect(connection)
        assert "parser_version" not in {
            column["name"] for column in inspector.get_columns("source_snapshots")
        }
        assert "identifiers" not in {
            column["name"] for column in inspector.get_columns("source_records")
        }
        assert inspector.get_columns("article_identifiers")[
            [column["name"] for column in inspector.get_columns("article_identifiers")].index("scheme")
        ]["type"].length == 40
        assert "search_run_observations" not in inspector.get_table_names()
        assert "identity_conflicts" not in inspector.get_table_names()


def test_observation_and_identity_conflict_audit_constraints(tmp_path: Path):
    database_path = tmp_path / "stage3-audit.sqlite3"
    command.upgrade(_alembic_config(database_path), "head")
    engine = create_database_engine(f"sqlite:///{database_path}")

    with Session(engine) as session:
        project = ResearchProject(name="Stage 3")
        session.add(project)
        session.flush()
        query = SavedQuery(project_id=project.id, expression="cancer")
        session.add(query)
        session.flush()

        run = SearchRun(query_id=query.id, provider="PubMed", state="completed")
        article = Article(title="A paper")
        session.add_all([run, article])
        session.flush()
        assert run.recent_window_days == 30
        assert run.updated_count == run.newly_published_count == run.conflict_count == 0

        snapshot = SourceSnapshot(
            provider="PubMed",
            search_run_id=run.id,
            storage_key="snapshots/pubmed/response.json",
            sha256="b" * 64,
            content_type="application/json",
            byte_size=32,
            parser_version="pubmed-v2",
        )
        session.add(snapshot)
        session.flush()
        source_record = SourceRecord(
            provider="PubMed",
            provider_record_id="12345",
            article_id=article.id,
            snapshot_id=snapshot.id,
            parser_version="pubmed-v2",
            identifiers=[{"scheme": "doi", "value": "10.1000/example"}],
        )
        session.add(source_record)
        session.flush()

        observation = SearchRunObservation(
            run_id=run.id,
            provider="PubMed",
            source_record_id=source_record.id,
            article_id=article.id,
            snapshot_id=snapshot.id,
            newly_discovered=True,
            new_to_query=True,
            newly_published=None,
            metadata_updated=False,
        )
        conflict = IdentityConflict(
            run_id=run.id,
            provider="PubMed",
            source_id="12345",
            snapshot_id=snapshot.id,
            identifiers={"doi": "10.1000/example"},
            candidate_article_ids=[article.id],
        )
        session.add_all([observation, conflict])
        session.commit()

        assert observation.newly_discovered is True
        assert observation.newly_published is None
        assert conflict.status == "unresolved"
        assert conflict.identifiers == {"doi": "10.1000/example"}

        session.add(
            SearchRunObservation(
                run_id=run.id,
                provider="PubMed",
                source_record_id=source_record.id,
                article_id=article.id,
                snapshot_id=snapshot.id,
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

        session.add(
            SearchRun(
                query_id=query.id,
                provider="PubMed",
                state="completed",
                recent_window_days=-1,
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


def test_sqlite_foreign_key_checks_restore_after_failed_migration(tmp_path: Path):
    spec = spec_from_file_location(
        "stage3_migration", PROJECT_ROOT / "migrations" / "versions" / "0002_stage3_audit.py"
    )
    assert spec is not None and spec.loader is not None
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_database_engine(f"sqlite:///{tmp_path / 'fk-restore.sqlite3'}")

    def fail_migration() -> None:
        raise RuntimeError("injected migration failure")

    with engine.connect() as connection:
        operations = Operations.context(MigrationContext.configure(connection))
        with operations:
            with pytest.raises(RuntimeError, match="injected migration failure"):
                migration._with_sqlite_fk_checks_disabled(fail_migration)()
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
