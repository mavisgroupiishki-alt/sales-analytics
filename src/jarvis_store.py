"""Server-side persistence for the Jarvis call-quality pipeline.

The module intentionally uses a private Postgres schema. It never exposes a
database credential to the browser and is optional until JARVIS_DATABASE_URL
is configured in the worker environment.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional


@dataclass(frozen=True)
class NormalizedCall:
    source_call_id: str
    occurred_at: str
    direction: str
    duration_seconds: Optional[int]
    manager_external_id: Optional[str]
    recording_available: bool
    audio_status: str
    crm_owner_type: Optional[str]
    crm_owner_id: Optional[str]


def normalize_bitrix_call(call: Dict[str, Any]) -> NormalizedCall:
    """Extract only deterministic call fields from the Bitrix snapshot."""
    activity_id = str(call.get("activity_id") or "").strip()
    occurred_at = str(call.get("created") or "").strip()
    if not activity_id or not occurred_at:
        raise ValueError("Bitrix call must contain activity_id and created")

    direction = call.get("direction")
    if direction not in {"incoming", "outgoing"}:
        direction = "unknown"

    duration = call.get("duration_sec")
    try:
        duration_seconds = int(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration_seconds = None

    audio = call.get("audio") or {}
    recording_available = bool(audio.get("file_id") or audio.get("url"))
    audio_status = "available" if recording_available else "unavailable"
    crm = call.get("crm") or {}
    owner_type = crm.get("owner_type")
    owner_id = crm.get("owner_id")
    if owner_type not in {"deal", "contact", "company", "lead"} or not owner_id:
        owner_type, owner_id = None, None

    return NormalizedCall(
        source_call_id=activity_id,
        occurred_at=occurred_at,
        direction=direction,
        duration_seconds=duration_seconds,
        manager_external_id=str((call.get("manager") or {}).get("id") or "") or None,
        recording_available=recording_available,
        audio_status=audio_status,
        crm_owner_type=owner_type,
        crm_owner_id=str(owner_id) if owner_id is not None else None,
    )


def payload_sha256(payload: Dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class JarvisStore:
    """Writes idempotent Bitrix snapshots into the private `jarvis` schema."""

    def __init__(self, connection: Any):
        self.connection = connection

    @classmethod
    def connect(cls, database_url: str) -> "JarvisStore":
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - exercised only in deployment
            raise RuntimeError("Install psycopg before enabling JARVIS_DATABASE_URL") from exc
        return cls(psycopg.connect(database_url))

    def write_bitrix_snapshot(self, calls: Iterable[Dict[str, Any]]) -> int:
        call_records = list(calls)
        rows = [normalize_bitrix_call(call) for call in call_records]
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                insert into jarvis.sync_runs (source, status, received_count)
                values ('bitrix24', 'running', %s)
                returning id
                """,
                (len(rows),),
            )
            sync_run_id = cursor.fetchone()[0]

            for source_call, original in zip(rows, call_records):
                raw_event_id = self._write_raw_event(cursor, original, sync_run_id)
                call_id = self._upsert_call(cursor, source_call, raw_event_id)
                self._upsert_crm_link(cursor, call_id, source_call)

            cursor.execute(
                """
                update jarvis.sync_runs
                set status = 'succeeded', finished_at = now()
                where id = %s
                """,
                (sync_run_id,),
            )
        self.connection.commit()
        return len(rows)

    def _write_raw_event(self, cursor: Any, payload: Dict[str, Any], sync_run_id: int) -> int:
        external_id = str(payload.get("activity_id") or "") or None
        checksum = payload_sha256(payload)
        cursor.execute(
            """
            insert into jarvis.raw_events
                (source, external_id, event_type, occurred_at, payload, payload_sha256, sync_run_id)
            values ('bitrix24', %s, 'crm.activity.call', %s, %s::jsonb, %s, %s)
            on conflict (source, external_id, event_type, payload_sha256) do nothing
            returning id
            """,
            (external_id, payload.get("created"), json.dumps(payload, ensure_ascii=False), checksum, sync_run_id),
        )
        row = cursor.fetchone()
        if row:
            return row[0]
        cursor.execute(
            """
            select id from jarvis.raw_events
            where source = 'bitrix24' and external_id is not distinct from %s
              and event_type = 'crm.activity.call' and payload_sha256 = %s
            """,
            (external_id, checksum),
        )
        return cursor.fetchone()[0]

    def _upsert_call(self, cursor: Any, call: NormalizedCall, raw_event_id: int) -> int:
        cursor.execute(
            """
            insert into jarvis.calls
                (source, source_call_id, activity_id, occurred_at, direction, duration_seconds,
                 manager_external_id, recording_available, audio_status, raw_event_id, source_updated_at)
            values ('bitrix24', %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
            on conflict (source, source_call_id) do update set
              activity_id = excluded.activity_id,
              occurred_at = excluded.occurred_at,
              direction = excluded.direction,
              duration_seconds = excluded.duration_seconds,
              manager_external_id = excluded.manager_external_id,
              recording_available = excluded.recording_available,
              audio_status = excluded.audio_status,
              raw_event_id = excluded.raw_event_id,
              source_updated_at = now(),
              updated_at = now()
            returning id
            """,
            (
                call.source_call_id,
                call.source_call_id,
                call.occurred_at,
                call.direction,
                call.duration_seconds,
                call.manager_external_id,
                call.recording_available,
                call.audio_status,
                raw_event_id,
            ),
        )
        return cursor.fetchone()[0]

    def _upsert_crm_link(self, cursor: Any, call_id: int, call: NormalizedCall) -> None:
        if not call.crm_owner_type or not call.crm_owner_id:
            return
        cursor.execute(
            """
            insert into jarvis.call_links
                (call_id, entity_type, external_id, link_method, confidence, is_primary)
            values (%s, %s, %s, 'crm_owner', 1, true)
            on conflict (call_id, entity_type, external_id) do update set
              confidence = excluded.confidence,
              is_primary = excluded.is_primary
            """,
            (call_id, call.crm_owner_type, call.crm_owner_id),
        )

    def close(self) -> None:
        self.connection.close()
