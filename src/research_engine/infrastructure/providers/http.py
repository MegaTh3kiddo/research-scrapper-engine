"""Conservative, retrying HTTP client shared by literature provider adapters."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import time
import logging
import re
from typing import Mapping

import httpx


class ProviderRequestError(RuntimeError):
    """The source could not be reached or returned an unsuccessful response."""


class ProviderResponseError(RuntimeError):
    """The source returned data that did not satisfy its expected schema."""


class ProviderLimitError(RuntimeError):
    """The source's documented pagination limit was reached."""


class _CredentialURLFilter(logging.Filter):
    """HTTPX logs request URLs at INFO, including query credentials."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        sanitized = re.sub(
            r"([?&](?:api_key|apikey|access_token)=)[^&\s\"']*",
            r"\1[REDACTED]", message, flags=re.IGNORECASE,
        )
        if sanitized != message:
            record.msg = sanitized
            record.args = ()
        return True


_CREDENTIAL_FILTER = _CredentialURLFilter()


class RateLimitedHTTPClient:
    """Per-provider paced GET requests with bounded retries for transient failures.

    Pass a shared instance for one provider to coordinate its requests. Callers
    supply credentials separately; this class never logs or stores parameters.
    """

    RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})

    def __init__(
        self,
        base_url: str,
        *,
        min_interval: float,
        client: httpx.AsyncClient | None = None,
        proxy_url: str | None = None,
        max_retries: int = 3,
        timeout: float = 20.0,
    ) -> None:
        if min_interval < 0 or max_retries < 0:
            raise ValueError("min_interval and max_retries must be non-negative")
        if client is not None and proxy_url is not None:
            raise ValueError("cannot configure a proxy with an injected HTTP client")
        http_logger = logging.getLogger("httpx")
        if _CREDENTIAL_FILTER not in http_logger.filters:
            http_logger.addFilter(_CREDENTIAL_FILTER)
        self._client = client or httpx.AsyncClient(
            base_url=base_url, timeout=timeout, proxy=proxy_url, trust_env=False,
        )
        self._owns_client = client is None
        self._base_url = base_url.rstrip("/") + "/"
        self._min_interval = min_interval
        self._max_retries = max_retries
        self._lock = asyncio.Lock()
        self._next_request_at = 0.0

    async def _pace(self) -> None:
        async with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next_request_at - now)
            if delay:
                await asyncio.sleep(delay)
            self._next_request_at = time.monotonic() + self._min_interval

    async def _cooldown(self, seconds: float) -> None:
        async with self._lock:
            self._next_request_at = max(
                self._next_request_at, time.monotonic() + seconds
            )

    async def get(self, path: str, *, params: Mapping[str, str] | None = None) -> httpx.Response:
        url = self._base_url + path.lstrip("/")
        for attempt in range(self._max_retries + 1):
            await self._pace()
            try:
                response = await self._client.get(url, params=params)
            except httpx.RequestError:
                if attempt == self._max_retries:
                    # httpx exceptions may embed a URL containing API keys.
                    raise ProviderRequestError("literature source request failed") from None
            else:
                if response.status_code < 400:
                    return response
                if response.status_code not in self.RETRYABLE_STATUSES or attempt == self._max_retries:
                    raise ProviderRequestError(
                        f"literature source returned HTTP {response.status_code}"
                    )
                retry_after = _retry_after_seconds(response.headers.get("Retry-After"))
                if retry_after is not None:
                    if retry_after > 60.0:
                        raise ProviderRequestError(
                            "literature source requested a retry delay longer than 60 seconds"
                        )
                    await self._cooldown(retry_after)
                    continue
                await self._cooldown(min(2**attempt, 8))
                continue
            await asyncio.sleep(min(2**attempt, 8))
        raise AssertionError("retry loop must return or raise")

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            instant = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=timezone.utc)
        return max(0.0, (instant - datetime.now(timezone.utc)).total_seconds())
