from types import SimpleNamespace

import pytest

from research_engine.application.publication_links import publication_link
from research_engine.domain.value_objects import Identifier


@pytest.mark.parametrize("url", [
    "file:///C:/Windows/notepad.exe", "javascript:alert(1)", "custom:paper",
    "https://user:password@example.org/paper", "https://example.org:99999/paper",
    "https://exa mple.org/paper", "https://example.org/\npaper", "https://[bad/paper",
])
def test_untrusted_publication_links_do_not_open_local_files_or_custom_protocols(url):
    detail = SimpleNamespace(url=url, source_metadata=(), identifiers=())
    assert publication_link(detail) is None


def test_links_prefer_safe_canonical_then_source_then_identifier_fallbacks():
    detail = SimpleNamespace(url="file:///unsafe", source_metadata=(SimpleNamespace(url="https://publisher.example/paper"),), identifiers=())
    assert publication_link(detail) == "https://publisher.example/paper"
    detail.url = "https://doi.org/10.1000/abc"
    assert publication_link(detail) == detail.url
    detail.url = None
    detail.source_metadata = ()
    detail.identifiers = (Identifier("doi", "10.1000/ABC"),)
    assert publication_link(detail) == "https://doi.org/10.1000/abc"
    assert publication_link(None) is None
