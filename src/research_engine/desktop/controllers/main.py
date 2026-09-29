"""Translate user actions into reusable application-service calls."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QEvent, QObject, QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox

from research_engine.desktop.bootstrap import DesktopServices
from research_engine.desktop.views.dialogs import (
    IdentityReviewDialog, ProjectDialog, QueryDialog, QueryHistoryDialog,
    RecoveryDialog, SettingsDialog,
)
from research_engine.application.publication_links import publication_link
from research_engine.desktop.views.main_window import MainWindow
from research_engine.desktop.workers.search import RetryWorker, SearchWorker
from research_engine.desktop.workers.export import ExportWorker
from research_engine.domain.models import ReviewStatus
from research_engine.domain.value_objects import SearchFilters
from research_engine.ports.settings import ApplicationSettings
from research_engine.ports.network_settings import NetworkSettings


class MainController(QObject):
    def __init__(self, window: MainWindow, services: DesktopServices) -> None:
        super().__init__(window)
        self.window = window
        self.services = services
        self._worker: SearchWorker | None = None
        self._retry_worker: RetryWorker | None = None
        self._export_worker: ExportWorker | None = None
        self._refreshing = False
        self._connect()
        self.window.installEventFilter(self)
        self.refresh_proxy_status()
        self.refresh_projects()

    def eventFilter(self, watched, event) -> bool:
        if watched is self.window and event.type() == QEvent.Type.Close and (
            self._worker is not None or self._retry_worker is not None
            or self._export_worker is not None
        ):
            self.window.statusBar().showMessage("Wait for the background task to finish before closing")
            event.ignore()
            return True
        return False

    def _connect(self) -> None:
        w = self.window
        w.new_project.clicked.connect(self.create_project)
        w.edit_project.clicked.connect(self.edit_project)
        w.delete_project.clicked.connect(self.delete_project)
        w.export_csv.clicked.connect(lambda: self.export_project("csv"))
        w.export_ris.clicked.connect(lambda: self.export_project("ris"))
        w.identity_review.clicked.connect(self.show_identity_review)
        w.proxy_settings.clicked.connect(self.configure_proxy)
        w.project_list.currentItemChanged.connect(lambda *_: self.refresh_project_content())
        w.new_query.clicked.connect(self.create_query)
        w.edit_query.clicked.connect(self.edit_query)
        w.archive_query.clicked.connect(self.archive_or_restore_query)
        w.query_history.clicked.connect(self.show_query_history)
        w.show_archived_queries.toggled.connect(lambda *_: self.refresh_project_content())
        w.query_list.currentItemChanged.connect(lambda *_: self.refresh_provider_choices())
        w.run_query.clicked.connect(self.run_query)
        w.show_excluded.toggled.connect(lambda *_: self.refresh_papers())
        w.paper_status_filter.currentIndexChanged.connect(lambda *_: self.refresh_papers())
        w.paper_read_filter.currentIndexChanged.connect(lambda *_: self.refresh_papers())
        w.paper_tag_filter.currentIndexChanged.connect(lambda *_: self.refresh_papers())
        w.recovery.clicked.connect(self.show_recovery)
        w.paper_table.itemSelectionChanged.connect(self.refresh_detail)
        w.mark_read.clicked.connect(lambda: self.set_status(ReviewStatus.READ))
        w.mark_important.clicked.connect(lambda: self.set_status(ReviewStatus.IMPORTANT))
        w.mark_excluded.clicked.connect(lambda: self.set_status(ReviewStatus.EXCLUDED))
        w.mark_unread.clicked.connect(lambda: self.set_status(ReviewStatus.UNREAD))
        w.add_tag.clicked.connect(self.add_tag)
        w.remove_tag.clicked.connect(self.remove_tag)
        w.open_url.clicked.connect(self.open_publication_url)

    def refresh_proxy_status(self) -> None:
        settings = self.services.network_settings.get()
        self.window.proxy_status.setText("Proxy: on" if settings.proxy_enabled else "Proxy: off")

    def configure_proxy(self) -> None:
        dialog = SettingsDialog(
            self.services.settings.get(), self.services.network_settings.get(),
            {source.name: self.services.settings.key_status(source.name)
             for source in self.services.settings.sources() if source.api_key_env}, self.window,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            def action() -> None:
                preferences = ApplicationSettings(
                    discovery_sources=tuple(name for name, check in dialog.discovery_checks.items() if check.isChecked()),
                    monitoring_sources=tuple(name for name, check in dialog.monitoring_checks.items() if check.isChecked()),
                    page_size=dialog.page_size.value(), max_pages=dialog.max_pages.value(),
                    monitoring_interval_hours=dialog.interval_hours.value(),
                    export_include_excluded=dialog.export_excluded.isChecked(),
                    contact_email=dialog.email_edit.text().strip(),
                )
                NetworkSettings(dialog.enabled_check.isChecked(), dialog.url_edit.text().strip())
                keys = {name: "" if dialog.clear_keys[name].isChecked() else edit.text()
                        for name, edit in dialog.key_edits.items()
                        if edit.text() or dialog.clear_keys[name].isChecked()}
                if any(any(c.isspace() for c in key) for key in keys.values()):
                    raise ValueError("API keys cannot contain whitespace")
                self.services.settings.update(preferences)
                self.services.network_settings.update(
                    proxy_enabled=dialog.enabled_check.isChecked(),
                    proxy_url=dialog.url_edit.text(),
                )
                for name, key in keys.items():
                    self.services.settings.set_session_key(name, key)
                self.refresh_proxy_status()
                self.refresh_provider_choices()
                self.window.statusBar().showMessage("Settings saved for new operations; existing queries are unchanged")
            self._attempt(action)

    def _attempt(self, action: Callable[[], None]) -> bool:
        try:
            action()
            return True
        except (ValueError, RuntimeError) as exc:
            QMessageBox.warning(self.window, "Research engine", str(exc))
        except Exception as exc:
            QMessageBox.warning(
                self.window, "Research engine",
                f"Operation failed ({type(exc).__name__}). Refresh to check the current state.",
            )
        return False

    def refresh_projects(self, selected: str | None = None) -> None:
        self._refreshing = True
        try:
            self.window.display_projects(
                self.services.projects.list_projects(),
                selected or self.window.selected_project_id(),
            )
        finally:
            self._refreshing = False
        self.refresh_project_content()

    def refresh_project_content(self) -> None:
        if self._refreshing:
            return
        project_id = self.window.selected_project_id()
        if project_id is None:
            self.window.identity_review.setEnabled(False)
            self.window.display_queries(())
            self._refresh_tag_filter(None)
            self.window.display_papers(())
            self.window.display_detail(None)
            return
        self.window.identity_review.setEnabled(
            getattr(self.services, "identity_review", None) is not None
        )
        self.window.display_queries(
            self.services.projects.list_queries(
                project_id, include_archived=self.window.show_archived_queries.isChecked(),
            ),
            self.window.selected_query_id(),
        )
        self.refresh_provider_choices()
        self.refresh_papers()

    def refresh_provider_choices(self) -> None:
        query_id = self.window.selected_query_id()
        query = self.services.projects.get_query(query_id) if query_id else None
        self.window.display_providers(query.enabled_providers if query else ())
        archived = bool(query and query.archived_at)
        self.window.edit_query.setEnabled(bool(query and not archived))
        self.window.archive_query.setEnabled(query is not None)
        self.window.archive_query.setText("Restore query" if archived else "Archive query")
        self.window.query_history.setEnabled(query is not None)
        self.window.run_query.setEnabled(bool(
            query and query.is_active and not archived
            and self._worker is None and self._retry_worker is None
        ))
        schedule = self.services.monitoring.get_schedule(query_id) if query_id else None
        if schedule and schedule.enabled:
            attempts = self.services.monitoring.list_executions(query_id)
            last = f"; last: {attempts[0].state}" if attempts else ""
            self.window.schedule_status.setText(
                f"Monitoring every {schedule.interval_minutes} min{last}"
            )
        else:
            self.window.schedule_status.setText("Monitoring: off")

    def refresh_papers(self, selected: str | None = None) -> None:
        project_id = self.window.selected_project_id()
        self._refresh_tag_filter(project_id)
        raw_status = self.window.paper_status_filter.currentData()
        status = ReviewStatus(raw_status) if raw_status else None
        read = self.window.paper_read_filter.currentData()
        tag = self.window.paper_tag_filter.currentData()
        papers = self.services.projects.list_papers(
            project_id,
            status=status,
            read=read,
            tags_all=(tag,) if tag else (),
            include_excluded=(
                self.window.show_excluded.isChecked() or status is ReviewStatus.EXCLUDED
            ),
        ) if project_id else ()
        self.window.display_papers(papers, selected or self.window.selected_article_id())
        self.refresh_detail()

    def _refresh_tag_filter(self, project_id: str | None) -> None:
        selected = self.window.paper_tag_filter.currentData()
        tags = self.services.projects.list_tags(project_id) if project_id else ()
        self.window.paper_tag_filter.blockSignals(True)
        try:
            self.window.paper_tag_filter.clear()
            self.window.paper_tag_filter.addItem("Any tag", None)
            for tag in tags:
                self.window.paper_tag_filter.addItem(tag, tag)
            index = self.window.paper_tag_filter.findData(selected)
            self.window.paper_tag_filter.setCurrentIndex(max(index, 0))
        finally:
            self.window.paper_tag_filter.blockSignals(False)

    def refresh_detail(self) -> None:
        project_id = self.window.selected_project_id()
        article_id = self.window.selected_article_id()
        detail = self.services.projects.get_paper_detail(project_id, article_id) if project_id and article_id else None
        self.window.display_detail(detail)

    def create_project(self) -> None:
        dialog = ProjectDialog(parent=self.window)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._attempt(lambda: self.refresh_projects(str(self.services.projects.create_project(
                dialog.name_edit.text(), dialog.description_edit.toPlainText().strip() or None
            ).id)))

    def edit_project(self) -> None:
        project_id = self.window.selected_project_id()
        project = self.services.projects.get_project(project_id) if project_id else None
        if project is None:
            return
        dialog = ProjectDialog(project, self.window)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._attempt(lambda: self.refresh_projects(str(self.services.projects.update_project(
                project_id, name=dialog.name_edit.text(),
                description=dialog.description_edit.toPlainText().strip() or None,
            ).id)))

    def delete_project(self) -> None:
        project_id = self.window.selected_project_id()
        if not project_id:
            return
        response = QMessageBox.question(
            self.window, "Archive project",
            "Remove this project from the active list? Its papers and search history will be preserved.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if response == QMessageBox.StandardButton.Yes:
            self._attempt(lambda: self.refresh_projects_after_archive(project_id))

    def refresh_projects_after_archive(self, project_id: str) -> None:
        self.services.projects.archive_project(project_id)
        self.refresh_projects()

    def archive_or_restore_query(self) -> None:
        query_id = self.window.selected_query_id()
        query = self.services.projects.get_query(query_id) if query_id else None
        if query is None:
            return
        if query.archived_at is None:
            response = QMessageBox.question(
                self.window, "Archive query",
                "Hide this query from the active list? Its revisions, runs, and papers will be preserved.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if response != QMessageBox.StandardButton.Yes:
                return
            action = self.services.projects.archive_query
        else:
            action = self.services.projects.restore_query
        self._attempt(lambda: self._refresh_queries_after_change(action, query_id))

    def _refresh_queries_after_change(self, action, query_id: str) -> None:
        action(query_id)
        project_id = self.window.selected_project_id()
        if project_id:
            self.window.display_queries(
                self.services.projects.list_queries(
                    project_id, include_archived=self.window.show_archived_queries.isChecked(),
                ),
                query_id,
            )
        self.refresh_provider_choices()

    def show_query_history(self) -> None:
        query_id = self.window.selected_query_id()
        if not query_id:
            return
        dialog = QueryHistoryDialog(
            self.services.projects.list_query_revisions(query_id),
            self.services.projects.list_query_runs(query_id),
            self.services.recovery.list_attempts(query_id),
            self.window,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            run = dialog.selected_run()
            if run and run.state in {"failed", "partial"} and not getattr(run, "scheduled_execution_id", None):
                self.retry_run(str(run.id))

    def retry_run(self, run_id: str) -> None:
        if self._retry_worker is not None or self._worker is not None:
            return
        worker = RetryWorker(self.services, run_id)
        self._retry_worker = worker
        self.window.run_query.setEnabled(False)
        self.window.search_progress.show()
        self.window.statusBar().showMessage("Resuming saved search from its last committed page…")
        worker.succeeded.connect(self._retry_succeeded)
        worker.failed.connect(self._retry_failed)
        worker.finished.connect(self._retry_finished)
        worker.start()

    def _retry_succeeded(self, result) -> None:
        self.window.statusBar().showMessage(
            f"Retry {result.state}: {result.outcome.observed} observed, "
            f"{result.outcome.newly_discovered} new"
        )
        self.refresh_papers()

    def _retry_failed(self, message: str) -> None:
        self.window.statusBar().showMessage(message)
        QMessageBox.warning(self.window, "Retry failed", message)

    def _retry_finished(self) -> None:
        self._retry_worker = None
        self.window.search_progress.hide()
        self.refresh_provider_choices()

    def show_recovery(self) -> None:
        query_id = self.window.selected_query_id()
        while True:
            schedule = self.services.monitoring.get_schedule(query_id) if query_id else None
            dialog = RecoveryDialog(
                self.services.recovery.list_running_runs(),
                self.services.monitoring.list_running(),
                self.services.recovery.list_coverage(query_id) if query_id else (),
                running_attempts=self.services.recovery.list_running_attempts(),
                can_queue_retry=bool(schedule and schedule.enabled),
                parent=self.window,
            )
            dialog.recover_provider_button.clicked.connect(
                lambda: self._recover_provider_run(dialog)
            )
            dialog.recover_schedule_button.clicked.connect(
                lambda: self._recover_scheduled_attempt(dialog)
            )
            dialog.recover_execution_attempt_button.clicked.connect(
                lambda: self._recover_execution_attempt(dialog)
            )
            dialog.restart_coverage_button.clicked.connect(
                lambda: self._restart_coverage(dialog)
            )
            dialog.queue_retry_button.clicked.connect(
                lambda: self._queue_query_retry(dialog, query_id)
            )
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return

    def _recover_provider_run(self, dialog: RecoveryDialog) -> None:
        run = dialog.selected_provider_run()
        if run is None or run.scheduled_execution_id:
            return
        if QMessageBox.question(
            self.window, "Recover provider run",
            "Confirm this provider worker has stopped. Its run will be marked failed; committed pages are retained.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        if self._attempt(lambda: self.services.recovery.recover_run(
            run.id, confirm_worker_stopped=True,
        )):
            dialog.accept()

    def _recover_scheduled_attempt(self, dialog: RecoveryDialog) -> None:
        attempt = dialog.selected_scheduled_attempt()
        if attempt is None:
            return
        if QMessageBox.question(
            self.window, "Recover scheduled attempt",
            "Confirm the scheduled worker has stopped. The attempt and any still-running provider runs will be marked failed.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        if self._attempt(lambda: self.services.monitoring.recover_interrupted(
            attempt.id, confirm_worker_stopped=True,
        )):
            dialog.accept()

    def _recover_execution_attempt(self, dialog: RecoveryDialog) -> None:
        attempt = dialog.selected_running_attempt()
        if attempt is None or attempt.run_id is not None:
            return
        if QMessageBox.question(
            self.window, "Recover pre-run request",
            "Confirm that this source request worker has stopped. The request will be marked failed; no provider run was created.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        if self._attempt(lambda: self.services.recovery.recover_attempt(
            attempt.id, confirm_worker_stopped=True,
        )):
            dialog.accept()

    def _restart_coverage(self, dialog: RecoveryDialog) -> None:
        checkpoint = dialog.selected_coverage()
        if checkpoint is None:
            return
        if QMessageBox.question(
            self.window, "Restart monitoring coverage",
            "Archive this cursor and restart the next scheduled search from the first page? Previous pages and history remain stored.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        if self._attempt(lambda: self.services.recovery.restart_coverage(
            checkpoint.query_id, checkpoint.provider, checkpoint.lane,
            confirm_worker_stopped=True,
        )):
            dialog.accept()

    def _queue_query_retry(self, dialog: RecoveryDialog, query_id: str | None) -> None:
        if not query_id:
            return
        if QMessageBox.question(
            self.window, "Queue scheduled retry",
            "Make this saved query due immediately? It will run when monitoring is active and no worker overlaps it.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        if self._attempt(lambda: self.services.recovery.queue_retry(query_id)):
            dialog.accept()

    def show_identity_review(self) -> None:
        project_id = self.window.selected_project_id()
        service = getattr(self.services, "identity_review", None)
        if not project_id or service is None:
            return
        while True:
            dialog = IdentityReviewDialog(
                service.list_conflicts(project_id, include_reviewed=True),
                service.list_candidates(project_id, include_reviewed=True),
                self.window,
            )
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            decision = dialog.selected_decision()
            if decision is None:
                return
            if decision[0] == "conflict":
                _, conflict_id, action, reason, article_id, rejected = decision
                success = self._attempt(lambda: service.review_conflict(
                    conflict_id, action=action, reason=reason, article_id=article_id,
                    rejected_identifiers=rejected,
                ))
            else:
                _, candidate_id, _, reason = decision
                success = self._attempt(lambda: service.keep_candidate_separate(
                    candidate_id, reason=reason,
                ))
            if not success:
                return

    def export_project(self, format: str) -> None:
        project_id = self.window.selected_project_id()
        if not project_id or self._export_worker is not None:
            return
        filename = f"research-project-{project_id}.{format}"
        filter_name = "CSV files (*.csv)" if format == "csv" else "RIS files (*.ris)"
        selected, _ = QFileDialog.getSaveFileName(
            self.window, "Export project papers", filename, filter_name,
        )
        if not selected:
            return
        worker = ExportWorker(self.services.exports, project_id, format, Path(selected),
                              include_excluded=self.services.settings.get().export_include_excluded)
        self._export_worker = worker
        self.window.export_csv.setEnabled(False)
        self.window.export_ris.setEnabled(False)
        self.window.statusBar().showMessage("Exporting project papers…")
        worker.succeeded.connect(lambda count: self.window.statusBar().showMessage(
            f"Exported {count} papers to {selected}"
        ))
        worker.failed.connect(lambda message: QMessageBox.warning(self.window, "Export failed", message))
        worker.finished.connect(self._export_finished)
        worker.start()

    def _export_finished(self) -> None:
        self._export_worker = None
        self.window.export_csv.setEnabled(True)
        self.window.export_ris.setEnabled(True)

    @staticmethod
    def _query_form(dialog: QueryDialog) -> tuple[str, SearchFilters, tuple[str, ...]]:
        providers = tuple(name for name, check in dialog.source_checks.items() if check.isChecked())
        if not providers:
            raise ValueError("Select at least one literature provider")
        def parsed_date(text: str) -> date | None:
            return date.fromisoformat(text.strip()) if text.strip() else None
        def csv(text: str) -> tuple[str, ...]:
            return tuple(part.strip() for part in text.split(",") if part.strip())
        def optional_bool(check) -> bool | None:
            return None if check.checkState() == Qt.CheckState.PartiallyChecked else check.isChecked()
        filters = SearchFilters(
            published_from=parsed_date(dialog.from_edit.text()),
            published_to=parsed_date(dialog.to_edit.text()),
            languages=csv(dialog.languages_edit.text()),
            publication_types=csv(dialog.types_edit.text()),
            humans_only=optional_bool(dialog.humans_check),
            open_access_only=optional_bool(dialog.open_access_check),
        )
        return dialog.expression_edit.text(), filters, providers

    def create_query(self) -> None:
        project_id = self.window.selected_project_id()
        if not project_id:
            return
        dialog = QueryDialog(parent=self.window, defaults=self.services.settings.get())
        if dialog.exec() == QDialog.DialogCode.Accepted:
            def action() -> None:
                expression, filters, providers = self._query_form(dialog)
                query = self.services.projects.create_query(
                    project_id, expression, filters=filters, enabled_providers=providers,
                )
                if dialog.monitor_check.isChecked():
                    self.services.monitoring.configure(
                        str(query.id), interval_minutes=dialog.interval_hours.value() * 60,
                    )
                self.window.display_queries(
                    self.services.projects.list_queries(project_id), str(query.id)
                )
                self.refresh_provider_choices()
            self._attempt(action)

    def edit_query(self) -> None:
        query_id = self.window.selected_query_id()
        query = self.services.projects.get_query(query_id) if query_id else None
        if query is None:
            return
        dialog = QueryDialog(
            query, self.window,
            schedule=self.services.monitoring.get_schedule(query_id),
            defaults=self.services.settings.get(),
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            def action() -> None:
                expression, filters, providers = self._query_form(dialog)
                self.services.projects.update_query(
                    query_id, expression=expression, filters=filters,
                    enabled_providers=providers,
                )
                existing_schedule = self.services.monitoring.get_schedule(query_id)
                if dialog.monitor_check.isChecked() or existing_schedule is not None:
                    self.services.monitoring.configure(
                        query_id,
                        interval_minutes=dialog.interval_hours.value() * 60,
                        enabled=dialog.monitor_check.isChecked(),
                    )
                self.window.display_queries(
                    self.services.projects.list_queries(query.project_id), query_id
                )
                self.refresh_provider_choices()
            self._attempt(action)

    def run_query(self) -> None:
        query_id = self.window.selected_query_id()
        provider = self.window.provider_combo.currentData()
        if not query_id or not provider or self._worker is not None:
            return
        providers = self.services.projects.get_query(query_id).enabled_providers if provider == "__all__" else provider
        worker = SearchWorker(self.services, query_id, providers)
        self._worker = worker
        self.window.run_query.setEnabled(False)
        self.window.search_progress.show()
        self.window.statusBar().showMessage(f"Searching {self.window.provider_combo.currentText()}…")
        worker.source_started.connect(self._source_started)
        worker.source_finished.connect(self._source_finished)
        worker.succeeded.connect(self._search_succeeded)
        worker.failed.connect(self._search_failed)
        worker.finished.connect(self._search_finished)
        worker.start()

    def _search_succeeded(self, result) -> None:
        if isinstance(result, tuple):
            summaries = []
            for provider, outcome, error in result:
                if error:
                    summaries.append(f"{provider}: failed ({error})")
                else:
                    summaries.append(f"{provider}: {outcome.state}, {outcome.outcome.newly_discovered} new")
            self.window.statusBar().showMessage("; ".join(summaries))
            failures = [summary for summary in summaries if "failed (" in summary]
            if failures:
                QMessageBox.warning(self.window, "Some sources failed", "\n".join(failures))
            self.refresh_papers()
            return
        self.window.statusBar().showMessage(
            f"Search {result.state}: {result.outcome.observed} observed, "
            f"{result.outcome.newly_discovered} new, {result.outcome.metadata_updated} updated"
        )
        self.refresh_papers()

    def _search_failed(self, message: str) -> None:
        self.window.statusBar().showMessage(message)
        QMessageBox.warning(self.window, "Search failed", message)

    def _search_finished(self) -> None:
        self._worker = None
        self.window.search_progress.hide()
        self.refresh_provider_choices()

    def _source_started(self, source_names: str) -> None:
        self.window.statusBar().showMessage(f"Searching {source_names}…")

    def _source_finished(self, source_name: str, state: str) -> None:
        self.window.statusBar().showMessage(f"{source_name}: {state}")

    def set_status(self, status: ReviewStatus) -> None:
        project_id, article_id = self.window.selected_project_id(), self.window.selected_article_id()
        if project_id and article_id:
            self._attempt(lambda: self._update_status(project_id, article_id, status))

    def _update_status(self, project_id: str, article_id: str, status: ReviewStatus) -> None:
        self.services.projects.set_review_status(project_id, article_id, status)
        self.refresh_papers(article_id)

    def add_tag(self) -> None:
        project_id, article_id = self.window.selected_project_id(), self.window.selected_article_id()
        name = self.window.tag_input.text().strip()
        if project_id and article_id and name:
            def action() -> None:
                self.services.projects.tag_paper(project_id, article_id, name)
                self.window.tag_input.clear()
                self.refresh_papers(article_id)
            self._attempt(action)

    def remove_tag(self) -> None:
        project_id, article_id = self.window.selected_project_id(), self.window.selected_article_id()
        tag = self.window.selected_tag()
        if project_id and article_id and tag:
            self._attempt(lambda: self._remove_tag(project_id, article_id, tag))

    def _remove_tag(self, project_id: str, article_id: str, tag: str) -> None:
        self.services.projects.untag_paper(project_id, article_id, tag)
        self.refresh_papers(article_id)

    def open_publication_url(self) -> None:
        project_id, article_id = self.window.selected_project_id(), self.window.selected_article_id()
        detail = self.services.projects.get_paper_detail(project_id, article_id) if project_id and article_id else None
        url = publication_link(detail)
        if url:
            QDesktopServices.openUrl(QUrl(url))
