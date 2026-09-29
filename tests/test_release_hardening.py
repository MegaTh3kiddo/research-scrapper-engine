from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from research_engine.desktop.bootstrap import create_desktop_services, find_alembic_config
from research_engine.infrastructure.persistence import models as m
from research_engine.infrastructure.persistence.scheduling import SqlAlchemyScheduleStore
from research_engine.recovery_cli import main as recovery_main


def test_migration_config_is_available_for_source_install():
    path = find_alembic_config()
    assert path.is_file()
    assert (path.parent / "migrations" / "versions" / "0004_monitoring.py").is_file()


def test_recovery_cli_lists_and_recovers_only_with_confirmation(tmp_path: Path, capsys):
    services = create_desktop_services(tmp_path)
    project = services.projects.create_project("Recovery test")
    query = services.projects.create_query(
        project.id, "kidney disease", enabled_providers=("pubmed",),
    )
    store = SqlAlchemyScheduleStore(services.session_factory)
    now = datetime.now(timezone.utc)
    store.configure(str(query.id), interval_minutes=60, enabled=True, next_due_at=now)
    claim = store.claim_due(now)
    with services.session_factory.begin() as session:
        run = m.SearchRun(
            query_id=str(query.id), scheduled_execution_id=claim.execution_id,
            provider="pubmed", state="running", started_at=now,
        )
        session.add(run)
        session.flush()
        run_id = run.id
    services.close()

    assert recovery_main(["--list-running", "--data-dir", str(tmp_path)]) == 0
    assert claim.execution_id in capsys.readouterr().out
    with pytest.raises(SystemExit) as error:
        recovery_main(["--recover", claim.execution_id, "--data-dir", str(tmp_path)])
    assert error.value.code == 2
    assert recovery_main([
        "--recover", claim.execution_id, "--confirm-worker-stopped",
        "--data-dir", str(tmp_path),
    ]) == 0
    assert "Recovered" in capsys.readouterr().out

    reopened = create_desktop_services(tmp_path)
    try:
        assert reopened.monitoring.list_running() == ()
        with reopened.session_factory() as session:
            assert session.get(m.SearchRun, run_id).state == "failed"
    finally:
        reopened.close()
