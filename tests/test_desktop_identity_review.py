from __future__ import annotations

import os
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QDialog

from research_engine.desktop.controllers.main import MainController
from research_engine.desktop.views.dialogs import IdentityReviewDialog
from research_engine.desktop.views.main_window import MainWindow
from research_engine.domain.models import ReviewStatus
from research_engine.domain.value_objects import Identifier, PartialDate
from research_engine.infrastructure.bootstrap import create_application_services
from research_engine.ports.identity_review import (
    IdentityCandidate, IdentityConflictView, PossibleDuplicate,
)
from research_engine.ports.projects import CitationCount, SourceMetadata


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


def _conflict(*, status="unresolved"):
    doi = Identifier("doi", "10.5555/shared")
    pmid = Identifier("pmid", "7001")
    return IdentityConflictView(
        id=str(uuid4()), provider="pubmed", source_id="7001",
        run_id=str(uuid4()), snapshot_id=str(uuid4()), status=status,
        created_at=datetime(2026, 9, 25, tzinfo=timezone.utc),
        asserted_identifiers=(doi, pmid),
        candidates=(
            IdentityCandidate(
                str(uuid4()), "Candidate paper A",
                (Identifier("pmid", "1001"),), (doi, pmid),
            ),
            IdentityCandidate(
                str(uuid4()), "Candidate paper B",
                (Identifier("pmid", "1002"),), (doi,),
            ),
        ),
    )


def _duplicate(*, status="unresolved"):
    return PossibleDuplicate(
        id=str(uuid4()),
        left=IdentityCandidate(
            str(uuid4()), "Left paper", (Identifier("doi", "10.1111/left"),),
        ),
        right=IdentityCandidate(
            str(uuid4()), "Right paper", (Identifier("pmid", "702"),),
        ),
        reason="Similar title", status=status,
        created_at=datetime(2026, 9, 25, tzinfo=timezone.utc),
    )


def test_identity_conflict_requires_each_rejected_identifier_confirmation(qt_app):
    conflict = _conflict()
    dialog = IdentityReviewDialog((conflict,), ())
    assert dialog.conflict_provenance.text().find(conflict.snapshot_id) >= 0
    assert "doi: 10.5555/shared" in dialog.conflict_provenance.text()
    assert "Canonical identifiers: pmid: 1001" in dialog.candidate_summary.text()
    assert "does not merge" in dialog.candidate_summary.text()

    dialog.conflict_reason_edit.setText("Reviewed against the source record")
    assert len(dialog._rejected_checks) == 2
    assert not dialog.select_candidate_button.isEnabled()
    dialog._rejected_checks[0][1].setChecked(True)
    assert not dialog.select_candidate_button.isEnabled()
    dialog._rejected_checks[1][1].setChecked(True)
    assert dialog.select_candidate_button.isEnabled()
    dialog.select_candidate_button.click()

    decision = dialog.selected_decision()
    assert decision == (
        "conflict", conflict.id, "select_article",
        "Reviewed against the source record", conflict.candidates[0].article_id,
        (Identifier("doi", "10.5555/shared"), Identifier("pmid", "7001")),
    )
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_reviewed_conflicts_and_possible_duplicates_are_not_actionable(qt_app):
    dialog = IdentityReviewDialog((_conflict(status="resolved"),), (_duplicate(status="kept_separate"),))
    assert not dialog.keep_separate_button.isEnabled()
    assert not dialog.reject_source_button.isEnabled()
    assert not dialog.select_candidate_button.isEnabled()
    assert not dialog.keep_candidate_separate_button.isEnabled()


def test_possible_duplicate_review_only_records_keep_separate(qt_app):
    duplicate = _duplicate()
    dialog = IdentityReviewDialog((), (duplicate,))
    assert duplicate.left.title in dialog.duplicate_detail.text()
    assert duplicate.right.title in dialog.duplicate_detail.text()
    assert "does not merge" in dialog.duplicate_detail.text()
    dialog.duplicate_reason_edit.setText("Distinct populations and study designs")
    assert dialog.keep_candidate_separate_button.isEnabled()
    dialog.keep_candidate_separate_button.click()
    assert dialog.selected_decision() == (
        "candidate", duplicate.id, "keep_separate",
        "Distinct populations and study designs",
    )


