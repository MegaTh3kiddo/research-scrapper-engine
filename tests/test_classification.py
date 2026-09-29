from datetime import datetime, timezone

import pytest

from research_engine.domain.classification import is_newly_published
from research_engine.domain.value_objects import PartialDate


def test_recent_publication_requires_day_precision():
    started = datetime(2026, 9, 25, tzinfo=timezone.utc)

    assert is_newly_published(PartialDate(2026, 9, 24), started, 30) is True
    assert is_newly_published(PartialDate(2026, 8, 1), started, 30) is False
    assert is_newly_published(PartialDate(2026, 9), started, 30) is None
    assert is_newly_published(None, started, 30) is None


def test_recent_publication_rejects_naive_clock_and_negative_window():
    with pytest.raises(ValueError, match="timezone-aware"):
        is_newly_published(PartialDate(2026, 9, 24), datetime(2026, 9, 25), 30)
    with pytest.raises(ValueError, match="non-negative"):
        is_newly_published(None, datetime(2026, 9, 25, tzinfo=timezone.utc), -1)
