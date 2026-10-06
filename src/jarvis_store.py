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
    explicit_audio_status = str(audio.get("status") or "").strip().lower()
    known_unavailable = explicit_audio_status in {"empty", "unavailable", "error", "invalid"} or audio.get("error") in {"empty_recording", "non_audio_file"}
    recording_available = bool(audio.get("file_id") or audio.get("url")) and not known_unavailable
    # The database contract uses ``unavailable`` for every non-playable source.
    # Keep the more precise ``empty`` reason in the raw Bitrix payload while
    # mapping it to the stable persisted enum at this boundary.
    audio_status = "unavailable" if known_unavailable else (explicit_audio_status or ("available" if recording_available else "unavailable"))
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


def context_scopes_for_call(call: Dict[str, Any]) -> list[tuple[str, str, str]]:
    """Return explicit CRM context folders for a call.

    A folder is never inferred from a display name or phone number.  The
    funnel id is part of every key so a company's separate sales funnels do
    not silently share commercial history.
    """
    crm = call.get("crm") or {}
    client = call.get("client") or {}
    funnel_id = str(crm.get("category_id") or "").strip()
    # A contact/company activity without an explicit sales funnel cannot be
    # safely shared: the same company may have several pipelines.  Exclude it
    # rather than placing it in a common empty-folder key.
    if not funnel_id:
        return []
    scopes: list[tuple[str, str, str]] = []

    owner_type = str(crm.get("owner_type") or "").strip()
    owner_id = str(crm.get("owner_id") or "").strip()
    if owner_type in {"deal", "contact", "company"} and owner_id:
        scopes.append((owner_type, owner_id, funnel_id))

    company_id = str(crm.get("company_id") or client.get("company_id") or "").strip()
    if company_id:
        scopes.append(("company", company_id, funnel_id))

    contact_values = crm.get("contact_ids") or []
    if not isinstance(contact_values, (list, tuple, set)):
        contact_values = [contact_values]
    if client.get("contact_id"):
        contact_values = [*contact_values, client.get("contact_id")]
    for contact_id in contact_values:
        normalized = str(contact_id or "").strip()
        if normalized:
            scopes.append(("contact", normalized, funnel_id))

    return list(dict.fromkeys(scopes))


def _context_text(value: Any, *, limit: int) -> str:
    return str(value or "").strip()[:limit]


