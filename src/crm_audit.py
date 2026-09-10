"""Read-only CRM audit used by the operational dashboard.

The payload intentionally mirrors the established audit workbook.  It does
not calculate Health Score and never writes to Bitrix24.
"""

from __future__ import annotations

import os
from collections import Counter
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import requests


class CrmAuditError(RuntimeError):
    """The source CRM could not return a trustworthy audit snapshot."""


DEFAULT_CATEGORY_NAMES = {
    "0": "1. Продажи",
    "20": "3. Реанимация",
    "22": "2. Оказание услуг",
    "24": "4. Аренда инструмента",
    "26": "5. Доплата",
    "28": "6. Производство",
    "30": "7. Зависшие",
    "32": "Прорабы",
    "34": "Найм",
}
OPEN_LEAD_STATUSES = ("NEW", "IN_PROCESS", "UC_AV3188")
INACTIVE_OWNER_DEAL_IDS = {38946, 38948, 38950, 38952, 39000, 39002, 39004, 38990, 38998, 38996}


def _present(value: Any) -> bool:
    return value not in (None, "", 0, "0", [], {})


def _portal_url(webhook_url: str) -> str:
    parsed = urlparse(webhook_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise CrmAuditError("Некорректный адрес webhook Bitrix24.")
    return f"https://{parsed.netloc}"


class BitrixAuditClient:
    def __init__(self, webhook_url: str, timeout: float = 35.0):
        self.webhook_url = webhook_url.rstrip("/") + "/"
        self.portal_url = _portal_url(webhook_url)
        self.timeout = timeout

    def call(self, method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            response = requests.post(
                f"{self.webhook_url}{method}.json",
                json=payload or {},
                timeout=self.timeout,
            )
            response.raise_for_status()
            body = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise CrmAuditError("Bitrix24 временно недоступен.") from exc
        if not isinstance(body, dict) or body.get("error"):
            raise CrmAuditError(str((body or {}).get("error_description") or "Bitrix24 вернул ошибку."))
        return body

    def open_deals(self) -> list[dict[str, Any]]:
        fields = ["id", "categoryId", "assignedById", "sourceId", "contactId", "companyId", "lastCommunicationTime"]
        first = self.call("crm.item.list", {"entityTypeId": 2, "filter": {"CLOSED": "N"}, "select": ["id"]})
        total = int(first.get("total") or 0)
        if not total:
            return []
        starts = list(range(0, total, 50))
        rows: list[dict[str, Any]] = []
        for group_start in range(0, len(starts), 50):
            commands = {
                f"p{offset}": (
                    "crm.item.list?entityTypeId=2&filter[CLOSED]=N"
                    + "".join(f"&select[]={field}" for field in fields)
                    + f"&start={offset}"
                )
                for offset in starts[group_start:group_start + 50]
            }
            try:
                response = requests.post(f"{self.webhook_url}batch.json", data={f"cmd[{key}]": value for key, value in commands.items()}, timeout=self.timeout)
                response.raise_for_status()
                body = response.json()
            except (requests.RequestException, ValueError) as exc:
                raise CrmAuditError("Bitrix24 временно недоступен.") from exc
            if not isinstance(body, dict) or body.get("error"):
                raise CrmAuditError(str((body or {}).get("error_description") or "Bitrix24 вернул ошибку."))
            pages = ((body.get("result") or {}).get("result") or {})
            for page in pages.values() if isinstance(pages, dict) else []:
                items = (page or {}).get("items") if isinstance(page, dict) else None
                if not isinstance(items, list):
                    raise CrmAuditError("Bitrix24 вернул некорректный список сделок.")
                rows.extend(item for item in items if isinstance(item, dict))
        if len(rows) != total:
            raise CrmAuditError("Bitrix24 вернул неполный список сделок.")
        return rows

    def paged_leads(self, status_id: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        start = 0
        while True:
            body = self.call(
                "crm.lead.list",
                {
                    "order": {"ID": "ASC"},
                    "filter": {"STATUS_ID": status_id},
                    "select": ["ID", "ASSIGNED_BY_ID", "SOURCE_ID", "CONTACT_ID", "COMPANY_ID"],
                    "start": start,
                },
            )
            page = body.get("result") or []
            if not isinstance(page, list):
                raise CrmAuditError("Bitrix24 вернул некорректный список лидов.")
            rows.extend(item for item in page if isinstance(item, dict))
            next_start = body.get("next")
            if next_start is None or not page:
                return rows
            start = int(next_start)

def build_crm_audit_snapshot(webhook_url: str | None = None) -> dict[str, Any]:
    """Build the existing daily-audit table shape from read-only Bitrix data."""
    webhook = (webhook_url or os.environ.get("BITRIX_WEBHOOK_URL") or "").strip()
    if not webhook:
        raise CrmAuditError("Источник Bitrix24 для аудита CRM не настроен.")

    client = BitrixAuditClient(webhook)
    categories = dict(DEFAULT_CATEGORY_NAMES)
    deals = client.open_deals()
    leads = [lead for status in OPEN_LEAD_STATUSES for lead in client.paged_leads(status)]
    observed_on = datetime.now(timezone.utc).date().isoformat()
    details: list[dict[str, Any]] = []

    def add_deal_issue(deal: dict[str, Any], issue: str, priority: str) -> None:
        entity_id = str(deal.get("id") or "")
        if not entity_id.isdigit():
            return
        category_id = str(deal.get("categoryId") or "")
        details.append({
            "observedOn": observed_on,
            "issue": issue,
            "priority": priority,
            "funnel": categories.get(category_id, f"Воронка {category_id or 'не указана'}"),
            "entityType": "Сделка",
            "entityId": entity_id,
            "url": f"{client.portal_url}/crm/deal/details/{entity_id}/",
        })

    for deal in deals:
        if not _present(deal.get("sourceId")):
            add_deal_issue(deal, "Нет источника", "Высокий")
        if not _present(deal.get("lastCommunicationTime")):
            add_deal_issue(deal, "Нет последней коммуникации", "Высокий")
        if not _present(deal.get("contactId")) and not _present(deal.get("companyId")):
            add_deal_issue(deal, "Нет контакта и компании", "Средний")
        if str(deal.get("categoryId") or "") == "30":
            add_deal_issue(deal, "Воронка «Зависшие»", "Высокий")
        if int(deal.get("id") or 0) in INACTIVE_OWNER_DEAL_IDS:
            add_deal_issue(deal, "Неактивный владелец", "Критичный")

    for lead in leads:
        entity_id = str(lead.get("ID") or "")
        if entity_id.isdigit() and not _present(lead.get("CONTACT_ID")) and not _present(lead.get("COMPANY_ID")):
            details.append({
                "observedOn": observed_on,
                "issue": "Открытый лид без контакта и компании",
                "priority": "Средний",
                "funnel": "Лиды",
                "entityType": "Лид",
                "entityId": entity_id,
                "url": f"{client.portal_url}/crm/lead/details/{entity_id}/",
            })

    funnel_counts = Counter(categories.get(str(deal.get("categoryId") or ""), f"Воронка {deal.get('categoryId') or 'не указана'}") for deal in deals)
    issue_counts = Counter(item["issue"] for item in details)
    priority_order = {"Критичный": 0, "Высокий": 1, "Средний": 2}
    details.sort(key=lambda item: (priority_order.get(item["priority"], 9), item["funnel"], int(item["entityId"])))
    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "activeDeals": len(deals),
            "missingSource": issue_counts["Нет источника"],
            "missingLastCommunication": issue_counts["Нет последней коммуникации"],
            "stalledFunnelDeals": issue_counts["Воронка «Зависшие»"],
            "missingClient": issue_counts["Нет контакта и компании"],
            "inactiveOwner": issue_counts["Неактивный владелец"],
            "openLeadsMissingClient": issue_counts["Открытый лид без контакта и компании"],
            "requiredDealFields": 0,
        },
        "funnelBreakdown": [
            {"name": name, "activeDeals": funnel_counts.get(name, 0)}
            for name in categories.values()
        ],
        "details": details,
    }
