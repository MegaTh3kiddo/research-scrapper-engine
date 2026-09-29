"""Explicit identity decisions; never automatically merge canonical records."""

from datetime import datetime, timezone
from typing import Callable
from uuid import UUID

from research_engine.domain.value_objects import Identifier
from research_engine.ports.identity_review import IdentityReviewStore


class IdentityReviewService:
    def __init__(self, store: IdentityReviewStore, *, clock: Callable[[], datetime] | None = None):
        self._store = store
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def list_conflicts(self, project_id: str, *, include_reviewed: bool = False):
        return self._store.list_conflicts(str(UUID(str(project_id))), include_reviewed=include_reviewed)

    def list_candidates(self, project_id: str, *, include_reviewed: bool = False):
        return self._store.list_candidates(str(UUID(str(project_id))), include_reviewed=include_reviewed)

    def review_conflict(
        self, conflict_id: str, *, action: str, reason: str,
        article_id: str | None = None, rejected_identifiers: tuple[Identifier, ...] = (),
    ) -> None:
        if action not in {"keep_separate", "reject_source", "select_article"}:
            raise ValueError("unknown identity review action")
        if (action == "select_article") != (article_id is not None):
            raise ValueError("only selecting a candidate requires an article ID")
        if action != "select_article" and rejected_identifiers:
            raise ValueError("identifier rejection requires selecting a candidate")
        self._store.review_conflict(
            str(UUID(str(conflict_id))), action=action,
            article_id=str(UUID(str(article_id))) if article_id else None,
            rejected_identifiers=tuple(rejected_identifiers), reason=self._reason(reason),
            reviewed_at=self._now(),
        )

    def keep_candidate_separate(self, candidate_id: str, *, reason: str) -> None:
        self._store.keep_candidate_separate(
            str(UUID(str(candidate_id))), reason=self._reason(reason), reviewed_at=self._now(),
        )

    @staticmethod
    def _reason(reason: str) -> str:
        reason = reason.strip()
        if not reason or len(reason) > 2000:
            raise ValueError("a review reason of 1–2000 characters is required")
        return reason

    def _now(self) -> datetime:
        at = self._clock()
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("review clock must return an aware datetime")
        return at
