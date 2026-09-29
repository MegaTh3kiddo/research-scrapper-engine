from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from research_engine.infrastructure.persistence.base import Base
from research_engine.infrastructure.persistence.database import create_database_engine


ROOT = Path(__file__).resolve().parents[1]
NOW = "2026-09-25 12:00:00.000000"


def _config(path: Path) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
    return config


def _seed_stage6_rows(config: Config, path: Path) -> None:
    command.upgrade(config, "0004_monitoring")
    engine = create_database_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO research_projects "
                "(id, name, created_at, updated_at) "
                "VALUES ('project-a', 'Project A', :now, :now)"
            ),
            {"now": NOW},
        )
        connection.execute(
            text(
                "INSERT INTO saved_queries "
                "(id, project_id, expression, filters, enabled_providers, is_active, "
                "created_at, updated_at) "
                "VALUES ('query-a', 'project-a', 'heart failure', '{}', '[\"pubmed\"]', "
                "1, :now, :now)"
            ),
            {"now": NOW},
        )
        connection.execute(
            text(
                "INSERT INTO query_schedules "
                "(id, query_id, interval_minutes, next_due_at, enabled, created_at, updated_at) "
                "VALUES ('schedule-a', 'query-a', 60, :now, 1, :now, :now)"
            ),
            {"now": NOW},
        )
        connection.execute(
            text(
                "INSERT INTO scheduled_executions "
                "(id, schedule_id, query_id, state, started_at, ended_at, error_code) "
                "VALUES ('execution-a', 'schedule-a', 'query-a', 'completed', :now, :now, NULL)"
            ),
            {"now": NOW},
        )
        connection.execute(
            text(
                "INSERT INTO search_runs "
                "(id, query_id, scheduled_execution_id, provider, state, started_at, ended_at) "
                "VALUES ('run-a', 'query-a', 'execution-a', 'pubmed', 'completed', :now, :now)"
            ),
            {"now": NOW},
        )
    engine.dispose()


def _insert_coverage(connection, *, coverage_id: str = "coverage-a", lane: str = "full") -> None:
    revision_id = connection.execute(
        text("SELECT id FROM saved_query_revisions WHERE query_id = 'query-a'")
    ).scalar_one_or_none()
    if revision_id is None:
        # This query was inserted after 0003 had backfilled its initial
        # revisions, so provide the equivalent initial revision explicitly.
        revision_id = "revision-a"
        connection.execute(
            text(
                "INSERT INTO saved_query_revisions "
                "(id, query_id, revision_number, expression, filters, enabled_providers, "
                "is_active, archived_at, created_at) "
                "VALUES (:id, 'query-a', 1, 'heart failure', '{}', '[\"pubmed\"]', "
                "1, NULL, :now)"
            ),
            {"id": revision_id, "now": NOW},
        )
    connection.execute(
        text(
            "INSERT INTO monitoring_coverage "
            "(id, query_id, provider, lane, revision_id, signature, created_at, updated_at) "
            "VALUES (:id, 'query-a', 'pubmed', :lane, :revision_id, :signature, :now, :now)"
        ),
        {
            "id": coverage_id,
            "lane": lane,
            "revision_id": revision_id,
            "signature": "a" * 64,
            "now": NOW,
        },
    )


