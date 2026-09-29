"""Browser adapter tests use real services/SQLite and deterministic provider I/O."""

import asyncio
from datetime import datetime, timedelta, timezone
from threading import Event
import time
from uuid import uuid4

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from research_engine.infrastructure import bootstrap
from research_engine.ports.providers import ProviderQueryPlan, ProviderRecord, RawResponse, SearchPage
from research_engine.web.app import create_app


class Provider:
    name = "pubmed"
    parser_version = "web-fixture-v1"
    closed = False

    def __init__(self, gate=None, fail=False):
        self.gate, self.fail = gate, fail

    def plan(self, request):
        return ProviderQueryPlan(provider=self.name, translated_query=request.expression,
                                 translation_version="web-v1", parameters={})

    async def search(self, plan, cursor=None):
        if self.gate:
            await asyncio.to_thread(self.gate.wait, 5)
        if self.fail:
            raise RuntimeError("secret-do-not-expose")
        return SearchPage(records=(ProviderRecord(source_id="web-paper", title="Browser discovery",
                                                  source_database="pubmed"),),
                          raw_responses=(RawResponse(body=b"web-fixture", media_type="text/plain",
                              fetched_at=datetime.now(timezone.utc), safe_request_metadata={}),))

    async def aclose(self):
        self.closed = True


