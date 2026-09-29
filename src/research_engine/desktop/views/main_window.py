"""Widget layout and rendering; actions are wired by the controller."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QPlainTextEdit, QPushButton, QSplitter,
    QProgressBar, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from research_engine.domain.models import ResearchProject, SavedQuery
from research_engine.ports.projects import PaperDetail, ProjectPaperView
from research_engine.application.source_catalog import source_label
from research_engine.application.publication_links import publication_link


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Research Scrapper Engine")
        self.resize(1200, 760)

        self.project_list = QListWidget()
        self.new_project = QPushButton("New")
        self.edit_project = QPushButton("Edit")
        self.delete_project = QPushButton("Delete (archive)")
        self.export_csv = QPushButton("Export CSV")
        self.export_ris = QPushButton("Export RIS")
        self.identity_review = QPushButton("Identity review…")
        self.identity_review.setEnabled(False)
        self.proxy_settings = QPushButton("Settings…")
        self.proxy_status = QLabel("Proxy: off")
        self.query_list = QListWidget()
        self.new_query = QPushButton("New")
        self.edit_query = QPushButton("Edit")
        self.archive_query = QPushButton("Archive query")
        self.query_history = QPushButton("Query history…")
        self.show_archived_queries = QCheckBox("Show archived queries")
        self.run_query = QPushButton("Run search")
        self.provider_combo = QComboBox()
        self.schedule_status = QLabel("Monitoring: off")
        self.recovery = QPushButton("Recovery…")
        self.show_excluded = QCheckBox("Show excluded")
        self.paper_status_filter = QComboBox()
        self.paper_status_filter.addItem("Any review status", None)
        for status in ("unread", "read", "important", "excluded"):
            self.paper_status_filter.addItem(status.title(), status)
        self.paper_read_filter = QComboBox()
        self.paper_read_filter.addItem("Read or unread", None)
        self.paper_read_filter.addItem("Read", True)
        self.paper_read_filter.addItem("Unread", False)
        self.paper_tag_filter = QComboBox()
        self.paper_tag_filter.addItem("Any tag", None)
        self.paper_table = QTableWidget(0, 3)
        self.paper_table.setHorizontalHeaderLabels(("Title", "Review", "Tags"))
        self.paper_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.paper_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.paper_table.horizontalHeader().setStretchLastSection(True)
        self.detail_text = QPlainTextEdit()
        self.detail_text.setReadOnly(True)
        self.open_url = QPushButton("Open publication URL")
        self.mark_read = QPushButton("Mark read")
        self.mark_important = QPushButton("Important")
        self.mark_excluded = QPushButton("Exclude")
        self.mark_unread = QPushButton("Reset to unread")
        self.tags_list = QListWidget()
        self.tag_input = QLineEdit()
        self.tag_input.setPlaceholderText("New project tag")
        self.add_tag = QPushButton("Add tag")
        self.remove_tag = QPushButton("Remove tag from paper")

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("Research projects"))
        left_layout.addWidget(self.project_list)
        left_layout.addLayout(_row(self.new_project, self.edit_project, self.delete_project))
        left_layout.addLayout(_row(self.export_csv, self.export_ris))
        left_layout.addWidget(self.identity_review)
        left_layout.addLayout(_row(self.proxy_settings, self.proxy_status))
        left_layout.addWidget(QLabel("Saved queries"))
        left_layout.addWidget(self.query_list)
        left_layout.addWidget(self.show_archived_queries)
        left_layout.addLayout(_row(self.new_query, self.edit_query, self.archive_query))
        left_layout.addWidget(self.query_history)
        left_layout.addLayout(_row(self.provider_combo, self.run_query))
        left_layout.addWidget(self.schedule_status)
        left_layout.addWidget(self.recovery)

        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.addLayout(_row(QLabel("Discovered papers"), self.show_excluded))
        center_layout.addLayout(_row(
            self.paper_status_filter, self.paper_read_filter, self.paper_tag_filter,
        ))
        center_layout.addWidget(self.paper_table)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(QLabel("Paper details"))
        right_layout.addWidget(self.detail_text)
        right_layout.addWidget(self.open_url)
        right_layout.addLayout(_row(self.mark_read, self.mark_important))
        right_layout.addLayout(_row(self.mark_excluded, self.mark_unread))
        right_layout.addWidget(QLabel("Project tags"))
        right_layout.addWidget(self.tags_list)
        right_layout.addLayout(_row(self.tag_input, self.add_tag))
        right_layout.addWidget(self.remove_tag)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        for widget in (left, center, right):
            splitter.addWidget(widget)
        splitter.setSizes((280, 480, 440))
        self.setCentralWidget(splitter)
        self.search_progress = QProgressBar()
        self.search_progress.setRange(0, 0)
        self.search_progress.setTextVisible(False)
        self.search_progress.setFixedWidth(120)
        self.search_progress.hide()
        self.statusBar().addPermanentWidget(self.search_progress)
        self.statusBar().showMessage("Ready")

    @staticmethod
    def _selected_id(widget: QListWidget) -> str | None:
        item = widget.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def selected_project_id(self) -> str | None:
        return self._selected_id(self.project_list)

    def selected_query_id(self) -> str | None:
        return self._selected_id(self.query_list)

    def selected_article_id(self) -> str | None:
        row = self.paper_table.currentRow()
        item = self.paper_table.item(row, 0) if row >= 0 else None
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def selected_tag(self) -> str | None:
        item = self.tags_list.currentItem()
        return item.text() if item else None

    def display_projects(self, projects: tuple[ResearchProject, ...], selected: str | None = None) -> None:
        self.project_list.clear()
        for project in projects:
            item = QListWidgetItem(project.name)
            item.setData(Qt.ItemDataRole.UserRole, str(project.id))
            self.project_list.addItem(item)
            if str(project.id) == selected:
                self.project_list.setCurrentItem(item)
        if self.project_list.currentItem() is None and self.project_list.count():
            self.project_list.setCurrentRow(0)

    def display_queries(self, queries: tuple[SavedQuery, ...], selected: str | None = None) -> None:
        self.query_list.clear()
        for query in queries:
            suffix = " (archived)" if query.archived_at else (" (paused)" if not query.is_active else "")
            item = QListWidgetItem(query.expression + suffix)
            item.setData(Qt.ItemDataRole.UserRole, str(query.id))
            self.query_list.addItem(item)
            if str(query.id) == selected:
                self.query_list.setCurrentItem(item)
        if self.query_list.currentItem() is None and self.query_list.count():
            self.query_list.setCurrentRow(0)

    def display_providers(self, providers: tuple[str, ...]) -> None:
        self.provider_combo.clear()
        for provider in providers:
            self.provider_combo.addItem(source_label(provider), provider)
        if len(providers) > 1:
            self.provider_combo.addItem("All query sources", "__all__")

    def display_papers(self, papers: tuple[ProjectPaperView, ...], selected: str | None = None) -> None:
        self.paper_table.setRowCount(0)
        for paper in papers:
            row = self.paper_table.rowCount()
            self.paper_table.insertRow(row)
            title = QTableWidgetItem(paper.title)
            title.setData(Qt.ItemDataRole.UserRole, str(paper.article_id))
            self.paper_table.setItem(row, 0, title)
            self.paper_table.setItem(row, 1, QTableWidgetItem(paper.status.value))
            self.paper_table.setItem(row, 2, QTableWidgetItem(", ".join(paper.tags)))
            if str(paper.article_id) == selected:
                self.paper_table.selectRow(row)
        if self.paper_table.currentRow() < 0 and self.paper_table.rowCount():
            self.paper_table.selectRow(0)

    def display_detail(self, detail: PaperDetail | None) -> None:
        self.tags_list.clear()
        if detail is None:
            self.detail_text.clear()
            self.open_url.setEnabled(False)
            self._publication_url = None
            return
        source_metadata = getattr(detail, "source_metadata", ())
        display_url = publication_link(detail)
        date = detail.publication_date
        date_text = "Unknown" if date is None else "-".join(
            str(value).zfill(2) if index else str(value)
            for index, value in enumerate((date.year, date.month, date.day)) if value is not None
        )
        lines = [
            detail.title, "", f"Journal: {detail.journal or 'Unknown'}",
            f"Publication date: {date_text}",
            f"Authors: {', '.join(detail.authors) or 'Unknown'}",
            f"Identifiers: {', '.join(f'{item.scheme}: {item.value}' for item in detail.identifiers) or 'None'}",
            f"Sources: {', '.join(detail.source_providers) or 'Unknown'}",
            f"Review: {detail.project_paper.status.value}",
            f"URL: {display_url or 'Unavailable'}", "", "Abstract", detail.abstract or "No abstract available.",
        ]
        publication_types = getattr(detail, "publication_types", ())
        publication_status = getattr(detail, "publication_status", None)
        citations = getattr(detail, "citations", ())
        if publication_types:
            lines.extend(("", f"Publication types: {', '.join(publication_types)}"))
        if publication_status:
            lines.append(f"Publication status: {publication_status}")
        if citations:
            lines.extend(("", "Citation counts (source-reported):"))
            lines.extend(
                f"{source_label(item.provider)}: {item.count} as of {item.as_of.isoformat()}"
                + (f" (snapshot {item.snapshot_id})" if item.snapshot_id else "")
                for item in citations
            )
        if source_metadata:
            lines.extend(("", "Source records:"))
            for source in source_metadata:
                lines.append(
                    f"{source_label(source.provider)} · source ID {source.source_id} · "
                    f"database {source.source_database or 'Unknown'}"
                )
                lines.append(f"URL: {source.url or 'Unavailable'}")
                lines.append(
                    f"Snapshot: {source.snapshot_id} · retrieved {source.retrieved_at.isoformat()} · "
                    f"parser {source.parser_version}"
                )
                if source.publication_types:
                    lines.append(f"Source publication types: {', '.join(source.publication_types)}")
                if source.publication_status:
                    lines.append(f"Source publication status: {source.publication_status}")
        if detail.canonical_metadata_provenance:
            lines.extend(("", "Canonical field sources:"))
            lines.extend(f"{name}: {source_label(entry['provider'])} (run {entry['run_id']})"
                         for name, entry in sorted(detail.canonical_metadata_provenance.items()))
        self.detail_text.setPlainText("\n".join(lines))
        self.tags_list.addItems(list(detail.project_paper.tags))
        self._publication_url = display_url
        self.open_url.setEnabled(self._publication_url is not None)


def _row(*widgets: QWidget) -> QHBoxLayout:
    layout = QHBoxLayout()
    for widget in widgets:
        layout.addWidget(widget)
    return layout
