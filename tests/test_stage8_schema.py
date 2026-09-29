from __future__ import annotations

import json
from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from research_engine.infrastructure.persistence.base import Base
from research_engine.infrastructure.persistence.database import create_database_engine
from research_engine.infrastructure.persistence.models import Article


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _alembic_config(database_path: Path) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    return config


def _provenance(value: object) -> dict:
    if isinstance(value, str):
        return json.loads(value)
    return value


def test_stage8_provenance_migration_preserves_rows_and_round_trips(tmp_path: Path):
    database_path = tmp_path / "stage8-provenance.sqlite3"
    config = _alembic_config(database_path)
    command.upgrade(config, "0004_monitoring")

    # These records predate provenance support. Their winning source must stay
    # unknown instead of being inferred from their current source records.
    with create_database_engine(f"sqlite:///{database_path}").begin() as connection:
        connection.execute(
            text(
                "INSERT INTO articles (id, title, created_at, updated_at) "
                "VALUES ('legacy-a', 'Legacy article A', '2025-01-01 00:00:00', "
                "'2025-01-01 00:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO articles (id, title, abstract, created_at, updated_at) "
                "VALUES ('legacy-b', 'Legacy article B', 'Older abstract', "
                "'2025-01-02 00:00:00', '2025-01-03 00:00:00')"
            )
        )

    command.upgrade(config, "head")
    engine = create_database_engine(f"sqlite:///{database_path}")
    with engine.connect() as connection:
        columns = {column["name"] for column in inspect(connection).get_columns("articles")}
        assert "canonical_metadata_provenance" in columns
        legacy_rows = connection.execute(
            text(
                "SELECT id, title, abstract, canonical_metadata_provenance "
                "FROM articles ORDER BY id"
            )
        ).mappings().all()
        assert [(row["id"], row["title"]) for row in legacy_rows] == [
            ("legacy-a", "Legacy article A"),
            ("legacy-b", "Legacy article B"),
        ]
        assert legacy_rows[0]["abstract"] is None
        assert legacy_rows[1]["abstract"] == "Older abstract"
        assert all(_provenance(row["canonical_metadata_provenance"]) == {} for row in legacy_rows)
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []

    with Session(engine) as session:
        session.add(
            Article(
                id="new-article",
                title="Newly ingested article",
                canonical_metadata_provenance={
                    "title": {
                        "provider": "pubmed",
                        "source_id": "12345",
                        "snapshot_id": "snapshot-a",
                        "run_id": "run-a",
                        "parser_version": "pubmed-parser-v3",
                    }
                },
            )
        )
        session.commit()

    command.downgrade(config, "0004_monitoring")
    with engine.connect() as connection:
        columns = {column["name"] for column in inspect(connection).get_columns("articles")}
        assert "canonical_metadata_provenance" not in columns
        rows = connection.execute(
            text("SELECT id, title FROM articles ORDER BY id")
        ).all()
        assert rows == [
            ("legacy-a", "Legacy article A"),
            ("legacy-b", "Legacy article B"),
            ("new-article", "Newly ingested article"),
        ]

    command.upgrade(config, "head")
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT id, canonical_metadata_provenance FROM articles ORDER BY id"
            )
        ).all()
        assert [(row[0], _provenance(row[1])) for row in rows] == [
            ("legacy-a", {}),
            ("legacy-b", {}),
            ("new-article", {}),
        ]
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    engine.dispose()
