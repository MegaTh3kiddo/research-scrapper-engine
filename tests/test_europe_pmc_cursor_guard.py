from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from research_engine.application.discovery import DiscoveryService, PaginationError
from research_engine.application.provider_registry import ProviderRegistry
from research_engine.infrastructure.providers.europe_pmc import EuropePMCProvider
from research_engine.ports.providers import SearchRequest


def test_europe_pmc_full_page_repeating_cursor_is_rejected_before_another_request():
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        cursor = request.url.params["cursorMark"]
        requests.append(cursor)
        return httpx.Response(
            200,
            request=request,
            headers={"content-type": "application/json"},
            content=json.dumps({
                "hitCount": 2,
                "nextCursorMark": cursor,
                "resultList": {"result": [
                    {"source": "MED", "id": "123", "title": "One paper"},
                ]},
            }).encode(),
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = EuropePMCProvider(client=client)
    service = DiscoveryService(ProviderRegistry((provider,)))

    async def run():
        try:
            await service.execute(
                provider.name, SearchRequest("dementia", page_size=1),
                max_pages=5, cursor="stalled-mark",
            )
        finally:
            await provider.aclose()

    with pytest.raises(PaginationError, match="repeated a search cursor"):
        asyncio.run(run())

    assert requests == ["stalled-mark"]
