# Portable Boolean search

The expression editor, saved-query service and provider adapters share a Python
parser. Syntax is checked before a query is saved and again when a provider plans
its execution. Browser previews use the application `QueryPreviewService`; they
make no literature API requests.

## Writing expressions

```text
("breast cancer" OR melanoma) AND (immunotherapy OR "checkpoint inhibitor") NOT "animal study"
Alzheimer AND ("artificial intelligence" OR "machine learning")
NOT (pediatric OR "animal study")
```

- `AND`: require both concepts. `OR`: require either concept. `NOT`: exclude a
  concept or group. Operators are case-insensitive outside double quotes.
- `NOT` binds first, then `AND`, then `OR`. Parentheses override that order.
  `cancer OR melanoma AND therapy` means `cancer OR (melanoma AND therapy)`.
- Adjacent unquoted keywords imply AND. Quote a phrase to search those words
  together, or a reserved operator to search it as literal text, e.g. `"NOT"`.
- Both `cancer NOT pediatric` and `cancer AND NOT pediatric` are accepted.
  Standalone and repeated negation are supported; double negation simplifies
  in the displayed provider translation.
- Unclosed quotes/groups, missing operands, empty phrases and excess nesting
  report a character position. Limits are 5,000 characters, 256 input tokens and
  32 levels of grouping/negation. Field tags, wildcards, proximity operators and
  escaped quotation marks are outside this portable grammar.

Source indexes still differ in medical term mapping, stemming, indexed text and
phrase handling. Equivalent Boolean structure does not promise identical papers
across databases. PubMed can expand concepts through its automatic term mapping
and can fall back when a quoted phrase is absent from its phrase index.

## Provider execution

| Source | Execution |
| --- | --- |
| PubMed | Native AND/OR/NOT with explicit grouping, preserving the shared precedence despite PubMed's default left-to-right evaluation. Standalone negation uses `all[sb]`. |
| Europe PMC | Native Boolean search with grouped operands; standalone negation uses the verified match-all universe `*`. Existing filters remain attached to the whole expression. |
| OpenAlex | Uppercase native Boolean search with explicitly grouped compound operands. Existing date/language/type/OA filters remain separate. |
| Crossref | Boolean/quoted queries retrieve ranked candidate pages and apply the expression to available title, abstract and subject metadata. Plain unquoted keyword queries retain ranked discovery. |

Crossref does not offer native arbitrary Boolean keyword search. The adapter uses
positive concept searches as a candidate union, with one candidate page per page
budget unit. If an expression has a branch that can match without a positive term
(such as `cancer OR NOT pediatric`), the adapter scans the catalogue with applicable
date filters. This may require many pages; narrow the date range where possible.

Matching in the Crossref adapter is case-insensitive after Unicode/punctuation
normalization. Unquoted concepts require all their words somewhere in the available
metadata; quoted phrases require adjacent words within one field. No stemming or
synonym expansion is claimed. Missing metadata, relevance retrieval and finite page
budgets can omit papers. A NOT match means absence from the metadata evaluated,
not proof that the article's full text excludes that topic. Candidate counts are
not displayed as totals of Boolean matches.

The preview distinguishes native execution from metadata filtering and includes
source notices, including disabled discovery sources. It validates syntax and
translation without estimating result counts.

## Audit and continuation

Crossref's provider cursor records the candidate branch and its source cursor.
Filtered-out full pages retain their raw snapshots and continue pagination. A short
raw page advances to the next branch; filtering does not signal exhaustion. Exact
identities across branches use the existing canonical identity resolution. Candidate
responses, original expression, normalized translation, branch, retrieval time and
parser version remain traceable through the existing snapshot/run audit.

Translation versions have advanced for all providers; Crossref's parser version has
also advanced because record selection changed. Existing saved expressions/history
are retained. A previous provider run must satisfy the existing compatibility check
before retry; a run planned with the old translations requires a fresh search.
Monitoring fingerprints invalidate old campaigns through the existing mechanism.
No database migration is required.

Live verification on 2026-09-26 confirmed standalone negation on PubMed, Europe PMC
and OpenAlex. Europe PMC's grouped `(*:* NOT cancer)` returned a different count
from standalone `NOT cancer`, while `(* NOT cancer)` preserved it; the renderer
uses the latter. This provider behavior is covered by a translation regression.

References: [PubMed search guide](https://pubmed.ncbi.nlm.nih.gov/help/),
[Europe PMC Boolean help](https://europepmc.org/Help),
[OpenAlex search syntax](https://help.openalex.org/api/searching/),
[Crossref REST API](https://www.crossref.org/documentation/retrieve-metadata/rest-api/).
