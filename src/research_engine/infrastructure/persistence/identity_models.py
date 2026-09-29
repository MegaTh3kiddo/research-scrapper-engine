"""Evidence-preserving identity review tables (registered by models)."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, ForeignKeyConstraint, Index, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, new_id, utc_now
from .types import UTCDateTime


class PossibleDuplicateCandidate(Base):
    __tablename__ = "possible_duplicate_candidates"
    __table_args__ = (
        ForeignKeyConstraint(["left_article_id"], ["articles.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["right_article_id"], ["articles.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["snapshot_id"], ["source_snapshots.id"], ondelete="RESTRICT"),
        UniqueConstraint("left_article_id", "right_article_id", name="uq_possible_duplicate_pair"),
        CheckConstraint("left_article_id < right_article_id", name="ordered_pair"),
        CheckConstraint("status IN ('unresolved', 'kept_separate')", name="status_valid"),
        Index("ix_possible_duplicate_status_created", "status", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    left_article_id: Mapped[str] = mapped_column(String(36), nullable=False)
    right_article_id: Mapped[str] = mapped_column(String(36), nullable=False)
    snapshot_id: Mapped[str] = mapped_column(String(36), nullable=False)
    reason: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="unresolved", server_default=text("'unresolved'"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class IdentityReviewDecision(Base):
    __tablename__ = "identity_review_decisions"
    __table_args__ = (
        ForeignKeyConstraint(["conflict_id"], ["identity_conflicts.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["candidate_id"], ["possible_duplicate_candidates.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["selected_article_id"], ["articles.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["snapshot_id"], ["source_snapshots.id"], ondelete="RESTRICT"),
        UniqueConstraint("conflict_id", name="uq_identity_review_conflict"),
        UniqueConstraint("candidate_id", name="uq_identity_review_candidate"),
        CheckConstraint("(conflict_id IS NULL) <> (candidate_id IS NULL)", name="one_issue"),
        CheckConstraint("action IN ('keep_separate', 'reject_source', 'select_article')", name="action_valid"),
        CheckConstraint("(action = 'select_article' AND selected_article_id IS NOT NULL) OR (action <> 'select_article' AND selected_article_id IS NULL)", name="selection_required"),
        CheckConstraint("candidate_id IS NULL OR action = 'keep_separate'", name="candidate_action"),
        CheckConstraint("conflict_id IS NULL OR (provider IS NOT NULL AND source_id IS NOT NULL AND assertion_signature IS NOT NULL)", name="conflict_evidence"),
        Index("ix_identity_review_source_signature", "provider", "source_id", "assertion_signature", "reviewed_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conflict_id: Mapped[str | None] = mapped_column(String(36))
    candidate_id: Mapped[str | None] = mapped_column(String(36))
    selected_article_id: Mapped[str | None] = mapped_column(String(36))
    snapshot_id: Mapped[str] = mapped_column(String(36), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(100))
    source_id: Mapped[str | None] = mapped_column(String(255))
    assertion_signature: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    rejected_identifiers: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list, server_default=text("'[]'"))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    reviewed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
