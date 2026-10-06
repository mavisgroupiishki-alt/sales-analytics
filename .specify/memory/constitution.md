# Sales Analytics (Jarvis) Constitution

## Principles

### I. Evidence before assessment
Jarvis MUST retain the source call, analysis version, manual review and the context used for every displayed assessment. A review or AI conclusion MUST be traceable to a concrete call and timestamp.

### II. Human decision overrides AI
Only a ROP, director, or a signed operational-dashboard session MAY confirm or override a call type or score. A manual decision MUST show who made it, when, and why; it MUST survive a later analysis run.

### III. Durable, bounded and relevant AI context
Jarvis MUST retain a durable, source-linked factual memory for every analysed sales call under its CRM deal, contact and company/funnel relations. A new call MUST read this memory before requesting remote CRM history or re-transcribing an older recording. An individual model prompt MUST contain no more than eight earlier call facts plus a traceable current-state digest; it MUST never contain an entire historical transcript. Every included fact MUST retain its source call and timestamp. The reactivation workflow MAY prepare every playable recording directly linked to an open reactivation deal, but it MUST persist each analysis independently and send only concise facts from the eight most relevant calls to the final decision prompt.

### IV. Calm, stable operations interface
Background refreshes MUST NOT clear an opened calls interface, its filters, scroll position, or an unfinished review form. Unavailable integrations MUST fail visibly with a recoverable state.

### V. Protected integrations
Service tokens, CRM webhooks, transcripts and database credentials MUST remain server-side. Dashboard handoff sessions MUST be signed and time-bounded.

## Constraints

- Bitrix24 remains the source of call and CRM data.
- The current Flask service and private Jarvis Postgres schema are the production storage path; JSON is compatibility fallback only.
- A manual reanalysis is explicitly requested for one call and must not widen into a historical batch. The dedicated reactivation worker is the sole exception: it may analyse the complete direct call history of an open reactivation deal.

## Working process

Every new call-workflow feature MUST include API/UI tests for access, persistence and data boundaries. Deployments require an explicit user request.

## Governance

This constitution has priority over feature implementation decisions. A principle change requires a version update.

**Version**: 1.3.0 | **Ratified**: 2026-09-28 | **Last Amended**: 2026-10-05
