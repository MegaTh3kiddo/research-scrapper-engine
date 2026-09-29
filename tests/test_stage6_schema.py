from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from research_engine.infrastructure.persistence.base import Base
from research_engine.infrastructure.persistence.database import create_database_engine


ROOT = Path(__file__).resolve().parents[1]


def _config(path: Path) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
    return config


def test_stage6_migration_matches_models_and_downgrades(tmp_path: Path):
    path = tmp_path / "stage6.sqlite3"
    config = _config(path)
    command.upgrade(config, "0003_saved_query_revisions")
    command.upgrade(config, "head")
    engine = create_database_engine(f"sqlite:///{path}")
    with engine.connect() as connection:
        inspector = inspect(connection)
        assert {"query_schedules", "scheduled_executions"}.issubset(inspector.get_table_names())
        assert "scheduled_execution_id" in {
            column["name"] for column in inspector.get_columns("search_runs")
        }
        assert any(
            fk["constrained_columns"] == ["scheduled_execution_id", "query_id"]
            and fk["referred_table"] == "scheduled_executions"
            for fk in inspector.get_foreign_keys("search_runs")
        )
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    engine.dispose()

    command.downgrade(config, "0003_saved_query_revisions")
    engine = create_database_engine(f"sqlite:///{path}")
    with engine.connect() as connection:
        inspector = inspect(connection)
        assert "query_schedules" not in inspector.get_table_names()
        assert "scheduled_executions" not in inspector.get_table_names()
        assert "scheduled_execution_id" not in {
            column["name"] for column in inspector.get_columns("search_runs")
        }
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    engine.dispose()
