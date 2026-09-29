"""Durable project-to-Sheet links; credentials never enter this store."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import sessionmaker

from research_engine.ports.sheets import SheetLink
from .models import GoogleSheetLink


def _view(row: GoogleSheetLink) -> SheetLink:
    return SheetLink(
        project_id=row.project_id, spreadsheet_id=row.spreadsheet_id,
        enabled=row.enabled, last_success_at=row.last_success_at,
        last_synced_count=row.last_synced_count, last_error_code=row.last_error_code,
    )


class SqlAlchemySheetLinkStore:
    def __init__(self, session_factory: sessionmaker) -> None:
        self._sessions = session_factory

    def get(self, project_id: str) -> SheetLink | None:
        with self._sessions() as session:
            row = session.get(GoogleSheetLink, project_id)
            return _view(row) if row else None

    def configure(self, project_id: str, spreadsheet_id: str, enabled: bool) -> SheetLink:
        with self._sessions.begin() as session:
            row = session.get(GoogleSheetLink, project_id)
            if row is None:
                row = GoogleSheetLink(
                    project_id=project_id, spreadsheet_id=spreadsheet_id, enabled=enabled,
                    last_synced_count=0,
                )
                session.add(row)
            else:
                if row.spreadsheet_id != spreadsheet_id:
                    row.last_success_at = None
                    row.last_synced_count = 0
                    row.last_error_code = None
                row.spreadsheet_id = spreadsheet_id
                row.enabled = enabled
            session.flush()
            return _view(row)

    def record_success(self, project_id: str, at: datetime, count: int) -> SheetLink:
        with self._sessions.begin() as session:
            row = session.get(GoogleSheetLink, project_id)
            if row is None:
                raise KeyError(project_id)
            row.last_success_at = at
            row.last_synced_count = count
            row.last_error_code = None
            session.flush()
            return _view(row)

    def record_failure(self, project_id: str, error_code: str) -> SheetLink:
        with self._sessions.begin() as session:
            row = session.get(GoogleSheetLink, project_id)
            if row is None:
                raise KeyError(project_id)
            row.last_error_code = error_code[:100]
            session.flush()
            return _view(row)
