"""Google Sheet projection and web workflow without external Google access."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import sessionmaker

from research_engine.application.sheets import spreadsheet_id
from research_engine.infrastructure.bootstrap import create_application_services, find_alembic_config
from research_engine.infrastructure.persistence.database import create_database_engine
from research_engine.infrastructure.persistence.projects import SqlAlchemyProjectStore
from research_engine.infrastructure.google_sheets import GoogleSheetsError, GoogleSheetsGateway
from test_web import web, setup_query, wait_job


SHEET_ID = "aBcDeFgHiJkLmNoPqRsTuVwXyZ1234567890"


class MemoryGateway:
    def __init__(self):
        self.tabs = set()
        self.rows = []
        self.status = None
        self.paper_count = None
        self.fail_append = False
        self.closed = 0

    def ensure_tab(self, spreadsheet_id, title):
        self.tabs.add((spreadsheet_id, title))

    def read_values(self, spreadsheet_id, cell_range):
        return [row[:] for row in self.rows]

    def write_ranges(self, spreadsheet_id, ranges):
        for item in ranges:
            cell = item["range"].split("!")[1]
            if cell.startswith("A1:"):
                self.status = item["values"][0]
            elif cell.startswith("A3:"):
                self.paper_count = item["values"][0]
            elif cell.startswith("A4:"):
                if self.rows:
                    self.rows[0] = item["values"][0]
                else:
                    self.rows.append(item["values"][0])
            elif cell.startswith("A5:"):
                self.rows[1] = item["values"][0]

    def append_rows(self, spreadsheet_id, cell_range, rows):
        if self.fail_append:
            raise RuntimeError("simulated interruption")
        self.rows.extend(row[:] for row in rows)

    def close(self):
        self.closed += 1


@pytest.mark.parametrize("bad", [
    "", "https://evil.example/spreadsheets/d/" + SHEET_ID,
    "http://docs.google.com/spreadsheets/d/" + SHEET_ID,
    "https://docs.google.com/spreadsheets/d/short", "a/b",
])
def test_only_google_sheet_ids_and_urls_are_accepted(bad):
    with pytest.raises(ValueError):
        spreadsheet_id(bad)


def test_sheet_url_extracts_id():
    assert spreadsheet_id(f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit#gid=0") == SHEET_ID


def test_project_sheet_link_migrates_and_survives_reopen(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    first = create_application_services(tmp_path)
    project = first.projects.create_project("Oncology")
    link = first.sheets.configure(project.id, SHEET_ID, True)
    assert link.spreadsheet_id == SHEET_ID
    assert link.last_success_at is None
    first.close()
    second = create_application_services(tmp_path)
    assert second.sheets.get(project.id) == link
    second.close()


def test_existing_database_upgrades_from_0009_without_losing_project(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    database_url = f"sqlite:///{tmp_path / 'research_engine.db'}"
    config = Config(str(find_alembic_config()))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "0009_identity_review")
    engine = create_database_engine(database_url)
    project = SqlAlchemyProjectStore(sessionmaker(engine, expire_on_commit=False)).create_project(
        "Existing workspace", None,
    )
    engine.dispose()
    upgraded = create_application_services(tmp_path)
    try:
        assert upgraded.projects.get_project(project.id).name == "Existing workspace"
        assert upgraded.sheets.configure(project.id, SHEET_ID, True).enabled is True
    finally:
        upgraded.close()


def test_sync_reuses_paper_id_and_records_success_only_after_remote_write(web):
    client, app, _ = web
    project, query = setup_query(client)
    gateway = MemoryGateway()
    app.state.services.sheets._gateway_factory = lambda: gateway
    link = client.put(f"/api/projects/{project}/sheet", json={
        "url": f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit", "enabled": True,
    })
    assert link.status_code == 200
    assert link.json()["last_success_at"] is None
    client.post(f"/api/queries/{query}/search", json={"max_pages": 1})
    wait_job(client)
    assert gateway.rows == []  # Manual search does not auto-sync.
    assert client.post(f"/api/projects/{project}/sheet/sync").status_code == 202
    assert wait_job(client)["state"] == "completed"
    assert len(gateway.rows) == 2
    assert gateway.rows[1][0] == project
    assert gateway.rows[1][2]
    assert gateway.status[0] == "Last successful update (UTC)"
    assert gateway.paper_count == ["Paper count", 1]
    assert len(gateway.rows[0]) == len(gateway.rows[1]) == 19
    first = client.get(f"/api/projects/{project}/sheet").json()
    assert first["last_synced_count"] == 1 and first["last_success_at"]
    client.post(f"/api/projects/{project}/sheet/sync")
    assert wait_job(client)["state"] == "completed"
    assert len(gateway.rows) == 2  # Idempotent; no duplicate row.
    gateway.rows[1][3] = "Outdated title"
    client.post(f"/api/projects/{project}/sheet/sync")
    assert wait_job(client)["state"] == "completed"
    assert gateway.rows[1][3] == "Browser discovery"
    assert gateway.closed == 3

    different = "zYxWvUtSrQpOnMlKjIhGfEdCbA0987654321"
    moved = client.put(f"/api/projects/{project}/sheet", json={"url": different, "enabled": False})
    assert moved.json()["last_success_at"] is None
    assert moved.json()["last_synced_count"] == 0


def test_failed_sync_preserves_previous_success_and_retries_without_duplicates(web):
    client, app, _ = web
    project, query = setup_query(client)
    gateway = MemoryGateway()
    app.state.services.sheets._gateway_factory = lambda: gateway
    client.put(f"/api/projects/{project}/sheet", json={"url": SHEET_ID, "enabled": True})
    client.post(f"/api/projects/{project}/sheet/sync")
    assert wait_job(client)["state"] == "completed"
    original = client.get(f"/api/projects/{project}/sheet").json()
    client.post(f"/api/queries/{query}/search", json={"max_pages": 1})
    wait_job(client)
    gateway.fail_append = True
    client.post(f"/api/projects/{project}/sheet/sync")
    assert wait_job(client)["state"] == "failed"
    failed = client.get(f"/api/projects/{project}/sheet").json()
    assert failed["last_success_at"] == original["last_success_at"]
    assert failed["last_error_code"] == "RuntimeError"
    assert gateway.status[1] == original["last_success_at"]
    gateway.fail_append = False
    client.post(f"/api/projects/{project}/sheet/sync")
    assert wait_job(client)["state"] == "completed"
    assert len(gateway.rows) == 2
    assert client.get(f"/api/projects/{project}/sheet").json()["last_error_code"] is None


def test_scheduled_search_triggers_enabled_sheet_sync(web):
    client, app, _ = web
    project, query = setup_query(client)
    gateway = MemoryGateway()
    app.state.services.sheets._gateway_factory = lambda: gateway
    client.put(f"/api/projects/{project}/sheet", json={"url": SHEET_ID, "enabled": True})
    app.state.services.monitoring.configure(
        query, interval_minutes=60,
        next_due_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    client.post("/api/monitoring/run-due")
    assert wait_job(client)["state"] == "completed"
    assert len(gateway.rows) == 2
    history = client.get(f"/api/queries/{query}/history").json()
    assert history["scheduled_executions"][0]["state"] == "completed"


def test_scheduled_sheet_failure_does_not_rewrite_search_outcome(web):
    client, app, _ = web
    project, query = setup_query(client)
    gateway = MemoryGateway()
    gateway.fail_append = True
    app.state.services.sheets._gateway_factory = lambda: gateway
    client.put(f"/api/projects/{project}/sheet", json={"url": SHEET_ID, "enabled": True})
    app.state.services.monitoring.configure(
        query, interval_minutes=60,
        next_due_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    client.post("/api/monitoring/run-due")
    assert wait_job(client)["state"] == "completed"
    history = client.get(f"/api/queries/{query}/history").json()
    assert history["scheduled_executions"][0]["state"] == "completed"
    link = client.get(f"/api/projects/{project}/sheet").json()
    assert link["last_success_at"] is None
    assert link["last_error_code"] == "RuntimeError"
    assert gateway.status is None


def test_google_adapter_uses_service_account_and_expected_api_shapes(tmp_path, monkeypatch):
    google_auth = pytest.importorskip("google.oauth2.service_account")
    filename = tmp_path / "google-service-account.json"
    filename.write_text(json.dumps({
        "type": "service_account", "token_uri": "https://oauth2.googleapis.com/token",
    }), encoding="utf-8")
    monkeypatch.setenv("RESEARCH_ENGINE_GOOGLE_SERVICE_ACCOUNT_FILE", str(filename))
    class Credentials:
        token = "test-token"
        def refresh(self, request):
            pass
    monkeypatch.setattr(google_auth.Credentials, "from_service_account_info",
                        lambda *args, **kwargs: Credentials())
    requests_seen = []
    def handler(request):
        requests_seen.append(request)
        if request.method == "GET" and request.url.path.endswith("/spreadsheets/" + SHEET_ID):
            return httpx.Response(200, json={"sheets": []})
        if request.method == "GET":
            return httpx.Response(200, json={"values": [["paper-id"]]})
        return httpx.Response(200, json={})
    original_client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    gateway = GoogleSheetsGateway()
    gateway.ensure_tab(SHEET_ID, "Research Engine - test")
    assert gateway.read_values(SHEET_ID, "'Research Engine - test'!A4:S") == [["paper-id"]]
    gateway.write_ranges(SHEET_ID, [{"range": "'Research Engine - test'!A1:B1", "values": [["Last update", "now"]]}])
    gateway.append_rows(SHEET_ID, "'Research Engine - test'!A4:S", [["paper-id"]])
    gateway.close()
    assert [request.method for request in requests_seen] == ["GET", "POST", "GET", "POST", "POST"]
    assert all(request.headers["Authorization"] == "Bearer test-token" for request in requests_seen)
    assert json.loads(requests_seen[1].content)["requests"][0]["addSheet"]["properties"]["title"] == "Research Engine - test"
    assert json.loads(requests_seen[3].content)["valueInputOption"] == "RAW"
    assert requests_seen[4].url.params["insertDataOption"] == "INSERT_ROWS"


def test_missing_google_credentials_have_safe_status_code(monkeypatch):
    monkeypatch.delenv("RESEARCH_ENGINE_GOOGLE_SERVICE_ACCOUNT_FILE", raising=False)
    with pytest.raises(GoogleSheetsError) as error:
        GoogleSheetsGateway()
    assert error.value.error_code == "CREDENTIALS_MISSING"
