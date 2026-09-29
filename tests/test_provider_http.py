import asyncio
import time
import traceback

import httpx
import pytest

from research_engine.infrastructure.providers.http import (
    ProviderRequestError,
    RateLimitedHTTPClient,
)


@pytest.mark.asyncio
async def test_retryable_status_recovers():
    statuses = iter([503, 200])
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(next(statuses), headers={"Retry-After": "0"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        fetcher = RateLimitedHTTPClient(
            "https://example.org/api", min_interval=0, max_retries=1, client=client
        )
        response = await fetcher.get("search", params={"query": "cancer"})

    assert response.status_code == 200
    assert len(requests) == 2
    assert requests[0].url.params["query"] == "cancer"


@pytest.mark.asyncio
async def test_nonretryable_error_does_not_repeat():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(400)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        fetcher = RateLimitedHTTPClient("https://example.org", min_interval=0, client=client)
        with pytest.raises(ProviderRequestError, match="HTTP 400"):
            await fetcher.get("search")

    assert len(requests) == 1


@pytest.mark.asyncio
async def test_network_failure_is_wrapped_without_request_details():
    secret = "secret-api-key"

    def handler(request):
        raise httpx.ConnectError(secret, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        fetcher = RateLimitedHTTPClient(
            "https://example.org", min_interval=0, max_retries=0, client=client
        )
        with pytest.raises(ProviderRequestError) as error:
            await fetcher.get("search", params={"api_key": secret})

    assert secret not in "".join(traceback.format_exception(error.value))


@pytest.mark.asyncio
async def test_long_retry_after_is_not_ignored():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(429, headers={"Retry-After": "3600"})
        )
    ) as client:
        fetcher = RateLimitedHTTPClient("https://example.org", min_interval=0, client=client)
        with pytest.raises(ProviderRequestError, match="longer than 60 seconds"):
            await fetcher.get("search")


@pytest.mark.asyncio
async def test_retry_after_cools_down_other_concurrent_requests():
    first_seen = asyncio.Event()
    request_times = []

    def handler(request):
        request_times.append(time.monotonic())
        if len(request_times) == 1:
            first_seen.set()
            return httpx.Response(429, headers={"Retry-After": "0.1"})
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        fetcher = RateLimitedHTTPClient(
            "https://example.org", min_interval=0, max_retries=1, client=client
        )
        first = asyncio.create_task(fetcher.get("search"))
        await first_seen.wait()
        await asyncio.sleep(0.01)
        second = asyncio.create_task(fetcher.get("search"))
        await asyncio.gather(first, second)

    assert len(request_times) == 3
    assert request_times[1] - request_times[0] >= 0.08


def test_owned_http_client_uses_selected_proxy_and_ignores_environment(monkeypatch):
    calls = []

    class StubClient:
        def __init__(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", StubClient)
    RateLimitedHTTPClient("https://example.org", min_interval=0, proxy_url=None)
    RateLimitedHTTPClient(
        "https://example.org", min_interval=0,
        proxy_url="socks5h://127.0.0.1:10808",
    )

    assert calls[0]["proxy"] is None
    assert calls[1]["proxy"] == "socks5h://127.0.0.1:10808"
    assert all(call["trust_env"] is False for call in calls)


def test_injected_http_client_cannot_silently_ignore_proxy():
    with pytest.raises(ValueError, match="injected HTTP client"):
        RateLimitedHTTPClient(
            "https://example.org", min_interval=0,
            client=object(), proxy_url="socks5h://localhost:10808",
        )
