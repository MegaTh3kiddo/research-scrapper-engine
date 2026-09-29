import pytest

from research_engine.application.provider_registry import ProviderRegistry


class FakeProvider:
    def __init__(self, name: str) -> None:
        self.name = name


def test_registry_registers_and_returns_provider_by_name():
    provider = FakeProvider("PubMed")
    registry = ProviderRegistry([provider])

    assert registry.get("PubMed") is provider
    assert registry.names() == ("PubMed",)


def test_registry_rejects_duplicate_provider_names():
    registry = ProviderRegistry([FakeProvider("PubMed")])

    with pytest.raises(ValueError, match="already registered: PubMed"):
        registry.register(FakeProvider("PubMed"))
