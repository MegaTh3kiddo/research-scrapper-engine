import json
from datetime import date, datetime, timezone

import httpx
import pytest

from research_engine.application.discovery import DiscoveryService, PaginationError
from research_engine.application.provider_registry import ProviderRegistry
from research_engine.domain.value_objects import SearchFilters
from research_engine.infrastructure.providers.crossref import CrossrefProvider
from research_engine.infrastructure.providers.http import ProviderRequestError, ProviderResponseError
from research_engine.ports.providers import SearchRequest


def _json_response(request: httpx.Request, payload, *, status_code=200):
    return httpx.Response(
        status_code,
        request=request,
        headers={"content-type": "application/json; charset=utf-8"},
        content=json.dumps(payload).encode(),
    )


def _work(doi="10.5555/Example", **overrides):
    payload = {
        "DOI": doi,
        "type": "journal-article",
        "is-referenced-by-count": 0,
        "title": ["A <i>useful</i> study"],
        "author": [
            {"given": "Ada", "family": "Example"},
            {"name": "Research Group"},
        ],
        "container-title": ["Journal of Testing"],
        "published-online": {"date-parts": [[2025, 2, 3]]},
        "abstract": "<jats:p>Methods &amp; results.</jats:p>",
        "subject": ["oncology", "clinical research"],
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_search_translates_supported_filters_and_normalizes_raw_snapshot():
    body_payload = {
        "message": {
            "total-results": 8,
            "next-cursor": "next+cursor/2",
            "items": [_work()],
        }
    }
    request_seen = []

    def handler(request):
        request_seen.append(request)
        return _json_response(request, body_payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        fetched_at = datetime(2026, 1, 2, tzinfo=timezone.utc)
        provider = CrossrefProvider(
            client=client,
            email="researcher@example.org",
            min_interval=0,
            clock=lambda: fetched_at,
        )
        plan = provider.plan(
            SearchRequest(
                "breast cancer immunotherapy",
                SearchFilters(
                    published_from=date(2020, 1, 2),
                    published_to=date(2024, 12, 31),
                ),
                page_size=1500,
            )
        )
        page = await provider.search(plan)

    request = request_seen[0]
    assert request.url.path == "/v1/works"
    assert request.url.params["query"] == "breast cancer immunotherapy"
    assert request.url.params["rows"] == "1000"
    assert request.url.params["cursor"] == "*"
    assert request.url.params["filter"] == (
        "from-pub-date:2020-01-02,until-pub-date:2024-12-31"
    )
    assert request.url.params["mailto"] == "researcher@example.org"

    assert page.total_count == 8
    # Crossref says fewer results than requested rows is terminal, even if a
    # cursor happens to be present in the response.
    assert page.next_cursor is None
    assert len(page.raw_responses) == 1
    raw = page.raw_responses[0]
    assert raw.body == json.dumps(body_payload).encode()
    assert raw.media_type == "application/json"
    assert raw.fetched_at == fetched_at
    assert raw.safe_request_metadata == {
        "source": "crossref",
        "endpoint": "works",
        "query": "breast cancer immunotherapy",
        "cursor": "*",
        "rows": "1000",
        "filter": "from-pub-date:2020-01-02,until-pub-date:2024-12-31",
    }
    record = page.records[0]
    assert record.source_id == "10.5555/Example"
    assert record.source_database == "crossref"
    assert record.response_index == 0
    assert record.title == "A useful study"
    assert record.authors == ("Ada Example", "Research Group")
    assert record.journal_name == "Journal of Testing"
    assert record.publication_date.year == 2025
    assert record.publication_date.month == 2
    assert record.publication_date.day == 3
    assert record.abstract == "Methods & results."
    assert record.keywords == ("oncology", "clinical research")
    assert record.citation_count == 0
    assert record.publication_types == ("journal-article",)
    assert record.publication_status is None
    assert record.identifiers[0].scheme == "doi"
    assert record.identifiers[0].value == "10.5555/Example"
    assert record.url == "https://doi.org/10.5555/Example"


@pytest.mark.asyncio
async def test_search_uses_cursor_and_returns_next_cursor_for_full_page():
    cursors = []

    def handler(request):
        cursor = request.url.params["cursor"]
        cursors.append(cursor)
        return _json_response(
            request,
            {"message": {"total-results": 2, "next-cursor": "next-page", "items": [_work()]}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = CrossrefProvider(client=client, min_interval=0)
        plan = provider.plan(SearchRequest("neuroscience", page_size=1))
        page = await provider.search(plan, cursor="cursor-one")

    assert cursors == ["cursor-one"]
    assert page.next_cursor == "next-page"


@pytest.mark.asyncio
async def test_full_page_repeated_cursor_reaches_discovery_pagination_guard():
    def handler(request):
        return _json_response(
            request,
            {
                "message": {
                    "total-results": 10,
                    "next-cursor": "same-cursor",
                    "items": [_work()],
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = CrossrefProvider(client=client, min_interval=0)
        discovery = DiscoveryService(ProviderRegistry([provider]))
        with pytest.raises(PaginationError, match="repeated a search cursor"):
            await discovery.execute(
                "crossref",
                SearchRequest("neuroscience", page_size=1),
                cursor="same-cursor",
                max_pages=2,
            )


@pytest.mark.asyncio
async def test_short_page_is_terminal_even_if_next_cursor_is_present():
    def handler(request):
        assert request.url.params["rows"] == "2"
        return _json_response(
            request,
            {
                "message": {
                    "total-results": 1,
                    "next-cursor": "should-not-be-followed",
                    "items": [_work()],
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = CrossrefProvider(client=client, min_interval=0)
        page = await provider.search(
            provider.plan(SearchRequest("neuroscience", page_size=2))
        )

    assert len(page.records) == 1
    assert page.next_cursor is None


@pytest.mark.asyncio
async def test_plan_supports_boolean_but_still_discloses_unsupported_filters():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: _json_response(req, {}))) as client:
        provider = CrossrefProvider(client=client, min_interval=0)
        plan = provider.plan(
            SearchRequest(
                "Alzheimer AND artificial intelligence",
                SearchFilters(
                    languages=("en",),
                    publication_types=("Review",),
                    humans_only=True,
                    open_access_only=True,
                ),
            )
        )

        assert provider.capabilities.supports_boolean_queries is True
        assert plan.parameters["boolean_expression"] == "Alzheimer AND artificial intelligence"
        assert provider.capabilities.supported_filters == frozenset(
            {"published_from", "published_to"}
        )
        assert plan.unsupported_filters == (
            "languages",
            "publication_types",
            "humans_only",
            "open_access_only",
        )
        with pytest.raises(ValueError, match="unsupported features"):
            await provider.search(plan)


@pytest.mark.asyncio
async def test_fetch_encodes_doi_checks_identity_and_omits_email_from_audit_metadata():
    seen = []

    def handler(request):
        seen.append(request)
        return _json_response(request, {"message": _work()})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = CrossrefProvider(
            client=client, email="researcher@example.org", min_interval=0
        )
        result = await provider.fetch("https://doi.org/10.5555/Example")

    assert str(seen[0].url).split("?", maxsplit=1)[0].endswith(
        "/works/10.5555%2FExample"
    )
    assert seen[0].url.params["mailto"] == "researcher@example.org"
    assert result.record.source_id == "10.5555/Example"
    assert result.raw_response.safe_request_metadata == {
        "source": "crossref",
        "endpoint": "works/{doi}",
        "doi": "10.5555/Example",
    }


@pytest.mark.asyncio
async def test_fetch_rejects_a_different_returned_doi():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: _json_response(request, {"message": _work("10.5555/other")})
        )
    ) as client:
        provider = CrossrefProvider(client=client, min_interval=0)
        with pytest.raises(ProviderResponseError, match="different DOI"):
            await provider.fetch("10.5555/requested")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source_id",
    [
        "https://example.org/10.5555/not-a-doi",
        "https://doi.org.evil.example/10.5555/not-a-doi",
        "https://doi.org/10.5555/example?redirect=https://evil.example",
        "https://doi.org/10.5555/example#fragment",
        "https://user@doi.org/10.5555/example",
    ],
)
async def test_fetch_rejects_noncanonical_or_ambiguous_doi_urls(source_id):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: pytest.fail("invalid DOI must not make a request"))
    ) as client:
        provider = CrossrefProvider(client=client, min_interval=0)
        with pytest.raises(ValueError, match="DOI resolver URL"):
            await provider.fetch(source_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"{broken", "invalid JSON"),
        (json.dumps({"message": {"items": "not-a-list"}}).encode(), "items list"),
        (json.dumps({"message": {"items": [{"title": ["No DOI"]}]}}).encode(), "include a DOI"),
    ],
)
async def test_malformed_search_payloads_raise_provider_response_error(payload, message):
    def handler(request):
        return httpx.Response(
            200,
            request=request,
            headers={"content-type": "application/json"},
            content=payload,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = CrossrefProvider(client=client, min_interval=0)
        plan = provider.plan(SearchRequest("cancer"))
        with pytest.raises(ProviderResponseError, match=message):
            await provider.search(plan)


@pytest.mark.asyncio
async def test_request_failures_are_wrapped_and_http_403_is_not_bypassed():
    attempts = []

    def handler(request):
        attempts.append(request)
        return httpx.Response(403, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = CrossrefProvider(client=client, min_interval=0)
        with pytest.raises(ProviderRequestError, match="HTTP 403"):
            await provider.search(provider.plan(SearchRequest("cancer")))

    assert len(attempts) == 1


def test_equal_author_names_preserve_distinct_ordered_contributors():
    record = CrossrefProvider._normalize_record({
        "DOI": "10.5555/equal-authors", "title": ["Two contributors"],
        "author": [{"given": "A", "family": "Smith"}, {"given": "A", "family": "Smith"}],
    }, response_index=0)
    assert record.authors == ("A Smith", "A Smith")


def test_crossref_missing_citation_count_stays_unknown():
    item = _work()
    item.pop("is-referenced-by-count")
    record = CrossrefProvider._normalize_record(item, response_index=0)

    assert record.citation_count is None
    assert record.publication_types == ("journal-article",)


@pytest.mark.parametrize("value", [-1, "-1", "unknown", True])
def test_crossref_rejects_invalid_citation_counts(value):
    with pytest.raises(ProviderResponseError, match="is-referenced-by-count.*non-negative integer"):
        CrossrefProvider._normalize_record(
            _work(**{"is-referenced-by-count": value}),
            response_index=0,
        )
