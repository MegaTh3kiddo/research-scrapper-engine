"""Identity review read models and persistence contract; no ORM/UI dependency."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from research_engine.domain.value_objects import Identifier


@dataclass(frozen=True, slots=True)
class IdentityCandidate:
    article_id: str
    title: str
    identifiers: tuple[Identifier, ...]
    rejected_identifiers: tuple[Identifier, ...] = ()


@dataclass(frozen=True, slots=True)
class IdentityConflictView:
    id: str
    provider: str
    source_id: str
    run_id: str
    snapshot_id: str
    status: str
    created_at: datetime
    asserted_identifiers: tuple[Identifier, ...]
    candidates: tuple[IdentityCandidate, ...]


@dataclass(frozen=True, slots=True)
class PossibleDuplicate:
    id: str
    left: IdentityCandidate
    right: IdentityCandidate
    reason: str
    status: str
    created_at: datetime


class IdentityReviewStore(Protocol):
    def list_conflicts(self, project_id: str, *, include_reviewed: bool = False) -> tuple[IdentityConflictView, ...]: ...

    def review_conflict(
        self, conflict_id: str, *, action: str, article_id: str | None,
        rejected_identifiers: tuple[Identifier, ...], reason: str, reviewed_at: datetime,
    ) -> None: ...

    def list_candidates(self, project_id: str, *, include_reviewed: bool = False) -> tuple[PossibleDuplicate, ...]: ...

    def keep_candidate_separate(self, candidate_id: str, *, reason: str, reviewed_at: datetime) -> None: ...