@pytest.fixture
def web(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    providers = []
    def factory(*args):
        provider = Provider()
        providers.append(provider)
        return (provider,)
    monkeypatch.setattr(bootstrap, "_provider_instances", factory)
    app = create_app(data_directory=tmp_path)
    with TestClient(app, base_url="http://localhost") as client:
        html = client.get("/").text
        token = html.split('name="research-token" content="')[1].split('"')[0]
        client.headers["x-research-token"] = token
        yield client, app, providers


def setup_query(client):
    p = client.post("/api/projects", json={"name": "Browser pilot"})
    assert p.status_code == 201
    project = p.json()["id"]
    q = client.post(f"/api/projects/{project}/queries",
                    json={"expression": "cancer immunotherapy", "providers": ["pubmed"]})
    assert q.status_code == 201
    return project, q.json()["id"]


def wait_job(client):
    for _ in range(200):
        jobs = client.get("/api/jobs").json()
        if jobs and jobs[0]["state"] != "running":
            return jobs[0]
        time.sleep(.01)
    pytest.fail("job did not finish")


def test_web_search_history_and_canonical_repeat(web):
    client, app, providers = web
    project, query = setup_query(client)
    for expected_new in (1, 0):
        response = client.post(f"/api/queries/{query}/search", json={"max_pages": 1})
        assert response.status_code == 202
        job = wait_job(client)
        assert job["state"] == "completed"
        h = client.get(f"/api/queries/{query}/history").json()
        assert h["runs"][-1]["new_count"] == expected_new
        assert all(p.closed for p in providers)
    papers = client.get(f"/api/projects/{project}/papers").json()
    assert len(papers) == 1
    detail = client.get(f"/api/projects/{project}/papers/{papers[0]['article_id']}").json()
    assert detail["title"] == "Browser discovery"
    assert detail["source_metadata"][0]["snapshot_id"]
    assert len(h["runs"]) == len(h["attempts"]) == 2


def test_local_browser_guards(web):
    client, _, _ = web
    assert client.post("/api/projects", json={"name": "No token"},
                       headers={"x-research-token": "bad"}).status_code == 403
    assert client.post("/api/projects", json={"name": "Evil origin"},
                       headers={"origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/projects", headers={"host": "evil.example"}).status_code == 400
    r = client.get("/")
    assert r.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    # Guide content must be served from packaged assets, not a checkout-only path.
    assert 'href="#help-guide"' in r.text
    assert 'id="guide-setup"' in r.text
    assert 'id="guide-workflow"' in r.text
    assert "First-Time Setup — install, connect, and run your first search" in r.text
    assert "__USER_GUIDE__" not in r.text


def test_invalid_and_missing_records(web):
    client, _, _ = web
    project, query = setup_query(client)
    assert client.post(f"/api/projects/{project}/queries", json={
        "expression": "x", "providers": ["unknown"]}).status_code == 400
    assert client.post(f"/api/projects/{project}/queries", json={
        "expression": "x", "providers": ["pubmed", "pubmed"]}).status_code == 400
    assert client.post("/api/projects", json={"name": " "}).status_code == 400
    assert client.get(f"/api/queries/{uuid4()}/history").status_code == 404
    assert client.get("/api/queries/not-uuid/history").status_code == 422
    assert client.post(f"/api/queries/{query}/search", json={"max_pages": 0}).status_code == 422


def test_nonblocking_search_and_overlap(web, monkeypatch):
    client, _, providers = web
    _, query = setup_query(client)
    gate = Event()
    p = Provider(gate=gate)
    monkeypatch.setattr(bootstrap, "_provider_instances", lambda *args: (p,))
    try:
        assert client.post(f"/api/queries/{query}/search", json={}).status_code == 202
        assert client.get("/api/projects").status_code == 200
        assert client.post(f"/api/queries/{query}/search", json={}).status_code == 409
        assert client.post("/api/monitoring/run-due").status_code == 409
    finally:
        gate.set()
    assert wait_job(client)["state"] == "completed"
    assert p.closed


def test_failed_provider_preserves_audit_and_redacts_text(web, monkeypatch):
    client, _, _ = web
    _, query = setup_query(client)
    p = Provider(fail=True)
    monkeypatch.setattr(bootstrap, "_provider_instances", lambda *args: (p,))
    client.post(f"/api/queries/{query}/search", json={})
    job = wait_job(client)
    assert job["result"][0]["error_code"] == "RuntimeError"
    assert "secret-do-not-expose" not in str(job)
    h = client.get(f"/api/queries/{query}/history").json()
    assert h["runs"][0]["state"] == h["attempts"][0]["state"] == "failed"
    assert p.closed


def test_schedule_execution_reuses_monitoring_service(web):
    client, app, _ = web
    _, query = setup_query(client)
    default_schedule = client.put(f"/api/queries/{query}/schedule", json={"enabled": True})
    assert default_schedule.status_code == 200
    assert default_schedule.json()["interval_minutes"] == 60
    r = client.put(f"/api/queries/{query}/schedule", json={"enabled": True, "interval_minutes": 10})
    assert r.status_code == 200
    assert r.json()["interval_minutes"] == 10
    app.state.services.monitoring.configure(query, interval_minutes=10,
        next_due_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    assert client.post("/api/monitoring/run-due").status_code == 202
    assert wait_job(client)["state"] == "completed"
    history = client.get(f"/api/queries/{query}/history").json()
    assert history["scheduled_executions"][0]["state"] == "completed"
    assert history["coverage"]


def test_optional_poller_starts_and_shutdown_closes_services(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    services = bootstrap.create_application_services(tmp_path)
    closed = Event()
    class Wrapper:
        monitoring = services.monitoring
        def close(self):
            services.close()
            closed.set()
    app = create_app(service_factory=Wrapper, monitor=True, poll_seconds=1)
    with TestClient(app, base_url="http://localhost") as client:
        assert wait_job(client)["kind"] == "monitoring"
    assert closed.is_set()


def test_boolean_preview_uses_real_provider_plans_without_network(web, monkeypatch):
    from research_engine.infrastructure.providers.pubmed import PubMedProvider
    from research_engine.infrastructure.providers.europe_pmc import EuropePMCProvider
    from research_engine.infrastructure.providers.openalex import OpenAlexProvider
    from research_engine.infrastructure.providers.crossref import CrossrefProvider
    client, _, _ = web
    instances = []
    def providers(*args):
        instances.extend([PubMedProvider(), EuropePMCProvider(), OpenAlexProvider(), CrossrefProvider()])
        return tuple(instances)
    monkeypatch.setattr(bootstrap, "_provider_instances", providers)
    response = client.post("/api/queries/preview", json={
        "expression": '("breast cancer" or melanoma) and NOT "animal study"',
        "providers": ["pubmed", "europe_pmc", "openalex", "crossref"],
    })
    assert response.status_code == 200
    data = response.json()
    assert data["valid"]
    assert len(data["providers"]) == 4
    assert data["normalized_expression"] == '("breast cancer" OR melanoma) AND (NOT "animal study")'
    assert data["providers"][-1]["execution_mode"] == "metadata_filter"
    assert "Missing metadata" in data["providers"][-1]["notice"]
    assert "NOT" in data["providers"][0]["translated_query"]


def test_invalid_boolean_rejected_before_saving_with_character_position(web):
    client, _, _ = web
    project, query = setup_query(client)
    body = {"expression": "cancer AND (therapy OR)", "providers": ["pubmed"]}
    for path in ("/api/queries/preview", f"/api/projects/{project}/queries"):
        response = client.post(path, json=body)
        assert response.status_code == 422
        assert response.json()["detail"]["position"] == 22
        assert response.json()["detail"]["message"]
    assert len(client.get(f"/api/projects/{project}/queries").json()) == 1


def test_web_query_edit_preserves_filters_and_appends_revision(web):
    from research_engine.domain.value_objects import SearchFilters
    from datetime import date
    client, app, _ = web
    project, query = setup_query(client)
    app.state.services.projects.update_query(query, filters=SearchFilters(published_from=date(2024, 1, 1)))
    revisions_before = len(app.state.services.projects.list_query_revisions(query))
    response = client.put(f"/api/queries/{query}", json={
        "expression": "cancer AND (therapy OR vaccine) NOT animals", "providers": ["pubmed"]})
    assert response.status_code == 200
    assert response.json()["filters"]["published_from"] == "2024-01-01"
    assert len(app.state.services.projects.list_query_revisions(query)) == revisions_before + 1
    bad = client.put(f"/api/queries/{query}", json={"expression": "cancer OR", "providers": ["pubmed"]})
    assert bad.status_code == 422
    assert app.state.services.projects.get_query(query).expression == response.json()["expression"]
