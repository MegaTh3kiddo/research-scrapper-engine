from datetime import datetime, timezone
from uuid import uuid4

import pytest

from research_engine.application.identity_review import IdentityReviewService
from research_engine.domain.identity_review import assertion_signature, possible_duplicate_key
from research_engine.domain.value_objects import Identifier


def test_assertion_signature_is_normalized_and_provider_scoped():
    a = assertion_signature("pubmed", "123", (Identifier("doi", "https://doi.org/10.1000/ABC"),))
    assert a == assertion_signature("pubmed", "123", (Identifier("doi", "10.1000/abc"),))
    assert a != assertion_signature("europe_pmc", "123", (Identifier("doi", "10.1000/abc"),))
    assert a != assertion_signature("pubmed", "123", (Identifier("doi", "10.1000/def"),))


def test_possible_duplicate_key_requires_long_title_and_known_year():
    title = "A systematic review of breast cancer immunotherapy outcomes"
    assert possible_duplicate_key(title, 2026) == possible_duplicate_key(title.upper() + ".", 2026)
    assert possible_duplicate_key(title, 2025) != possible_duplicate_key(title, 2026)
    assert possible_duplicate_key(title, None) is None
    assert possible_duplicate_key("Editorial", 2026) is None


@pytest.mark.parametrize("action,article,reason", [
    ("merge", None, "evidence"), ("select_article", None, "evidence"),
    ("keep_separate", str(uuid4()), "evidence"), ("reject_source", None, " "),
])
def test_review_validates_decision_before_repository_write(action, article, reason):
    class Store:
        def review_conflict(self, *args, **kwargs):
            pytest.fail("invalid review must not write")
    service = IdentityReviewService(Store())
    with pytest.raises(ValueError):
        service.review_conflict(str(uuid4()), action=action, article_id=article, reason=reason)


def test_review_passes_reason_confirmation_and_aware_timestamp_without_ui():
    calls = []
    class Store:
        def review_conflict(self, *args, **kwargs):
            calls.append((args, kwargs))
    at = datetime(2026, 9, 26, tzinfo=timezone.utc)
    service = IdentityReviewService(Store(), clock=lambda: at)
    rejected = (Identifier("pmid", "12"),)
    service.review_conflict(str(uuid4()), action="select_article", article_id=str(uuid4()),
                            reason=" Checked original identifiers ", rejected_identifiers=rejected)
    assert calls[0][1]["reason"] == "Checked original identifiers"
    assert calls[0][1]["rejected_identifiers"] == rejected
    assert calls[0][1]["reviewed_at"] == at
