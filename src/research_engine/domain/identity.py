"""Pure identifier normalization and exact-match identity decisions.

This module deliberately has no persistence or provider dependencies. Applications
can use :class:`IdentifierToken`'s ``key`` directly in SQL lookups, then pass the
union of exact-token candidate article IDs to :func:`decide_identity_resolution`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Hashable, Iterable
from urllib.parse import unquote, urlsplit

from research_engine.domain.value_objects import Identifier


@dataclass(frozen=True, slots=True, order=True)
class IdentifierToken:
    """A normalized identifier suitable for an ``(scheme, value)`` SQL lookup."""

    scheme: str
    value: str

    def __post_init__(self) -> None:
        if not self.scheme or not self.value:
            raise ValueError("identifier token scheme and value must be non-empty")

    @property
    def key(self) -> tuple[str, str]:
        """Return the normalized composite key used by ``article_identifiers``."""

        return (self.scheme, self.value)


_DOI_URL = re.compile(r"^(?:https?://)?(?:dx\.)?doi\.org/", re.IGNORECASE)
_DOI_PREFIX = re.compile(r"^doi\s*:\s*", re.IGNORECASE)
_PMID_PREFIX = re.compile(r"^pmid\s*:\s*", re.IGNORECASE)
_PMCID_PREFIX = re.compile(r"^(?:pmcid|pmc)\s*:\s*", re.IGNORECASE)
_PROVIDER_SCHEMES = frozenset({"provider", "provider_id", "source_id"})


def normalize_identifier(
    scheme: str,
    value: str,
    *,
    provider: str | None = None,
) -> IdentifierToken | None:
    """Normalize a source identifier without discarding its original source value.

    DOI, PMID, and PMCID tokens use globally comparable canonical forms. Other
    schemes retain their trimmed value and case-normalized scheme; provider IDs
    should use :func:`normalize_provider_identifier` (or a provider scheme plus
    the ``provider`` argument) so the same local ID from two sources cannot match.

    Invalid or empty values return ``None`` so callers can retain the original
    source assertion separately without using it as an automatic merge key.
    """

    normalized_scheme = scheme.strip().casefold()
    raw_value = value.strip()
    if not normalized_scheme or not raw_value:
        return None

    if normalized_scheme in {"doi", "digital object identifier"}:
        normalized_value = _normalize_doi(raw_value)
        return IdentifierToken("doi", normalized_value) if normalized_value else None

    if normalized_scheme in {"pmid", "pubmed", "pubmed_id"}:
        normalized_value = _normalize_pmid(raw_value)
        return IdentifierToken("pmid", normalized_value) if normalized_value else None

    if normalized_scheme in {"pmcid", "pmc_id", "pmc"}:
        normalized_value = _normalize_pmcid(raw_value)
        return IdentifierToken("pmcid", normalized_value) if normalized_value else None

    if normalized_scheme in _PROVIDER_SCHEMES:
        if provider is None:
            return None
        return normalize_provider_identifier(provider, raw_value)

    # Provider-specific identifier schemes supplied by an adapter are namespaced
    # whenever the adapter context is available. This also supports IDs such as
    # OpenAlex's work ID without treating it as globally interchangeable with an
    # unrelated provider's similarly shaped ID.
    if provider is not None:
        return _provider_token(provider, normalized_scheme, raw_value)

    return IdentifierToken(normalized_scheme, raw_value)


def normalize_provider_identifier(provider: str, value: str) -> IdentifierToken | None:
    """Normalize a provider-local record ID in that provider's namespace."""

    raw_value = value.strip()
    if not raw_value:
        return None
    return _provider_token(provider, "id", raw_value)


def normalize_identifiers(
    identifiers: Iterable[Identifier],
    *,
    provider: str | None = None,
) -> tuple[IdentifierToken, ...]:
    """Normalize and deduplicate identifiers into a deterministic token tuple."""

    tokens = {
        token
        for identifier in identifiers
        if (
            token := normalize_identifier(
                identifier.scheme,
                identifier.value,
                provider=provider,
            )
        )
        is not None
    }
    return tuple(sorted(tokens))


class IdentityResolutionKind(StrEnum):
    NEW = "new"
    MATCH = "match"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class IdentityResolution:
    """The result of matching a record against all of its exact identifier tokens."""

    kind: IdentityResolutionKind
    candidate_article_ids: tuple[Hashable, ...]

    @property
    def article_id(self) -> Hashable | None:
        """Return the sole match, or ``None`` when the result is new/conflicted."""

        if self.kind is IdentityResolutionKind.MATCH:
            return self.candidate_article_ids[0]
        return None


def decide_identity_resolution(
    candidate_article_ids: Iterable[Hashable],
) -> IdentityResolution:
    """Choose new, match, or conflict from exact identifier lookup candidates.

    Repeated IDs from multiple tokens collapse to one candidate. No title or
    fuzzy similarity signal is accepted here, so uncertain matches stay separate.
    """

    candidates = tuple(sorted(set(candidate_article_ids), key=str))
    if not candidates:
        kind = IdentityResolutionKind.NEW
    elif len(candidates) == 1:
        kind = IdentityResolutionKind.MATCH
    else:
        kind = IdentityResolutionKind.CONFLICT
    return IdentityResolution(kind=kind, candidate_article_ids=candidates)


def _normalize_doi(value: str) -> str | None:
    candidate = value.strip().strip("<>")
    candidate = _DOI_PREFIX.sub("", candidate).strip()
    if _DOI_URL.match(candidate):
        parsed = urlsplit(candidate if "://" in candidate else f"https://{candidate}")
        candidate = parsed.path.lstrip("/")
    candidate = unquote(candidate).casefold()
    # DOI strings have a registrant prefix and suffix separated by a slash.
    if not candidate.startswith("10.") or "/" not in candidate:
        return None
    prefix, suffix = candidate.split("/", 1)
    if not prefix[3:].isdigit() or not suffix.strip():
        return None
    return candidate


def _normalize_pmid(value: str) -> str | None:
    candidate = _PMID_PREFIX.sub("", value.strip())
    if candidate.casefold().startswith(("http://", "https://")):
        parsed = urlsplit(candidate)
        if parsed.hostname and parsed.hostname.casefold() == "pubmed.ncbi.nlm.nih.gov":
            candidate = parsed.path.strip("/").split("/", 1)[0]
    return candidate if candidate.isdigit() else None


def _normalize_pmcid(value: str) -> str | None:
    candidate = _PMCID_PREFIX.sub("", value.strip())
    if candidate.casefold().startswith(("http://", "https://")):
        parsed = urlsplit(candidate)
        if parsed.hostname and parsed.hostname.casefold() in {
            "pmc.ncbi.nlm.nih.gov",
            "www.ncbi.nlm.nih.gov",
        }:
            candidate = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    if candidate[:3].casefold() == "pmc":
        candidate = candidate[3:]
    return f"PMC{candidate}" if candidate.isdigit() else None


def _provider_token(provider: str, scheme: str, value: str) -> IdentifierToken | None:
    provider_namespace = re.sub(r"[^a-z0-9._-]+", "_", provider.strip().casefold()).strip("_")
    normalized_value = value.strip()
    normalized_scheme = scheme.strip().casefold()
    if not provider_namespace or not normalized_value or not normalized_scheme:
        return None
    return IdentifierToken(f"provider:{provider_namespace}:{normalized_scheme}", normalized_value)
