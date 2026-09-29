"""Persistence contract for local literature-provider network settings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class NetworkSettings:
    proxy_enabled: bool = False
    proxy_url: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.proxy_enabled, bool):
            raise ValueError("proxy_enabled must be a boolean")
        if not isinstance(self.proxy_url, str):
            raise ValueError("proxy_url must be a string")
        value = self.proxy_url.strip()
        if self.proxy_enabled and not value:
            raise ValueError("enter a proxy URL before enabling the proxy")
        if not value:
            return
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError as exc:
            raise ValueError("invalid proxy URL") from exc
        if parsed.scheme.lower() not in {"http", "socks5", "socks5h"}:
            raise ValueError("proxy URL must use http, socks5, or socks5h")
        if not parsed.hostname or port is None or port < 1:
            raise ValueError("proxy URL needs a host and port")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("proxy credentials are not supported in local settings")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("proxy URL must not contain a path, query, or fragment")

    @property
    def active_proxy_url(self) -> str | None:
        return self.proxy_url.strip() if self.proxy_enabled else None


class NetworkSettingsStore(Protocol):
    def load(self) -> NetworkSettings: ...

    def save(self, settings: NetworkSettings) -> None: ...
