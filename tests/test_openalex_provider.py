import asyncio
from datetime import date, datetime, timezone
import json

import httpx
import pytest

from research_engine.domain.value_objects import SearchFilters
from research_engine.infrastructure.providers.http import (
    ProviderRequestError,
    ProviderResponseError,
)
from research_engine.infrastructure.providers.openalex import OpenAlexProvider
from research_engine.ports.providers import ProviderQueryPlan, SearchRequest


def _response(request: httpx.Request, payload: object) -> httpx.Response:
    return httpx.Response(
        200,
        request=request,
        headers={"content-type": "application/json; charset=utf-8"},
        content=json.dumps(payload).encode(),
    )


def _work(**overrides: object) -> dict[str, object]:
    work: dict[str, object] = {
        "id": "https://openalex.org/W1234567890",
        "title": "A medical paper",
        "doi": "https://doi.org/10.1234/example",
        "ids": {
            "openalex": "https://openalex.org/W1234567890",
            "doi": "https://doi.org/10.1234/example",
            "pmid": "https://pubmed.ncbi.nlm.nih.gov/40123456/",
            "pmcid": "https://pmc.ncbi.nlm.nih.gov/articles/PMC7654321/",
        },
        "publication_date": "2025-02-03",
        "publication_year": 2025,
        "type": "article",
        "cited_by_count": 0,
        "authorships": [
            {"author": {"display_name": "Ada Example"}},
            {"author": {"display_name": "Grace Researcher"}},
        ],
        "primary_location": {
            "landing_page_url": "https://publisher.example/paper",
            "source": {"display_name": "Journal of Medical Studies"},
        },
        "abstract_inverted_index": {
            "Findings": [4],
            "Despite": [0],
            "are": [5],
            "interest": [2],
            "growing": [1],
            "in": [3],
        },
        "keywords": [{"display_name": "immunotherapy"}],
        "topics": [{"display_name": "Oncology"}],
        "mesh": [{"display_name": "Neoplasms"}],
    }
    work.update(overrides)
    return work


def test_capabilities_and_plan_translate_supported_filters_without_weakening_others():
    provider = OpenAlexProvider()
    request = SearchRequest(
        expression='Alzheimer disease AND "artificial intelligence"',
        filters=SearchFilters(
            published_from=date(2024, 1, 2),
            published_to=date(2025, 6, 30),
            languages=("eng", "French"),
            publication_types=("Review", "Clinical Trial"),
            humans_only=True,
            open_access_only=True,
        ),
        page_size=75,
    )

    plan = provider.plan(request)

    assert provider.name == "openalex"
    assert provider.capabilities.supports_boolean_queries is True
    assert plan.translation_version == "openalex-boolean-v2"
    assert plan.translated_query == request.expression
    assert plan.parameters == {
        "search": request.expression,
        "per_page": "75",
        "filter": (
            "from_publication_date:2024-01-02,to_publication_date:2025-06-30,"
            "language:en|fr,open_access.is_oa:true"
        ),
    }
    assert plan.unsupported_filters == ("publication_types", "humans_only")
    with pytest.raises(ValueError, match="unsupported filters"):
        asyncio.run(provider.search(plan))
    asyncio.run(provider.aclose())


def test_boolean_keywords_are_normalized_but_quoted_terms_are_preserved():
    provider = OpenAlexProvider()
    plan = provider.plan(
        SearchRequest(expression='cancer and "not related" oR therapy')
    )
    assert plan.translated_query == '(cancer AND "not related") OR therapy'
    asyncio.run(provider.aclose())


@pytest.mark.parametrize(
    "expression",
    ["cancer[tiab]", '"breast cancer"[Title/Abstract]', "therapy [MeSH Terms]"],
)
def test_pubmed_field_syntax_is_rejected_instead_of_sent_as_an_openalex_query(expression):
    provider = OpenAlexProvider()
    with pytest.raises(ValueError, match="PubMed bracket field tags"):
        provider.plan(SearchRequest(expression=expression))
    asyncio.run(provider.aclose())


