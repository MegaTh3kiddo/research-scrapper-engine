import asyncio
from datetime import date, datetime, timezone
import json

import httpx
import pytest

from research_engine.domain.value_objects import SearchFilters
from research_engine.infrastructure.providers.europe_pmc import EuropePMCProvider
from research_engine.infrastructure.providers.http import (
    ProviderRequestError,
    ProviderResponseError,
)
from research_engine.ports.providers import ProviderQueryPlan, SearchRequest


def _json_response(request: httpx.Request, payload: object) -> httpx.Response:
    return httpx.Response(
        200,
        request=request,
        headers={"content-type": "application/json; charset=utf-8"},
        content=json.dumps(payload).encode(),
    )


def test_plan_translates_supported_filters_and_reports_human_filter_unsupported():
    provider = EuropePMCProvider()
    request = SearchRequest(
        expression='breast cancer OR "ovarian cancer"',
        filters=SearchFilters(
            published_from=date(2024, 1, 2),
            published_to=date(2024, 6, 30),
            languages=("eng", "fra"),
            publication_types=("Review", "Clinical Trial"),
            humans_only=True,
            open_access_only=True,
        ),
        page_size=75,
    )

    plan = provider.plan(request)

    assert plan.provider == "europe_pmc"
    assert plan.translation_version == "europe-pmc-boolean-v2"
    assert plan.translated_query.startswith('(breast cancer OR "ovarian cancer")')
    assert "FIRST_PDATE:[2024-01-02 TO 2024-06-30]" in plan.translated_query
    assert 'LANG:"eng"' in plan.translated_query
    assert 'LANG:"fra"' in plan.translated_query
    assert 'PUB_TYPE:"Review"' in plan.translated_query
    assert 'PUB_TYPE:"Clinical Trial"' in plan.translated_query
    assert "OPEN_ACCESS:y" in plan.translated_query
    assert plan.unsupported_filters == ("humans_only",)
    assert plan.parameters["pageSize"] == "75"
    assert plan.parameters["resultType"] == "core"

    asyncio.run(provider.aclose())