def test_monitoring_coverage_migration_adds_schema_without_rewriting_existing_runs(
    tmp_path: Path,
):
    path = tmp_path / "monitoring-coverage.sqlite3"
    config = _config(path)
    _seed_stage6_rows(config, path)
    command.upgrade(config, "0005_metadata_provenance")
    command.upgrade(config, "head")

    engine = create_database_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        inspector = inspect(connection)
        assert "monitoring_coverage" in inspector.get_table_names()
        coverage_columns = {
            column["name"] for column in inspector.get_columns("monitoring_coverage")
        }
        assert {
            "id", "query_id", "provider", "lane", "revision_id", "signature", "cursor",
            "seen_cursors",
            "complete", "is_active", "published_from", "published_to", "last_completed_at",
            "pages_completed", "created_at", "updated_at",
        } == coverage_columns
        run_columns = {column["name"] for column in inspector.get_columns("search_runs")}
        assert {"coverage_id", "start_cursor"}.issubset(run_columns)
        coverage_fk = next(
            fk for fk in inspector.get_foreign_keys("search_runs")
            if fk["name"] == "fk_search_runs_coverage_query_provider_monitoring_coverage"
        )
        assert coverage_fk["constrained_columns"] == ["coverage_id", "query_id", "provider"]
        assert coverage_fk["referred_columns"] == ["id", "query_id", "provider"]
        assert any(
            index["name"] == "uq_monitoring_coverage_active"
            and index["unique"]
            and index["dialect_options"].get("sqlite_where") is not None
            for index in inspector.get_indexes("monitoring_coverage")
        )

        # Existing searches and their schedule history remain intact and are
        # deliberately not assigned guessed coverage state.
        old_run = connection.execute(
            text("SELECT id, scheduled_execution_id, coverage_id, start_cursor FROM search_runs")
        ).mappings().one()
        assert dict(old_run) == {
            "id": "run-a",
            "scheduled_execution_id": "execution-a",
            "coverage_id": None,
            "start_cursor": None,
        }
        assert connection.execute(
            text("SELECT state FROM scheduled_executions WHERE id = 'execution-a'")
        ).scalar_one() == "completed"

        _insert_coverage(connection)
        connection.execute(
            text(
                "INSERT INTO search_runs "
                "(id, query_id, provider, state, started_at, coverage_id, start_cursor) "
                "VALUES ('run-b', 'query-a', 'pubmed', 'partial', :now, 'coverage-a', 'cursor-1')"
            ),
            {"now": NOW},
        )
        assert connection.execute(
            text("SELECT start_cursor FROM search_runs WHERE id = 'run-b'")
        ).scalar_one() == "cursor-1"

        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                _insert_coverage(connection, coverage_id="coverage-duplicate")
        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                connection.execute(
                    text(
                        "INSERT INTO search_runs "
                        "(id, query_id, provider, state, started_at, coverage_id) "
                        "VALUES ('run-mismatched-provider', 'query-a', 'crossref', "
                        "'partial', :now, 'coverage-a')"
                    ),
                    {"now": NOW},
                )
        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                connection.execute(
                    text(
                        "INSERT INTO monitoring_coverage "
                        "(id, query_id, provider, lane, revision_id, signature, created_at, updated_at) "
                "VALUES ('coverage-invalid-lane', 'query-a', 'crossref', 'tail', "
                        "(SELECT id FROM saved_query_revisions WHERE query_id = 'query-a'), "
                        ":signature, :now, :now)"
                    ),
                    {"signature": "b" * 64, "now": NOW},
                )

        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    engine.dispose()

    command.downgrade(config, "0005_metadata_provenance")
    engine = create_database_engine(f"sqlite:///{path}")
    with engine.connect() as connection:
        assert "monitoring_coverage" not in inspect(connection).get_table_names()
        run_columns = {column["name"] for column in inspect(connection).get_columns("search_runs")}
        assert not {"coverage_id", "start_cursor"}.intersection(run_columns)
        assert connection.execute(text("SELECT count(*) FROM search_runs")).scalar_one() == 2
        assert connection.execute(
            text("SELECT state FROM scheduled_executions WHERE id = 'execution-a'")
        ).scalar_one() == "completed"
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    engine.dispose()


def test_downgrade_preserves_partial_execution_history_with_failure_marker(tmp_path: Path):
    path = tmp_path / "monitoring-coverage-downgrade.sqlite3"
    config = _config(path)
    _seed_stage6_rows(config, path)
    command.upgrade(config, "0005_metadata_provenance")
    command.upgrade(config, "head")

    engine = create_database_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO scheduled_executions "
                "(id, schedule_id, query_id, state, started_at, ended_at, error_code) "
                "VALUES ('execution-partial', 'schedule-a', 'query-a', 'partial', :now, :now, NULL)"
            ),
            {"now": NOW},
        )
    engine.dispose()

    command.downgrade(config, "0005_metadata_provenance")
    engine = create_database_engine(f"sqlite:///{path}")
    with engine.connect() as connection:
        executions = connection.execute(
            text(
                "SELECT id, state, error_code FROM scheduled_executions "
                "ORDER BY id"
            )
        ).all()
        assert executions == [
            ("execution-a", "completed", None),
            ("execution-partial", "failed", "CoverageDowngrade"),
        ]
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    engine.dispose()
