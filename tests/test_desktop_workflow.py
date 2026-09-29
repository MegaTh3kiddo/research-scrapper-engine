from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox
from sqlalchemy import select

from research_engine.application.ingestion import IngestionService
from research_engine.application.provider_registry import ProviderRegistry
from research_engine.application.searches import ManualSearchService
from research_engine.desktop.bootstrap import create_desktop_services
from research_engine.desktop.controllers.main import MainController
from research_engine.desktop.views.main_window import MainWindow
from research_engine.desktop.views.dialogs import ProjectDialog, QueryDialog, SettingsDialog
from research_engine.domain.models import ReviewStatus
from research_engine.domain.value_objects import Identifier
from research_engine.infrastructure.persistence import models as m
from research_engine.infrastructure.persistence.ingestion import SqlAlchemyIngestionStore
from research_engine.infrastructure.snapshots import LocalSnapshotStorage
from research_engine.ports.providers import (
    ProviderQueryPlan, ProviderRecord, RawResponse, SearchPage, SearchRequest,
)


class OnePageProvider:
    name = "pubmed"
    parser_version = "desktop-test-parser-v1"

    def plan(self, request: SearchRequest) -> ProviderQueryPlan:
        return ProviderQueryPlan(
            provider=self.name, translated_query=request.expression,
            translation_version="test-v1", parameters={},
        )

    async def search(self, plan: ProviderQueryPlan, cursor: str | None = None) -> SearchPage:
        return SearchPage(
            records=(ProviderRecord(
                source_id="123456", source_database="pubmed",
                title="A desktop workflow paper", abstract="A useful abstract.",
                authors=("A. Researcher",), journal_name="Medical Journal",
                identifiers=(Identifier("pmid", "123456"),),
            ),),
            raw_responses=(RawResponse(
                body=b"desktop-test-response", media_type="text/plain",
                fetched_at=datetime.now(timezone.utc), safe_request_metadata={},
            ),),
        )

    async def aclose(self) -> None:
        pass


class OfflineServices:
    def __init__(self, real_services):
        self.projects = real_services.projects
        self.exports = real_services.exports
        self.monitoring = real_services.monitoring
        self.network_settings = real_services.network_settings
        self.settings = real_services.settings
        self._real = real_services

    def search_service(self):
        provider = OnePageProvider()
        ingestion = IngestionService(
            ProviderRegistry((provider,)),
            SqlAlchemyIngestionStore(self._real.session_factory),
            LocalSnapshotStorage(self._real.snapshot_directory),
        )
        return ManualSearchService(ingestion), (provider,)


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


def test_offscreen_desktop_manual_search_review_and_tags(tmp_path: Path, qt_app, monkeypatch):
    services = create_desktop_services(tmp_path)
    try:
        project = services.projects.create_project("Oncology")
        query = services.projects.create_query(
            project.id, "breast cancer", enabled_providers=("pubmed",),
        )
        window = MainWindow()
        controller = MainController(window, OfflineServices(services))
        assert window.selected_project_id() == str(project.id)
        assert window.selected_query_id() == str(query.id)
        assert window.provider_combo.currentData() == "pubmed"

        window.run_query.click()
        assert controller._worker is not None
        assert controller._worker.wait(5000)
        qt_app.processEvents()
        assert window.paper_table.rowCount() == 1
        article_id = window.selected_article_id()
        detail = services.projects.get_paper_detail(project.id, article_id)
        assert detail.title == "A desktop workflow paper"
        assert detail.abstract == "A useful abstract."
        assert detail.authors == ("A. Researcher",)
        assert detail.journal == "Medical Journal"
        assert detail.url == "https://pubmed.ncbi.nlm.nih.gov/123456/"
        assert detail.source_providers == ("pubmed",)

        window.mark_read.click()
        assert services.projects.get_paper_detail(project.id, article_id).project_paper.status is ReviewStatus.READ
        window.mark_important.click()
        important = services.projects.get_paper_detail(project.id, article_id).project_paper
        assert important.status is ReviewStatus.IMPORTANT
        assert important.is_read
        window.tag_input.setText("screening")
        window.add_tag.click()
        assert services.projects.get_paper_detail(project.id, article_id).project_paper.tags == ("screening",)
        assert window.tags_list.count() == 1
        window.tags_list.setCurrentRow(0)
        window.remove_tag.click()
        assert services.projects.get_paper_detail(project.id, article_id).project_paper.tags == ()
        window.mark_excluded.click()
        assert window.paper_table.rowCount() == 0
        window.show_excluded.setChecked(True)
        assert window.paper_table.rowCount() == 1

        exported_path = tmp_path / "papers.csv"
        monkeypatch.setattr(
            QFileDialog, "getSaveFileName",
            lambda *args, **kwargs: (str(exported_path), "CSV files (*.csv)"),
        )
        window.export_csv.click()
        assert controller._export_worker is not None
        assert controller._export_worker.wait(5000)
        qt_app.processEvents()
        assert "A desktop workflow paper" in exported_path.read_text()
        assert "excluded" in exported_path.read_text()

        with services.session_factory() as session:
            assert session.scalar(select(m.SearchRun.query_revision_id)) is not None
        window.close()
    finally:
        services.close()


