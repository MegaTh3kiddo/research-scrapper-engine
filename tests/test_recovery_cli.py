from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from research_engine.recovery_cli import main


class _Provider:
    def __init__(self):
        self.closed = False

    async def aclose(self):
        self.closed = True


class _Searches:
    def __init__(self):
        self.run_ids = []

    async def retry_run(self, run_id):
        self.run_ids.append(run_id)
        return SimpleNamespace(run_id="new-run", state="completed")


class _Recovery:
    def __init__(self):
        self.calls = []
        self.runs = ()
        self.coverage = ()
        self.schedule = SimpleNamespace(query_id="query-1")

    def list_running_runs(self):
        return self.runs

    def recover_run(self, *args, **kwargs):
        self.calls.append(("recover_run", args, kwargs))

    def list_coverage(self, *args):
        self.calls.append(("list_coverage", args))
        return self.coverage

    def restart_coverage(self, *args, **kwargs):
        self.calls.append(("restart_coverage", args, kwargs))

    def queue_retry(self, *args):
        self.calls.append(("queue_retry", args))
        return self.schedule


class _Services:
    def __init__(self):
        self.recovery = _Recovery()
        self.monitoring = SimpleNamespace(list_running=lambda: ())
        self.searches = _Searches()
        self.provider = _Provider()
        self.closed = False

    def search_service(self):
        return self.searches, (self.provider,)

    def close(self):
        self.closed = True


@pytest.fixture
def cli_services(monkeypatch):
    services = _Services()
    opened = []

    def create(data_directory=None):
        opened.append(data_directory)
        return services

    monkeypatch.setattr("research_engine.recovery_cli.create_application_services", create)
    return services, opened


def _run(tmp_path, operation):
    return main([*operation, "--data-dir", str(tmp_path)])


def test_cli_lists_and_recovers_running_provider_runs(cli_services, tmp_path, capsys):
    services, opened = cli_services
    services.recovery.runs = (SimpleNamespace(
        id="run-1", query_id="query-1", provider="pubmed",
        started_at=datetime(2026, 9, 26, 12, tzinfo=timezone.utc),
        scheduled_execution_id=None,
    ),)

    assert _run(tmp_path, ["--list-runs"]) == 0
    listing = capsys.readouterr().out
    assert "run-1" in listing and "query=query-1" in listing and "provider=pubmed" in listing

    assert _run(tmp_path, ["--recover-run", "run-1", "--confirm-worker-stopped"]) == 0
    assert services.recovery.calls[-1] == (
        "recover_run", ("run-1",), {"confirm_worker_stopped": True},
    )
    assert "committed pages retained" in capsys.readouterr().out
    assert opened == [tmp_path, tmp_path]
    assert services.closed


def test_cli_lists_coverage_and_restarts_only_with_confirmation(cli_services, tmp_path, capsys):
    services, _ = cli_services
    services.recovery.coverage = (SimpleNamespace(
        id="coverage-1", provider="europe_pmc", lane="recent", complete=False,
    ),)

    assert _run(tmp_path, ["--coverage", "query-1"]) == 0
    assert "coverage-1" in capsys.readouterr().out
    assert services.recovery.calls[-1] == ("list_coverage", ("query-1",))

    with pytest.raises(SystemExit) as error:
        _run(tmp_path, ["--restart-coverage", "query-1", "europe_pmc", "recent"])
    assert error.value.code == 2

    assert _run(tmp_path, [
        "--restart-coverage", "query-1", "europe_pmc", "recent",
        "--confirm-worker-stopped",
    ]) == 0
    assert services.recovery.calls[-1] == (
        "restart_coverage", ("query-1", "europe_pmc", "recent"),
        {"confirm_worker_stopped": True},
    )
    assert "new campaign" in capsys.readouterr().out


def test_cli_queues_query_retry_without_mutating_query_settings(cli_services, tmp_path, capsys):
    services, _ = cli_services

    assert _run(tmp_path, ["--retry-query", "query-1"]) == 0
    assert services.recovery.calls == [("queue_retry", ("query-1",))]
    assert "requires an active worker" in capsys.readouterr().out
    assert services.closed


def test_cli_resumes_manual_run_and_closes_provider_even_on_failure(cli_services, tmp_path, capsys):
    services, _ = cli_services

    assert _run(tmp_path, ["--resume-run", "run-2", "--confirm-worker-stopped"]) == 0
    assert services.searches.run_ids == ["run-2"]
    assert "Retry recorded" in capsys.readouterr().out
    assert services.provider.closed
    assert services.closed


def test_cli_resume_requires_worker_confirmation_and_sanitizes_errors(
    cli_services, tmp_path, capsys,
):
    services, opened = cli_services
    with pytest.raises(SystemExit) as error:
        _run(tmp_path, ["--resume-run", "run-2"])
    assert error.value.code == 2
    assert opened == []

    async def fail_retry(run_id):
        raise RuntimeError("private-provider-secret")

    services.searches.retry_run = fail_retry
    assert _run(tmp_path, ["--resume-run", "run-2", "--confirm-worker-stopped"]) == 1
    captured = capsys.readouterr()
    assert "RuntimeError" in captured.err
    assert "private-provider-secret" not in captured.err
    assert services.provider.closed
    assert services.closed
