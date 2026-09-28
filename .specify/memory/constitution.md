# Sales Analytics (Jarvis) Constitution

## Principles

### I. Evidence before assessment
Jarvis MUST retain the source call, analysis version, manual review and the context used for every displayed assessment. A review or AI conclusion MUST be traceable to a concrete call and timestamp.

### II. Human decision overrides AI
Only a ROP, director, or a signed operational-dashboard session MAY confirm or override a call type or score. A manual decision MUST show who made it, when, and why; it MUST survive a later analysis run.

### III. Bounded, relevant AI context
The analyzer MUST use no more than eight earlier calls linked to the same deal. It MUST send concise prior-call facts rather than raw historical transcripts, and MUST score the current call rather than the whole history.

### IV. Calm, stable operations interface
Background refreshes MUST NOT clear an opened calls interface, its filters, scroll position, or an unfinished review form. Unavailable integrations MUST fail visibly with a recoverable state.

### V. Protected integrations
Service tokens, CRM webhooks, transcripts and database credentials MUST remain server-side. Dashboard handoff sessions MUST be signed and time-bounded.

## Constraints

- Bitrix24 remains the source of call and CRM data.
- The current Flask service and private Jarvis Postgres schema are the production storage path; JSON is compatibility fallback only.
- Reanalysis is explicitly requested for one call and must not widen into a historical batch.

## Working process

Every new call-workflow feature MUST include API/UI tests for access, persistence and data boundaries. Deployments require an explicit user request.

## Governance

This constitution has priority over feature implementation decisions. A principle change requires a version update.

**Version**: 1.0.0 | **Ratified**: 2026-09-28 | **Last Amended**: 2026-09-28
