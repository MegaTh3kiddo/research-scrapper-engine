from datetime import datetime, timezone

import pytest

from research_engine.application.discovery import (
    DiscoveryService,
    PaginationError,
    UnsupportedFiltersError,
)
from research_engine.application.provider_registry import ProviderRegistry
from research_engine.ports.providers import (
    ProviderQueryPlan,
    ProviderRecord,
    RawResponse,
    SearchPage,
    SearchRequest,
)


class FakeProvider:
    name = "fake"

    def __init__(self, *, unsupported=(), repeat=False):
        self.unsupported = unsupported
        self.repeat = repeat
        self.cursors = []

    def plan(self, request):
        return ProviderQueryPlan("fake", request.expression, "1", self.unsupported)

    async def search(self, plan, cursor=None):
        self.cursors.append(cursor)
        number = len(self.cursors)
        return SearchPage(
            records=(ProviderRecord(str(number), "Title", "FAKE"),),
            raw_responses=(RawResponse(b"{}", "application/json", datetime.now(timezone.utc), {}),),
            next_cursor="repeat" if self.repeat else (str(number) if number < 2 else None),
            total_count=2,
        )


@pytest.mark.asyncio
async def test_discovery_executes_pages_and_returns_provenance():
    provider = FakeProvider()
    service = DiscoveryService(ProviderRegistry([provider]))

    result = await service.execute("fake", SearchRequest("cancer"), max_pages=3)

    assert provider.cursors == [None, "1"]
    assert result.records_found == 2
    assert result.next_cursor is None
    assert len(result.pages[0].raw_responses) == 1


@pytest.mark.asyncio
async def test_discovery_rejects_unsupported_filters_before_network():
    provider = FakeProvider(unsupported=("open_access_only",))
    service = DiscoveryService(ProviderRegistry([provider]))

    with pytest.raises(UnsupportedFiltersError, match="open_access_only"):
        await service.execute("fake", SearchRequest("cancer"))
    assert provider.cursors == []


@pytest.mark.asyncio
async def test_discovery_rejects_repeated_cursor():
    provider = FakeProvider(repeat=True)
    service = DiscoveryService(ProviderRegistry([provider]))

    with pytest.raises(PaginationError):
        await service.execute("fake", SearchRequest("cancer"), max_pages=2, cursor="repeat")


def test_search_page_requires_record_provenance_to_point_to_a_snapshot():
    snapshot = RawResponse(b"{}", "application/json", datetime.now(timezone.utc), {})

    with pytest.raises(ValueError, match="response_index"):
        SearchPage(
            records=(ProviderRecord("id", "Title", "FAKE", response_index=1),),
            raw_responses=(snapshot,),
        )
