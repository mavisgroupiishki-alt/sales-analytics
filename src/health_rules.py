"""Deterministic health scoring for active deals in the main Bitrix sales funnel.

This module has no Bitrix transport and no write operations.  It consumes a
normalised deal snapshot and returns an explainable result that a ROP can
review.  Stage norms are versioned in code for the shadow run and must be
promoted to a database-backed rule version only after calibration.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, Mapping, Optional


MAIN_SALES_CATEGORY_ID = 0
RULE_VERSION = "crm_health_v0"


@dataclass(frozen=True)
class StageRule:
    label: str
    max_stage_hours: Optional[int] = None
    max_stage_business_days: Optional[int] = None
    max_contact_business_days: Optional[int] = None
    requires_product: bool = False
    deferred_demand: bool = False


STAGE_RULES: Dict[str, StageRule] = {
    "NEW": StageRule("1. Новая сделка", max_stage_hours=4),
    "UC_B9P2EQ": StageRule("2. В работе", max_stage_business_days=3, max_contact_business_days=3),
    "8": StageRule("3. Собрана потребность клиента", max_stage_business_days=5, max_contact_business_days=5, requires_product=True),
    "9": StageRule("4. Сделано предложение", max_stage_business_days=2, max_contact_business_days=2, requires_product=True),
    "PREPARATION": StageRule("5. КП отправлено", max_stage_business_days=3, max_contact_business_days=3, requires_product=True),
    "UC_1HFCFO": StageRule("6. Защита КП сделана", max_stage_business_days=2, max_contact_business_days=2, requires_product=True),
    "10": StageRule("7. Получена ОС по КП", max_stage_business_days=2, max_contact_business_days=2, requires_product=True),
    "11": StageRule("8. Предложен контроффер", max_stage_business_days=2, max_contact_business_days=2, requires_product=True),
    "12": StageRule("9. Отложенный спрос", deferred_demand=True, requires_product=True),
    "UC_K9EQG6": StageRule("10. Договор клиенту выслан", max_stage_business_days=3, max_contact_business_days=3, requires_product=True),
    "UC_NSAZEE": StageRule("11. Согласовано, оплата до конца недели", max_stage_business_days=5, max_contact_business_days=2, requires_product=True),
    "13": StageRule("12. Согласовано, оплата до конца месяца", max_stage_business_days=20, max_contact_business_days=10, requires_product=True),
    "14": StageRule("13. Согласовано, оплата дольше месяца", max_stage_business_days=20, max_contact_business_days=20, requires_product=True),
    "UC_BX6RXO": StageRule("14. Предоплата получена", max_stage_business_days=2, max_contact_business_days=2, requires_product=True),
}


@dataclass(frozen=True)
class HealthIssue:
    code: str
    penalty: int
    message: str
    hard_risk: bool = False


@dataclass(frozen=True)
class HealthAssessment:
    score: int
    zone: str
    issues: tuple[HealthIssue, ...]
    primary_risk: str
    rule_version: str = RULE_VERSION

    @property
    def reasons(self) -> list[str]:
        return [issue.message for issue in self.issues]


def _parse_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _same_timezone(value: datetime, reference: datetime) -> datetime:
    """Make naive/aware values comparable without inventing a timezone."""
    if value.tzinfo is None and reference.tzinfo is not None:
        return value.replace(tzinfo=reference.tzinfo)
    if value.tzinfo is not None and reference.tzinfo is None:
        return value.replace(tzinfo=None)
    return value


def business_days_overdue(due_at: Any, now: datetime) -> int:
    """Return completed working days after a passed deadline (Mon–Fri)."""
    due = _parse_datetime(due_at)
    if not due:
        return 0
    due = _same_timezone(due, now)
    if due >= now:
        return 0
    cursor = due.date()
    end = now.date()
    days = 0
    while cursor < end:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            days += 1
    return days


def business_days_elapsed(since_at: Any, now: datetime) -> Optional[int]:
    since = _parse_datetime(since_at)
    if not since:
        return None
    since = _same_timezone(since, now)
    if since >= now:
        return 0
    return business_days_overdue(since, now)


def _has_value(value: Any) -> bool:
    return value not in (None, "", [], {})


def _is_active_responsible(deal: Mapping[str, Any]) -> bool:
    return bool(deal.get("responsible_id")) and deal.get("responsible_active") is not False


def _current_activities(deal: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    for activity in deal.get("open_activities") or []:
        if isinstance(activity, Mapping):
            yield activity


def _latest_due(activities: Iterable[Mapping[str, Any]]) -> Optional[Any]:
    dates = [activity.get("due_at") for activity in activities if _parse_datetime(activity.get("due_at"))]
    if not dates:
        return None
    return min(dates, key=lambda item: _parse_datetime(item) or datetime.max)


def _issue(code: str, penalty: int, message: str, *, hard: bool = False) -> HealthIssue:
    return HealthIssue(code=code, penalty=penalty, message=message, hard_risk=hard)


def assess_deal_health(deal: Mapping[str, Any], *, now: Optional[datetime] = None) -> HealthAssessment:
    """Score one active main-sales deal without mutating the supplied snapshot."""
    now = now or datetime.now().astimezone()
    issues: list[HealthIssue] = []
    hard_cap = False

    category_id = int(deal.get("category_id") or MAIN_SALES_CATEGORY_ID)
    stage_id = str(deal.get("stage_id") or "")
    stage = STAGE_RULES.get(stage_id) if category_id == MAIN_SALES_CATEGORY_ID else None

    if not _is_active_responsible(deal):
        issues.append(_issue("inactive_or_missing_owner", 10, "Нет активного ответственного менеджера.", hard=True))
        hard_cap = True
    if not stage or deal.get("stage_known") is False:
        issues.append(_issue("unknown_stage", 10, "Неизвестна активная воронка или стадия сделки.", hard=True))
        hard_cap = True

    if not _has_value(deal.get("company_id")) and not _has_value(deal.get("contact_id")):
        issues.append(_issue("missing_client", 3, "Не указан контакт или компания клиента."))
    if not _has_value(deal.get("source_id")):
        issues.append(_issue("missing_source", 3, "Не указан источник сделки."))
    if not _has_value(deal.get("client_type")):
        issues.append(_issue("missing_client_type", 3, "Не указан тип клиента."))
    if stage and stage.requires_product and not _has_value(deal.get("product_or_service")):
        issues.append(_issue("missing_product", 3, "Не указан продукт или услуга."))

    activities = list(_current_activities(deal))
    deferred_plan = bool(deal.get("deferred_return_at") or deal.get("deferred_return_trigger"))
    if stage and stage.deferred_demand:
        deferred_plan = deferred_plan or bool(_latest_due(activities))
    if not activities:
        issues.append(_issue("missing_next_activity", 25, "Нет открытого CRM-дела со следующим шагом."))
    else:
        due_at = _latest_due(activities)
        if not due_at:
            issues.append(_issue("activity_without_due", 15, "У открытого CRM-дела нет срока."))
        else:
            overdue_days = business_days_overdue(due_at, now)
            if overdue_days > 2 and not deferred_plan:
                issues.append(_issue("activity_overdue_severe", 25, f"Следующее CRM-дело просрочено на {overdue_days} р. д.", hard=True))
                hard_cap = True
            elif overdue_days == 2:
                issues.append(_issue("activity_overdue_two_days", 15, "Следующее CRM-дело просрочено на 2 р. д."))
            elif overdue_days == 1:
                issues.append(_issue("activity_overdue_one_day", 7, "Следующее CRM-дело просрочено на 1 р. д."))

    if not _has_value(deal.get("last_communication_at")):
        issues.append(_issue("missing_last_communication", 15, "Нет даты последней CRM-коммуникации."))
    elif stage and not stage.deferred_demand:
        elapsed = business_days_elapsed(deal.get("last_communication_at"), now) or 0
        limit = stage.max_contact_business_days
        if limit and elapsed > limit * 2:
            issues.append(_issue("communication_stale_severe", 20, f"Последняя CRM-коммуникация была {elapsed} р. д. назад."))
        elif limit and elapsed > limit:
            issues.append(_issue("communication_stale", 10, f"Последняя CRM-коммуникация была {elapsed} р. д. назад."))

    if stage and stage.deferred_demand:
        if not deferred_plan:
            issues.append(_issue("missing_deferred_plan", 20, "Для отложенного спроса не указана дата или триггер возврата."))
    elif stage:
        moved_at = deal.get("moved_at")
        if stage.max_stage_hours and _parse_datetime(moved_at):
            moved = _same_timezone(_parse_datetime(moved_at), now)  # type: ignore[arg-type]
            hours = (now - moved).total_seconds() / 3600
            if hours > stage.max_stage_hours * 1.5:
                issues.append(_issue("stage_age_severe", 20, f"Сделка на стадии {hours:.0f} ч. при норме {stage.max_stage_hours} ч."))
            elif hours > stage.max_stage_hours:
                issues.append(_issue("stage_age", 10, f"Сделка на стадии {hours:.0f} ч. при норме {stage.max_stage_hours} ч."))
        elif stage.max_stage_business_days:
            elapsed = business_days_elapsed(moved_at, now)
            if elapsed is not None and elapsed > stage.max_stage_business_days * 1.5:
                issues.append(_issue("stage_age_severe", 20, f"Сделка на стадии {elapsed} р. д. при норме {stage.max_stage_business_days} р. д."))
            elif elapsed is not None and elapsed > stage.max_stage_business_days:
                issues.append(_issue("stage_age", 10, f"Сделка на стадии {elapsed} р. д. при норме {stage.max_stage_business_days} р. д."))

    score = max(0, 100 - sum(issue.penalty for issue in issues))
    if hard_cap:
        score = min(score, 49)
    zone = "red" if hard_cap or score < 50 else ("yellow" if score < 80 else "green")
    issues.sort(key=lambda item: (item.hard_risk, item.penalty), reverse=True)
    primary_risk = issues[0].message if issues else "Сделка ведётся по текущим правилам."
    return HealthAssessment(score=score, zone=zone, issues=tuple(issues), primary_risk=primary_risk)
