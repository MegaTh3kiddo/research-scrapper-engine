"""Simple forms; parsing and persistence belong to the controller/services."""

from __future__ import annotations

from PySide6.QtCore import Qt

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLineEdit,
    QPlainTextEdit, QSpinBox, QVBoxLayout, QLabel, QTabWidget, QWidget, QGridLayout,
    QHBoxLayout, QPushButton, QTableWidget, QTableWidgetItem,
)

from research_engine.domain.models import ResearchProject, SavedQuery
from research_engine.ports.scheduling import QuerySchedule
from research_engine.ports.network_settings import NetworkSettings
from research_engine.application.source_catalog import LITERATURE_SOURCES
from research_engine.application.source_catalog import source_label
from research_engine.ports.settings import ApplicationSettings
from research_engine.ports.projects import QueryRunSummary, SavedQueryRevision
from research_engine.ports.recovery import (
    ExecutionAttemptSummary, RunningExecutionAttempt, RunningProviderRun,
)
from research_engine.ports.scheduling import ScheduledExecution
from research_engine.ports.ingestion import CoverageCheckpoint
from research_engine.domain.value_objects import Identifier
from research_engine.ports.identity_review import (
    IdentityCandidate, IdentityConflictView, PossibleDuplicate,
)


class ProjectDialog(QDialog):
    def __init__(self, project: ResearchProject | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit project" if project else "New project")
        self.name_edit = QLineEdit(project.name if project else "")
        self.description_edit = QPlainTextEdit((project.description or "") if project else "")
        form = QFormLayout()
        form.addRow("Name", self.name_edit)
        form.addRow("Description", self.description_edit)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)


