"""Source-independent classification of recent publication dates."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from research_engine.domain.models import _require_aware
from research_engine.domain.value_objects import PartialDate


def is_newly_published(
    publication_date: PartialDate | None,
    run_started_at: datetime,
    recent_window_days: int,
) -> bool | None:
    """Return unknown for dates too imprecise for a day-bounded window."""

    _require_aware(run_started_at, "run_started_at")
    if recent_window_days < 0:
        raise ValueError("recent_window_days must be non-negative")
    if publication_date is None or publication_date.day is None:
        return None
    observed_day = run_started_at.astimezone(timezone.utc).date()
    earliest_day = observed_day - timedelta(days=recent_window_days)
    published_day = datetime(
        publication_date.year, publication_date.month, publication_date.day
    ).date()
    return earliest_day <= published_day <= observed_day
