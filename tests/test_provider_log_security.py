import asyncio
import logging

import httpx

from research_engine.infrastructure.providers.http import RateLimitedHTTPClient


def test_httpx_info_request_logging_cannot_expose_query_api_keys(caplog, monkeypatch):
    # Alembic may disable already-created library loggers during other tests.
    # Explicitly emulate an embedding application enabling HTTPX request logs.
    monkeypatch.setattr(logging.getLogger("httpx"), "disabled", False)
    async def execute():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={}, request=request)
        )) as client:
            transport = RateLimitedHTTPClient("https://example.org", min_interval=0, client=client)
            await transport.get("works", params={"api_key": "not-for-logs/secret", "query": "medical"})

    with caplog.at_level(logging.INFO, logger="httpx"):
        asyncio.run(execute())
    assert "HTTP Request" in caplog.text
    assert "[REDACTED]" in caplog.text
    assert "not-for-logs" not in caplog.text
