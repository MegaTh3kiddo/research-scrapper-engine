import os
from uuid import uuid4

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QDialog

from research_engine.desktop.bootstrap import create_desktop_services
from research_engine.desktop.controllers.main import MainController
from research_engine.desktop.views.dialogs import QueryDialog, SettingsDialog
from research_engine.desktop.views.main_window import MainWindow
from research_engine.domain.models import SavedQuery
from research_engine.domain.value_objects import SearchFilters
from research_engine.ports.settings import ApplicationSettings
from research_engine.ports.network_settings import NetworkSettings


def test_settings_and_new_query_present_all_sources_and_apply_defaults():
    app = QApplication.instance() or QApplication([])
    settings = ApplicationSettings(discovery_sources=("openalex", "crossref"),
                                   monitoring_sources=("europe_pmc", "crossref"))
    dialog = SettingsDialog(settings, NetworkSettings(), {
        "pubmed": "No key configured", "openalex": "No key configured",
    })
    query = QueryDialog(defaults=settings)
    try:
        assert set(dialog.discovery_checks) == {"pubmed", "europe_pmc", "openalex", "crossref"}
        assert set(dialog.monitoring_checks) == set(dialog.discovery_checks)
        assert set(query.source_checks) == set(dialog.discovery_checks)
        assert {name for name, check in dialog.discovery_checks.items() if check.isChecked()} == {"openalex", "crossref"}
        assert {name for name, check in dialog.monitoring_checks.items() if check.isChecked()} == {"europe_pmc", "crossref"}
        assert {name for name, check in query.source_checks.items() if check.isChecked()} == {"openalex", "crossref"}
        for check in query.source_checks.values():
            check.setChecked(True)
        assert set(MainController._query_form(query)[2]) == set(dialog.discovery_checks)
    finally:
        dialog.close()
        query.close()


def test_settings_controller_persists_choices_and_keeps_key_off_disk(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    services = create_desktop_services(tmp_path)
    window = MainWindow()
    controller = MainController(window, services)
    try:
        def choose(dialog):
            for name, check in dialog.discovery_checks.items():
                check.setChecked(name in ("openalex", "crossref"))
            for name, check in dialog.monitoring_checks.items():
                check.setChecked(name == "crossref")
            dialog.page_size.setValue(20)
            dialog.max_pages.setValue(3)
            dialog.interval_hours.setValue(6)
            dialog.export_excluded.setChecked(False)
            dialog.key_edits["openalex"].setText("session-secret")
            return QDialog.DialogCode.Accepted
        monkeypatch.setattr(SettingsDialog, "exec", choose)
        controller.configure_proxy()
        preferences = services.settings.get()
        assert preferences.discovery_sources == ("openalex", "crossref")
        assert preferences.monitoring_sources == ("crossref",)
        assert (preferences.page_size, preferences.max_pages) == (20, 3)
        assert not preferences.export_include_excluded
        assert services.settings.api_key("openalex") == "session-secret"
        assert "session-secret" not in (tmp_path / "application-settings.json").read_text()
    finally:
        window.close()
        services.close()
    reopened = create_desktop_services(tmp_path)
    try:
        assert reopened.settings.get() == preferences
        assert reopened.settings.api_key("openalex") != "session-secret"
    finally:
        reopened.close()


def test_edit_query_preserves_explicit_false_filters():
    app = QApplication.instance() or QApplication([])
    query = SavedQuery(uuid4(), "research", filters=SearchFilters(
        humans_only=False, open_access_only=False,
    ), enabled_providers=("openalex",))
    dialog = QueryDialog(query)
    try:
        _, filters, providers = MainController._query_form(dialog)
        assert filters.humans_only is False
        assert filters.open_access_only is False
        assert providers == ("openalex",)
        empty = QueryDialog()
        try:
            assert MainController._query_form(empty)[1] == SearchFilters()
        finally:
            empty.close()
    finally:
        dialog.close()
