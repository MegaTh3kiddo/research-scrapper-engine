from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from research_engine.infrastructure.persistence.base import Base
from research_engine.infrastructure.persistence.database import create_database_engine


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _alembic_config(database_path: Path) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    return config


def _seed_stage1_project_and_queries(database_path: Path) -> None:
    config = _alembic_config(database_path)
    command.upgrade(config, "0001_initial_schema")
    with create_engine(f"sqlite:///{database_path}").begin() as connection:
        connection.execute(
            text(
                "INSERT INTO research_projects (id, name, created_at, updated_at) "
                "VALUES ('project-a', 'Legacy project', '2025-01-01 00:00:00', '2025-01-01 00:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO research_projects (id, name, created_at, updated_at) "
                "VALUES ('project-b', 'Second project', '2025-01-01 00:00:00', '2025-01-01 00:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO saved_queries "
                "(id, project_id, expression, filters, enabled_providers, is_active, archived_at, created_at, updated_at) "
                "VALUES ('query-a', 'project-a', 'breast cancer', :filters, :providers, 0, "
                "'2025-02-01 00:00:00', '2025-01-01 00:00:00', '2025-01-01 00:00:00')"
            ),
            {"filters": '{"year":2024}', "providers": '["pubmed"]'},
        )
        connection.execute(
            text(
                "INSERT INTO saved_queries "
                "(id, project_id, expression, filters, enabled_providers, is_active, created_at, updated_at) "
                "VALUES ('query-b', 'project-b', 'heart disease', '{}', '[\"europe_pmc\"]', 1, "
                "'2025-01-01 00:00:00', '2025-01-01 00:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO search_runs (id, query_id, plan_id, provider, state, started_at) "
                "VALUES ('legacy-run', 'query-a', NULL, 'PubMed', 'completed', '2025-01-02 00:00:00')"
            )
        )


def test_stage4_migration_backfills_query_revisions_and_links_future_runs(tmp_path: Path):
    database_path = tmp_path / "stage4.sqlite3"
    _seed_stage1_project_and_queries(database_path)
    command.upgrade(_alembic_config(database_path), "head")

    engine = create_database_engine(f"sqlite:///{database_path}")
    with engine.connect() as connection:
        inspector = inspect(connection)
        assert "saved_query_revisions" in set(inspector.get_table_names())

        revisions = connection.execute(
            text(
                "SELECT query_id, revision_number, expression, filters, enabled_providers, "
                "is_active, archived_at FROM saved_query_revisions ORDER BY query_id"
            )
        ).mappings().all()
        assert [(row["query_id"], row["revision_number"]) for row in revisions] == [
            ("query-a", 1),
            ("query-b", 1),
        ]
        archived = revisions[0]
        assert archived["expression"] == "breast cancer"
        assert archived["filters"] == '{"year": 2024}'
        assert archived["enabled_providers"] == '["pubmed"]'
        assert archived["is_active"] == 0
        assert archived["archived_at"] == "2025-02-01 00:00:00.000000"
        assert connection.execute(
            text("SELECT revision_number FROM saved_queries WHERE id='query-a'")
        ).scalar_one() == 1
        assert connection.execute(
            text("SELECT query_revision_id FROM search_runs WHERE id='legacy-run'")
        ).scalar_one() is None

        revision_id = connection.execute(
            text("SELECT id FROM saved_query_revisions WHERE query_id='query-a'")
        ).scalar_one()
        connection.execute(
            text(
                "INSERT INTO search_runs (id, query_id, query_revision_id, provider, state, started_at) "
                "VALUES ('pinned-run', 'query-a', :revision_id, 'PubMed', 'completed', '2025-01-03 00:00:00')"
            ),
            {"revision_id": revision_id},
        )
        connection.commit()

        with connection.begin_nested():
            # A revision cannot be pinned to a run for a different saved query.
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        "INSERT INTO search_runs (id, query_id, query_revision_id, provider, state, started_at) "
                        "VALUES ('mismatched-run', 'query-b', :revision_id, 'PubMed', 'completed', '2025-01-03 00:00:00')"
                    ),
                    {"revision_id": revision_id},
                )

        assert "revision_number" in {
            column["name"] for column in inspector.get_columns("saved_queries")
        }
        revision_fks = inspector.get_foreign_keys("search_runs")
        assert any(
            fk["constrained_columns"] == ["query_revision_id", "query_id"]
            and fk["referred_table"] == "saved_query_revisions"
            and fk["referred_columns"] == ["id", "query_id"]
            for fk in revision_fks
        )

        context = MigrationContext.configure(connection)
        assert compare_metadata(context, Base.metadata) == []
    engine.dispose()


def test_stage4_migration_downgrade_preserves_sqlite_fk_checks(tmp_path: Path):
    database_path = tmp_path / "stage4-downgrade.sqlite3"
    _seed_stage1_project_and_queries(database_path)
    config = _alembic_config(database_path)
    command.upgrade(config, "head")
    command.downgrade(config, "0002_stage3_audit")

    with create_database_engine(f"sqlite:///{database_path}").connect() as connection:
        inspector = inspect(connection)
        assert "saved_query_revisions" not in set(inspector.get_table_names())
        assert "revision_number" not in {
            column["name"] for column in inspector.get_columns("saved_queries")
        }
        assert "query_revision_id" not in {
            column["name"] for column in inspector.get_columns("search_runs")
        }
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
