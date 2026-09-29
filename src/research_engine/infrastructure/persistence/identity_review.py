"""Transactional, evidence-bound identity review repository."""

from __future__ import annotations

from datetime import datetime
from typing import Callable
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from research_engine.domain.identity import normalize_identifiers
from research_engine.domain.identity_review import assertion_signature
from research_engine.domain.value_objects import Identifier
from research_engine.ports.identity_review import IdentityCandidate, IdentityConflictView, PossibleDuplicate

from . import models as m


def _candidate(session: Session, article_id: str, *, asserted: tuple[Identifier, ...] = (), provider: str | None = None) -> IdentityCandidate:
    article = session.get(m.Article, article_id)
    if article is None:
        raise ValueError("identity candidate no longer exists")
    identifiers = tuple(Identifier(row.scheme, row.value) for row in session.scalars(
        select(m.ArticleIdentifier).where(m.ArticleIdentifier.article_id == article_id)
        .order_by(m.ArticleIdentifier.scheme, m.ArticleIdentifier.value)
    ))
    rejected = []
    if provider:
        for identifier in asserted:
            for token in normalize_identifiers((identifier,), provider=provider):
                owner = session.scalar(select(m.ArticleIdentifier.article_id).where(
                    m.ArticleIdentifier.scheme == token.scheme, m.ArticleIdentifier.value == token.value,
                ))
                if owner is not None and owner != article_id:
                    rejected.append(identifier)
                    break
    return IdentityCandidate(article.id, article.title, identifiers, tuple(rejected))


def _asserted(conflict: m.IdentityConflict) -> tuple[Identifier, ...]:
    return tuple(Identifier(item["scheme"], item["value"])
                 for item in conflict.identifiers.get("asserted", []))


def _event(session: Session, decision, at: datetime) -> None:
    session.add(m.OutboxEvent(
        id=str(uuid4()), type="IdentityReviewed", schema_version=1,
        aggregate_type="IdentityReviewDecision", aggregate_id=decision.id,
        payload={"decision_id": decision.id, "conflict_id": decision.conflict_id,
                 "candidate_id": decision.candidate_id, "action": decision.action,
                 "selected_article_id": decision.selected_article_id}, occurred_at=at,
    ))


class SqlAlchemyIdentityReviewStore:
    def __init__(self, session_factory: Callable[[], Session]):
        self._session_factory = session_factory

    def list_conflicts(self, project_id: str, *, include_reviewed: bool = False):
        with self._session_factory() as session:
            statement = select(m.IdentityConflict).join(
                m.SearchRun, m.IdentityConflict.run_id == m.SearchRun.id,
            ).join(m.SavedQuery, m.SearchRun.query_id == m.SavedQuery.id).where(
                m.SavedQuery.project_id == project_id,
            ).order_by(m.IdentityConflict.created_at, m.IdentityConflict.id)
            if not include_reviewed:
                statement = statement.where(m.IdentityConflict.status == "unresolved")
            return tuple(IdentityConflictView(
                row.id, row.provider, row.source_id, row.run_id, row.snapshot_id,
                row.status, row.created_at, _asserted(row),
                tuple(_candidate(session, article_id, asserted=_asserted(row), provider=row.provider)
                      for article_id in row.candidate_article_ids),
            ) for row in session.scalars(statement))

    def review_conflict(self, conflict_id: str, *, action: str, article_id: str | None,
                        rejected_identifiers: tuple[Identifier, ...], reason: str,
                        reviewed_at: datetime) -> None:
        with self._session_factory.begin() as session:
            row = session.get(m.IdentityConflict, conflict_id)
            if row is None or row.status != "unresolved":
                raise ValueError("conflict is missing or already reviewed")
            asserted = _asserted(row)
            rejected = ()
            if action == "select_article":
                if article_id not in row.candidate_article_ids:
                    raise ValueError("select an existing conflict candidate")
                _candidate(session, article_id)
                source = session.scalar(select(m.SourceRecord).where(
                    m.SourceRecord.provider == row.provider,
                    m.SourceRecord.provider_record_id == row.source_id,
                ))
                if source is not None and source.article_id != article_id:
                    raise ValueError("existing source ownership cannot be reassigned by review")
                rejected = tuple(token for token in normalize_identifiers(asserted, provider=row.provider)
                                 if (owner := session.scalar(select(m.ArticleIdentifier.article_id).where(
                                     m.ArticleIdentifier.scheme == token.scheme,
                                     m.ArticleIdentifier.value == token.value,
                                 ))) is not None and owner != article_id)
                confirmed = set(normalize_identifiers(rejected_identifiers, provider=row.provider))
                if confirmed != set(rejected):
                    raise ValueError("explicitly confirm all and only conflicting identifiers to reject")
            changed = session.execute(update(m.IdentityConflict).where(
                m.IdentityConflict.id == row.id, m.IdentityConflict.status == "unresolved",
            ).values(status="dismissed" if action == "reject_source" else "resolved"))
            if changed.rowcount != 1:
                raise ValueError("conflict was reviewed concurrently; refresh")
            decision = m.IdentityReviewDecision(
                conflict_id=row.id, candidate_id=None, action=action,
                selected_article_id=article_id, provider=row.provider, source_id=row.source_id,
                assertion_signature=assertion_signature(row.provider, row.source_id, asserted),
                rejected_identifiers=[{"scheme": token.scheme, "value": token.value} for token in rejected],
                reason=reason, reviewed_at=reviewed_at, snapshot_id=row.snapshot_id,
            )
            session.add(decision)
            session.flush()
            _event(session, decision, reviewed_at)

    def list_candidates(self, project_id: str, *, include_reviewed: bool = False):
        with self._session_factory() as session:
            linked = select(m.ProjectArticle.article_id).where(m.ProjectArticle.project_id == project_id)
            statement = select(m.PossibleDuplicateCandidate).where(
                m.PossibleDuplicateCandidate.left_article_id.in_(linked)
                | m.PossibleDuplicateCandidate.right_article_id.in_(linked)
            ).order_by(m.PossibleDuplicateCandidate.created_at, m.PossibleDuplicateCandidate.id)
            if not include_reviewed:
                statement = statement.where(m.PossibleDuplicateCandidate.status == "unresolved")
            return tuple(PossibleDuplicate(
                row.id, _candidate(session, row.left_article_id), _candidate(session, row.right_article_id),
                row.reason, row.status, row.created_at,
            ) for row in session.scalars(statement))

    def keep_candidate_separate(self, candidate_id: str, *, reason: str, reviewed_at: datetime) -> None:
        with self._session_factory.begin() as session:
            row = session.get(m.PossibleDuplicateCandidate, candidate_id)
            if row is None or row.status != "unresolved":
                raise ValueError("candidate is missing or already reviewed")
            changed = session.execute(update(m.PossibleDuplicateCandidate).where(
                m.PossibleDuplicateCandidate.id == row.id,
                m.PossibleDuplicateCandidate.status == "unresolved",
            ).values(status="kept_separate"))
            if changed.rowcount != 1:
                raise ValueError("candidate was reviewed concurrently; refresh")
            decision = m.IdentityReviewDecision(
                candidate_id=row.id, conflict_id=None, action="keep_separate",
                reason=reason, reviewed_at=reviewed_at, snapshot_id=row.snapshot_id,
                rejected_identifiers=[],
            )
            session.add(decision)
            session.flush()
            _event(session, decision, reviewed_at)
