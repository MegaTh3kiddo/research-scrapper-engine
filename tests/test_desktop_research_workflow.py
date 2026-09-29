from __future__ import annotations

import asyncio
import os
from datetime import date, datetime, timezone
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from sqlalchemy import select

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from research_engine.application.ingestion import IngestionService
from research_engine.application.provider_registry import ProviderRegistry
from research_engine.application.searches import ManualSearchService
from research_engine.desktop.bootstrap import create_desktop_services
from research_engine.desktop.controllers.main import MainController
from research_engine.desktop.views.dialogs import QueryHistoryDialog, RecoveryDialog
from research_engine.desktop.views.main_window import MainWindow
from research_engine.domain.models import ReviewStatus
from research_engine.domain.value_objects import Identifier
from research_engine.infrastructure.persistence import models as m
from research_engine.infrastructure.persistence.ingestion import SqlAlchemyIngestionStore
from research_engine.infrastructure.persistence.scheduling import SqlAlchemyScheduleStore
from research_engine.infrastructure.snapshots import LocalSnapshotStorage
from research_engine.ports.providers import (
    ProviderQueryPlan,
    ProviderRecord,
    RawResponse,
    SearchPage,
    SearchRequest,
)


class DesktopFixtureProvider:
    name = "pubmed"
    parser_version = "desktop-workflow-v1"

    def __init__(self, cursors: list[str | None], *, paged: bool = False):
        self._cursors = cursors
        self._paged = paged

    def plan(self, request: SearchRequest) -> ProviderQueryPlan:
        return ProviderQueryPlan(
            provider=self.name,
            translated_query=request.expression,
            translation_version="desktop-workflow-query-v1",
            parameters={"page_size": str(request.page_size)},
        )

    async def search(self, plan: ProviderQueryPlan, cursor: str | None = None) -> SearchPage:
        self._cursors.append(cursor)
        if self._paged:
            label = "first" if cursor is None else "second"
            records = (ProviderRecord(
                source_id=f"desktop-{label}", source_database=self.name,
                title=f"Desktop paper {label}",
                identifiers=(Identifier("pmid", "800001" if cursor is None else "800002"),),
            ),)
            next_cursor = "page-2" if cursor is None else None
        else:
            records = tuple(ProviderRecord(
                source_id=f"desktop-{index}", source_database=self.name,
                title=title, identifiers=(Identifier("pmid", f"81000{index}"),),
            ) for index, title in enumerate(("Read paper", "Important paper", "Excluded paper"), 1))
            next_cursor = None
        fetched = datetime.now(timezone.utc)
        return SearchPage(
            records=records,
            raw_responses=(RawResponse(
                body=f"desktop:{plan.translated_query}:{cursor}".encode(),
                media_type="application/json", fetched_at=fetched,
                safe_request_metadata={},
            ),),
            next_cursor=next_cursor,
        )

    async def aclose(self) -> None:
        pass


class DesktopServicesAdapter:
    def __init__(self, real, *, paged: bool = False):
        for name in (
            "projects", "exports", "monitoring", "recovery", "network_settings",
            "settings", "session_factory", "snapshot_directory",
        ):
            setattr(self, name, getattr(real, name))
        self._real = real
        self._paged = paged
        self.cursors: list[str | None] = []

    def search_service(self):
        provider = DesktopFixtureProvider(self.cursors, paged=self._paged)
        return ManualSearchService(IngestionService(
            ProviderRegistry((provider,)),
            SqlAlchemyIngestionStore(self._real.session_factory),
            LocalSnapshotStorage(self._real.snapshot_directory),
        )), (provider,)


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


def _project_query(services, *, expression="desktop search"):
    project = services.projects.create_project("Desktop workflow")
    query = services.projects.create_query(
        project.id, expression, enabled_providers=(DesktopFixtureProvider.name,),
    )
    return project, query


def _ingest(adapter, query_id):
    search, providers = adapter.search_service()
    try:
        return asyncio.run(search.run_query(query_id, DesktopFixtureProvider.name))
    finally:
        for provider in providers:
            asyncio.run(provider.aclose())


def _select_combo(combo, value):
    index = combo.findData(value)
    assert index >= 0
    combo.setCurrentIndex(index)


