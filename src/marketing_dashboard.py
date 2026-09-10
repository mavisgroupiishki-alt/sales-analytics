"""Read-only export of the v7 Bitrix marketing dashboard for Operations.

The rules here deliberately follow the installed Bitrix local app: the
``1. Продажи`` funnel, created-in-month leads/deals, and successful deals by
their moved date.  It does not save plans or change CRM records.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from bitrix import Bitrix24Client


class MarketingDashboardError(RuntimeError):
    """The live marketing snapshot cannot be built."""


METRIC_KEYS = ("leads", "targetLeads", "inWork", "kp", "agreement", "postponed", "rejected", "sales", "salesAmount")
SOURCE_ORDER = (
    "yandex_ads", "google_ads", "yandex_seo", "google_seo", "incoming", "website",
    "quiz", "telegram", "partner", "referral", "cold", "reanimation", "base", "expert", "recommendation", "targeted",
)
SOURCE_LABELS = {
    "yandex_ads": "Яндекс.Директ", "google_ads": "Google Ads", "yandex_seo": "Яндекс Звонок (SEO)",
    "google_seo": "Google Звонок (SEO)", "incoming": "Входящий звонок", "website": "Заявки с сайта",
    "quiz": "Квиз", "telegram": "Telegram", "partner": "Партнёрка", "referral": "Реферальная программа",
    "cold": "Холодный звонок", "reanimation": "Из реанимации", "base": "База успешных клиентов",
    "expert": "Передан экспертом", "recommendation": "По рекомендации", "targeted": "Таргет",
}


def _norm(value: Any) -> str:
    return " ".join(str(value or "").lower().replace("ё", "е").replace("\xa0", " ").split())


def _number(value: Any) -> float:
    try:
        return float(str(value or 0).replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return 0.0


def _empty() -> dict[str, float]:
    return {key: 0.0 for key in METRIC_KEYS}


def _add(target: dict[str, float], value: dict[str, float]) -> dict[str, float]:
    for key in METRIC_KEYS:
        target[key] += float(value.get(key) or 0)
    return target


def _classify_source(source_name: str) -> str | None:
    value = _norm(source_name)
    if not value or value == "без источника":
        return None
    if "яндекс" in value and "реклам" in value: return "yandex_ads"
    if "google" in value and "реклам" in value: return "google_ads"
    if "яндекс" in value and ("органик" in value or "seo" in value): return "yandex_seo"
    if "google" in value and ("органик" in value or "seo" in value): return "google_seo"
    if "заявк" in value and "сайт" in value or "сайт органик" in value or "mavisgroup.by" in value: return "website"
    if "входящ" in value and "звон" in value or "прямой" in value: return "incoming"
    if "квиз" in value: return "quiz"
    if "telegram" in value or "телеграм" in value: return "telegram"
    if "реферал" in value: return "referral"
    if "партнер" in value or "партнёр" in value or "белтехэкспертиз" in value: return "partner"
    if "реанимац" in value: return "reanimation"
    if "холодн" in value: return "cold"
    if "база" in value and "успеш" in value: return "base"
    if "передан" in value and "эксперт" in value: return "expert"
    if "по рекомендации" in value or "рекомендац" in value: return "recommendation"
    if "таргет" in value: return "targeted"
    return None


def _bucket(source: str, client_type: str) -> str:
    if source in {"reanimation", "base", "expert", "recommendation"}:
        return "existing"
    if source in {"yandex_ads", "google_ads", "quiz", "telegram", "partner", "referral", "targeted"}:
        return "new"
    return "existing" if any(word in _norm(client_type) for word in ("действ", "повтор")) else "new"


def _stage_bucket(stage: dict[str, str]) -> str | None:
    value = _norm(stage.get("name"))
    if any(word in value for word in ("отказ", "проигран", "неуспеш", "не успеш")): return "rejected"
    if "отложенн" in value and "спрос" in value: return "postponed"
    if any(word in value for word in ("согласован", "согласование договор", "договор выслан", "оплата до")) or ("жд" in value and "оплат" in value): return "agreement"
    if any(word in value for word in ("кп отправ", "получ", "ос", "защит", "обратн")): return "kp"
    return "inWork" if "в работе" in value else None


def _month_period(month: str) -> tuple[str, str]:
    try:
        start = datetime.strptime(month, "%Y-%m")
    except ValueError as exc:
        raise MarketingDashboardError("Неверный месяц маркетинга.") from exc
    year, number = start.year, start.month
    next_year, next_month = (year + 1, 1) if number == 12 else (year, number + 1)
    return f"{year:04d}-{number:02d}-01T00:00:00+03:00", f"{next_year:04d}-{next_month:02d}-01T00:00:00+03:00"


def _result(response: dict[str, Any]) -> Any:
    if not isinstance(response, dict) or "error" in response:
        raise MarketingDashboardError("Bitrix24 временно недоступен.")
    return response.get("result")


def _client_type_field(fields: dict[str, Any]) -> tuple[str, dict[str, str]]:
    for key, field in (fields or {}).items():
        if not isinstance(field, dict):
            continue
        caption = _norm(" ".join(str(field.get(name) or "") for name in ("title", "formLabel", "listLabel", "filterLabel")))
        if "тип клиента" not in caption:
            continue
        items = field.get("items") or []
        return key, {str(item.get("ID", item.get("id", ""))): str(item.get("VALUE", item.get("value", ""))) for item in items if isinstance(item, dict)}
    return "", {}


def build_marketing_snapshot(month: str) -> dict[str, Any]:
    """Return the factual part of the installed Marketing v7 dashboard."""
    period_start, period_end = _month_period(month)
    client = Bitrix24Client()
    try:
        sources, lead_statuses, categories, deal_fields, lead_fields = (
            _result(client.call("crm.status.list", {"filter": {"ENTITY_ID": "SOURCE"}, "order": {"SORT": "ASC"}})) or [],
            _result(client.call("crm.status.list", {"filter": {"ENTITY_ID": "STATUS"}, "order": {"SORT": "ASC"}})) or [],
            _result(client.call("crm.category.list", {"entityTypeId": 2})) or {},
            _result(client.call("crm.deal.fields")) or {},
            _result(client.call("crm.lead.fields")) or {},
        )
    except Exception as exc:
        raise MarketingDashboardError(f"Не удалось получить справочники Bitrix24 ({type(exc).__name__}).") from exc

    source_map = {str(row.get("STATUS_ID")): str(row.get("NAME") or row.get("STATUS_ID") or "") for row in sources if isinstance(row, dict)}
    lead_status_map = {str(row.get("STATUS_ID")): {"name": str(row.get("NAME") or ""), "semantics": str(row.get("SEMANTICS") or (row.get("EXTRA") or {}).get("SEMANTICS") or "").upper()} for row in lead_statuses if isinstance(row, dict)}
    category_rows = categories.get("categories") if isinstance(categories, dict) else []
    sales_category = next((row for row in category_rows or [] if "продаж" in _norm(row.get("name"))), {"id": 0, "name": "1. Продажи"})
    category_id = int(sales_category.get("id") or 0)
    stage_entity = "DEAL_STAGE" if category_id == 0 else f"DEAL_STAGE_{category_id}"
    stages = _result(client.call("crm.status.list", {"filter": {"ENTITY_ID": stage_entity}, "order": {"SORT": "ASC"}})) or []
    stage_map = {str(row.get("STATUS_ID")): {"name": str(row.get("NAME") or row.get("STATUS_ID") or ""), "semantics": str(row.get("SEMANTICS") or (row.get("EXTRA") or {}).get("SEMANTICS") or "").upper()} for row in stages if isinstance(row, dict)}
    deal_client_field, deal_client_values = _client_type_field(deal_fields)
    lead_client_field, lead_client_values = _client_type_field(lead_fields)

    lead_select = ["ID", "DATE_CREATE", "STATUS_ID", "SOURCE_ID"] + ([lead_client_field] if lead_client_field else [])
    deal_select = ["ID", "DATE_CREATE", "MOVED_TIME", "STAGE_ID", "SOURCE_ID", "CATEGORY_ID", "OPPORTUNITY"] + ([deal_client_field] if deal_client_field else [])
    try:
        leads = client.call_all("crm.lead.list", {"order": {"ID": "ASC"}, "filter": {">=DATE_CREATE": period_start, "<DATE_CREATE": period_end}, "select": lead_select})
        deals = client.call_all("crm.deal.list", {"order": {"ID": "ASC"}, "filter": {"CATEGORY_ID": category_id, ">=DATE_CREATE": period_start, "<DATE_CREATE": period_end}, "select": deal_select})
        success_ids = [stage_id for stage_id, stage in stage_map.items() if stage.get("semantics") == "S" or any(word in _norm(stage.get("name")) for word in ("успеш", "оплачен", "закрыт"))]
        sales_by_id: dict[str, dict[str, Any]] = {}
        for stage_id in success_ids:
            for deal in client.call_all("crm.deal.list", {"order": {"ID": "ASC"}, "filter": {"CATEGORY_ID": category_id, "STAGE_ID": stage_id, ">=MOVED_TIME": period_start, "<MOVED_TIME": period_end}, "select": deal_select}):
                sales_by_id[str(deal.get("ID"))] = deal
    except Exception as exc:
        raise MarketingDashboardError(f"Не удалось получить лиды и сделки Bitrix24 ({type(exc).__name__}).") from exc

    store: dict[str, dict[str, dict[str, float]]] = {"new": defaultdict(_empty), "existing": defaultdict(_empty)}

    def resolved_client_type(row: dict[str, Any], field: str, labels: dict[str, str]) -> str:
        raw = row.get(field) if field else ""
        if isinstance(raw, list): raw = raw[0] if raw else ""
        return labels.get(str(raw), str(raw or ""))

    for lead in leads:
        source = _classify_source(source_map.get(str(lead.get("SOURCE_ID") or ""), str(lead.get("SOURCE_ID") or "")))
        if not source: continue
        metric = store[_bucket(source, resolved_client_type(lead, lead_client_field, lead_client_values))][source]
        metric["leads"] += 1
        status = lead_status_map.get(str(lead.get("STATUS_ID") or ""), {"name": "", "semantics": ""})
        status_name = _norm(status.get("name"))
        if not any(word in status_name for word in ("нецелев", "не целев", "спам", "мусор", "дубл", "ошибоч", "тестов")) and ("целев" in status_name or status.get("semantics") == "S"):
            metric["targetLeads"] += 1

    for deal in deals:
        source = _classify_source(source_map.get(str(deal.get("SOURCE_ID") or ""), str(deal.get("SOURCE_ID") or "")))
        if not source: continue
        stage = _stage_bucket(stage_map.get(str(deal.get("STAGE_ID") or ""), {"name": str(deal.get("STAGE_ID") or "")}))
        if stage:
            store[_bucket(source, resolved_client_type(deal, deal_client_field, deal_client_values))][source][stage] += 1

    for deal in sales_by_id.values():
        source = _classify_source(source_map.get(str(deal.get("SOURCE_ID") or ""), str(deal.get("SOURCE_ID") or "")))
        if not source: continue
        metric = store[_bucket(source, resolved_client_type(deal, deal_client_field, deal_client_values))][source]
        metric["sales"] += 1
        metric["salesAmount"] += _number(deal.get("OPPORTUNITY"))

    buckets = {bucket: _empty() for bucket in ("new", "existing")}
    source_rows = []
    for bucket, values in store.items():
        for source in SOURCE_ORDER:
            metric = values.get(source)
            if not metric or not any(metric.values()):
                continue
            _add(buckets[bucket], metric)
            source_rows.append({"bucket": bucket, "key": source, "label": SOURCE_LABELS[source], "metrics": {key: round(value, 2) for key, value in metric.items()}})
    total = _add(_empty(), buckets["new"])
    _add(total, buckets["existing"])
    total["averageCheck"] = total["salesAmount"] / total["sales"] if total["sales"] else 0
    total["conversion"] = total["sales"] / total["targetLeads"] if total["targetLeads"] else 0
    return {
        "generatedAt": datetime.utcnow().isoformat() + "Z", "month": month,
        "summary": {key: round(value, 2) for key, value in total.items()},
        "segments": [{"key": key, "label": "Новые клиенты" if key == "new" else "Действующие клиенты", "metrics": {field: round(value, 2) for field, value in metric.items()}} for key, metric in buckets.items()],
        "sources": source_rows,
        "funnel": [{"key": key, "label": label, "value": round(total[key], 2)} for key, label in (("inWork", "В работе"), ("kp", "КП / ОС по КП"), ("agreement", "Согласование / ждём оплату"), ("postponed", "Отложенный спрос"), ("rejected", "Отказ"))],
    }
