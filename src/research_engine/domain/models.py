"""Domain entities. Persistence and transport models live outside this package."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID, uuid4

from research_engine.domain.value_objects import Identifier, PartialDate, SearchFilters


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


class ReviewDecision(StrEnum):
    NONE = "none"
    IMPORTANT = "important"
    EXCLUDED = "excluded"


class ReviewStatus(StrEnum):
    UNREAD = "unread"
    READ = "read"
    IMPORTANT = "important"
    EXCLUDED = "excluded"


@dataclass(frozen=True, slots=True)
class ResearchProject:
    name: str
    id: UUID = field(default_factory=uuid4)
    description: str | None = None
    archived_at: datetime | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("project name must be non-empty")
        _require_aware(self.created_at, "created_at")
        _require_aware(self.updated_at, "updated_at")
        if self.archived_at is not None:
            _require_aware(self.archived_at, "archived_at")


@dataclass(frozen=True, slots=True)
class SavedQuery:
    project_id: UUID
    expression: str
    id: UUID = field(default_factory=uuid4)
    filters: SearchFilters = field(default_factory=SearchFilters)
    enabled_providers: tuple[str, ...] = ()
    is_active: bool = True
    archived_at: datetime | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.expression.strip():
            raise ValueError("query expression must be non-empty")
        _require_aware(self.created_at, "created_at")
        _require_aware(self.updated_at, "updated_at")
        if self.archived_at is not None:
            _require_aware(self.archived_at, "archived_at")


@dataclass(frozen=True, slots=True)
class Article:
    title: str
    id: UUID = field(default_factory=uuid4)
    publication_date: PartialDate | None = None
    abstract: str | None = None
    journal_name: str | None = None
    publication_type: str | None = None
    publication_status: str | None = None
    identifiers: tuple[Identifier, ...] = ()
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    publication_types: tuple[str, ...] = ()
    url: str | None = None

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise ValueError("article title must be non-empty")
        _require_aware(self.created_at, "created_at")
        _require_aware(self.updated_at, "updated_at")


@dataclass(frozen=True, slots=True)
class ProjectArticle:
    project_id: UUID
    article_id: UUID
    review_decision: ReviewDecision = ReviewDecision.NONE
    read_at: datetime | None = None
    first_seen_at: datetime = field(default_factory=utc_now)
    last_seen_at: datetime = field(default_factory=utc_now)
    status_updated_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "review_decision", ReviewDecision(self.review_decision))
        if self.read_at is not None:
            _require_aware(self.read_at, "read_at")
        _require_aware(self.first_seen_at, "first_seen_at")
        _require_aware(self.last_seen_at, "last_seen_at")
        if self.status_updated_at is not None:
            _require_aware(self.status_updated_at, "status_updated_at")
        if self.last_seen_at < self.first_seen_at:
            raise ValueError("last_seen_at must not precede first_seen_at")

    @property
    def status(self) -> ReviewStatus:
        if self.review_decision is ReviewDecision.IMPORTANT:
            return ReviewStatus.IMPORTANT
        if self.review_decision is ReviewDecision.EXCLUDED:
            return ReviewStatus.EXCLUDED
        return ReviewStatus.READ if self.read_at is not None else ReviewStatus.UNREAD

    @property
    def is_read(self) -> bool:
        return self.read_at is not None

    def with_review_status(
        self, status: ReviewStatus, *, at: datetime | None = None
    ) -> ProjectArticle:
        status = ReviewStatus(status)
        when = at or utc_now()
        _require_aware(when, "at")
        if status is self.status:
            return self
        if status is ReviewStatus.UNREAD:
            return replace(
                self, review_decision=ReviewDecision.NONE,
                read_at=None, status_updated_at=when,
            )
        if status is ReviewStatus.READ:
            return replace(
                self, review_decision=ReviewDecision.NONE,
                read_at=self.read_at or when, status_updated_at=when,
            )
        decision = (
            ReviewDecision.IMPORTANT
            if status is ReviewStatus.IMPORTANT else ReviewDecision.EXCLUDED
        )
        return replace(self, review_decision=decision, status_updated_at=when)
