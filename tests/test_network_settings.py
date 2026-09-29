from __future__ import annotations

import json
import os
import asyncio

import pytest

from research_engine.application.network_settings import NetworkSettingsService
from research_engine.infrastructure.network_settings import LocalNetworkSettingsStore
from research_engine.ports.network_settings import NetworkSettings
from research_engine.desktop.bootstrap import create_desktop_services
from research_engine.infrastructure import bootstrap


@pytest.mark.parametrize("url", [
    "", "ftp://localhost:10808", "socks5h://localhost",
    "socks5h://user:pass@localhost:10808", "http://localhost:8080/path",
    "socks5h://localhost:10808?key=secret", "socks5h://localhost:99999",
])
def test_invalid_enabled_proxy_is_rejected(url):
    with pytest.raises(ValueError):
        NetworkSettings(True, url)


def test_proxy_settings_persist_and_disable_without_losing_url(tmp_path):
    service = NetworkSettingsService(LocalNetworkSettingsStore(tmp_path))
    assert service.get() == NetworkSettings()

    configured = service.update(
        proxy_enabled=True, proxy_url=" socks5h://127.0.0.1:10808 ",
    )
    assert configured.active_proxy_url == "socks5h://127.0.0.1:10808"
    path = tmp_path / "network-settings.json"
    assert json.loads(path.read_text())["proxy_enabled"] is True
    if os.name == "posix":
        assert path.stat().st_mode & 0o077 == 0
    reopened = NetworkSettingsService(LocalNetworkSettingsStore(tmp_path))
    assert reopened.get() == configured

    disabled = reopened.update(proxy_enabled=False, proxy_url=configured.proxy_url)
    assert disabled.active_proxy_url is None
    assert disabled.proxy_url == configured.proxy_url


def test_corrupt_settings_do_not_silently_enable_or_disable_proxy(tmp_path):
    (tmp_path / "network-settings.json").write_text('{"proxy_enabled": "yes", "proxy_url": ""}')
    with pytest.raises(ValueError, match="proxy_enabled"):
        LocalNetworkSettingsStore(tmp_path).load()


def test_manual_and_scheduled_provider_factories_apply_current_proxy(tmp_path, monkeypatch):
    seen = []

    class StubProvider:
        def __init__(self, name, *, proxy_url):
            self.name = name
            seen.append((name, proxy_url))

        async def aclose(self):
            pass

    monkeypatch.setattr(bootstrap, "PubMedProvider", lambda **kwargs: StubProvider("pubmed", **kwargs))
    monkeypatch.setattr(bootstrap, "EuropePMCProvider", lambda **kwargs: StubProvider("europe_pmc", **kwargs))
    services = create_desktop_services(tmp_path)
    try:
        services.network_settings.update(
            proxy_enabled=True, proxy_url="socks5h://127.0.0.1:10808",
        )
        services.search_service()

        async def open_schedule():
            async with services.monitoring._search_factory():
                pass

        asyncio.run(open_schedule())
        assert seen == [
            ("pubmed", "socks5h://127.0.0.1:10808"),
            ("europe_pmc", "socks5h://127.0.0.1:10808"),
        ] * 2

        services.network_settings.update(
            proxy_enabled=False, proxy_url="socks5h://127.0.0.1:10808",
        )
        services.search_service()
        assert seen[-2:] == [("pubmed", None), ("europe_pmc", None)]
    finally:
        services.close()
