"""Safe browser links from untrusted provider metadata, reusable by any client."""

from urllib.parse import quote, urlsplit

from research_engine.domain.identity import normalize_identifiers
from research_engine.ports.projects import PaperDetail


def publication_link(detail: PaperDetail | None) -> str | None:
    if detail is None:
        return None
    urls = (detail.url, *(source.url for source in detail.source_metadata))
    for url in urls:
        if not isinstance(url, str) or any(ord(char) < 32 for char in url):
            continue
        try:
            parsed = urlsplit(url.strip())
            if (parsed.scheme in {"http", "https"} and parsed.hostname
                and not any(char.isspace() for char in parsed.netloc)
                and parsed.username is None and parsed.password is None):
                parsed.port  # Validate malformed or out-of-range ports as well.
                return url.strip()
        except ValueError:
            continue
    for token in normalize_identifiers(detail.identifiers):
        if token.scheme == "doi":
            return "https://doi.org/" + quote(token.value, safe="/")
        if token.scheme == "pmid":
            return f"https://pubmed.ncbi.nlm.nih.gov/{token.value}/"
    return None