def test_archived_query_history_and_project_paper_filters(tmp_path, qt_app, monkeypatch):
    services = create_desktop_services(tmp_path)
    try:
        project, query = _project_query(services)
        adapter = DesktopServicesAdapter(services)
        _ingest(adapter, str(query.id))
        papers = services.projects.list_papers(project.id)
        assert len(papers) == 3
        first, second, third = papers
        services.projects.set_review_status(project.id, first.article_id, ReviewStatus.READ)
        services.projects.set_review_status(project.id, second.article_id, ReviewStatus.IMPORTANT)
        services.projects.set_review_status(project.id, third.article_id, ReviewStatus.EXCLUDED)
        services.projects.tag_paper(project.id, first.article_id, "screening")
        services.projects.tag_paper(project.id, second.article_id, "screening")
        services.projects.tag_paper(project.id, third.article_id, "replication")
        services.projects.update_query(str(query.id), expression="revised desktop search")
        with services.session_factory.begin() as session:
            session.add(m.ExecutionAttempt(
                requested_query_id=str(query.id), requested_provider="pubmed",
                request_reference="rejected-plan", state="failed", error_code="InvalidQuery",
                started_at=datetime.now(timezone.utc), ended_at=datetime.now(timezone.utc),
            ))

        window = MainWindow()
        controller = MainController(window, adapter)
        assert window.selected_project_id() == str(project.id)
        assert window.selected_query_id() == str(query.id)
        assert window.paper_table.rowCount() == 2  # excluded remain hidden by default

        _select_combo(window.paper_status_filter, "read")
        assert window.paper_table.rowCount() == 1
        assert window.paper_table.item(0, 0).data(Qt.ItemDataRole.UserRole) == str(first.article_id)
        _select_combo(window.paper_status_filter, None)
        _select_combo(window.paper_read_filter, False)
        window.show_excluded.setChecked(True)
        assert window.paper_table.rowCount() == 2
        assert {window.paper_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
                for row in range(2)} == {
            str(second.article_id), str(third.article_id),
        }
        _select_combo(window.paper_read_filter, None)
        _select_combo(window.paper_tag_filter, "screening")
        assert window.paper_table.rowCount() == 2
        assert {window.paper_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
                for row in range(2)} == {str(first.article_id), str(second.article_id)}

        captured_dialogs = []

        def inspect_history(dialog):
            captured_dialogs.append(dialog)
            return QDialog.DialogCode.Rejected

        monkeypatch.setattr(QueryHistoryDialog, "exec", inspect_history)
        window.query_history.click()
        history = captured_dialogs[-1]
        assert history.revision_table.rowCount() == 2
        assert history.run_table.rowCount() == 1
        assert history.attempt_table.rowCount() == 2
        assert history.attempt_table.item(0, 4).text() == "rejected-plan"
        assert history.attempt_table.item(0, 5).text() == "InvalidQuery"
        assert history.attempt_table.item(0, 6).text() == "Not created"
        assert not history.retry_button.isEnabled()  # the completed run cannot be retried

        monkeypatch.setattr(
            QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
        )
        window.archive_query.click()
        assert window.query_list.count() == 0
        window.show_archived_queries.setChecked(True)
        assert window.query_list.count() == 1
        assert "archived" in window.query_list.item(0).text()
        assert window.archive_query.text() == "Restore query"
        window.archive_query.click()
        assert services.projects.get_query(query.id).archived_at is None
        assert window.query_list.count() == 1
        assert window.archive_query.text() == "Archive query"
        assert services.projects.get_query(query.id) is not None
        window.close()
    finally:
        services.close()


def test_query_history_retry_runs_off_thread_from_last_committed_cursor(
    tmp_path, qt_app, monkeypatch,
):
    services = create_desktop_services(tmp_path)
    try:
        project, query = _project_query(services)
        adapter = DesktopServicesAdapter(services, paged=True)
        first = _ingest(adapter, str(query.id))
        assert first.state == "partial"
        assert adapter.cursors == [None]

        window = MainWindow()
        controller = MainController(window, adapter)

        def start_retry(dialog):
            assert dialog.revision_table.rowCount() == 1
            assert dialog.run_table.rowCount() == 1
            dialog.run_table.selectRow(0)
            assert dialog.retry_button.isEnabled()
            dialog.retry_button.click()
            return dialog.result()

        monkeypatch.setattr(QueryHistoryDialog, "exec", start_retry)
        window.query_history.click()
        worker = controller._retry_worker
        assert worker is not None
        assert worker.wait(5000)
        qt_app.processEvents()

        assert controller._retry_worker is None
        assert adapter.cursors == [None, "page-2"]
        assert window.search_progress.isHidden()
        assert window.search_progress.minimum() == window.search_progress.maximum() == 0
        with services.session_factory() as session:
            rows = session.scalars(select(m.SearchRun).order_by(m.SearchRun.started_at)).all()
            assert [row.state for row in rows] == ["partial", "completed"]
            assert rows[1].start_cursor == "page-2"
        assert len(services.projects.list_papers(project.id)) == 2
        window.close()
    finally:
        services.close()


