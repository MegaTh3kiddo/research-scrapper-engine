"""Conservative review signatures. Similarity never establishes identity."""

import hashlib
import json
import re
import unicodedata

from research_engine.domain.identity import normalize_identifiers, normalize_provider_identifier
from research_engine.domain.value_objects import Identifier


def assertion_signature(provider: str, source_id: str, identifiers: tuple[Identifier, ...]) -> str:
    tokens = set(normalize_identifiers(identifiers, provider=provider))
    token = normalize_provider_identifier(provider, source_id)
    if token is None:
        raise ValueError("source identifier is required")
    tokens.add(token)
    encoded = json.dumps([item.key for item in sorted(tokens)], separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def possible_duplicate_key(title: str, publication_year: int | None) -> str | None:
    normalized = " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", title).casefold()))
    if publication_year is None or len(normalized) < 40:
        return None
    encoded = f"title-year-v1\n{publication_year}\n{normalized}"
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
