"""Atomic local JSON persistence for non-secret network settings."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from research_engine.ports.network_settings import NetworkSettings


class LocalNetworkSettingsStore:
    def __init__(self, data_directory: Path) -> None:
        self._path = Path(data_directory) / "network-settings.json"

    def load(self) -> NetworkSettings:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return NetworkSettings()
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("cannot read network settings") from exc
        if not isinstance(data, dict) or set(data) != {"proxy_enabled", "proxy_url"}:
            raise ValueError("invalid network settings file")
        return NetworkSettings(data["proxy_enabled"], data["proxy_url"])

    def save(self, settings: NetworkSettings) -> None:
        if not isinstance(settings, NetworkSettings):
            raise TypeError("settings must be NetworkSettings")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=".network-settings-", dir=self._path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(
                    {"proxy_enabled": settings.proxy_enabled, "proxy_url": settings.proxy_url},
                    output,
                )
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self._path)
        finally:
            temporary.unlink(missing_ok=True)