def context_fact_from_analysis(call: Dict[str, Any], analysis: Dict[str, Any]) -> Dict[str, Any]:
    """Project one assessment into compact, source-traceable CRM memory."""
    call_type = analysis.get("call_type") or {}
    if not isinstance(call_type, dict):
        call_type = {}
    objection_texts = [
        _context_text(item.get("text") or item.get("detail"), limit=240)
        for item in (analysis.get("key_moments") or [])
        if isinstance(item, dict) and str(item.get("type") or "").strip().lower() == "negative"
    ]
    next_contact = analysis.get("next_contact") or {}
    if not isinstance(next_contact, dict):
        next_contact = {}
    return {
        "activity_id": str(call.get("activity_id") or ""),
        "date": str(call.get("created") or ""),
        "call_type_key": _context_text(call_type.get("key") or "unknown", limit=80),
        "call_type": _context_text(call_type.get("label") or "Тип не указан", limit=160),
        "summary": _context_text(analysis.get("summary"), limit=600),
        "outcome": _context_text(analysis.get("outcome"), limit=400),
        "objections": objection_texts[:3],
        "next_step": _context_text(
            analysis.get("recommended_action")
            or analysis.get("recommendation")
            or next_contact.get("context")
            or "",
            limit=400,
        ),
    }


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
                self._write_context_facts(cursor, call_id, analysis_id, call, analysis, status)
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

    def _write_context_facts(
        self,
        cursor: Any,
        call_id: int,
        analysis_id: int,
        call: Dict[str, Any],
        analysis: Dict[str, Any],
        status: str,
    ) -> None:
        """Upsert compact memory items for the current analysis version only."""
        if status in {"excluded", "failed"}:
            # A forced reanalysis can turn a formerly working call into a
            # service/short/unusable one. Remove its previous sales fact so
            # later prompts never retain invalid historical evidence.
            cursor.execute(
                """
                delete from jarvis.context_facts
                where call_id = %s
                returning scope_type, scope_external_id, funnel_id
                """,
                (call_id,),
            )
            for stale_scope in cursor.fetchall():
                self._remove_context_profile(
                    cursor,
                    str(stale_scope["scope_type"] if isinstance(stale_scope, dict) else stale_scope[0]),
                    str(stale_scope["scope_external_id"] if isinstance(stale_scope, dict) else stale_scope[1]),
                    str(stale_scope["funnel_id"] if isinstance(stale_scope, dict) else stale_scope[2]),
                )
            return
        occurred_at = str(call.get("created") or "").strip()
        if not occurred_at:
            return
        fact = context_fact_from_analysis(call, analysis)
        scopes = context_scopes_for_call(call)
        stale_scope_filter = " or ".join(
            "(scope_type = %s and scope_external_id = %s and funnel_id = %s)" for _ in scopes
        )
        stale_sql = """
            delete from jarvis.context_facts
            where call_id = %s
        """
        stale_params: list[Any] = [call_id]
        if stale_scope_filter:
            stale_sql += f" and not ({stale_scope_filter})"
            stale_params.extend(value for scope in scopes for value in scope)
        stale_sql += " returning scope_type, scope_external_id, funnel_id"
        cursor.execute(stale_sql, stale_params)
        for stale_scope in cursor.fetchall():
            self._remove_context_profile(
                cursor,
                str(stale_scope["scope_type"] if isinstance(stale_scope, dict) else stale_scope[0]),
                str(stale_scope["scope_external_id"] if isinstance(stale_scope, dict) else stale_scope[1]),
                str(stale_scope["funnel_id"] if isinstance(stale_scope, dict) else stale_scope[2]),
            )

        for scope_type, scope_external_id, funnel_id in scopes:
            cursor.execute(
                """
                select 1 from jarvis.context_facts
                where call_id = %s and scope_type = %s and scope_external_id = %s and funnel_id = %s
                """,
                (call_id, scope_type, scope_external_id, funnel_id),
            )
            already_present = bool(cursor.fetchone())
            cursor.execute(
                """
                insert into jarvis.context_facts
                    (call_id, analysis_id, scope_type, scope_external_id, funnel_id, occurred_at, fact)
                values (%s, %s, %s, %s, %s, %s::timestamptz, %s::jsonb)
                on conflict (call_id, scope_type, scope_external_id, funnel_id) do update set
                    analysis_id = excluded.analysis_id,
                    occurred_at = excluded.occurred_at,
                    fact = excluded.fact,
                    updated_at = now()
                """,
                (
                    call_id,
                    analysis_id,
                    scope_type,
                    scope_external_id,
                    funnel_id,
                    occurred_at,
                    json.dumps(fact, ensure_ascii=False),
                ),
            )
            self._touch_context_profile(cursor, scope_type, scope_external_id, funnel_id, occurred_at, fact, added=not already_present)

    @staticmethod
    def _touch_context_profile(
        cursor: Any,
        scope_type: str,
        scope_external_id: str,
        funnel_id: str,
        occurred_at: str,
        fact: Dict[str, Any],
        *,
        added: bool,
    ) -> None:
        """Update a profile in constant work after an insert or replacement."""
        cursor.execute(
            """
            insert into jarvis.context_profiles
              (scope_type, scope_external_id, funnel_id, facts_count, latest_call_at, current_state)
            values (%s, %s, %s, 1, %s::timestamptz, %s::jsonb)
            on conflict (scope_type, scope_external_id, funnel_id) do update set
              facts_count = jarvis.context_profiles.facts_count + %s,
              latest_call_at = greatest(jarvis.context_profiles.latest_call_at, excluded.latest_call_at),
              current_state = case
                when jarvis.context_profiles.latest_call_at is null
                  or jarvis.context_profiles.latest_call_at <= excluded.latest_call_at
                then excluded.current_state else jarvis.context_profiles.current_state end,
              updated_at = now()
            """,
            (
                scope_type,
                scope_external_id,
                funnel_id,
                occurred_at,
                json.dumps({
                    "latest_call_id": fact.get("activity_id") or "",
                    "last_summary": fact.get("summary") or "",
                    "last_outcome": fact.get("outcome") or "",
                    "next_step": fact.get("next_step") or "",
                }, ensure_ascii=False),
                1 if added else 0,
            ),
        )

    @staticmethod
    def _remove_context_profile(cursor: Any, scope_type: str, scope_external_id: str, funnel_id: str) -> None:
        """Repair one old folder after a call moved to another CRM relation."""
        cursor.execute(
            """
            delete from jarvis.context_profiles profile
            where profile.scope_type = %s and profile.scope_external_id = %s and profile.funnel_id = %s
              and not exists (
                select 1 from jarvis.context_facts fact
                where fact.scope_type = profile.scope_type
                  and fact.scope_external_id = profile.scope_external_id
                  and fact.funnel_id = profile.funnel_id
              )
            """,
            (scope_type, scope_external_id, funnel_id),
        )
        cursor.execute(
            """
            with latest as (
              select occurred_at, fact
              from jarvis.context_facts
              where scope_type = %s and scope_external_id = %s and funnel_id = %s
              order by occurred_at desc, call_id desc
              limit 1
            )
            update jarvis.context_profiles profile
            set facts_count = greatest(profile.facts_count - 1, 0),
                latest_call_at = (select occurred_at from latest),
                current_state = jsonb_build_object(
                  'latest_call_id', coalesce((select fact ->> 'activity_id' from latest), ''),
                  'last_summary', coalesce((select fact ->> 'summary' from latest), ''),
                  'last_outcome', coalesce((select fact ->> 'outcome' from latest), ''),
                  'next_step', coalesce((select fact ->> 'next_step' from latest), '')
                ),
                updated_at = now()
            where profile.scope_type = %s and profile.scope_external_id = %s and profile.funnel_id = %s
            """,
            (scope_type, scope_external_id, funnel_id, scope_type, scope_external_id, funnel_id),
        )

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
            select %s, %s, %s, 'crm_owner', 1,
                   not exists (
                     select 1
                     from jarvis.call_links existing
                     where existing.call_id = %s
                       and existing.entity_type = %s
                       and existing.is_primary
                   )
            on conflict (call_id, entity_type, external_id) do update set
              confidence = excluded.confidence,
              -- A call can have one primary link per CRM entity type.  Keep
              -- the already selected primary link instead of promoting every
              -- later Bitrix relation to primary.
              is_primary = jarvis.call_links.is_primary or not exists (
                select 1
                from jarvis.call_links existing
                where existing.call_id = excluded.call_id
                  and existing.entity_type = excluded.entity_type
                  and existing.is_primary
              )
            """,
            (call_id, call.crm_owner_type, call.crm_owner_id, call_id, call.crm_owner_type),
        )

    def write_reactivation_action(self, payload: Dict[str, Any], *, status: str, error: str = "") -> None:
        """Persist the narrow CRM write audit before its result is returned."""
        if status not in {"succeeded", "rejected"}:
            raise ValueError("Unknown reactivation action status")
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                create table if not exists jarvis.reactivation_actions (
                    id bigserial primary key,
                    deal_id text not null,
                    source_category_id text,
                    target_category_id text,
                    target_stage_id text,
                    actor text not null,
                    status text not null,
                    error text not null default '',
                    happened_at timestamptz not null,
                    created_at timestamptz not null default now()
                )
                """
            )
            cursor.execute(
                """
                insert into jarvis.reactivation_actions
                    (deal_id, source_category_id, target_category_id, target_stage_id, actor, status, error, happened_at)
                values (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    str(payload.get("dealId") or ""),
                    str(payload.get("sourceCategoryId") or ""),
                    str(payload.get("targetCategoryId") or ""),
                    str(payload.get("targetStageId") or ""),
                    str(payload.get("actor") or "dashboard-full-access"),
                    status,
                    error,
                    str(payload.get("happenedAt") or ""),
                ),
            )
        self.connection.commit()

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
                  transcript.diarization as transcript_segments,
                  review.call_type_key as review_call_type_key,
                  review.reason as review_reason,
                  review.reviewer_name as review_reviewer_name,
                  review.reviewed_at as review_reviewed_at,
                  manual_view.reviewed as manual_reviewed,
                  manual_view.reviewer_name as manual_reviewer_name,
                  manual_view.reviewed_at as manual_reviewed_at
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
                left join lateral (
                  select cr.*
                  from jarvis.call_reviews cr
                  where cr.call_id = c.id
                  order by cr.reviewed_at desc, cr.id desc
                  limit 1
                ) review on true
                left join jarvis.call_manual_views manual_view on manual_view.call_id = c.id
                order by c.occurred_at desc, c.id desc
                """
            )
            rows = cursor.fetchall()
        self.connection.commit()

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
            call["_manual_review"] = {
                "reviewed": bool(row.get("manual_reviewed")),
                "reviewer_name": str(row.get("manual_reviewer_name") or ""),
                "reviewed_at": self._timestamp(row.get("manual_reviewed_at")),
            }
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
            manual_type_key = str(row.get("review_call_type_key") or "").strip()
            if manual_type_key:
                from claude_analyzer import CALL_TYPES

                ai_call_type = result.get("call_type") if isinstance(result.get("call_type"), dict) else {}
                result["ai_call_type"] = dict(ai_call_type)
                result["call_type"] = {
                    "key": manual_type_key,
                    "label": str((CALL_TYPES.get(manual_type_key) or {}).get("label") or manual_type_key),
                    "confirmed": True,
                    "source": "manual",
                }
                result["manual_review"] = {
                    "call_type_key": manual_type_key,
                    "reason": str(row.get("review_reason") or ""),
                    "reviewer_name": str(row.get("review_reviewer_name") or "Руководитель"),
                    "reviewed_at": self._timestamp(row.get("review_reviewed_at")),
                }
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

    def load_persisted_activity_ids(self, activity_ids: Iterable[str]) -> set[str]:
        """Return only completed current-run ids, without loading all history.

        Workers use this small lookup to skip calls already handled in an
        earlier run.  Historical CRM context is read separately from
        ``context_facts`` so a new recording never needs thousands of prior
        transcripts or analysis payloads in process memory.
        """
        identifiers = sorted({str(activity_id).strip() for activity_id in activity_ids if str(activity_id).strip()})
        if not identifiers:
            return set()
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                select distinct c.source_call_id
                from jarvis.calls c
                join jarvis.call_analyses ca on ca.call_id = c.id
                where c.source = 'bitrix24'
                  and c.source_call_id = any(%s)
                  and ca.status <> 'failed'
                  and ca.result is not null
                """,
                (identifiers,),
            )
            rows = cursor.fetchall()
        self.connection.commit()
        return {
            str(row["source_call_id"] if isinstance(row, dict) else row[0])
            for row in rows
        }

    def load_context_for_call(self, call: Dict[str, Any], *, limit: int = 8) -> Dict[str, Any] | None:
        """Read ready company memory without opening or transcribing old audio.

        The selection is deliberately bounded at the database boundary.  All
        facts remain in the CRM folder, while the model receives only recent,
        source-labelled evidence from the same sales funnel.
        """
        scopes = context_scopes_for_call(call)
        occurred_at = str(call.get("created") or "").strip()
        if not scopes or not occurred_at:
            return None
        limit = max(1, min(int(limit), 8))
        # Bound database work before deduplication: each explicit relation
        # contributes at most eight indexed rows, then only eight facts reach
        # the prompt.  A company's full history can grow without making this
        # selection scan its whole archive.
        scopes = scopes[:8]
        priority_by_scope = {"deal": 0, "contact": 1, "company": 2}
        candidates = []
        candidate_params: list[Any] = []
        for scope_type, scope_external_id, funnel_id in scopes:
            candidates.append(
                """
                (select cf.call_id, cf.occurred_at, cf.fact, cf.scope_type, cf.scope_external_id, %s::integer as scope_priority
                 from jarvis.context_facts cf
                 where cf.scope_type = %s and cf.scope_external_id = %s and cf.funnel_id = %s
                   and cf.occurred_at < %s::timestamptz
                 order by cf.occurred_at desc, cf.call_id desc
                 limit %s)
                """
            )
            candidate_params.extend([
                priority_by_scope.get(scope_type, 3), scope_type, scope_external_id, funnel_id, occurred_at, limit,
            ])
        candidates_sql = " union all ".join(candidates)
        with self.connection.cursor() as cursor:
            cursor.execute(
                f"""
                with candidates as (
                  {candidates_sql}
                ), selected as (
                  select distinct on (cf.call_id)
                    cf.call_id, cf.occurred_at, cf.fact, cf.scope_type, cf.scope_external_id
                  from candidates cf
                  order by cf.call_id, cf.scope_priority, cf.occurred_at desc
                ), recent as (
                  select * from selected
                  order by occurred_at desc, call_id desc
                  limit %s
                )
                select * from recent
                order by occurred_at asc, call_id asc
                """,
                [*candidate_params, limit],
            )
            facts = cursor.fetchall()
            profile_conditions = " or ".join(
                "(scope_type = %s and scope_external_id = %s and funnel_id = %s)"
                for _ in scopes
            )
            cursor.execute(
                f"""
                select scope_type, scope_external_id, facts_count, latest_call_at, current_state
                from jarvis.context_profiles
                where {profile_conditions}
                """,
                [value for scope in scopes for value in scope],
            )
            profiles = cursor.fetchall()
        self.connection.commit()
        if not facts and not profiles:
            return None

        source_labels = {"deal": "current_deal", "contact": "contact", "company": "company"}
        previous_calls = []
        sources: Dict[str, Dict[str, Any]] = {}
        for row in facts:
            fact = self._json_object(row.get("fact"))
            source = source_labels.get(str(row.get("scope_type") or ""), "company")
            source_meta = sources.setdefault(source, {"count": 0})
            source_meta["count"] += 1
            if source == "current_deal":
                source_meta["deal_id"] = str(row.get("scope_external_id") or "")
            previous_calls.append(
                {
                    "activity_id": str(fact.get("activity_id") or row.get("call_id") or ""),
                    "date": str(fact.get("date") or self._timestamp(row.get("occurred_at"))),
                    "call_type": str(fact.get("call_type") or "Тип не указан"),
                    "summary": str(fact.get("summary") or "нет"),
                    "objections": "; ".join(str(item) for item in (fact.get("objections") or []) if str(item)) or "не выделены",
                    "agreements": str(fact.get("outcome") or "не выделены"),
                    "next_step": str(fact.get("next_step") or "не указан"),
                    "source": source,
                    "source_deal_id": "",
                }
            )

        preferred_profiles = sorted(
            profiles,
            key=lambda row: ({"company": 0, "contact": 1, "deal": 2}.get(str(row.get("scope_type") or ""), 3), -int(row.get("facts_count") or 0)),
        )
        profile = preferred_profiles[0] if preferred_profiles else {}
        state = self._json_object(profile.get("current_state")) if profile else {}
        crm = call.get("crm") or {}
        return {
            "crm": {
                "stage": crm.get("stage_name") or crm.get("stage_id"),
                "responsible": (call.get("manager") or {}).get("name"),
                "next_activity_date": crm.get("next_activity_date"),
            },
            "previous_calls": previous_calls,
            "sources": sources,
            "memory": {
                "scope": str(profile.get("scope_type") or ""),
                "facts_count": int(profile.get("facts_count") or 0),
                "latest_call_at": self._timestamp(profile.get("latest_call_at")) if profile else "",
                "last_outcome": str(state.get("last_outcome") or ""),
                "next_step": str(state.get("next_step") or ""),
            },
        }

    def save_call_manual_review(
        self, activity_id: str, reviewed: bool, reviewer_name: str,
    ) -> Dict[str, Any]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                select id from jarvis.calls
                where source = 'bitrix24' and source_call_id = %s
                """,
                (activity_id,),
            )
            row = cursor.fetchone()
            if not row:
                raise ValueError("Звонок не найден в хранилище Jarvis")
            call_id = row["id"] if isinstance(row, dict) else row[0]
            cursor.execute(
                """
                insert into jarvis.call_manual_views
                  (call_id, reviewed, reviewer_name, reviewed_at)
                values (%s, %s, %s, case when %s then now() else null end)
                on conflict (call_id) do update set
                  reviewed = excluded.reviewed,
                  reviewer_name = excluded.reviewer_name,
                  reviewed_at = case when excluded.reviewed then now() else jarvis.call_manual_views.reviewed_at end,
                  updated_at = now()
                returning reviewed, reviewer_name, reviewed_at
                """,
                (call_id, bool(reviewed), reviewer_name, bool(reviewed)),
            )
            saved = cursor.fetchone()
        self.connection.commit()
        return dict(saved) if isinstance(saved, dict) else {
            "reviewed": bool(saved[0]), "reviewer_name": str(saved[1] or ""),
            "reviewed_at": self._timestamp(saved[2]),
        }

    def save_call_review(
        self,
        activity_id: str,
        call_type_key: str,
        reason: str,
        reviewer_name: str,
        *,
        reanalysis_requested: bool = False,
    ) -> Dict[str, Any]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                select id from jarvis.calls
                where source = 'bitrix24' and source_call_id = %s
                """,
                (activity_id,),
            )
            row = cursor.fetchone()
            if not row:
                raise ValueError("Звонок не найден в хранилище Jarvis")
            call_id = row["id"] if isinstance(row, dict) else row[0]
            cursor.execute(
                """
                insert into jarvis.call_reviews
                    (call_id, call_type_key, reason, reviewer_name, reviewed_at, reanalysis_requested)
                values (%s, %s, %s, %s, now(), %s)
                on conflict (call_id) do update set
                  call_type_key = excluded.call_type_key,
                  reason = excluded.reason,
                  reviewer_name = excluded.reviewer_name,
                  reviewed_at = excluded.reviewed_at,
                  reanalysis_requested = excluded.reanalysis_requested
                returning call_type_key, reason, reviewer_name, reviewed_at, reanalysis_requested
                """,
                (call_id, call_type_key, reason, reviewer_name, reanalysis_requested),
            )
            saved = cursor.fetchone()
            from claude_analyzer import CALL_TYPES

            label = str((CALL_TYPES.get(call_type_key) or {}).get("label") or call_type_key)
            cursor.execute(
                """
                update jarvis.context_facts
                set fact = jsonb_set(
                  jsonb_set(fact, '{call_type_key}', to_jsonb(%s::text), true),
                  '{call_type}', to_jsonb(%s::text), true
                ), updated_at = now()
                where call_id = %s
                """,
                (call_type_key, label, call_id),
            )
        self.connection.commit()
        return dict(saved) if isinstance(saved, dict) else {
            "call_type_key": saved[0], "reason": saved[1], "reviewer_name": saved[2],
            "reviewed_at": self._timestamp(saved[3]), "reanalysis_requested": bool(saved[4]),
        }

    def review_corrections(self) -> Dict[str, Any]:
        """Expose current manual type decisions to the isolated analysis worker."""
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                select c.source_call_id, cr.call_type_key, cr.reason, cr.reviewer_name, cr.reviewed_at
                from jarvis.call_reviews cr
                join jarvis.calls c on c.id = cr.call_id
                """
            )
            rows = cursor.fetchall()
        self.connection.commit()
        return {
            str(row["source_call_id"] if isinstance(row, dict) else row[0]): {
                "call_type_key": str(row["call_type_key"] if isinstance(row, dict) else row[1]),
                "reason": str(row["reason"] if isinstance(row, dict) else row[2]),
                "reviewer_name": str(row["reviewer_name"] if isinstance(row, dict) else row[3]),
                "reviewed_at": self._timestamp(row["reviewed_at"] if isinstance(row, dict) else row[4]),
            }
            for row in rows
        }

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
