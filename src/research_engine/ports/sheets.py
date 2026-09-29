"""UI-neutral contracts for a project-owned Google Sheets projection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class SheetLink:
    project_id: str
    spreadsheet_id: str
    enabled: bool
    last_success_at: datetime | None = None
    last_synced_count: int = 0
    last_error_code: str | None = None


class SheetLinkStore(Protocol):
    def get(self, project_id: str) -> SheetLink | None: ...
    def configure(self, project_id: str, spreadsheet_id: str, enabled: bool) -> SheetLink: ...
    def record_success(self, project_id: str, at: datetime, count: int) -> SheetLink: ...
    def record_failure(self, project_id: str, error_code: str) -> SheetLink: ...


class SheetGateway(Protocol):
    def ensure_tab(self, spreadsheet_id: str, title: str) -> None: ...
    def read_values(self, spreadsheet_id: str, cell_range: str) -> list[list[str]]: ...
    def write_ranges(self, spreadsheet_id: str, ranges: list[dict]) -> None: ...
    def append_rows(self, spreadsheet_id: str, cell_range: str, rows: list[list[str]]) -> None: ...
    def close(self) -> None: ...