def test_search_normalizes_core_result_and_retains_raw_response_provenance():
    payload = {
        "hitCount": "8",
        "nextCursorMark": "next-mark",
        "resultList": {
            "result": [
                {
                    "source": "MED",
                    "id": "40123456",
                    "pmid": "40123456",
                    "pmcid": "PMC1234567",
                    "doi": "https://doi.org/10.1234/example",
                    "title": "<i>Immune</i> therapy outcomes",
                    "authorList": {
                        "author": [
                            {"fullName": "Ada Example"},
                            {"firstName": "Grace", "lastName": "Researcher"},
                        ]
                    },
                    "journalTitle": "Journal of Medical Studies",
                    "firstPublicationDate": "2025-02-03",
                    "abstractText": "<h4>BACKGROUND</h4> Findings&nbsp;are useful.",
                    "pubTypeList": {"pubType": ["Journal Article", "Review"]},
                    "citedByCount": "0",
                    "keywordList": {"keyword": ["immunotherapy", "breast cancer"]},
                    "meshHeadingList": {"meshHeading": [{"descriptorName": "Neoplasms"}]},
                }
            ]
        },
    }
    body = json.dumps(payload).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/europepmc/webservices/rest/search"
        assert request.url.params["query"] == "(breast cancer)"
        assert request.url.params["cursorMark"] == "*"
        assert request.url.params["resultType"] == "core"
        return httpx.Response(
            200,
            request=request,
            headers={"content-type": "application/json; charset=utf-8"},
            content=body,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    fetched_at = datetime(2025, 3, 1, tzinfo=timezone.utc)
    provider = EuropePMCProvider(client=client, clock=lambda: fetched_at)
    plan = provider.plan(SearchRequest(expression="breast cancer"))

    page = asyncio.run(provider.search(plan))

    assert page.total_count == 8
    assert page.next_cursor == "next-mark"
    assert len(page.raw_responses) == 1
    snapshot = page.raw_responses[0]
    assert snapshot.body == body
    assert snapshot.media_type == "application/json"
    assert snapshot.fetched_at == fetched_at
    assert snapshot.safe_request_metadata == {
        "source": "europe_pmc",
        "endpoint": "search",
        "query": "(breast cancer)",
        "cursor_mark": "*",
        "page_size": "100",
        "result_type": "core",
    }

    record = page.records[0]
    assert record.source_id == "MED:40123456"
    assert record.source_database == "MED"
    assert record.response_index == 0
    assert record.title == "Immune therapy outcomes"
    assert record.authors == ("Ada Example", "Grace Researcher")
    assert record.journal_name == "Journal of Medical Studies"
    assert record.publication_date is not None
    assert (record.publication_date.year, record.publication_date.month, record.publication_date.day) == (2025, 2, 3)
    assert record.abstract == "BACKGROUND Findings are useful."
    assert record.citation_count == 0
    assert record.publication_types == ("Journal Article", "Review")
    assert record.publication_status is None
    assert [(item.scheme, item.value) for item in record.identifiers] == [
        ("pmid", "40123456"),
        ("pmcid", "PMC1234567"),
        ("doi", "10.1234/example"),
    ]
    assert record.keywords == ("immunotherapy", "breast cancer", "Neoplasms")
    assert record.url == "https://europepmc.org/article/MED/40123456"

    asyncio.run(client.aclose())


def test_europe_pmc_missing_citation_count_stays_unknown():
    record = EuropePMCProvider._normalize_record(
        {"source": "MED", "id": "123", "title": "A paper"},
        response_index=0,
    )

    assert record.citation_count is None
    assert record.publication_types == ()
    assert record.publication_status is None


@pytest.mark.parametrize("value", [-1, "-1", "unknown", True])
def test_europe_pmc_rejects_invalid_citation_counts(value):
    with pytest.raises(ProviderResponseError, match="citedByCount.*non-negative integer"):
        EuropePMCProvider._normalize_record(
            {
                "source": "MED",
                "id": "123",
                "title": "A paper",
                "citedByCount": value,
            },
            response_index=0,
        )


def test_search_stops_if_service_repeats_cursor_for_nonempty_page():
    seen_cursors: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        cursor = request.url.params["cursorMark"]
        seen_cursors.append(cursor)
        return _json_response(
            request,
            {
                "hitCount": 1,
                "nextCursorMark": cursor,
                "resultList": {
                    "result": [
                        {"source": "MED", "id": "123", "title": "A paper"}
                    ]
                },
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = EuropePMCProvider(client=client)
    plan = provider.plan(SearchRequest(expression="dementia", page_size=200))

    page = asyncio.run(provider.search(plan, cursor="mark-1"))

    assert page.total_count == 1
    assert page.next_cursor is None
    assert len(page.records) == 1
    assert seen_cursors == ["mark-1"]

    asyncio.run(client.aclose())


def test_empty_page_stops_even_when_service_returns_a_new_cursor():
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            request,
            {"hitCount": 20, "nextCursorMark": "new-mark", "resultList": {"result": []}},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = EuropePMCProvider(client=client)
    plan = provider.plan(SearchRequest(expression="dementia"))

    page = asyncio.run(provider.search(plan))

    assert page.total_count == 20
    assert page.records == ()
    assert page.next_cursor is None

    asyncio.run(client.aclose())


def test_fetch_normalizes_article_endpoint_and_preserves_source_identity():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/europepmc/webservices/rest/article/PPR/PPR812345"
        return _json_response(
            request,
            {
                "result": {
                    "source": "PPR",
                    "id": "PPR812345",
                    "title": "A preprint",
                    "authorString": "One Author, Two Author",
                    "pubYear": "2024",
                    "abstractText": "Abstract text",
                }
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = EuropePMCProvider(client=client)

    result = asyncio.run(provider.fetch("PPR:PPR812345"))

    assert result.record.source_id == "PPR:PPR812345"
    assert result.record.source_database == "PPR"
    assert result.record.authors == ("One Author", "Two Author")
    assert result.record.publication_date is not None
    assert result.record.publication_date.year == 2024
    assert result.raw_response.safe_request_metadata["source_id"] == "PPR:PPR812345"
    assert result.record.url == "https://europepmc.org/article/PPR/PPR812345"

    asyncio.run(client.aclose())


def test_search_preserves_preprint_identity_and_year_only_date():
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(request, {
            "hitCount": 1,
            "resultList": {"result": [{
                "source": "PPR", "id": "PPR812345", "title": "A preprint", "pubYear": "2024",
            }]},
        })

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = EuropePMCProvider(client=client)
    page = asyncio.run(provider.search(provider.plan(SearchRequest("rare disease"))))

    assert page.records[0].source_id == "PPR:PPR812345"
    assert page.records[0].source_database == "PPR"
    assert page.records[0].identifiers == ()
    assert page.records[0].publication_date.year == 2024
    assert page.records[0].publication_date.month is None
    assert page.records[0].publication_date.day is None
    asyncio.run(client.aclose())


def test_fetch_rejects_a_different_source_record():
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            request,
            {"result": {"source": "MED", "id": "other", "title": "Wrong record"}},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = EuropePMCProvider(client=client)
    with pytest.raises(ProviderResponseError, match="different source record"):
        asyncio.run(provider.fetch("MED:42224955"))
    asyncio.run(client.aclose())


def test_search_rejects_malformed_payload_and_http_errors():
    async def run_with(handler):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = EuropePMCProvider(client=client)
        try:
            return await provider.search(
                provider.plan(SearchRequest(expression="heart failure"))
            )
        finally:
            await client.aclose()

    def malformed_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, content=b"{broken")

    with pytest.raises(ProviderResponseError, match="invalid JSON"):
        asyncio.run(run_with(malformed_handler))

    def failing_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, request=request)

    with pytest.raises(ProviderRequestError, match="HTTP 400"):
        asyncio.run(run_with(failing_handler))


def test_plan_rejects_page_sizes_over_europe_pmc_limit_and_search_rejects_foreign_plan():
    provider = EuropePMCProvider()
    with pytest.raises(ValueError, match="cannot exceed 1000"):
        provider.plan(SearchRequest(expression="stroke", page_size=1001))

    foreign_plan = ProviderQueryPlan(
        provider="pubmed",
        translated_query="stroke",
        translation_version="1",
    )
    with pytest.raises(ValueError, match="not europe_pmc"):
        asyncio.run(provider.search(foreign_plan))

    asyncio.run(provider.aclose())


def test_plan_reports_unsupported_false_open_access_and_open_ended_date_filters():
    provider = EuropePMCProvider()

    open_access_plan = provider.plan(
        SearchRequest(
            expression="oncology",
            filters=SearchFilters(open_access_only=False),
        )
    )
    from_only_plan = provider.plan(
        SearchRequest(
            expression="oncology",
            filters=SearchFilters(published_from=date(2024, 1, 1)),
        )
    )

    assert open_access_plan.unsupported_filters == ("open_access_only",)
    assert "OPEN_ACCESS" not in open_access_plan.translated_query
    assert from_only_plan.unsupported_filters == ("published_from",)
    assert "FIRST_PDATE:" not in from_only_plan.translated_query

    asyncio.run(provider.aclose())
