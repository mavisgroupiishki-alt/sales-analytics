"""Explainable recommendations for deals in the Bitrix reactivation funnel.

The scanner is deliberately read-only.  A different explicit function performs
the only permitted CRM mutation and validates the deal again immediately before
it is moved to the main sales funnel.
"""

from __future__ import annotations

import os
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Sequence
from urllib.parse import urlparse


RECENT_CONTACT_DAYS = 60
FAR_FUTURE_DAYS = 30
MAX_AI_ASSESSMENTS = 50
MAX_TEXT = 900

logger = logging.getLogger(__name__)


class ReactivationError(RuntimeError):
    """A safe, user-presentable failure in the reactivation workflow."""


def _as_list(value: Any) -> list[Dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [item for item in value.values() if isinstance(item, dict)]
    return []


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _iso(value: datetime | None) -> str:
    return value.isoformat() if value else ""


def _text(value: Any, limit: int = MAX_TEXT) -> str:
    return " ".join(str(value or "").split())[:limit]


def _portal_url(client: Any) -> str:
    endpoint = str(getattr(client, "endpoint", "") or getattr(client, "webhook", ""))
    parsed = urlparse(endpoint)
    return f"https://{parsed.netloc}" if parsed.scheme == "https" and parsed.netloc else ""


def _category_rows(client: Any) -> list[Dict[str, Any]]:
    # Portals may expose either the legacy or the current category method.
    for method, params in (
        ("crm.dealcategory.list", {"order": {"SORT": "ASC"}}),
        ("crm.category.list", {"entityTypeId": 2}),
    ):
        try:
            data = client.call(method, params)
            result = data.get("result") or {}
            rows = _as_list(result.get("categories") if isinstance(result, dict) else result)
            if rows:
                return rows
        except Exception:
            continue
    raise ReactivationError("Не удалось проверить воронку «Реанимация» в Bitrix.")


def find_reactivation_category(client: Any) -> tuple[int, str]:
    rows = _category_rows(client)
    matches = [
        row for row in rows
        if "реанимац" in _text(row.get("NAME") or row.get("name")).casefold()
    ]
    if len(matches) != 1:
        raise ReactivationError("В Bitrix должна быть ровно одна воронка с названием «Реанимация».")
    row = matches[0]
    value = row.get("ID", row.get("id"))
    try:
        return int(value), _text(row.get("NAME") or row.get("name"))
    except (TypeError, ValueError) as exc:
        raise ReactivationError("У воронки «Реанимация» не найден корректный ID.") from exc


def _deal_rows(client: Any, category_id: int) -> list[Dict[str, Any]]:
    fields = [
        "ID", "TITLE", "CATEGORY_ID", "STAGE_ID", "ASSIGNED_BY_ID", "DATE_CREATE", "DATE_MODIFY",
        "LAST_ACTIVITY_TIME", "OPPORTUNITY", "CURRENCY_ID", "COMMENTS", "COMPANY_ID", "CONTACT_ID",
        "SOURCE_ID", "CLOSEDATE",
    ]
    try:
        rows = client.call_all(
            "crm.deal.list",
            {"order": {"DATE_MODIFY": "DESC", "ID": "DESC"}, "filter": {"CATEGORY_ID": category_id, "CLOSED": "N"}, "select": fields},
        )
    except Exception as exc:
        raise ReactivationError("Не удалось получить открытые сделки реанимации.") from exc
    # This is the source set for the historical-call worker.  Do not cap it:
    # an omitted open deal would silently miss its call history and could lead
    # to an ungrounded recommendation.
    return [item for item in rows if isinstance(item, dict)]


def fetch_reactivation_calls(client: Any, deals: Sequence[Mapping[str, Any]]) -> list[Dict[str, Any]]:
    """Return every completed call directly linked to an open reactivation deal.

    This intentionally does not infer a deal through the contact or company.
    The caller asked for the evidence that belongs to this exact deal, and a
    direct Bitrix activity link is the only unambiguous relationship.
    """
    raw_by_id: dict[str, Dict[str, Any]] = {}
    for deal in deals:
        deal_id = str(deal.get("ID") or "")
        if not deal_id:
            continue
        try:
            activities = client.call_all(
                "crm.activity.list",
                {
                    "filter": {
                        "TYPE_ID": 2,
                        "COMPLETED": "Y",
                        "OWNER_TYPE_ID": 2,
                        "OWNER_ID": deal_id,
                    },
                    "select": ["*", "COMMUNICATIONS"],
                    "order": {"CREATED": "ASC", "ID": "ASC"},
                },
            )
        except Exception as exc:
            # A partial scan must not masquerade as a complete history.  Keep
            # the previous persisted snapshot intact and retry the whole run.
            raise ReactivationError(
                f"Не удалось получить историю звонков сделки {deal_id}; анализ реанимации не обновлён."
            ) from exc
        for activity in activities:
            if not isinstance(activity, dict) or not activity.get("ID"):
                continue
            # Do not trust a broad CRM response over the exact requested link.
            if str(activity.get("OWNER_ID") or "") != deal_id or int(activity.get("OWNER_TYPE_ID") or 0) != 2:
                continue
            raw_by_id[str(activity["ID"])] = activity

    if not raw_by_id:
        return []

    from bitrix import fetch_users, normalize_call

    manager_ids: set[int] = set()
    for activity in raw_by_id.values():
        for field in ("AUTHOR_ID", "CREATED_BY_ID", "CREATED_BY", "RESPONSIBLE_ID"):
            try:
                if activity.get(field):
                    manager_ids.add(int(activity[field]))
            except (TypeError, ValueError):
                continue
    users = fetch_users(client, list(manager_ids))
    calls = [
        normalize_call(activity, users, allowed_manager_ids=set())
        for activity in raw_by_id.values()
    ]
    return sorted(calls, key=lambda call: (str(call.get("created") or ""), str(call.get("activity_id") or "")))


def _activities_for_deals(client: Any, deal_ids: Sequence[str]) -> tuple[dict[str, list[Dict[str, Any]]], dict[str, list[Dict[str, Any]]]]:
    completed: dict[str, list[Dict[str, Any]]] = {deal_id: [] for deal_id in deal_ids}
    planned: dict[str, list[Dict[str, Any]]] = {deal_id: [] for deal_id in deal_ids}
    fields = ["ID", "OWNER_ID", "TYPE_ID", "COMPLETED", "CREATED", "LAST_UPDATED", "START_TIME", "DEADLINE", "SUBJECT", "DESCRIPTION"]
    for offset in range(0, len(deal_ids), 50):
        chunk = deal_ids[offset:offset + 50]
        for status, target in (("Y", completed), ("N", planned)):
            try:
                rows = client.call_all(
                    "crm.activity.list",
                    {"order": {"CREATED": "DESC", "ID": "DESC"}, "filter": {"OWNER_TYPE_ID": 2, "OWNER_ID": chunk, "COMPLETED": status}, "select": fields},
                )
            except Exception:
                # A missing activity permission must not turn into a fictitious
                # "no contacts" fact. The response marks that source absent.
                continue
            for row in rows:
                owner_id = str(row.get("OWNER_ID") or "")
                if owner_id in target:
                    target[owner_id].append(row)
    return completed, planned


def _latest_activity(items: Iterable[Mapping[str, Any]]) -> datetime | None:
    values = [
        _parse_time(item.get("LAST_UPDATED") or item.get("CREATED") or item.get("START_TIME"))
        for item in items
    ]
    return max((value for value in values if value), default=None)


def _nearest_planned(items: Iterable[Mapping[str, Any]], now: datetime) -> tuple[datetime | None, str]:
    dated: list[tuple[datetime, str]] = []
    for item in items:
        value = _parse_time(item.get("DEADLINE") or item.get("START_TIME"))
        if value:
            dated.append((value, _text(item.get("SUBJECT") or item.get("DESCRIPTION"), 240)))
    if not dated:
        return None, ""
    future = [item for item in dated if item[0] >= now]
    return min(future or dated, key=lambda item: item[0])


def _has_ai_analysis(record: Any) -> bool:
    analysis = record.get("analysis") if isinstance(record, dict) else None
    if not isinstance(analysis, dict):
        return False
    # Short calls have a stored technical transcription status but were never
    # sent to the model. They must not be displayed as an AI assessment.
    return not str(analysis.get("exclusion_reason") or "").startswith("Звонок короче")


def _is_short_call_record(record: Any) -> bool:
    analysis = record.get("analysis") if isinstance(record, dict) else None
    return isinstance(analysis, dict) and str(analysis.get("exclusion_reason") or "").startswith("Звонок короче")


def _call_stats(calls: Iterable[Mapping[str, Any]], analyses: Mapping[str, Any], deal_id: str) -> Dict[str, Any]:
    relevant = [
        call for call in calls
        if str((call.get("crm") or {}).get("owner_type") or "") == "deal"
        and str((call.get("crm") or {}).get("owner_id") or "") == deal_id
    ]
    analyzed = 0
    unavailable = 0
    pending = 0
    not_scored = 0
    issues: list[Dict[str, str]] = []
    for call in relevant:
        activity_id = str(call.get("activity_id") or "")
        occurred_at = str(call.get("created") or "")
        if _has_ai_analysis(analyses.get(activity_id)):
            analyzed += 1
            continue
        if _is_short_call_record(analyses.get(activity_id)):
            not_scored += 1
            issues.append({
                "activityId": activity_id,
                "occurredAt": occurred_at,
                "reason": "Короткий звонок: сохранён без оценки качества.",
            })
            continue
        audio = call.get("audio") or {}
        status = str(audio.get("status") or "").lower()
        if not audio.get("file_id") or status in {"empty", "unavailable", "invalid", "error"}:
            unavailable += 1
            reason = {
                "empty": "Bitrix передал запись нулевой длительности.",
                "unavailable": "Запись недоступна в Bitrix.",
                "invalid": "Файл в Bitrix не является аудиозаписью.",
                "error": "Не удалось получить запись из Bitrix.",
            }.get(status, "В Bitrix нет доступной записи.")
            issues.append({"activityId": activity_id, "occurredAt": occurred_at, "reason": reason})
        else:
            pending += 1
            issues.append({
                "activityId": activity_id,
                "occurredAt": occurred_at,
                "reason": "Запись ожидает фонового анализа.",
            })
    return {
        "total": len(relevant),
        "analyzed": analyzed,
        "unavailable": unavailable,
        "pending": pending,
        "notScored": not_scored,
        "issues": issues,
    }


def _call_context(calls: Iterable[Mapping[str, Any]], analyses: Mapping[str, Any], deal_id: str) -> list[Dict[str, Any]]:
    rows = []
    for call in calls:
        crm = call.get("crm") or {}
        if str(crm.get("owner_type") or "") != "deal" or str(crm.get("owner_id") or "") != deal_id:
            continue
        activity_id = str(call.get("activity_id") or "")
        record = analyses.get(activity_id) or {}
        if not _has_ai_analysis(record):
            continue
        analysis = record.get("analysis") if isinstance(record, dict) else {}
        if not isinstance(analysis, dict):
            analysis = {}
        rows.append({
            "activityId": activity_id,
            "occurredAt": str(call.get("created") or ""),
            "summary": _text(analysis.get("summary") or analysis.get("result") or analysis.get("call_goal"), 320),
            "nextStep": _text(analysis.get("next_step") or analysis.get("recommendation"), 240),
            "score": analysis.get("overall_score") if isinstance(analysis.get("overall_score"), (int, float)) else None,
        })
    rows.sort(key=lambda row: row["occurredAt"], reverse=True)
    return rows[:8]


def _comment_context(deal: Mapping[str, Any]) -> list[Dict[str, str]]:
    text = _text(deal.get("COMMENTS"))
    return [{"source": "Комментарий сделки", "text": text}] if text else []


def _field_context(deal: Mapping[str, Any]) -> list[Dict[str, str]]:
    labels = {
        "STAGE_ID": "Стадия", "DATE_CREATE": "Создана", "DATE_MODIFY": "Изменена",
        "LAST_ACTIVITY_TIME": "Последняя активность", "CLOSEDATE": "Плановая дата закрытия",
        "SOURCE_ID": "Источник", "OPPORTUNITY": "Сумма",
    }
    return [
        {"label": label, "value": _text(deal.get(key), 160)}
        for key, label in labels.items() if _text(deal.get(key), 160)
    ]


def _ai_assessment(*, deal: Mapping[str, Any], calls: list[Dict[str, Any]], comments: list[Dict[str, str]], fields: list[Dict[str, str]]) -> Dict[str, Any] | None:
    """Ask the configured Jarvis model for an advisory decision.

    Deterministic exclusions always win. If the Vibe key is unavailable or the
    answer is invalid, the scanner falls back to the transparent rule-based
    recommendation and labels that fact rather than pretending an AI answer.
    """
    if not os.environ.get("VIBE_API_KEY"):
        return None
    prompt = f"""Ты — Джарвис, помощник РОПа. Реши, стоит ли сейчас вернуть сделку из воронки «Реанимация» в работу.
Не выдумывай фактов, не предлагай контакт без основания. Тебе доступны не исходные персональные данные, а краткие результаты уже разобранных звонков, комментарии и поля CRM.

СДЕЛКА:
{json.dumps({'title': _text(deal.get('TITLE'), 240), 'stage': _text(deal.get('STAGE_ID'), 120)}, ensure_ascii=False)}

ПОСЛЕДНИЕ ЗВОНКИ JARVIS:
{json.dumps(calls[:8], ensure_ascii=False)}

КОММЕНТАРИИ СДЕЛКИ:
{json.dumps(comments[:3], ensure_ascii=False)}

ПОЛЯ СДЕЛКИ:
{json.dumps(fields[:10], ensure_ascii=False)}

Верни строго JSON без Markdown:
{{"recommend":true,"priority":1,"reasons":["конкретный факт"],"next_step":"одно действие","confidence":0.0}}
priority от 1 до 5. recommend=false, если доказательств для контакта нет."""
    try:
        from claude_analyzer import call_claude_api, decode_json_response
        text, _meta = call_claude_api(prompt, max_tokens=900)
        result = decode_json_response(text)
        if not isinstance(result.get("recommend"), bool):
            return None
        priority = int(result.get("priority") or 0)
        reasons = result.get("reasons") if isinstance(result.get("reasons"), list) else []
        return {
            "recommend": result["recommend"],
            "priority": max(1, min(priority, 5)),
            "reasons": [_text(reason, 320) for reason in reasons if _text(reason, 320)][:4],
            "nextStep": _text(result.get("next_step"), 300),
            "confidence": result.get("confidence") if isinstance(result.get("confidence"), (int, float)) else None,
        }
    except Exception:
        return None


def _recommendation_for(
    deal: Mapping[str, Any], *, completed: Sequence[Mapping[str, Any]], planned: Sequence[Mapping[str, Any]],
    calls: Iterable[Mapping[str, Any]], analyses: Mapping[str, Any], now: datetime, portal: str, run_ai: bool = True,
) -> Dict[str, Any]:
    deal_id = str(deal.get("ID") or "")
    call_context = _call_context(calls, analyses, deal_id)
    call_stats = _call_stats(calls, analyses, deal_id)
    comments = _comment_context(deal)
    fields = _field_context(deal)
    last_contact = max(
        [value for value in (_latest_activity(completed), _parse_time(deal.get("LAST_ACTIVITY_TIME")), _parse_time(call_context[0]["occurredAt"]) if call_context else None) if value],
        default=None,
    )
    next_contact, next_subject = _nearest_planned(planned, now)
    days_silent = (now - last_contact).days if last_contact else None
    exclusions: list[str] = []
    if last_contact and last_contact >= now - timedelta(days=RECENT_CONTACT_DAYS):
        exclusions.append(f"С клиентом уже связывались {days_silent} дн. назад: повторная реанимация пока не нужна.")
    if next_contact and next_contact > now + timedelta(days=FAR_FUTURE_DAYS):
        exclusions.append(
            f"Следующая договорённость только {_iso(next_contact)[:10]}; это дальше {FAR_FUTURE_DAYS} дней."
        )

    reasons: list[str] = []
    priority = 0
    if next_contact and next_contact <= now:
        reasons.append(f"Просрочен согласованный следующий контакт: {_iso(next_contact)[:10]}{(': ' + next_subject) if next_subject else ''}.")
        priority += 5
    if days_silent is None:
        reasons.append("В доступном контексте нет зафиксированного последнего контакта.")
        priority += 2
    elif days_silent >= RECENT_CONTACT_DAYS:
        reasons.append(f"Нет контакта с клиентом {days_silent} дн.")
        priority += 4
    if call_context:
        latest = call_context[0]
        if latest.get("nextStep"):
            reasons.append("Jarvis нашёл в последнем звонке следующий шаг: " + latest["nextStep"])
            priority += 2
        elif latest.get("summary"):
            reasons.append("Jarvis учёл резюме последнего звонка: " + latest["summary"])
            priority += 1
    for comment in comments:
        lowered = comment["text"].casefold()
        if any(word in lowered for word in ("перезвон", "договор", "вернут", "бюджет", "соглас")):
            reasons.append("Комментарий сделки содержит рабочий сигнал: " + comment["text"])
            priority += 1
            break

    ai = _ai_assessment(deal=deal, calls=call_context, comments=comments, fields=fields) if run_ai and not exclusions else None
    if ai and ai["recommend"]:
        priority += ai["priority"]
        reasons = ai["reasons"] + reasons
    elif ai and not ai["recommend"]:
        exclusions.append("Jarvis не нашёл достаточного основания для нового контакта в доступном контексте.")
    recommended = bool(reasons and priority >= 3 and not exclusions)
    if recommended:
        level = "high" if priority >= 6 else "medium"
        next_step = "Связаться с клиентом и сверить актуальность договорённости."
    else:
        level = "excluded" if exclusions else "low"
        next_step = "Не возвращать в работу без новой причины для контакта."
    return {
        "dealId": deal_id,
        "title": _text(deal.get("TITLE") or f"Сделка {deal_id}", 240),
        "stage": _text(deal.get("STAGE_ID"), 120),
        "assignedById": str(deal.get("ASSIGNED_BY_ID") or ""),
        "amount": deal.get("OPPORTUNITY"),
        "currency": _text(deal.get("CURRENCY_ID") or "BYN", 16),
        "url": f"{portal}/crm/deal/details/{deal_id}/" if portal and deal_id.isdigit() else "",
        "recommended": recommended,
        "priority": priority,
        "priorityLabel": {"high": "Высокий", "medium": "Средний", "low": "Низкий", "excluded": "Не реанимировать"}[level],
        "priorityLevel": level,
        "reasons": reasons,
        "exclusions": exclusions,
        "suggestedNextStep": next_step,
        "lastContactAt": _iso(last_contact),
        "nextContactAt": _iso(next_contact),
        "context": {"calls": call_context, "callStats": call_stats, "comments": comments, "fields": fields},
        "analysisMode": "ai" if ai else "rules_with_call_analysis",
        "confidence": ai.get("confidence") if ai else None,
    }


def build_reactivation_queue(client: Any, calls: Iterable[Mapping[str, Any]], analyses: Mapping[str, Any], *, now: datetime | None = None) -> Dict[str, Any]:
    now = now or datetime.now().astimezone()
    category_id, category_name = find_reactivation_category(client)
    deals = _deal_rows(client, category_id)
    completed, planned = _activities_for_deals(client, [str(item.get("ID") or "") for item in deals])
    portal = _portal_url(client)
    deal_by_id = {str(deal.get("ID") or ""): deal for deal in deals if str(deal.get("ID") or "")}
    all_rows = [
        _recommendation_for(
            deal, completed=completed.get(str(deal.get("ID") or ""), []), planned=planned.get(str(deal.get("ID") or ""), []),
            calls=calls, analyses=analyses, now=now, portal=portal, run_ai=False,
        ) for deal in deals if str(deal.get("ID") or "")
    ]
    # The CRM may contain hundreds of dormant items.  First use deterministic
    # facts to select the urgent bounded subset, then ask the model only about
    # that subset. This keeps the hourly scan predictable and auditable.
    ai_candidates = sorted(
        (row for row in all_rows if not row["exclusions"] and int(row["priority"]) >= 3),
        key=lambda row: (-int(row["priority"]), row["dealId"]),
    )[:MAX_AI_ASSESSMENTS]
    recalculated = {
        row["dealId"]: _recommendation_for(
            deal_by_id[row["dealId"]], completed=completed.get(row["dealId"], []), planned=planned.get(row["dealId"], []),
            calls=calls, analyses=analyses, now=now, portal=portal, run_ai=True,
        ) for row in ai_candidates
    }
    all_rows = [recalculated.get(row["dealId"], row) for row in all_rows]
    recommendations = sorted(
        (row for row in all_rows if row["recommended"]), key=lambda row: (-int(row["priority"]), row["title"].casefold())
    )
    excluded = sorted(
        (row for row in all_rows if row["exclusions"]), key=lambda row: (row["lastContactAt"] or "", row["title"].casefold()), reverse=True
    )[:100]
    return {
        "ok": True,
        "generatedAt": now.isoformat(),
        "funnel": {"id": category_id, "name": category_name},
        "thresholds": {"recentContactDays": RECENT_CONTACT_DAYS, "farFutureDays": FAR_FUTURE_DAYS},
        "summary": {"scanned": len(all_rows), "recommended": len(recommendations), "excluded": sum(bool(row["exclusions"]) for row in all_rows)},
        "recommendations": recommendations,
        "excluded": excluded,
    }


def analyse_reactivation_calls(client: Any, runtime_dir: Path, *, now: datetime | None = None) -> Dict[str, int]:
    """Prepare every available direct recording from open reactivation deals.

    The worker owns this expensive operation. The dashboard route stays a fast
    read and only presents analyses that have been safely persisted. Existing
    immutable analyses are never sent to the model again.
    """
    from jarvis_store import JarvisRepository

    now = now or datetime.now().astimezone()
    category_id, _category_name = find_reactivation_category(client)
    deals = _deal_rows(client, category_id)
    direct_calls = fetch_reactivation_calls(client, deals)

    database_url = os.environ.get("JARVIS_DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("Для фонового анализа реанимации нужна private Jarvis database.")
    repository = JarvisRepository.connect(database_url)
    try:
        stored_calls, analyses = repository.load_snapshot()
    except Exception:
        repository.close()
        raise

    try:
        return _analyse_reactivation_calls_with_repository(
            client,
            runtime_dir,
            now=now,
            deals=deals,
            direct_calls=direct_calls,
            repository=repository,
            stored_calls=stored_calls,
            analyses=analyses,
        )
    finally:
        repository.close()


def _analyse_reactivation_calls_with_repository(
    client: Any,
    runtime_dir: Path,
    *,
    now: datetime,
    deals: list[Dict[str, Any]],
    direct_calls: list[Dict[str, Any]],
    repository: Any,
    stored_calls: list[Dict[str, Any]],
    analyses: Dict[str, Any],
) -> Dict[str, int]:
    """Run the analysis with one already-open durable context repository."""
    from bitrix import NonAudioFileError, download_audio, mirror_snapshot_to_jarvis
    from claude_analyzer import (
        MIN_DURATION_FOR_ANALYSIS,
        analyze_transcript,
        apply_manual_corrections,
        build_deal_context,
        load_manual_corrections,
        load_scripts,
        mirror_analyses_to_jarvis,
        transcribe_audio,
    )

    # A fresh Bitrix activity does not retain a prior terminal download status.
    # Carry that status forward before writing the latest raw snapshot, so a
    # known non-audio file is not downloaded and billed again each hour.
    stored_by_id = {
        str(call.get("activity_id") or ""): call
        for call in stored_calls
        if str(call.get("activity_id") or "")
    }
    for call in direct_calls:
        activity_id = str(call.get("activity_id") or "")
        previous_audio = (stored_by_id.get(activity_id) or {}).get("audio") or {}
        current_audio = call.get("audio") or {}
        if (
            previous_audio.get("status") in {"empty", "unavailable", "invalid"}
            and str(previous_audio.get("file_id") or "") == str(current_audio.get("file_id") or "")
        ):
            call.setdefault("audio", {}).update({
                "status": previous_audio["status"],
                "error": previous_audio.get("error"),
            })

    mirror_snapshot_to_jarvis(direct_calls)
    history_by_id = {str(call.get("activity_id") or ""): call for call in stored_calls if str(call.get("activity_id") or "")}
    history_by_id.update({str(call.get("activity_id") or ""): call for call in direct_calls if str(call.get("activity_id") or "")})
    history_calls = list(history_by_id.values())
    corrections = load_manual_corrections()
    scripts = load_scripts()
    audio_dir = runtime_dir / "reactivation_audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    summary = {"deals": len(deals), "calls": len(direct_calls), "analyzed": 0, "alreadyAnalyzed": 0, "notScored": 0, "unavailable": 0, "failed": 0}
    completed: dict[str, Dict[str, Any]] = {}
    # Persist each completed assessment immediately so a later call of the
    # same company in this run can read it from durable context.  Keep track
    # of successful writes: a final batch must not create a second immutable
    # analysis version when a manually requested reanalysis is forced.
    durably_persisted_activity_ids: set[str] = set()
    for call in direct_calls:
        activity_id = str(call.get("activity_id") or "")
        if not activity_id:
            continue
        if _has_ai_analysis(analyses.get(activity_id)):
            summary["alreadyAnalyzed"] += 1
            continue
        if _is_short_call_record(analyses.get(activity_id)):
            summary["notScored"] += 1
            continue
        audio = call.get("audio") or {}
        file_id = audio.get("file_id")
        if not file_id or str(audio.get("status") or "").lower() in {"empty", "unavailable", "invalid"}:
            summary["unavailable"] += 1
            continue
        path: Path | None = None
        try:
            path = download_audio(client, file_id, audio_dir, audio.get("url"), activity_id)
            call.setdefault("audio", {})["status"] = "available"
            transcription = transcribe_audio(path)
            if len(transcription.get("text") or "") < 50:
                summary["failed"] += 1
                continue
            correction = corrections.get(activity_id) or {}
            duration = int(call.get("duration_sec") or 0)
            if duration and duration < MIN_DURATION_FOR_ANALYSIS:
                analysis = {
                    "review_status": "excluded",
                    "exclude_from_stats": True,
                    "exclusion_reason": "Звонок короче 30 секунд: транскрипт сохранён без оценки качества",
                }
            else:
                try:
                    deal_context = repository.load_context_for_call(call)
                except Exception as exc:
                    logger.warning("Could not read durable call context %s (%s)", activity_id, type(exc).__name__)
                    deal_context = None
                analysis = analyze_transcript(
                    transcription,
                    call,
                    scripts,
                    deal_context=deal_context or build_deal_context(history_calls, analyses, call, corrections),
                    forced_call_type_key=correction.get("call_type_key"),
                )
                analysis = apply_manual_corrections(activity_id, analysis, corrections)
            record = {"call_meta": call, "transcription": transcription, "analysis": analysis, "analyzed_at": now.isoformat()}
            analyses[activity_id] = record
            completed[activity_id] = record
            if mirror_analyses_to_jarvis([call], {activity_id: record}):
                durably_persisted_activity_ids.add(activity_id)
            if _has_ai_analysis(record):
                summary["analyzed"] += 1
        except NonAudioFileError:
            call.setdefault("audio", {})["status"] = "invalid"
            call["audio"]["error"] = "non_audio_file"
            summary["unavailable"] += 1
        except Exception as exc:
            logger.warning("Could not analyze reactivation call %s (%s)", activity_id, type(exc).__name__)
            summary["failed"] += 1
        finally:
            # The transcription and structured result are persisted; retaining
            # the downloaded source recording would grow the worker disk on
            # every historical scan without adding evidence to the queue.
            if path:
                path.unlink(missing_ok=True)

    # Persist terminal audio states (for example, a Bitrix file that is not a
    # recording) so the next hourly pass reports it honestly instead of trying
    # the same unusable file forever.
    mirror_snapshot_to_jarvis(direct_calls)
    if completed:
        pending_persistence = {
            activity_id: record
            for activity_id, record in completed.items()
            if activity_id not in durably_persisted_activity_ids
        }
        if pending_persistence:
            mirror_analyses_to_jarvis(history_calls, pending_persistence)
    return summary


def _main_sales_new_stage(client: Any) -> str:
    try:
        data = client.call("crm.dealcategory.stage.list", {"id": 0})
        rows = _as_list(data.get("result"))
    except Exception as exc:
        raise ReactivationError("Не удалось проверить этапы основной воронки продаж.") from exc
    matches = [
        row for row in rows
        if _text(row.get("NAME") or row.get("name")).casefold() == "новая"
    ]
    if len(matches) != 1:
        raise ReactivationError("В основной воронке Bitrix должен быть ровно один этап «Новая».")
    stage_id = str(matches[0].get("STATUS_ID") or matches[0].get("ID") or "")
    if not stage_id:
        raise ReactivationError("У этапа «Новая» не найден ID.")
    return stage_id


def reactivate_to_new(client: Any, deal_id: str, *, actor: str) -> Dict[str, Any]:
    if not str(deal_id).isdigit():
        raise ReactivationError("Некорректный ID сделки.")
    reactivation_category_id, reactivation_name = find_reactivation_category(client)
    try:
        current = (client.call("crm.deal.get", {"id": int(deal_id)}).get("result") or {})
    except Exception as exc:
        raise ReactivationError("Не удалось прочитать сделку перед переносом.") from exc
    if int(current.get("CATEGORY_ID") or -1) != reactivation_category_id:
        raise ReactivationError("Сделка уже не находится в воронке «Реанимация»; перенос отменён.")
    target_stage = _main_sales_new_stage(client)
    try:
        client.call("crm.deal.update", {"id": int(deal_id), "fields": {"CATEGORY_ID": 0, "STAGE_ID": target_stage}})
    except Exception as exc:
        raise ReactivationError("Bitrix не подтвердил перенос сделки в этап «Новая».") from exc
    return {
        "ok": True,
        "dealId": str(deal_id),
        "from": {"categoryId": reactivation_category_id, "categoryName": reactivation_name, "stageId": str(current.get("STAGE_ID") or "")},
        "to": {"categoryId": 0, "stageId": target_stage, "stageName": "Новая"},
        "actor": _text(actor, 120) or "dashboard-full-access",
        "happenedAt": datetime.now().astimezone().isoformat(),
    }