class QueryDialog(QDialog):
    def __init__(
        self, query: SavedQuery | None = None, parent=None,
        schedule: QuerySchedule | None = None,
        defaults: ApplicationSettings | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit saved query" if query else "New saved query")
        filters = query.filters if query else None
        self.expression_edit = QLineEdit(query.expression if query else "")
        defaults = defaults or ApplicationSettings()
        providers = query.enabled_providers if query else defaults.discovery_sources
        self.source_checks = {}
        for source in LITERATURE_SOURCES:
            check = QCheckBox(source.label)
            check.setToolTip(source.description)
            check.setChecked(source.name in providers)
            self.source_checks[source.name] = check
        self.pubmed_check = self.source_checks["pubmed"]
        self.europe_check = self.source_checks["europe_pmc"]
        self.from_edit = QLineEdit(filters.published_from.isoformat() if filters and filters.published_from else "")
        self.to_edit = QLineEdit(filters.published_to.isoformat() if filters and filters.published_to else "")
        self.from_edit.setPlaceholderText("YYYY-MM-DD")
        self.to_edit.setPlaceholderText("YYYY-MM-DD")
        self.languages_edit = QLineEdit(", ".join(filters.languages) if filters else "")
        self.types_edit = QLineEdit(", ".join(filters.publication_types) if filters else "")
        self.humans_check = QCheckBox("Humans only")
        self.open_access_check = QCheckBox("Open access only")
        for check, value in ((self.humans_check, filters.humans_only if filters else None),
                             (self.open_access_check, filters.open_access_only if filters else None)):
            check.setTristate(True)
            check.setCheckState(Qt.CheckState.PartiallyChecked if value is None else
                                Qt.CheckState.Checked if value else Qt.CheckState.Unchecked)
            check.setToolTip("Partial: no filter. Checked: yes. Unchecked: no. Unsupported source filters fail explicitly.")
        self.monitor_check = QCheckBox("Monitor automatically while desktop is open")
        self.monitor_check.setChecked(schedule.enabled if schedule else False)
        self.interval_hours = QSpinBox()
        self.interval_hours.setRange(1, 24 * 30)
        self.interval_hours.setValue(max(1, (schedule.interval_minutes + 59) // 60) if schedule else defaults.monitoring_interval_hours)
        self.interval_hours.setSuffix(" hours")
        form = QFormLayout()
        source_hint = QLabel("Manual: choose one or all sources.\nMonitoring: query sources intersect Settings allow-list.")
        source_hint.setWordWrap(True)
        form.addRow("Discovery sources", source_hint)
        for check in self.source_checks.values():
            form.addRow("", check)
        for label, widget in (
            ("Query", self.expression_edit), ("Published from", self.from_edit),
            ("Published to", self.to_edit), ("Languages (comma-separated)", self.languages_edit),
            ("Publication types", self.types_edit), ("", self.humans_check),
            ("", self.open_access_check),
            ("", self.monitor_check), ("Check every", self.interval_hours),
        ):
            form.addRow(label, widget)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)


class SettingsDialog(QDialog):
    """Preference editor only; validation and persistence remain in services."""

    def __init__(self, settings: ApplicationSettings, network: NetworkSettings,
                 key_status: dict[str, str], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings — sources and research workflow")
        self.resize(720, 540)
        tabs = QTabWidget()
        sources = QWidget()
        grid = QGridLayout(sources)
        for column, label in enumerate(("Source", "New-query default", "Allow monitoring")):
            heading = QLabel(label)
            font = heading.font()
            font.setBold(True)
            heading.setFont(font)
            grid.addWidget(heading, 0, column)
        self.discovery_checks = {}
        self.monitoring_checks = {}
        for row, source in enumerate(LITERATURE_SOURCES, 1):
            label = QLabel(source.label)
            label.setToolTip(source.description)
            discovery, monitoring = QCheckBox(), QCheckBox()
            discovery.setAccessibleName(f"{source.label} new-query default")
            monitoring.setAccessibleName(f"{source.label} allow monitoring")
            discovery.setChecked(source.name in settings.discovery_sources)
            monitoring.setChecked(source.name in settings.monitoring_sources)
            self.discovery_checks[source.name] = discovery
            self.monitoring_checks[source.name] = monitoring
            for column, widget in enumerate((label, discovery, monitoring)):
                grid.addWidget(widget, row, column)
        note = QLabel("Defaults apply to new queries only. Edit a saved query to change its sources.\n"
                      "Monitoring uses the intersection of this allow-list and the saved query.\n\n"
                      "Future steps (not active): identifier-based enrichment, citation analysis\n"
                      "with Semantic Scholar, and separate ClinicalTrials.gov trial monitoring.")
        note.setWordWrap(True)
        grid.addWidget(note, len(LITERATURE_SOURCES) + 1, 0, 1, 3)
        grid.setRowStretch(len(LITERATURE_SOURCES) + 2, 1)
        tabs.addTab(sources, "Sources")

        workflow = QWidget()
        form = QFormLayout(workflow)
        self.page_size = QSpinBox()
        self.page_size.setRange(1, 100)
        self.page_size.setValue(settings.page_size)
        self.max_pages = QSpinBox()
        self.max_pages.setRange(1, 100)
        self.max_pages.setValue(settings.max_pages)
        self.interval_hours = QSpinBox()
        self.interval_hours.setRange(1, 720)
        self.interval_hours.setValue(settings.monitoring_interval_hours)
        self.interval_hours.setSuffix(" hours")
        self.export_excluded = QCheckBox("Include excluded papers in exports")
        self.export_excluded.setChecked(settings.export_include_excluded)
        for label, widget in (("Results per page", self.page_size), ("Page budget per source/run", self.max_pages),
                              ("New-query monitoring interval", self.interval_hours), ("", self.export_excluded)):
            form.addRow(label, widget)
        explanation = QLabel("Limits apply to newly started manual and scheduled searches.\n"
                             "A page-capped run is partial, not complete. Monitoring runs only\n"
                             "while the app is open and must also be enabled on each query.")
        explanation.setWordWrap(True)
        form.addRow(explanation)
        tabs.addTab(workflow, "Workflow")

        access = QWidget()
        form = QFormLayout(access)
        self.email_edit = QLineEdit(settings.contact_email)
        self.email_edit.setPlaceholderText("Optional contact email for NCBI / Crossref")
        form.addRow("Contact email", self.email_edit)
        self.key_edits = {}
        self.clear_keys = {}
        for source in LITERATURE_SOURCES:
            if source.api_key_env:
                edit = QLineEdit()
                edit.setEchoMode(QLineEdit.EchoMode.Password)
                edit.setPlaceholderText("Leave blank to keep current key")
                clear = QCheckBox("Clear / do not use environment key this session")
                self.key_edits[source.name] = edit
                self.clear_keys[source.name] = clear
                form.addRow(source.label, QLabel(key_status[source.name]))
                form.addRow("Session API key", edit)
                form.addRow("", clear)
                form.addRow("Environment variable", QLabel(source.api_key_env))
        explanation = QLabel("Keys are never saved to disk. Use environment variables for restart persistence.\n"
                             "PubMed and OpenAlex support basic keyless access; keys increase access limits.\n"
                             "Europe PMC and public Crossref do not require a key.")
        explanation.setWordWrap(True)
        form.addRow(explanation)
        tabs.addTab(access, "API access")

        net = QWidget()
        form = QFormLayout(net)
        self.enabled_check = QCheckBox("Use proxy for all literature API requests")
        self.enabled_check.setChecked(network.proxy_enabled)
        self.url_edit = QLineEdit(network.proxy_url)
        self.url_edit.setPlaceholderText("socks5h://host:port")
        self.url_edit.setEnabled(network.proxy_enabled)
        self.enabled_check.toggled.connect(self.url_edit.setEnabled)
        form.addRow(self.enabled_check)
        form.addRow("Proxy URL", self.url_edit)
        form.addRow(QLabel("Direct mode ignores environment proxies. Proxy failure does not fall back\n"
                           "to direct access. Credentials in proxy URLs are not supported."))
        tabs.addTab(net, "Network")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(buttons)


class QueryHistoryDialog(QDialog):
    """Read-only saved-query revisions and execution history."""

    def __init__(self, revisions: tuple[SavedQueryRevision, ...],
                 runs: tuple[QueryRunSummary, ...],
                 attempts: tuple[ExecutionAttemptSummary, ...] = (), parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Saved query history")
        self.resize(980, 780)
        layout = QVBoxLayout(self)
        history_tabs = QTabWidget(self)
        self.revision_table = QTableWidget(0, 5)
        self.revision_table.setHorizontalHeaderLabels(
            ("Revision", "Created", "State", "Sources", "Expression")
        )
        self.revision_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.revision_table.horizontalHeader().setStretchLastSection(True)
        for revision in revisions:
            row = self.revision_table.rowCount()
            self.revision_table.insertRow(row)
            values = (
                str(revision.revision_number), _display_time(revision.created_at),
                "Archived" if revision.archived_at else ("Active" if revision.is_active else "Paused"),
                ", ".join(source_label(name) for name in revision.enabled_providers),
                revision.expression,
            )
            for column, value in enumerate(values):
                self.revision_table.setItem(row, column, QTableWidgetItem(value))
        history_tabs.addTab(self.revision_table, "Revisions")

        self.run_table = QTableWidget(0, 9)
        self.run_table.setHorizontalHeaderLabels((
            "Source", "State", "Started", "Revision", "Results", "New",
            "Updated", "Cursor", "Error",
        ))
        self.run_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.run_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.run_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.run_table.horizontalHeader().setStretchLastSection(True)
        self._runs: list[QueryRunSummary] = []
        for run in reversed(runs):
            row = self.run_table.rowCount()
            self.run_table.insertRow(row)
            self._runs.append(run)
            scheduled = getattr(run, "scheduled_execution_id", None)
            source = source_label(run.provider) + (" · scheduled" if scheduled else " · manual")
            values = (
                source, run.state.title(), _display_time(run.started_at),
                str(run.query_revision_number or "—"), str(run.result_count),
                str(run.new_count), str(run.updated_count), run.cursor or "—",
                getattr(run, "error_code", None) or "—",
            )
            for column, value in enumerate(values):
                self.run_table.setItem(row, column, QTableWidgetItem(value))
        self.run_table.itemSelectionChanged.connect(self._update_retry_enabled)
        history_tabs.addTab(self.run_table, "Search runs")

        self.attempt_table = QTableWidget(0, 7)
        self.attempt_table.setHorizontalHeaderLabels((
            "Source", "State", "Started", "Ended", "Request", "Error", "Search run",
        ))
        self.attempt_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.attempt_table.horizontalHeader().setStretchLastSection(True)
        for attempt in attempts:
            row = self.attempt_table.rowCount()
            self.attempt_table.insertRow(row)
            values = (
                source_label(attempt.requested_provider), attempt.state.title(),
                _display_time(attempt.started_at), _display_time(attempt.ended_at),
                attempt.request_reference or "—", attempt.error_code or "—",
                attempt.run_id or "Not created",
            )
            for column, value in enumerate(values):
                self.attempt_table.setItem(row, column, QTableWidgetItem(value))
        history_tabs.addTab(
            self.attempt_table, "Execution attempts (including pre-run failures)",
        )
        layout.addWidget(history_tabs)

        buttons = QHBoxLayout()
        self.retry_button = QPushButton("Retry selected run…")
        self.retry_button.setEnabled(False)
        self.close_button = QPushButton("Close")
        self.retry_button.clicked.connect(self.accept)
        self.close_button.clicked.connect(self.reject)
        buttons.addWidget(self.retry_button)
        buttons.addStretch(1)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

    def selected_run(self) -> QueryRunSummary | None:
        row = self.run_table.currentRow()
        return self._runs[row] if 0 <= row < len(self._runs) else None

    def _update_retry_enabled(self) -> None:
        run = self.selected_run()
        self.retry_button.setEnabled(bool(
            run and run.state in {"failed", "partial"}
            and getattr(run, "scheduled_execution_id", None) is None
        ))


class RecoveryDialog(QDialog):
    """Display the recoverable state; controller owns confirmations/actions."""

    def __init__(
        self,
        provider_runs: tuple[RunningProviderRun, ...],
        scheduled_attempts: tuple[ScheduledExecution, ...],
        coverage: tuple[CoverageCheckpoint, ...],
        *,
        running_attempts: tuple[RunningExecutionAttempt, ...] = (),
        can_queue_retry: bool,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Search recovery")
        self.resize(900, 620)
        layout = QVBoxLayout(self)
        recovery_tabs = QTabWidget(self)

        self.provider_runs_table = _recovery_table(
            ("Run", "Query", "Source", "Execution", "Cursor", "Results")
        )
        for run in provider_runs:
            row = self.provider_runs_table.rowCount()
            self.provider_runs_table.insertRow(row)
            scheduled = run.scheduled_execution_id
            values = (
                run.id, run.query_id, source_label(run.provider), scheduled or "Manual",
                run.cursor or "First page", str(run.result_count),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, run)
                self.provider_runs_table.setItem(row, column, item)
        recovery_tabs.addTab(self.provider_runs_table, "Provider runs")

        self.scheduled_table = _recovery_table(("Execution", "Query", "Started", "Runs"))
        for attempt in scheduled_attempts:
            row = self.scheduled_table.rowCount()
            self.scheduled_table.insertRow(row)
            values = (
                attempt.id, attempt.query_id, _display_time(attempt.started_at),
                ", ".join(attempt.search_run_ids) or "No provider run yet",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, attempt)
                self.scheduled_table.setItem(row, column, item)
        recovery_tabs.addTab(self.scheduled_table, "Scheduled attempts")

        self.running_attempts_table = _recovery_table(
            ("Attempt", "Query", "Source", "Request", "Search run")
        )
        for attempt in running_attempts:
            row = self.running_attempts_table.rowCount()
            self.running_attempts_table.insertRow(row)
            values = (
                attempt.id, attempt.requested_query_id,
                source_label(attempt.requested_provider), attempt.request_reference or "—",
                attempt.run_id or "Not created",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, attempt)
                self.running_attempts_table.setItem(row, column, item)
        recovery_tabs.addTab(self.running_attempts_table, "Pre-run requests")

        self.coverage_table = _recovery_table(("Provider", "Lane", "Cursor", "State", "Published window"))
        for checkpoint in coverage:
            row = self.coverage_table.rowCount()
            self.coverage_table.insertRow(row)
            bounds = "–".join(
                value.isoformat() if value else "open"
                for value in (checkpoint.published_from, checkpoint.published_to)
            ) if checkpoint.lane == "recent" else "All dates"
            values = (
                source_label(checkpoint.provider), checkpoint.lane.title(),
                checkpoint.cursor or "First page", "Complete" if checkpoint.complete else "In progress",
                bounds,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, checkpoint)
                self.coverage_table.setItem(row, column, item)
        recovery_tabs.addTab(self.coverage_table, "Monitoring coverage")
        layout.addWidget(recovery_tabs)

        self.recover_provider_button = QPushButton("Recover selected provider run…")
        self.recover_schedule_button = QPushButton("Recover selected scheduled attempt…")
        self.recover_execution_attempt_button = QPushButton("Recover selected pre-run request…")
        self.recover_execution_attempt_button.setToolTip(
            "Only a request with no SearchRun can be recovered here."
        )
        self.restart_coverage_button = QPushButton("Restart selected coverage…")
        self.queue_retry_button = QPushButton("Queue this query now…")
        self.queue_retry_button.setEnabled(can_queue_retry)
        self.refresh_button = QPushButton("Refresh")
        self.close_button = QPushButton("Close")
        for table, button in (
            (self.provider_runs_table, self.recover_provider_button),
            (self.scheduled_table, self.recover_schedule_button),
            (self.running_attempts_table, self.recover_execution_attempt_button),
            (self.coverage_table, self.restart_coverage_button),
        ):
            button.setEnabled(False)
            table.itemSelectionChanged.connect(
                lambda table=table, button=button: button.setEnabled(table.currentRow() >= 0)
            )
        self.provider_runs_table.itemSelectionChanged.connect(self._update_provider_action)
        self.running_attempts_table.itemSelectionChanged.connect(self._update_execution_attempt_action)

        action_row = QHBoxLayout()
        for button in (self.recover_provider_button, self.recover_schedule_button,
                       self.recover_execution_attempt_button,
                       self.restart_coverage_button, self.queue_retry_button):
            action_row.addWidget(button)
        layout.addLayout(action_row)
        bottom_row = QHBoxLayout()
        bottom_row.addStretch(1)
        bottom_row.addWidget(self.refresh_button)
        bottom_row.addWidget(self.close_button)
        self.refresh_button.clicked.connect(self.accept)
        self.close_button.clicked.connect(self.reject)
        layout.addLayout(bottom_row)

    def selected_provider_run(self) -> RunningProviderRun | None:
        return _selected_object(self.provider_runs_table)

    def selected_scheduled_attempt(self) -> ScheduledExecution | None:
        return _selected_object(self.scheduled_table)

    def selected_running_attempt(self) -> RunningExecutionAttempt | None:
        return _selected_object(self.running_attempts_table)

    def selected_coverage(self) -> CoverageCheckpoint | None:
        return _selected_object(self.coverage_table)

    def _update_provider_action(self) -> None:
        run = self.selected_provider_run()
        self.recover_provider_button.setEnabled(bool(run and not run.scheduled_execution_id))
        if run and run.scheduled_execution_id:
            self.recover_provider_button.setToolTip(
                "Recover the parent scheduled attempt instead."
            )
        else:
            self.recover_provider_button.setToolTip("")

    def _update_execution_attempt_action(self) -> None:
        attempt = self.selected_running_attempt()
        recoverable = bool(attempt and attempt.run_id is None)
        self.recover_execution_attempt_button.setEnabled(recoverable)
        if attempt and attempt.run_id:
            self.recover_execution_attempt_button.setToolTip(
                "This request already has a provider run; recover the run or scheduled execution instead."
            )
        else:
            self.recover_execution_attempt_button.setToolTip("")


class IdentityReviewDialog(QDialog):
    """Gather explicit, reasoned identity decisions without merging records."""

    def __init__(
        self,
        conflicts: tuple[IdentityConflictView, ...],
        candidates: tuple[PossibleDuplicate, ...],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Identity review")
        self.resize(1050, 740)
        self._conflicts = conflicts
        self._possible_duplicates = candidates
        self._decision: tuple | None = None
        layout = QVBoxLayout(self)
        tabs = QTabWidget(self)

        conflict_page = QWidget()
        conflict_layout = QVBoxLayout(conflict_page)
        self.conflict_table = _recovery_table(("Source", "Source record", "Status", "Candidates"))
        for conflict in conflicts:
            row = self.conflict_table.rowCount()
            self.conflict_table.insertRow(row)
            values = (
                source_label(conflict.provider), conflict.source_id,
                conflict.status.replace("_", " ").title(), str(len(conflict.candidates)),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, conflict)
                self.conflict_table.setItem(row, column, item)
        conflict_layout.addWidget(self.conflict_table)

        self.conflict_provenance = QLabel("Select a source conflict to review its provenance.")
        self.conflict_provenance.setWordWrap(True)
        conflict_layout.addWidget(self.conflict_provenance)
        self.candidate_combo = QComboBox()
        self.candidate_combo.setAccessibleName("Canonical paper candidate")
        conflict_layout.addWidget(self.candidate_combo)
        self.candidate_summary = QLabel("Select a candidate to inspect its canonical identifiers.")
        self.candidate_summary.setWordWrap(True)
        conflict_layout.addWidget(self.candidate_summary)
        conflict_layout.addWidget(QLabel(
            "Confirm every identifier that should be rejected for this one source assertion. "
            "No conflicting identifiers are rejected unless explicitly checked."
        ))
        self.rejected_ids_widget = QWidget()
        self.rejected_ids_layout = QVBoxLayout(self.rejected_ids_widget)
        self.rejected_ids_layout.setContentsMargins(0, 0, 0, 0)
        conflict_layout.addWidget(self.rejected_ids_widget)
        conflict_layout.addWidget(QLabel("Reason for this decision"))
        self.conflict_reason_edit = QLineEdit()
        self.conflict_reason_edit.setMaxLength(2000)
        self.conflict_reason_edit.setPlaceholderText("Required; recorded in the identity audit")
        conflict_layout.addWidget(self.conflict_reason_edit)
        conflict_actions = QHBoxLayout()
        self.keep_separate_button = QPushButton("Keep records separate")
        self.reject_source_button = QPushButton("Reject this source assertion")
        self.select_candidate_button = QPushButton("Use candidate for future matching…")
        for button in (
            self.keep_separate_button, self.reject_source_button, self.select_candidate_button,
        ):
            conflict_actions.addWidget(button)
        conflict_layout.addLayout(conflict_actions)
        self.conflict_table.itemSelectionChanged.connect(self._load_conflict)
        self.candidate_combo.currentIndexChanged.connect(self._load_candidate)
        self.conflict_reason_edit.textChanged.connect(self._update_conflict_actions)
        self.keep_separate_button.clicked.connect(lambda: self._submit_conflict("keep_separate"))
        self.reject_source_button.clicked.connect(lambda: self._submit_conflict("reject_source"))
        self.select_candidate_button.clicked.connect(lambda: self._submit_conflict("select_article"))
        tabs.addTab(conflict_page, "Source identifier conflicts")

        duplicate_page = QWidget()
        duplicate_layout = QVBoxLayout(duplicate_page)
        self.duplicate_table = _recovery_table(("First paper", "Second paper", "Reason", "Status"))
        for duplicate in candidates:
            row = self.duplicate_table.rowCount()
            self.duplicate_table.insertRow(row)
            values = (
                duplicate.left.title, duplicate.right.title, duplicate.reason,
                duplicate.status.replace("_", " ").title(),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, duplicate)
                self.duplicate_table.setItem(row, column, item)
        duplicate_layout.addWidget(self.duplicate_table)
        self.duplicate_detail = QLabel("Select a possible duplicate to compare canonical identifiers.")
        self.duplicate_detail.setWordWrap(True)
        duplicate_layout.addWidget(self.duplicate_detail)
        duplicate_layout.addWidget(QLabel("Reason for keeping the records separate"))
        self.duplicate_reason_edit = QLineEdit()
        self.duplicate_reason_edit.setMaxLength(2000)
        self.duplicate_reason_edit.setPlaceholderText("Required; recorded in the identity audit")
        duplicate_layout.addWidget(self.duplicate_reason_edit)
        self.keep_candidate_separate_button = QPushButton("Keep these records separate")
        duplicate_layout.addWidget(self.keep_candidate_separate_button)
        self.duplicate_table.itemSelectionChanged.connect(self._load_duplicate)
        self.duplicate_reason_edit.textChanged.connect(self._update_duplicate_action)
        self.keep_candidate_separate_button.clicked.connect(self._submit_duplicate)
        tabs.addTab(duplicate_page, "Possible duplicates")
        layout.addWidget(tabs)

        close_button = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_button.rejected.connect(self.reject)
        layout.addWidget(close_button)
        if self.conflict_table.rowCount():
            self.conflict_table.selectRow(0)
        if self.duplicate_table.rowCount():
            self.duplicate_table.selectRow(0)

    def selected_decision(self) -> tuple | None:
        return self._decision

    def _selected_conflict(self) -> IdentityConflictView | None:
        return _selected_object(self.conflict_table)

    def _selected_candidate(self) -> IdentityCandidate | None:
        conflict = self._selected_conflict()
        article_id = self.candidate_combo.currentData()
        if conflict is None or article_id is None:
            return None
        return next((item for item in conflict.candidates if item.article_id == article_id), None)

    def _load_conflict(self) -> None:
        conflict = self._selected_conflict()
        self.candidate_combo.blockSignals(True)
        self.candidate_combo.clear()
        if conflict is None:
            self.conflict_provenance.setText("Select a source conflict to review its provenance.")
        else:
            assertions = ", ".join(_identifier_text(item) for item in conflict.asserted_identifiers)
            self.conflict_provenance.setText(
                f"Provider: {source_label(conflict.provider)} · Source record: {conflict.source_id}\n"
                f"Search run: {conflict.run_id} · Response snapshot: {conflict.snapshot_id}\n"
                f"Identifiers asserted in the captured response: {assertions or 'None'}\n"
                f"Recorded: {_display_time(conflict.created_at)} · Review state: {conflict.status}"
            )
            for candidate in conflict.candidates:
                self.candidate_combo.addItem(
                    f"{candidate.title}  [{candidate.article_id}]", candidate.article_id,
                )
        self.candidate_combo.blockSignals(False)
        self._load_candidate()

    def _load_candidate(self) -> None:
        candidate = self._selected_candidate()
        while self.rejected_ids_layout.count():
            item = self.rejected_ids_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._rejected_checks: list[tuple[Identifier, QCheckBox]] = []
        if candidate is None:
            self.candidate_summary.setText("Select a candidate to inspect its canonical identifiers.")
        else:
            canonical = ", ".join(_identifier_text(item) for item in candidate.identifiers)
            rejected = ", ".join(_identifier_text(item) for item in candidate.rejected_identifiers)
            self.candidate_summary.setText(
                f"Candidate: {candidate.title}\nCanonical identifiers: {canonical or 'None'}\n"
                f"Assertions owned by another paper and requiring explicit rejection: {rejected or 'None'}\n\n"
                "Selecting a candidate only affects future matching of the same source assertion. "
                "It does not merge canonical papers or rewrite historical runs."
            )
            for identifier in candidate.rejected_identifiers:
                check = QCheckBox(f"Reject this conflicting assertion: {_identifier_text(identifier)}")
                check.setAccessibleName(f"Confirm rejection of {_identifier_text(identifier)}")
                check.setProperty("identity_identifier", identifier)
                check.stateChanged.connect(self._update_conflict_actions)
                self.rejected_ids_layout.addWidget(check)
                self._rejected_checks.append((identifier, check))
        self._update_conflict_actions()

    def _update_conflict_actions(self) -> None:
        conflict = self._selected_conflict()
        candidate = self._selected_candidate()
        active = bool(conflict and conflict.status == "unresolved")
        has_reason = bool(self.conflict_reason_edit.text().strip())
        self.keep_separate_button.setEnabled(active and has_reason)
        self.reject_source_button.setEnabled(active and has_reason)
        all_confirmed = all(check.isChecked() for _, check in getattr(self, "_rejected_checks", ()))
        self.select_candidate_button.setEnabled(
            active and candidate is not None and has_reason and all_confirmed
        )

    def _submit_conflict(self, action: str) -> None:
        conflict = self._selected_conflict()
        candidate = self._selected_candidate()
        if conflict is None or conflict.status != "unresolved":
            return
        article_id = candidate.article_id if action == "select_article" and candidate else None
        if action == "select_article" and (candidate is None or any(
            not check.isChecked() for _, check in self._rejected_checks
        )):
            return
        rejected = tuple(
            identifier for identifier, check in self._rejected_checks if check.isChecked()
        ) if action == "select_article" else ()
        self._decision = (
            "conflict", conflict.id, action, self.conflict_reason_edit.text().strip(),
            article_id, rejected,
        )
        self.accept()

    def _selected_duplicate(self) -> PossibleDuplicate | None:
        return _selected_object(self.duplicate_table)

    def _load_duplicate(self) -> None:
        duplicate = self._selected_duplicate()
        if duplicate is None:
            self.duplicate_detail.setText("Select a possible duplicate to compare canonical identifiers.")
        else:
            self.duplicate_detail.setText(
                f"First: {duplicate.left.title} [{duplicate.left.article_id}]\n"
                f"Canonical identifiers: {_identifiers_text(duplicate.left.identifiers)}\n\n"
                f"Second: {duplicate.right.title} [{duplicate.right.article_id}]\n"
                f"Canonical identifiers: {_identifiers_text(duplicate.right.identifiers)}\n\n"
                f"Reason raised: {duplicate.reason}\nReview state: {duplicate.status}\n\n"
                "This workflow does not merge records. Keeping them separate only records your review decision."
            )
        self._update_duplicate_action()

    def _update_duplicate_action(self) -> None:
        duplicate = self._selected_duplicate()
        self.keep_candidate_separate_button.setEnabled(bool(
            duplicate and duplicate.status == "unresolved"
            and self.duplicate_reason_edit.text().strip()
        ))

    def _submit_duplicate(self) -> None:
        duplicate = self._selected_duplicate()
        if duplicate is None or duplicate.status != "unresolved":
            return
        reason = self.duplicate_reason_edit.text().strip()
        if not reason:
            return
        self._decision = ("candidate", duplicate.id, "keep_separate", reason)
        self.accept()


def _identifier_text(identifier) -> str:
    return f"{identifier.scheme}: {identifier.value}"


def _identifiers_text(identifiers) -> str:
    return ", ".join(_identifier_text(item) for item in identifiers) or "None"


def _recovery_table(headers: tuple[str, ...]) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
    table.horizontalHeader().setStretchLastSection(True)
    return table


def _selected_object(table: QTableWidget):
    row = table.currentRow()
    item = table.item(row, 0) if row >= 0 else None
    return item.data(Qt.ItemDataRole.UserRole) if item else None


def _display_time(value) -> str:
    return value.isoformat(timespec="seconds") if value else "—"
