# Private live worker: DigitalOcean

This package changes the call pipeline to:

`Bitrix24 → private Docker worker → Supabase jarvis → Render interface`

The worker has no public port. n8n starts a sync every five minutes using the
private Docker hostname `jarvis-worker` and an HMAC-safe shared header.

## One-time production checklist

1. Put the deployed source in a private server directory, for example
   `/opt/mavis-pilot/jarvis-worker`.
2. Run `seed-jarvis-rubric.sql` in the private Supabase SQL editor once and
   use the returned rubric id. In the server-owned `.env` set the existing
   `BITRIX_WEBHOOK_URL` and `VIBE_API_KEY`, plus server-only
   `JARVIS_DATABASE_URL`, `JARVIS_RUBRIC_ID`, and a long random
   `JARVIS_SYNC_SECRET`.
3. Add `JARVIS_SYNC_SECRET` to the existing n8n container environment; do not
   put the value in the workflow JSON.
4. Merge `docker-compose.live-worker.yml` with the n8n compose project, build
   `jarvis-worker`, and verify its private `/health` endpoint from the Docker
   network. Keep the worker compose file in
   `/opt/mavis-pilot/jarvis-worker/deploy/` and run Docker Compose with both
   explicit paths, so its `context: ..` resolves to
   `/opt/mavis-pilot/jarvis-worker`.
5. Import `n8n-jarvis-live-sync.json`, confirm the header expression uses the
   server environment variable, then activate it.
6. Trigger one private `{"mode":"reanalyze_today"}` request and check the
   `jarvis.sync_runs`, `calls`, `transcripts`, `call_analyses`, and
   `critical_cases` counts before turning on the five-minute schedule.

No secret, recording, transcript, or result may be committed to GitHub.
