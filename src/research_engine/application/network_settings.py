"""UI-neutral, opt-in proxy configuration for provider requests."""

from __future__ import annotations

from research_engine.ports.network_settings import NetworkSettings, NetworkSettingsStore


class NetworkSettingsService:
    def __init__(self, store: NetworkSettingsStore) -> None:
        self._store = store

    def get(self) -> NetworkSettings:
        return self._store.load()

    def update(self, *, proxy_enabled: bool, proxy_url: str) -> NetworkSettings:
        if not isinstance(proxy_url, str):
            raise ValueError("proxy_url must be a string")
        settings = NetworkSettings(proxy_enabled, proxy_url.strip())
        self._store.save(settings)
        return settings
