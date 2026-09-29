# Project Vision

## Original idea

The idea began with a need to follow medical research by topic or search query and detect newly available publications without repeatedly searching by hand. A user should be able to save searches such as `breast cancer immunotherapy` or `Alzheimer disease AND artificial intelligence`, inspect the resulting papers, and keep useful results organized. Early investigation of Google Scholar showed that ordinary `curl` access returned HTTP 403, including through a tested proxy. The platform therefore centers on documented scientific literature APIs.

## Why this system exists

Medical literature is spread across overlapping indexes with different search syntax, identifiers, dates, and metadata. A researcher needs a durable record of what was found, when it was found, where it came from, and how it relates to a project. The system should make source differences visible while presenting a coherent research workflow.

## Version 1 outcome

Version 1 is intentionally narrow: find new medical papers, store metadata, manage saved queries and Research Projects, review papers, and export results. It will not download PDFs or perform AI analysis.

## Long-term goals

- Keep the research engine reusable independently of desktop, API, and database adapters.
- Run locally first and support a server, REST API, web dashboard, cloud workers, and mobile clients later.
- Add literature sources through provider modules with stable contracts.
- Preserve source evidence so metadata can be audited and reprocessed.
- Support multiple researchers and shared projects when the product needs them.

## Future possibilities, outside version 1

Notifications, AI summaries, relevance ranking, trend analysis, evidence extraction, clinical trial monitoring, and literature review assistance can consume normalized records and domain events. These are extension points, not commitments to implement them in the first release. Patient-specific recommendations or clinical decision support would require a separate scope and validation decision.