def test_recovery_dialog_confirms_manual_provider_recovery_and_lists_state(
    tmp_path, qt_app, monkeypatch,
):
    services = create_desktop_services(tmp_path)
    try:
        project, query = _project_query(services)
        now = datetime.now(timezone.utc)
        with services.session_factory.begin() as session:
            running = m.SearchRun(
                query_id=str(query.id), provider=DesktopFixtureProvider.name,
                state="running", started_at=now, cursor="cursor-2", result_count=3,
            )
            session.add(running)
            session.flush()
            run_id = running.id
            session.add(m.ExecutionAttempt(
                requested_query_id=str(query.id), requested_provider="pubmed",
                state="running", started_at=now, run_id=run_id,
            ))
        adapter = DesktopServicesAdapter(services)
        window = MainWindow()
        controller = MainController(window, adapter)
        monkeypatch.setattr(
            QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
        )

        exec_count = [0]

        def drive_dialog(dialog):
            exec_count[0] += 1
            if exec_count[0] == 1:
                assert dialog.provider_runs_table.rowCount() == 1
                assert dialog.provider_runs_table.item(0, 4).text() == "cursor-2"
                dialog.provider_runs_table.selectRow(0)
                assert dialog.running_attempts_table.rowCount() == 1
                dialog.running_attempts_table.selectRow(0)
                assert not dialog.recover_execution_attempt_button.isEnabled()
                dialog.recover_provider_button.click()
                return dialog.result()
            return QDialog.DialogCode.Rejected

        monkeypatch.setattr(RecoveryDialog, "exec", drive_dialog)
        window.recovery.click()

        with services.session_factory() as session:
            assert session.get(m.SearchRun, run_id).state == "failed"
        assert exec_count[0] == 2  # successful action refreshes the dialog state
        window.close()
    finally:
        services.close()


def test_recovery_dialog_marks_orphaned_pre_run_attempt_failed(
    tmp_path, qt_app, monkeypatch,
):
    services = create_desktop_services(tmp_path)
    try:
        _, query = _project_query(services)
        with services.session_factory.begin() as session:
            attempt = m.ExecutionAttempt(
                requested_query_id=str(query.id), requested_provider="pubmed",
                request_reference="pre-run-request", state="running",
                started_at=datetime.now(timezone.utc),
            )
            session.add(attempt)
            session.flush()
            attempt_id = attempt.id
        adapter = DesktopServicesAdapter(services)
        window = MainWindow()
        controller = MainController(window, adapter)
        monkeypatch.setattr(
            QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
        )

        exec_count = [0]

        def drive_dialog(dialog):
            exec_count[0] += 1
            if exec_count[0] == 1:
                assert dialog.running_attempts_table.rowCount() == 1
                assert dialog.running_attempts_table.item(0, 3).text() == "pre-run-request"
                dialog.running_attempts_table.selectRow(0)
                assert dialog.recover_execution_attempt_button.isEnabled()
                dialog.recover_execution_attempt_button.click()
                return dialog.result()
            return QDialog.DialogCode.Rejected

        monkeypatch.setattr(RecoveryDialog, "exec", drive_dialog)
        window.recovery.click()

        with services.session_factory() as session:
            recovered = session.get(m.ExecutionAttempt, attempt_id)
            assert recovered.state == "failed"
            assert recovered.error_code == "InterruptedProcess"
        assert exec_count[0] == 2
        window.close()
    finally:
        services.close()


