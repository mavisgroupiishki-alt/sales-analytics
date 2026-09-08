"""Server-side persistence for the Jarvis call-quality pipeline.

The module intentionally uses a private Postgres schema. It never exposes a
database credential to the browser and is optional until JARVIS_DATABASE_URL
is configured in the worker environment.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Tuple


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

    def write_analysis_snapshot(
        self,
        calls: Iterable[Dict[str, Any]],
        analyses: Dict[str, Any],
        rubric_id: int,
        *,
        force_new_version: bool = False,
    ) -> int:
        """Persist analysis facts without overwriting their history.

        The caller supplies the active rubric selected by the administrator.
        This prevents an undeclared default rubric from quietly changing scores.
        Re-running an unchanged batch is idempotent; an explicitly requested
        reanalysis can retain a new immutable transcript/analysis version.
        """
        if rubric_id <= 0:
            raise ValueError("rubric_id must be a positive Jarvis rubric id")

        calls_by_activity = {
            str(call.get("activity_id") or ""): call
            for call in calls
            if str(call.get("activity_id") or "")
        }
        written = 0
        with self.connection.cursor() as cursor:
            self._validate_rubric(cursor, rubric_id)
            for activity_id, record in analyses.items():
                if not isinstance(record, dict):
                    continue
                call = calls_by_activity.get(str(activity_id)) or record.get("call_meta")
                if not isinstance(call, dict):
                    continue
                analysis = record.get("analysis")
                if not isinstance(analysis, dict):
                    continue

                call_id = self._call_id(cursor, str(call.get("activity_id") or activity_id))
                if not call_id:
                    continue

                payload = json.dumps(analysis, ensure_ascii=False, sort_keys=True)
                if not force_new_version and self._analysis_exists(cursor, call_id, rubric_id):
                    continue

                transcript_id = self._write_transcript(cursor, call_id, record.get("transcription") or {})
                status, reason, rule_id = self._analysis_status(analysis)
                analysis_id = self._write_analysis(
                    cursor,
                    call_id,
                    transcript_id,
                    rubric_id,
                    analysis,
                    payload,
                    status,
                    record.get("analyzed_at"),
                )
                self._write_evidence(cursor, analysis_id, analysis, rule_id, reason)
                if status == "critical":
                    self._open_critical_case(cursor, analysis_id, rule_id, reason, analysis)
                written += 1
        self.connection.commit()
        return written

    @staticmethod
    def _validate_rubric(cursor: Any, rubric_id: int) -> None:
        """Reject a database rubric that this deterministic scorer cannot use."""
        from claude_analyzer import EXPECTED_RUBRIC_CODE, EXPECTED_RUBRIC_VERSION

        cursor.execute("select code, version, status from jarvis.rubrics where id = %s", (rubric_id,))
        row = cursor.fetchone()
        if not row or row[0] != EXPECTED_RUBRIC_CODE or int(row[1]) != EXPECTED_RUBRIC_VERSION or row[2] != "active":
            raise RuntimeError("Configured Jarvis rubric does not match the active deterministic scorer")

    @staticmethod
    def _analysis_status(analysis: Dict[str, Any]) -> Tuple[str, str, str]:
        # Import lazily: the store remains usable for raw ingestion without the
        # AI module and its transport dependencies.
        from claude_analyzer import evaluate_triage

        return evaluate_triage(analysis)

    def _call_id(self, cursor: Any, activity_id: str) -> Optional[int]:
        cursor.execute(
            """
            select id from jarvis.calls
            where source = 'bitrix24' and source_call_id = %s
            """,
            (activity_id,),
        )
        row = cursor.fetchone()
        return row[0] if row else None

    def _analysis_exists(self, cursor: Any, call_id: int, rubric_id: int) -> bool:
        cursor.execute(
            """
            select 1 from jarvis.call_analyses
            where call_id = %s and rubric_id = %s
            limit 1
            """,
            (call_id, rubric_id),
        )
        return bool(cursor.fetchone())

    def _write_transcript(self, cursor: Any, call_id: int, transcription: Dict[str, Any]) -> int:
        cursor.execute(
            """
            select coalesce(max(version), 0) + 1
            from jarvis.transcripts where call_id = %s
            """,
            (call_id,),
        )
        version = cursor.fetchone()[0]
        text = str(transcription.get("text") or transcription.get("text_with_timecodes") or "")
        segments = transcription.get("segments") or []
        cursor.execute(
            """
            insert into jarvis.transcripts
                (call_id, version, provider, language_code, transcript, diarization, confidence, status)
            values (%s, %s, 'bitrix_vibe_whisper', 'ru', %s, %s::jsonb, %s, 'complete')
            returning id
            """,
            (call_id, version, text, json.dumps(segments, ensure_ascii=False), transcription.get("confidence")),
        )
        return cursor.fetchone()[0]

    def _write_analysis(
        self,
        cursor: Any,
        call_id: int,
        transcript_id: int,
        rubric_id: int,
        analysis: Dict[str, Any],
        payload: str,
        status: str,
        analyzed_at: Any,
    ) -> int:
        call_type = analysis.get("call_type") or {}
        if not isinstance(call_type, dict):
            call_type = {}
        confidence = analysis.get("analysis_confidence", analysis.get("confidence"))
        cursor.execute(
            """
            insert into jarvis.call_analyses
                (call_id, transcript_id, rubric_id, status, call_type, call_goal,
                 analysis_confidence, applicable_score, result, analyzed_at)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, coalesce(%s::timestamptz, now()))
            returning id
            """,
            (
                call_id,
                transcript_id,
                rubric_id,
                status,
                str(call_type.get("key") or "unknown"),
                str(analysis.get("call_goal") or analysis.get("summary") or ""),
                confidence,
                analysis.get("overall_score"),
                payload,
                analyzed_at,
            ),
        )
        return cursor.fetchone()[0]

    def _write_evidence(
        self,
        cursor: Any,
        analysis_id: int,
        analysis: Dict[str, Any],
        rule_id: str,
        reason: str,
    ) -> None:
        flags = analysis.get("flags") or {}
        evidence = flags.get("critical_evidence") or {}
        quote = str(evidence.get("quote") or "").strip()
        timecode = str(evidence.get("time") or "").strip()
        if not quote:
            return
        start_second = self._timecode_seconds(timecode)
        cursor.execute(
            """
            insert into jarvis.analysis_evidence
                (analysis_id, criterion_code, finding, quote, start_second, end_second, confidence)
            values (%s, %s, %s, %s, %s, %s, %s)
            """,
            (analysis_id, rule_id or "triage_signal", reason or "AI evidence", quote, start_second, start_second, evidence.get("confidence")),
        )

    @staticmethod
    def _timecode_seconds(value: str) -> Optional[int]:
        try:
            minutes, seconds = value.split(":", 1)
            return int(minutes) * 60 + int(seconds)
        except (AttributeError, TypeError, ValueError):
            return None

    def _open_critical_case(
        self,
        cursor: Any,
        analysis_id: int,
        rule_id: str,
        reason: str,
        analysis: Dict[str, Any],
    ) -> None:
        cursor.execute(
            """
            insert into jarvis.critical_cases
                (analysis_id, rule_id, severity, status, reason, recommended_action)
            select %s, %s, 'high', 'open', %s, %s
            where not exists (
                select 1 from jarvis.critical_cases
                where analysis_id = %s and status = 'open'
            )
            """,
            (
                analysis_id,
                rule_id,
                reason,
                str(analysis.get("recommended_action") or analysis.get("recommendation") or "Открыть звонок и принять решение РОПа"),
                analysis_id,
            ),
        )

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


class JarvisRepository:
    """Read the dashboard projection from the private Jarvis schema.

    JSON exports are intentionally not consulted here.  The projection retains
    the existing Flask view model while the source of truth moves to Postgres.
    """

    def __init__(self, connection: Any):
        self.connection = connection

    @classmethod
    def connect(cls, database_url: str) -> "JarvisRepository":
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise RuntimeError("Install psycopg before enabling JARVIS_DATABASE_URL") from exc
        return cls(psycopg.connect(database_url, row_factory=dict_row))

    def load_snapshot(self) -> Tuple[list[Dict[str, Any]], Dict[str, Any]]:
        """Return calls and their newest immutable analysis version.

        The raw Bitrix payload is stored privately and carries CRM/client fields
        that are not duplicated in the normalized call table.  It is never sent
        outside this server-side request.
        """
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                select status, coalesce(finished_at, started_at) as happened_at
                from jarvis.sync_runs
                where source = 'bitrix24'
                order by id desc
                limit 1
                """
            )
            latest_sync = cursor.fetchone()
            cursor.execute(
                """
                select
                  c.id as call_id, c.source_call_id, c.activity_id, c.occurred_at,
                  c.direction, c.duration_seconds, c.manager_external_id,
                  c.recording_available, c.audio_status,
                  re.payload as raw_payload,
                  latest.id as analysis_id, latest.status as analysis_status,
                  latest.result as analysis_result, latest.analyzed_at,
                  transcript.transcript as transcript_text,
                  transcript.diarization as transcript_segments
                from jarvis.calls c
                left join jarvis.raw_events re on re.id = c.raw_event_id
                left join lateral (
                  select ca.*
                  from jarvis.call_analyses ca
                  where ca.call_id = c.id
                  order by ca.analyzed_at desc nulls last, ca.id desc
                  limit 1
                ) latest on true
                left join jarvis.transcripts transcript on transcript.id = latest.transcript_id
                order by c.occurred_at desc, c.id desc
                """
            )
            rows = cursor.fetchall()

        calls: list[Dict[str, Any]] = []
        analyses: Dict[str, Any] = {}
        for row in rows:
            original = self._json_object(row.get("raw_payload"))
            activity_id = str(row.get("activity_id") or row.get("source_call_id") or "")
            if not activity_id:
                continue
            call = dict(original)
            call["activity_id"] = activity_id
            call.setdefault("created", self._timestamp(row.get("occurred_at")))
            call.setdefault("direction", row.get("direction") or "unknown")
            call.setdefault("duration_sec", row.get("duration_seconds"))
            call.setdefault("manager", {"id": row.get("manager_external_id"), "name": "Менеджер не определён"})
            call.setdefault(
                "audio",
                {"available": bool(row.get("recording_available")), "status": row.get("audio_status") or "unknown"},
            )
            call.setdefault("crm", {})
            if latest_sync:
                call["_jarvis_sync"] = {
                    "status": latest_sync.get("status"),
                    "at": self._timestamp(latest_sync.get("happened_at")),
                }
            calls.append(call)

            result = self._json_object(row.get("analysis_result"))
            if not result:
                continue
            # The persisted status is authoritative and protects a historic
            # analysis from being reinterpreted by a later UI-only change.
            result["review_status"] = row.get("analysis_status") or result.get("review_status")
            analyses[activity_id] = {
                "call_meta": call,
                "transcription": {
                    "text": row.get("transcript_text") or "",
                    "segments": self._json_value(row.get("transcript_segments"), []),
                },
                "analysis": result,
                "analyzed_at": self._timestamp(row.get("analyzed_at")),
            }
        return calls, analyses

    @staticmethod
    def _timestamp(value: Any) -> str:
        return value.isoformat() if hasattr(value, "isoformat") else str(value or "")

    @staticmethod
    def _json_value(value: Any, default: Any) -> Any:
        if value is None:
            return default
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return default
        return value

    @classmethod
    def _json_object(cls, value: Any) -> Dict[str, Any]:
        parsed = cls._json_value(value, {})
        return parsed if isinstance(parsed, dict) else {}

    def close(self) -> None:
        self.connection.close()


def record_sync_failure(database_url: str, source: str, error_type: str) -> None:
    """Commit a source failure independently of the rolled-back batch."""
    store = JarvisStore.connect(database_url)
    try:
        with store.connection.cursor() as cursor:
            cursor.execute(
                """
                insert into jarvis.sync_runs (source, status, error_summary, finished_at)
                values (%s, 'failed', %s, now())
                """,
                (source, error_type),
            )
        store.connection.commit()
    finally:
        store.close()