def test_search_preserves_response_and_normalizes_work_metadata_and_key_is_not_provenance():
    payload = {
        "meta": {"count": 8, "next_cursor": "next-token"},
        "results": [_work()],
    }
    body = json.dumps(payload).encode()
    captured: list[httpx.Request] = []
    fetched_at = datetime(2025, 3, 1, tzinfo=timezone.utc)

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        assert request.url.path == "/works"
        assert request.url.params["search"] == 'breast cancer AND "immunotherapy"'
        assert request.url.params["cursor"] == "*"
        assert request.url.params["per_page"] == "50"
        assert request.url.params["api_key"] == "secret-test-key"
        return httpx.Response(
            200,
            request=request,
            headers={"content-type": "application/json; charset=utf-8"},
            content=body,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAlexProvider(
        client=client,
        api_key=" secret-test-key ",
        email="researcher@example.org",
        clock=lambda: fetched_at,
    )
    plan = provider.plan(
        SearchRequest(expression='breast cancer AND "immunotherapy"', page_size=50)
    )

    page = asyncio.run(provider.search(plan))

    assert page.total_count == 8
    assert page.next_cursor == "next-token"
    snapshot = page.raw_responses[0]
    assert snapshot.body == body
    assert snapshot.media_type == "application/json"
    assert snapshot.fetched_at == fetched_at
    assert snapshot.safe_request_metadata == {
        "source": "openalex",
        "endpoint": "works",
        "query": 'breast cancer AND "immunotherapy"',
        "filter": "",
        "cursor": "*",
        "page_size": "50",
    }
    assert "secret-test-key" not in repr(snapshot.safe_request_metadata)

    record = page.records[0]
    assert record.source_id == "W1234567890"
    assert record.source_database == "OpenAlex"
    assert record.response_index == 0
    assert record.title == "A medical paper"
    assert [(identifier.scheme, identifier.value) for identifier in record.identifiers] == [
        ("openalex", "W1234567890"),
        ("doi", "10.1234/example"),
        ("pmid", "40123456"),
        ("pmcid", "PMC7654321"),
    ]
    assert record.authors == ("Ada Example", "Grace Researcher")
    assert record.journal_name == "Journal of Medical Studies"
    assert record.publication_date is not None
    assert (
        record.publication_date.year,
        record.publication_date.month,
        record.publication_date.day,
    ) == (2025, 2, 3)
    assert record.abstract == "Despite growing interest in Findings are"
    assert record.url == "https://publisher.example/paper"
    assert record.keywords == ("immunotherapy", "Oncology", "Neoplasms")
    assert record.citation_count == 0
    assert record.publication_types == ("article",)
    assert record.publication_status is None
    assert "api_key" not in snapshot.safe_request_metadata
    assert "mailto" not in captured[0].url.params
    asyncio.run(client.aclose())


@pytest.mark.parametrize("count", [None, -1, "-1", "unknown", True, 1.5])
def test_openalex_citation_count_is_unknown_when_missing_and_rejects_invalid_values(count):
    work = _work(cited_by_count=count)

    def handler(request: httpx.Request) -> httpx.Response:
        return _response(
            request,
            {"meta": {"count": 1, "next_cursor": None}, "results": [work]},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAlexProvider(client=client)
    operation = provider.search(provider.plan(SearchRequest(expression="cancer")))
    try:
        if count is None:
            page = asyncio.run(operation)
            assert page.records[0].citation_count is None
        else:
            with pytest.raises(ProviderResponseError, match="cited_by_count"):
                asyncio.run(operation)
    finally:
        asyncio.run(client.aclose())


@pytest.mark.parametrize(
    ("raw_status", "expected_status"),
    [(True, "retracted"), (False, None), (None, None)],
)
def test_openalex_exposes_only_explicit_retraction_status(raw_status, expected_status):
    work = _work(is_retracted=raw_status)

    def handler(request: httpx.Request) -> httpx.Response:
        return _response(
            request,
            {"meta": {"count": 1, "next_cursor": None}, "results": [work]},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAlexProvider(client=client)
    page = asyncio.run(
        provider.search(provider.plan(SearchRequest(expression="cancer")))
    )
    assert page.records[0].publication_status == expected_status
    asyncio.run(client.aclose())


def test_openalex_rejects_malformed_retraction_status():
    work = _work(is_retracted="yes")

    def handler(request: httpx.Request) -> httpx.Response:
        return _response(
            request,
            {"meta": {"count": 1, "next_cursor": None}, "results": [work]},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAlexProvider(client=client)
    with pytest.raises(ProviderResponseError, match="is_retracted"):
        asyncio.run(provider.search(provider.plan(SearchRequest(expression="cancer"))))
    asyncio.run(client.aclose())


def test_search_cursor_is_forwarded_and_missing_key_keeps_anonymous_request():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _response(
            request,
            {"meta": {"count": 1, "next_cursor": "cursor-2"}, "results": [_work()]},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAlexProvider(client=client)
    plan = provider.plan(SearchRequest(expression="oncology"))

    page = asyncio.run(provider.search(plan, cursor="cursor-1"))

    assert seen[0].url.params["cursor"] == "cursor-1"
    assert "api_key" not in seen[0].url.params
    assert page.next_cursor == "cursor-2"
    asyncio.run(client.aclose())


def test_repeated_cursor_is_returned_so_application_layer_can_fail_the_run():
    def handler(request: httpx.Request) -> httpx.Response:
        cursor = request.url.params["cursor"]
        return _response(
            request,
            {"meta": {"count": 1, "next_cursor": cursor}, "results": [_work()]},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAlexProvider(client=client)
    page = asyncio.run(
        provider.search(
            provider.plan(SearchRequest(expression="cancer")), cursor="repeat-me"
        )
    )
    assert page.next_cursor == "repeat-me"
    asyncio.run(client.aclose())


def test_fetch_normalizes_source_id_and_returns_snapshot():
    body = json.dumps(_work()).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/works/W1234567890"
        assert "api_key" not in request.url.params
        return httpx.Response(200, request=request, content=body)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAlexProvider(client=client)

    result = asyncio.run(provider.fetch("https://openalex.org/w1234567890"))

    assert result.record.source_id == "W1234567890"
    assert result.raw_response.body == body
    assert result.raw_response.safe_request_metadata["source_id"] == "W1234567890"
    with pytest.raises(ValueError, match="W-prefixed"):
        asyncio.run(provider.fetch("A123"))
    asyncio.run(client.aclose())


def test_secret_is_absent_from_http_errors_and_invalid_json_is_rejected():
    def error_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(error_handler))
    provider = OpenAlexProvider(client=client, api_key="never-show-this-key")
    plan = provider.plan(SearchRequest(expression="cancer"))
    with pytest.raises(ProviderRequestError) as error:
        asyncio.run(provider.search(plan))
    assert "never-show-this-key" not in str(error.value)
    assert "never-show-this-key" not in repr(error.value.__cause__)
    asyncio.run(client.aclose())

    def invalid_json(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, content=b"not-json")

    bad_client = httpx.AsyncClient(transport=httpx.MockTransport(invalid_json))
    bad_provider = OpenAlexProvider(client=bad_client)
    with pytest.raises(ProviderResponseError, match="invalid JSON"):
        asyncio.run(bad_provider.search(bad_provider.plan(SearchRequest(expression="x"))))
    asyncio.run(bad_client.aclose())


def test_unknown_language_or_publication_type_is_reported_not_partially_applied():
    provider = OpenAlexProvider()
    plan = provider.plan(
        SearchRequest(
            expression="cancer",
            filters=SearchFilters(
                languages=("eng", "Klingon"),
                publication_types=("Review", "Clinical Trial"),
            ),
        )
    )
    assert plan.unsupported_filters == ("languages", "publication_types")
    assert "language:" not in plan.parameters.get("filter", "")
    assert "type:" not in plan.parameters.get("filter", "")
    asyncio.run(provider.aclose())


def test_malformed_openalex_records_are_not_silently_dropped():
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: _response(
                request,
                {"meta": {"count": 1, "next_cursor": None}, "results": [{"title": "Missing ID"}]},
            )
        )
    )
    provider = OpenAlexProvider(client=client)
    with pytest.raises(ProviderResponseError, match="W-prefixed"):
        asyncio.run(provider.search(provider.plan(SearchRequest(expression="cancer"))))
    asyncio.run(client.aclose())


def test_page_size_over_api_limit_and_wrong_plan_are_rejected():
    provider = OpenAlexProvider()
    with pytest.raises(ValueError, match="cannot exceed 100"):
        provider.plan(SearchRequest(expression="cancer", page_size=101))
    with pytest.raises(ValueError, match="not openalex"):
        asyncio.run(
            provider.search(ProviderQueryPlan("other", "cancer", "v1"))
        )
    asyncio.run(provider.aclose())