def test_recovery_dialog_queue_retry_is_confirmed_and_uses_application_service(
    tmp_path, qt_app, monkeypatch,
):
    services = create_desktop_services(tmp_path)
    try:
        _, query = _project_query(services)
        schedules = SqlAlchemyScheduleStore(services.session_factory)
        now = datetime.now(timezone.utc)
        schedules.configure(
            str(query.id), interval_minutes=60, enabled=True,
            next_due_at=now.replace(year=now.year + 1),
        )
        adapter = DesktopServicesAdapter(services)
        window = MainWindow()
        controller = MainController(window, adapter)
        monkeypatch.setattr(
            QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
        )

        exec_count = [0]

        def drive_dialog(dialog):
            exec_count[0] += 1
            if exec_count[0] == 1:
                assert dialog.queue_retry_button.isEnabled()
                dialog.queue_retry_button.click()
                return dialog.result()
            return QDialog.DialogCode.Rejected

        monkeypatch.setattr(RecoveryDialog, "exec", drive_dialog)
        window.recovery.click()
        assert schedules.get_schedule(str(query.id)).next_due_at <= datetime.now(timezone.utc)
        assert exec_count[0] == 2
        window.close()
    finally:
        services.close()


def test_recovery_dialog_recovers_scheduled_execution_through_parent_service(
    tmp_path, qt_app, monkeypatch,
):
    services = create_desktop_services(tmp_path)
    try:
        _, query = _project_query(services)
        schedules = SqlAlchemyScheduleStore(services.session_factory)
        now = datetime.now(timezone.utc)
        schedules.configure(
            str(query.id), interval_minutes=60, enabled=True, next_due_at=now,
        )
        claim = schedules.claim_due(now)
        assert claim is not None
        adapter = DesktopServicesAdapter(services)
        window = MainWindow()
        controller = MainController(window, adapter)
        monkeypatch.setattr(
            QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
        )
        exec_count = [0]

        def drive_dialog(dialog):
            exec_count[0] += 1
            if exec_count[0] == 1:
                assert dialog.scheduled_table.rowCount() == 1
                assert dialog.scheduled_table.item(0, 3).text() == "No provider run yet"
                dialog.scheduled_table.selectRow(0)
                dialog.recover_schedule_button.click()
                return dialog.result()
            return QDialog.DialogCode.Rejected

        monkeypatch.setattr(RecoveryDialog, "exec", drive_dialog)
        window.recovery.click()

        assert schedules.list_running() == ()
        attempts = services.monitoring.list_executions(str(query.id))
        assert attempts[0].id == claim.execution_id
        assert attempts[0].state == "failed"
        assert attempts[0].error_code == "InterruptedProcess"
        assert exec_count[0] == 2
        window.close()
    finally:
        services.close()


def test_recovery_dialog_archives_selected_coverage_after_confirmation(
    tmp_path, qt_app, monkeypatch,
):
    services = create_desktop_services(tmp_path)
    try:
        _, query = _project_query(services)
        with services.session_factory.begin() as session:
            query_row = session.get(m.SavedQuery, str(query.id))
            revision = session.scalar(select(m.SavedQueryRevision).where(
                m.SavedQueryRevision.query_id == str(query.id),
                m.SavedQueryRevision.revision_number == query_row.revision_number,
            ))
            coverage = m.MonitoringCoverage(
                query_id=str(query.id), provider=DesktopFixtureProvider.name,
                lane="recent", revision_id=revision.id, signature="b" * 64,
                cursor="next-page", complete=False, published_from=date(2026, 9, 1),
                published_to=date(2026, 9, 26), pages_completed=2,
                created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
            )
            session.add(coverage)
            session.flush()
            coverage_id = coverage.id
        adapter = DesktopServicesAdapter(services)
        window = MainWindow()
        controller = MainController(window, adapter)
        monkeypatch.setattr(
            QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
        )
        exec_count = [0]

        def drive_dialog(dialog):
            exec_count[0] += 1
            if exec_count[0] == 1:
                assert dialog.coverage_table.rowCount() == 1
                assert dialog.coverage_table.item(0, 2).text() == "next-page"
                assert "2026-09-01" in dialog.coverage_table.item(0, 4).text()
                dialog.coverage_table.selectRow(0)
                dialog.restart_coverage_button.click()
                return dialog.result()
            return QDialog.DialogCode.Rejected

        monkeypatch.setattr(RecoveryDialog, "exec", drive_dialog)
        window.recovery.click()

        assert services.recovery.list_coverage(str(query.id)) == ()
        with services.session_factory() as session:
            archived = session.get(m.MonitoringCoverage, coverage_id)
            assert archived.is_active is False
            assert archived.cursor == "next-page"
            event = session.scalar(select(m.OutboxEvent).where(
                m.OutboxEvent.type == "CoverageRestartRequested"
            ))
            assert event.payload["coverage_id"] == coverage_id
        assert exec_count[0] == 2
        window.close()
    finally:
        services.close()