def test_offscreen_project_and_query_create_edit_archive(tmp_path: Path, qt_app, monkeypatch):
    services = create_desktop_services(tmp_path)
    try:
        window = MainWindow()
        controller = MainController(window, services)

        def create_project_dialog(dialog):
            dialog.name_edit.setText("Cardiology")
            dialog.description_edit.setPlainText("Initial scope")
            return QDialog.DialogCode.Accepted
        monkeypatch.setattr(ProjectDialog, "exec", create_project_dialog)
        window.new_project.click()
        project_id = window.selected_project_id()
        assert services.projects.get_project(project_id).name == "Cardiology"

        def edit_project_dialog(dialog):
            dialog.name_edit.setText("Cardiology review")
            return QDialog.DialogCode.Accepted
        monkeypatch.setattr(ProjectDialog, "exec", edit_project_dialog)
        window.edit_project.click()
        assert services.projects.get_project(project_id).name == "Cardiology review"

        def create_query_dialog(dialog):
            dialog.expression_edit.setText("heart failure")
            dialog.europe_check.setChecked(False)
            dialog.monitor_check.setChecked(True)
            dialog.interval_hours.setValue(12)
            return QDialog.DialogCode.Accepted
        monkeypatch.setattr(QueryDialog, "exec", create_query_dialog)
        window.new_query.click()
        query_id = window.selected_query_id()
        assert services.projects.get_query(query_id).expression == "heart failure"
        assert window.provider_combo.currentData() == "pubmed"
        assert services.monitoring.get_schedule(query_id).enabled
        assert services.monitoring.get_schedule(query_id).interval_minutes == 720

        def edit_query_dialog(dialog):
            dialog.expression_edit.setText("heart failure outcomes")
            dialog.monitor_check.setChecked(False)
            return QDialog.DialogCode.Accepted
        monkeypatch.setattr(QueryDialog, "exec", edit_query_dialog)
        window.edit_query.click()
        assert services.projects.get_query(query_id).expression == "heart failure outcomes"
        assert len(services.projects.list_query_revisions(query_id)) == 2
        assert not services.monitoring.get_schedule(query_id).enabled

        monkeypatch.setattr(
            QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
        )
        window.delete_project.click()
        assert window.project_list.count() == 0
        assert services.projects.get_project(project_id).archived_at is not None
        assert services.projects.get_query(query_id) is not None
        window.close()
    finally:
        services.close()


def test_offscreen_proxy_setting_can_be_enabled_and_disabled(tmp_path: Path, qt_app, monkeypatch):
    services = create_desktop_services(tmp_path)
    try:
        window = MainWindow()
        controller = MainController(window, services)
        assert window.proxy_status.text() == "Proxy: off"

        def enable(dialog):
            dialog.enabled_check.setChecked(True)
            dialog.url_edit.setText("socks5h://127.0.0.1:10808")
            return QDialog.DialogCode.Accepted

        monkeypatch.setattr(SettingsDialog, "exec", enable)
        window.proxy_settings.click()
        assert window.proxy_status.text() == "Proxy: on"
        assert services.network_settings.get().active_proxy_url == "socks5h://127.0.0.1:10808"

        def disable(dialog):
            dialog.enabled_check.setChecked(False)
            return QDialog.DialogCode.Accepted

        monkeypatch.setattr(SettingsDialog, "exec", disable)
        window.proxy_settings.click()
        assert window.proxy_status.text() == "Proxy: off"
        assert services.network_settings.get().active_proxy_url is None
        window.close()
    finally:
        services.close()