class _FakeIdentityReview:
    def __init__(self, conflict, duplicate=None):
        self.conflict = conflict
        self.duplicate = duplicate
        self.conflict_calls = []
        self.duplicate_calls = []

    def list_conflicts(self, project_id, *, include_reviewed=False):
        if self.conflict_calls:
            return (replace(self.conflict, status="resolved"),)
        return (self.conflict,)

    def list_candidates(self, project_id, *, include_reviewed=False):
        return (self.duplicate,) if self.duplicate else ()

    def review_conflict(self, conflict_id, **kwargs):
        self.conflict_calls.append((conflict_id, kwargs))

    def keep_candidate_separate(self, candidate_id, *, reason):
        self.duplicate_calls.append((candidate_id, reason))


class _ServiceAdapter:
    def __init__(self, services, identity_review):
        self._services = services
        self.identity_review = identity_review

    def __getattr__(self, name):
        return getattr(self._services, name)


def test_controller_routes_conflict_decision_to_application_service(
    tmp_path, qt_app, monkeypatch,
):
    services = create_application_services(tmp_path)
    try:
        services.projects.create_project("Identity review project")
        conflict = _conflict()
        identity = _FakeIdentityReview(conflict)
        adapter = _ServiceAdapter(services, identity)
        window = MainWindow()
        controller = MainController(window, adapter)
        assert window.identity_review.isEnabled()
        calls = [0]

        def decide(dialog):
            calls[0] += 1
            if calls[0] > 1:
                return QDialog.DialogCode.Rejected
            dialog.conflict_reason_edit.setText("Human confirmed source-level association")
            for _, check in dialog._rejected_checks:
                check.setChecked(True)
            dialog.select_candidate_button.click()
            return dialog.result()

        monkeypatch.setattr(IdentityReviewDialog, "exec", decide)
        window.identity_review.click()
        assert identity.conflict_calls == [(
            conflict.id,
            {
                "action": "select_article",
                "reason": "Human confirmed source-level association",
                "article_id": conflict.candidates[0].article_id,
                "rejected_identifiers": conflict.candidates[0].rejected_identifiers,
            },
        )]
        assert calls[0] == 2  # refreshes from the service after recording
        window.close()
    finally:
        services.close()


def test_controller_routes_possible_duplicate_separation_without_merge(
    tmp_path, qt_app, monkeypatch,
):
    services = create_application_services(tmp_path)
    try:
        services.projects.create_project("Duplicate review project")
        duplicate = _duplicate()
        identity = _FakeIdentityReview(_conflict(), duplicate)
        adapter = _ServiceAdapter(services, identity)
        window = MainWindow()
        MainController(window, adapter)

        def decide(dialog):
            dialog.duplicate_reason_edit.setText("Different cohorts; do not combine")
            dialog.keep_candidate_separate_button.click()
            return dialog.result()

        calls = [0]

        def only_once(dialog):
            calls[0] += 1
            return decide(dialog) if calls[0] == 1 else QDialog.DialogCode.Rejected

        monkeypatch.setattr(IdentityReviewDialog, "exec", only_once)
        window.identity_review.click()
        assert identity.duplicate_calls == [(duplicate.id, "Different cohorts; do not combine")]
        window.close()
    finally:
        services.close()


def test_paper_detail_displays_source_metadata_and_citations(qt_app):
    window = MainWindow()
    detail = SimpleNamespace(
        title="Metadata-rich paper", journal="Test Journal",
        publication_date=PartialDate(2024, 2), authors=("A. Author",),
        identifiers=(Identifier("doi", "10.1234/paper"),),
        source_providers=("pubmed",), abstract="An example abstract.",
        project_paper=SimpleNamespace(status=ReviewStatus.READ, tags=("screened",)),
        url=None, canonical_metadata_provenance={},
        publication_types=("Clinical Trial", "Randomized Controlled Trial"),
        publication_status="published",
        citations=(CitationCount("pubmed", 12, datetime(2026, 9, 25, tzinfo=timezone.utc), "citation-snapshot"),),
        source_metadata=(SourceMetadata(
            provider="pubmed", source_id="7001", source_database="MEDLINE",
            url="https://pubmed.ncbi.nlm.nih.gov/7001/", snapshot_id="raw-snapshot",
            retrieved_at=datetime(2026, 9, 25, tzinfo=timezone.utc), parser_version="test-parser",
            publication_types=("Clinical Trial",), publication_status="epublish",
        ),),
    )
    window.display_detail(detail)
    rendered = window.detail_text.toPlainText()
    for expected in (
        "Publication types: Clinical Trial, Randomized Controlled Trial",
        "Publication status: published", "Citation counts (source-reported)",
        "PubMed: 12", "database MEDLINE", "raw-snapshot", "test-parser",
        "https://pubmed.ncbi.nlm.nih.gov/7001/",
    ):
        assert expected in rendered
    assert window.open_url.isEnabled()
    assert window._publication_url == "https://pubmed.ncbi.nlm.nih.gov/7001/"
