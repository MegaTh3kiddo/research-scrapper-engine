# Literature API Sources

## Metadata assertion semantics (gap remediation)

Provider publication types/statuses are labels, not a shared clinical taxonomy.
PubMed publication stage (e.g. `epublish`) and OpenAlex retraction evidence are
different concepts; inspect provider-specific assertions as well as the selected
canonical value. Absence of a retraction flag does not prove safety.

- [OpenAlex work attributes](https://help.openalex.org/data/works/attributes/):
  `cited_by_count`, `type`, and explicit `is_retracted=true` evidence.
- [Europe PMC REST reference](https://dev.europepmc.org/docs/EBI_Europe_PMC_Web_Service_Reference.pdf):
  `citedByCount` and `pubType`/`pubTypeList`; no status is inferred from indexing.
- [Crossref Cited-by](https://www.crossref.org/documentation/cited-by/):
  `is-referenced-by-count` measures incoming citations in Crossref's coverage.
  Preserve native `type`; do not infer publication/retraction status.
- [PubMed DTD](https://dtd.nlm.nih.gov/ncbi/pubmed/doc/out/250101/el-PubmedData.html):
  multiple publication types and explicit `PublicationStatus` are parsed. The
  adapter does not invent citation counts from reference lists.

Zero counts are retained, missing counts remain unknown, malformed/negative
counts fail normalization. Counts from different indexes are not interchangeable.
Parser changes invalidate incompatible monitoring cursor signatures.

Assessment recorded 2026-09-24. API limits, documentation, and terms can change; verify them again when each provider is implemented. Quality and stability assessments are engineering judgments, not service guarantees.

## 0.1.3 implemented sources (2026-09-26)

PubMed, Europe PMC, **OpenAlex and Crossref** have working isolated adapters.
The latter two are opt-in wider discovery sources; identifier-targeted enrichment
is not implemented. Semantic Scholar and ClinicalTrials.gov remain evaluated,
not active. See [source/settings design](SOURCE_SETTINGS_DESIGN.md) and
[settings guide](SETTINGS.md).

OpenAlex supports cursor paging, work fetch, Boolean queries, dates, language
aliases, exact mapped work types and OA filters. PubMed field syntax and filters
that would broaden medical categories are rejected. A free key is optional for
basic calls and increases budget. Maximum page size is 100; `mailto` is ignored.
[Authentication](https://help.openalex.org/api/authentication/),
[paging](https://help.openalex.org/api/paging/),
[deprecations](https://help.openalex.org/api/deprecations/).

Crossref supports keyword search, inclusive date bounds, cursors and DOI lookup.
Boolean/quoted queries now use candidate retrieval and local evaluation over available
title/abstract/subject metadata, with audited composite pagination. Plain unquoted
keyword results retain relevance ranking. Medical/OA filters remain unsupported.
See [Boolean execution and coverage limits](BOOLEAN_SEARCH.md). Conservative pacing is 1 request/second
public or 3/second with contact email, including singleton fetches. Existing
transport handles retries/Retry-After; throughput does not dynamically increase
from returned headers. [July 2026 update](https://community.crossref.org/t/refining-rest-api-limits-for-improved-stability-and-reliability/16137).

Live smoke checks on 2026-09-26 returned five records from each new source using
direct access without a key. Tests cover normalization, malformed responses,
identity checks, filters, cursor protection, and secret-safe provenance.

## Stage 2 implementation notes

PubMed and Europe PMC adapters were implemented and checked with one-record live searches on 2026-09-24. PubMed uses ESearch JSON for IDs and counts followed by EFetch XML for article metadata; both responses remain attached to each page. A PubMed search cannot paginate beyond the first 10,000 ESearch results; at that boundary the next cursor remains visible and the next attempt raises an explicit limit error. Narrow the query or date window for more complete coverage. EFetch XML legitimately includes a DTD declaration, so parsing allows the declaration but blocks XML entities and external references. [NCBI E-utilities reference](https://www.ncbi.nlm.nih.gov/books/NBK25499/), [defusedxml parser controls](https://github.com/tiran/defusedxml/blob/main/README.md).

The 0.1.1 PubMed parser reads current-paper identifiers only from the direct `PubmedData/ArticleIdList`; cited references have separate `Reference/ArticleIdList` elements and must not be asserted as identifiers of the containing paper. The [NLM PubmedData element definition](https://dtd.nlm.nih.gov/ncbi/pubmed/doc/out/250101/el-PubmedData.html) and [Reference element definition](https://dtd.nlm.nih.gov/ncbi/pubmed/doc/out/250101/el-Reference.html) document this distinction. Earlier 0.1.0 data may be contaminated; see [Data Integrity Remediation](DATA_INTEGRITY_REMEDIATION.md).

Europe PMC uses `resultType=core` and `cursorMark` pagination, keeping a `SOURCE:ID` source record identity (for example `MED:...` or `PPR:...`). One-sided publication date bounds, a negative open-access filter, and a human-only filter are not silently approximated: the plan marks them unsupported. PubMed's open-access filter is also unsupported because its free-full-text subset is not equivalent. Unsupported filters cause the discovery service to reject the run. [Europe PMC REST reference](https://europepmc.org/RestfulWebService), [Europe PMC web service reference](https://dev.europepmc.org/docs/EBI_Europe_PMC_Web_Service_Reference.pdf).

Rates are coordinated only within a provider HTTP client instance, not across processes or machines. Production workers will need a shared limiter or conservative worker configuration before parallel deployment. Stage 2's standalone discovery service still returns raw responses in memory; Stage 3's ingestion service persists immutable snapshots and canonical records.

## Provider strategy

**Primary discovery:** PubMed / NCBI E-utilities and Europe PMC. They have strong medical relevance and complementary coverage, though their records overlap substantially. **Enrichment and wider discovery:** Crossref for DOI and publisher metadata; OpenAlex for interdisciplinary coverage, identifiers, open access links, and citation context. **Optional:** Semantic Scholar for citation and recommendation features after its terms fit the product's use model. **Separate record family:** ClinicalTrials.gov v2 for trial monitoring, not a substitute for publication search.

| Source | Access and rate guidance | Medical relevance and useful fields | Role and terms |
| --- | --- | --- | --- |
| [PubMed / E-utilities](https://www.ncbi.nlm.nih.gov/books/NBK25497/) | Public; key optional. 3 requests/second without a key, 10/second by default with a key. Follow NCBI identification and large-job guidance. | Excellent biomedical indexing. PMID, DOI when present, authors, journal, dates, abstracts, MeSH, publication types, and correction links. Some indexing is delayed. | Primary. NCBI requires its disclaimer and copyright notice in software using E-utilities; abstracts may carry third-party copyright. [Policy](https://www.ncbi.nlm.nih.gov/home/about/policies/), [field guidance](https://pubmed.ncbi.nlm.nih.gov/help/). |
| [Europe PMC](https://europepmc.org/RestfulWebService) | Public REST API; no universal numeric rate limit was identified in reviewed documentation. Pace conservatively and honor throttling. | Strong life sciences coverage, including PubMed records, preprints, PMC links, abstracts, citations, references, and available MeSH. `lite` and `core` responses support staged retrieval. | Primary complement. Free access does not override copyright or content licenses. [Content guidance](https://europepmc.org/help). |
| [OpenAlex](https://help.openalex.org/api/) | Basic public access. A free key raises the keyless daily budget 10×; 100 requests/second ceiling and budget headers. Current service budgets should be checked before scale-up. | Broad scholarly graph, topics, authors, institutions, citations, and open access locations; topics are not MeSH. | Wider discovery and future enrichment. OpenAlex describes its data as CC0; API service budgets are separate. [Limits](https://help.openalex.org/api/authentication/), [pricing](https://help.openalex.org/access/pricing/). |
| [Crossref](https://www.crossref.org/documentation/retrieve-metadata/rest-api/) | Public, no signup; `mailto` selects the polite pool. Request-type and pool limits differ; observe returned rate and concurrency headers. | Strong DOI resolution, publisher-deposited titles, authors, venues, dates, funding, licenses, references, and sometimes abstracts. Completeness varies by depositor. | DOI verification and enrichment. Most bibliographic metadata is reusable; some abstracts are not. [Access](https://www.crossref.org/documentation/retrieve-metadata/rest-api/access-and-authentication/), [limit changes](https://www.crossref.org/blog/announcing-changes-to-rest-api-rate-limits/). |
| [Semantic Scholar](https://webflow.semanticscholar.org/product/api) | Many endpoints public; unauthenticated traffic has a shared throttle. Key recommended; introductory key limit is 1 request/second. | Broad paper and citation graph, links, counts, and some abstracts. | Optional enrichment. API attribution and separate data or third-party licenses apply; commercial and redistribution fit must be reviewed. [API agreement](https://webflow.semanticscholar.org/product/api/license). |
| [ClinicalTrials.gov v2](https://www.nlm.nih.gov/pubs/techbull/ma24/ma24_clinicaltrials_api.html) | Public REST and JSON; no official numeric limit identified in reviewed material. Pace modestly. | NCT IDs, conditions, interventions, status, dates, design, sponsors, outcomes, and some results. These are study registrations, not papers. | Future trial module with a separate clinical-study model. NLM recommends API use for programmatic access. [Example](https://www.nlm.nih.gov/pubs/techbull/ja25/ja25_clinical_trials_screen-scraping.html). |

## Other sources considered

PubMed Central's open access subset is relevant for later licensed full-text work, outside v1. The [NCI cancer clinical trials API](https://clinicaltrialsapi.cancer.gov/developer-guide) may be useful for an oncology-specific trial module and requires its own key and limits. Google Scholar is not a planned provider: the project's initial direct and proxied requests returned HTTP 403, and an unofficial scraping dependency would be unreliable for this architecture.

## Merge and identity rules

Normalize DOI, PMID, PMCID, OpenAlex ID, Semantic Scholar ID, and NCT ID; retain each source's original identifier and snapshot. Exact DOI or PMID matches can identify the same publication. Title and author similarity only propose a match for review. Prefer source authority by field: PubMed for medical indexing, Crossref for DOI registration metadata. Preserve conflicting values, provenance, fetch times, and source-specific citation counts. Link preprints, versions, corrections, retractions, and trials explicitly rather than collapsing them into one paper.
