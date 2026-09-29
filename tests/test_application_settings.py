from __future__ import annotations

import json

import pytest

from research_engine.application.settings import ApplicationSettingsService
from research_engine.infrastructure.settings import LocalSettingsStore
from research_engine.ports.settings import ApplicationSettings


def test_defaults_are_available_for_a_new_install(tmp_path):
    service = ApplicationSettingsService(LocalSettingsStore(tmp_path))

    assert service.get().monitoring_interval_hours == 1
    assert service.get() == ApplicationSettings(
        discovery_sources=("pubmed", "europe_pmc"),
        monitoring_sources=("pubmed", "europe_pmc"),
    )


def test_unknown_sources_are_rejected_by_the_application_service(tmp_path):
    service = ApplicationSettingsService(LocalSettingsStore(tmp_path))

    with pytest.raises(ValueError, match="unknown literature source"):
        service.update(ApplicationSettings(discovery_sources=("made_up_source",)))
    with pytest.raises(ValueError, match="unknown literature source"):
        service.update(ApplicationSettings(monitoring_sources=("crossref", "unknown")))


@pytest.mark.parametrize("kwargs", [
    {"discovery_sources": []},
    {"discovery_sources": ["pubmed"]},
    {"discovery_sources": ("pubmed", "pubmed")},
    {"monitoring_sources": ("",)},
    {"page_size": True},
    {"page_size": 0},
    {"page_size": 101},
    {"max_pages": 0},
    {"monitoring_interval_hours": 721},
    {"export_include_excluded": 1},
    {"contact_email": "not-an-email"},
])
def test_settings_reject_invalid_values(kwargs):
    with pytest.raises(ValueError):
        ApplicationSettings(**kwargs)


def test_settings_round_trip_reload_and_atomic_replacement(tmp_path, monkeypatch):
    store = LocalSettingsStore(tmp_path)
    service = ApplicationSettingsService(store)
    first = ApplicationSettings(
        discovery_sources=("openalex", "crossref"),
        monitoring_sources=("crossref",),
        page_size=37,
        max_pages=9,
        monitoring_interval_hours=12,
        export_include_excluded=False,
        contact_email="research@example.org",
    )
    service.update(first)
    settings_path = tmp_path / "application-settings.json"
    assert LocalSettingsStore(tmp_path).load() == first
    assert list(tmp_path.glob(".application-settings-*")) == []

    # A failed atomic rename must preserve the previous complete settings file.
    import research_engine.infrastructure.settings as settings_module

    def fail_replace(*_args, **_kwargs):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(settings_module.os, "replace", fail_replace)
    second = ApplicationSettings(discovery_sources=("pubmed",))
    with pytest.raises(OSError, match="simulated replace failure"):
        store.save(second)
    monkeypatch.undo()

    assert LocalSettingsStore(tmp_path).load() == first
    assert list(tmp_path.glob(".application-settings-*")) == []


def test_api_keys_are_environment_or_session_only_and_session_clear_masks_environment(
    tmp_path, monkeypatch,
):
    secret = "test-openalex-secret-which-must-not-be-persisted"
    monkeypatch.setenv("OPENALEX_API_KEY", "environment-key")
    service = ApplicationSettingsService(LocalSettingsStore(tmp_path))

    assert service.api_key("openalex") == "environment-key"
    assert service.key_status("openalex") == "Environment key configured"
    service.set_session_key("openalex", secret)
    assert service.api_key("openalex") == secret
    assert service.key_status("openalex") == "Session key configured"

    # Empty session value is an explicit per-process opt-out of environment fallback.
    service.set_session_key("openalex", "")
    assert service.api_key("openalex") is None
    assert service.key_status("openalex") == "No key (explicitly cleared)"

    settings = ApplicationSettings()
    service.update(settings)
    serialized = (tmp_path / "application-settings.json").read_text(encoding="utf-8")
    assert secret not in serialized
    assert "environment-key" not in serialized
    assert set(json.loads(serialized)) == set(settings.__dataclass_fields__)
    assert LocalSettingsStore(tmp_path).load() == settings

    # The session-only clear does not survive a new service instance.
    reopened = ApplicationSettingsService(LocalSettingsStore(tmp_path))
    assert reopened.api_key("openalex") == "environment-key"


@pytest.mark.parametrize("source", ["crossref", "not-a-source"])
def test_sources_without_supported_keys_reject_session_credentials(tmp_path, source):
    service = ApplicationSettingsService(LocalSettingsStore(tmp_path))
    with pytest.raises(ValueError):
        service.set_session_key(source, "example-key")
