"""Idempotent project-paper projection into a dedicated Google Sheet tab."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from io import StringIO
import re
from threading import Lock
from urllib.parse import urlsplit
from uuid import UUID

from research_engine.application.exports import ExportService
from research_engine.application.projects import ProjectService
from research_engine.ports.sheets import SheetGateway, SheetLink, SheetLinkStore


_ID = re.compile(r"[A-Za-z0-9_-]{20,128}\Z")


def spreadsheet_id(value: str) -> str:
    value = value.strip()
    if value.startswith("https://"):
        url = urlsplit(value)
        if url.hostname != "docs.google.com" or url.username or url.password:
            raise ValueError("enter a Google Sheets URL or spreadsheet ID")
        parts = url.path.split("/")
        if len(parts) < 4 or parts[1:3] != ["spreadsheets", "d"]:
            raise ValueError("enter a Google Sheets URL or spreadsheet ID")
        value = parts[3]
    if not _ID.fullmatch(value):
        raise ValueError("invalid Google spreadsheet ID")
    return value


class SheetSyncService:
    def __init__(self, links: SheetLinkStore, projects: ProjectService,
                 exports: ExportService, gateway_factory) -> None:
        self._links, self._projects, self._exports = links, projects, exports
        self._gateway_factory = gateway_factory
        self._lock = Lock()

    def get(self, project_id: UUID | str) -> SheetLink | None:
        return self._links.get(str(project_id))

    def configure(self, project_id: UUID | str, url_or_id: str, enabled: bool) -> SheetLink:
        if self._projects.get_project(UUID(str(project_id))) is None:
            raise KeyError("project")
        if type(enabled) is not bool:
            raise ValueError("enabled must be boolean")
        target = spreadsheet_id(url_or_id)
        with self._lock:
            return self._links.configure(str(project_id), target, enabled)

    def sync(self, project_id: UUID | str) -> SheetLink:
        link = self.get(project_id)
        if link is None:
            raise ValueError("connect a Google Sheet before syncing")
        if not self._lock.acquire(blocking=False):
            raise ValueError("a Google Sheet sync is already running")
        try:
            return self._sync(link)
        except Exception as exc:
            self._links.record_failure(
                link.project_id, getattr(exc, "error_code", type(exc).__name__),
            )
            raise
        finally:
            self._lock.release()

    def sync_scheduled_query(self, query_id: str) -> None:
        query = self._projects.get_query(UUID(query_id))
        if query is None:
            return
        link = self.get(query.project_id)
        if link and link.enabled:
            self.sync(link.project_id)

    def _sync(self, link: SheetLink) -> SheetLink:
        document = self._exports.export_project(link.project_id, "csv", include_excluded=True)
        content = list(csv.reader(StringIO(document.content.decode("utf-8"))))
        headers, rows = content[0], content[1:]
        last_column = chr(ord("A") + len(headers) - 1)
        title = f"Research Engine - {link.project_id}"
        prefix = f"'{title}'!"
        gateway: SheetGateway = self._gateway_factory()
        try:
            gateway.ensure_tab(link.spreadsheet_id, title)
            previous = gateway.read_values(link.spreadsheet_id, prefix + f"A4:{last_column}")
            if previous and previous[0] != headers:
                raise ValueError("the Research Engine tab has unexpected headers")
            existing: dict[str, tuple[int, list[str]]] = {}
            for row_number, old in enumerate(previous[1:], start=5):
                if len(old) < 3 or not old[2]:
                    raise ValueError("the Research Engine tab contains a row without a paper ID")
                if old[2] in existing or old[0] != link.project_id:
                    raise ValueError("the Research Engine tab contains duplicate or foreign papers")
                existing[old[2]] = (row_number, old)
            updates: list[dict] = []
            additions: list[list[str]] = []
            for row in rows:
                if row[2] in existing:
                    number, old = existing[row[2]]
                    if row != old + [""] * (len(row) - len(old)):
                        updates.append({"range": prefix + f"A{number}:{last_column}{number}", "values": [row]})
                else:
                    additions.append(row)
            if not previous:
                updates.append({"range": prefix + f"A4:{last_column}4", "values": [headers]})
            updates.append({"range": prefix + "A2:B2", "values": [["Project", rows[0][1] if rows else self._projects.get_project(UUID(link.project_id)).name]]})
            # Keep individual requests bounded; interrupted batches replay by article ID.
            for start in range(0, len(updates), 100):
                gateway.write_ranges(link.spreadsheet_id, updates[start:start + 100])
            for start in range(0, len(additions), 100):
                gateway.append_rows(
                    link.spreadsheet_id, prefix + f"A4:{last_column}",
                    additions[start:start + 100],
                )
            timestamp = datetime.now(timezone.utc)
            gateway.write_ranges(link.spreadsheet_id, [
                {"range": prefix + "A1:B1",
                 "values": [["Last successful update (UTC)", timestamp.isoformat()]]},
                {"range": prefix + "A3:B3", "values": [["Paper count", len(rows)]]},
            ])
            return self._links.record_success(link.project_id, timestamp, len(rows))
        finally:
            gateway.close()
