from datetime import datetime, timezone
from uuid import uuid4

from research_engine.domain.models import ProjectArticle, ReviewDecision, ReviewStatus


def test_review_status_preserves_reading_independently_from_importance():
    association = ProjectArticle(project_id=uuid4(), article_id=uuid4())
    first = datetime(2026, 9, 25, 10, tzinfo=timezone.utc)
    second = datetime(2026, 9, 25, 11, tzinfo=timezone.utc)

    assert association.status is ReviewStatus.UNREAD
    read = association.with_review_status(ReviewStatus.READ, at=first)
    important = read.with_review_status(ReviewStatus.IMPORTANT, at=second)

    assert read.status is ReviewStatus.READ
    assert read.read_at == first
    assert important.status is ReviewStatus.IMPORTANT
    assert important.review_decision is ReviewDecision.IMPORTANT
    assert important.read_at == first
    assert important.status_updated_at == second


def test_unread_clears_display_decision_and_read_timestamp():
    association = ProjectArticle(
        project_id=uuid4(), article_id=uuid4(),
        read_at=datetime(2026, 9, 25, tzinfo=timezone.utc),
        review_decision=ReviewDecision.EXCLUDED,
    )

    unread = association.with_review_status(
        ReviewStatus.UNREAD, at=datetime(2026, 9, 26, tzinfo=timezone.utc)
    )

    assert unread.status is ReviewStatus.UNREAD
    assert unread.read_at is None
    assert unread.review_decision is ReviewDecision.NONE
