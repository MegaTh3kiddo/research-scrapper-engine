from datetime import date, datetime, timezone
from uuid import uuid4

import pytest

from research_engine.domain.models import (
    Article,
    ProjectArticle,
    ResearchProject,
    ReviewDecision,
    SavedQuery,
)
from research_engine.domain.value_objects import PartialDate, SearchFilters


@pytest.mark.parametrize(
    ("year", "month", "day"),
    [
        (2024, 2, 30),
        (2023, 2, 29),
        (2024, 13, 1),
        (0, None, None),
    ],
)
def test_partial_date_rejects_invalid_calendar_dates(year, month, day):
    with pytest.raises(ValueError):
        PartialDate(year=year, month=month, day=day)


def test_partial_date_accepts_valid_leap_day_and_coarse_precision():
    assert PartialDate(2024, 2, 29).precision == "day"
    assert PartialDate(2024, 2).precision == "month"
    assert PartialDate(2024).precision == "year"


def test_search_date_range_must_be_chronological():
    with pytest.raises(ValueError, match="published_from"):
        SearchFilters(published_from=date(2024, 2, 1), published_to=date(2024, 1, 31))


@pytest.mark.parametrize(
    ("factory", "field_name"),
    [
        (lambda timestamp: ResearchProject(name="Study", created_at=timestamp), "created_at"),
        (
            lambda timestamp: SavedQuery(
                project_id=uuid4(), expression="cancer", updated_at=timestamp
            ),
            "updated_at",
        ),
        (lambda timestamp: Article(title="Study", created_at=timestamp), "created_at"),
        (
            lambda timestamp: ProjectArticle(
                project_id=uuid4(), article_id=uuid4(), last_seen_at=timestamp
            ),
            "last_seen_at",
        ),
    ],
)
def test_entities_reject_naive_timestamps(factory, field_name):
    with pytest.raises(ValueError, match="timezone-aware"):
        factory(datetime(2024, 1, 1, 12, 0))


def test_project_article_rejects_last_seen_before_first_seen():
    with pytest.raises(ValueError, match="last_seen_at"):
        ProjectArticle(
            project_id=uuid4(),
            article_id=uuid4(),
            first_seen_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
            last_seen_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )


def test_project_article_can_be_read_and_marked_important_independently():
    read_at = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)

    association = ProjectArticle(
        project_id=uuid4(),
        article_id=uuid4(),
        review_decision=ReviewDecision.IMPORTANT,
        read_at=read_at,
    )

    assert association.read_at == read_at
    assert association.review_decision is ReviewDecision.IMPORTANT


def test_project_article_rejects_naive_read_at():
    with pytest.raises(ValueError, match="read_at.*timezone-aware"):
        ProjectArticle(
            project_id=uuid4(),
            article_id=uuid4(),
            read_at=datetime(2024, 1, 1, 12, 0),
        )
