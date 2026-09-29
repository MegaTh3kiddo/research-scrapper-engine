"""UI-neutral source/workflow settings and non-persistent credential access."""

import os
from threading import RLock

from research_engine.application.source_catalog import LITERATURE_SOURCES
from research_engine.ports.settings import ApplicationSettings, SettingsStore


class ApplicationSettingsService:
    def __init__(self, store: SettingsStore) -> None:
        self._store = store
        self._keys: dict[str, str] = {}
        self._lock = RLock()

    def sources(self):
        return LITERATURE_SOURCES

    def get(self) -> ApplicationSettings:
        with self._lock:
            settings = self._store.load()
            self._validate(settings)
            return settings

    def update(self, settings: ApplicationSettings) -> ApplicationSettings:
        self._validate(settings)
        with self._lock:
            self._store.save(settings)
        return settings

    def _validate(self, settings: ApplicationSettings) -> None:
        if not isinstance(settings, ApplicationSettings):
            raise TypeError("settings must be ApplicationSettings")
        known = {source.name for source in self.sources()}
        if set(settings.discovery_sources + settings.monitoring_sources) - known:
            raise ValueError("unknown literature source in settings")

    def set_session_key(self, source_name: str, key: str) -> None:
        if not any(s.name == source_name and s.api_key_env for s in self.sources()):
            raise ValueError("source does not accept an API key")
        if not isinstance(key, str) or any(c.isspace() for c in key):
            raise ValueError("API key cannot contain whitespace")
        with self._lock:
            # An empty override explicitly clears both session and environment use.
            self._keys[source_name] = key

    def api_key(self, source_name: str) -> str | None:
        descriptor = next((s for s in self.sources() if s.name == source_name), None)
        if descriptor is None:
            raise ValueError("unknown literature source")
        with self._lock:
            value = self._keys.get(source_name)
            if value is None and descriptor.api_key_env:
                value = os.environ.get(descriptor.api_key_env)
            return value or None

    def key_status(self, source_name: str) -> str:
        with self._lock:
            if source_name in self._keys:
                return "Session key configured" if self._keys[source_name] else "No key (explicitly cleared)"
            return "Environment key configured" if self.api_key(source_name) else "No key configured"
