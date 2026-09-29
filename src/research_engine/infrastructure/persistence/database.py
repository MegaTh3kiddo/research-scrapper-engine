"""Engine construction helpers for the persistence adapter."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Engine, URL, create_engine, event


def create_database_engine(url: str | URL, **engine_options: Any) -> Engine:
    """Create an engine and enforce SQLite foreign keys on every connection.

    The listener is registered only for SQLite engines. Other supported
    dialects retain SQLAlchemy's normal connection behavior.
    """

    engine = create_engine(url, **engine_options)
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def _enable_sqlite_foreign_keys(dbapi_connection: Any, _connection_record: Any) -> None:
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
            finally:
                cursor.close()

    return engine
