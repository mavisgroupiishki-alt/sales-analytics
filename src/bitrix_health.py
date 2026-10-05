"""Read-only Bitrix24 adapter for CRM Health snapshots.

The adapter deliberately permits a fixed allowlist of read methods.  It never
receives a method name from a web request and it never calls Bitrix write APIs.
"""

from __future__ import annotations

import os
import time
from collections import defaultdict
from typing import Any, Dict, Iterable, Iterator, Mapping, Optional, Sequence

import requests

from bitrix_url import normalize_bitrix_webhook_url
from health_rules import MAIN_SALES_CATEGORY_ID


READ_ONLY_METHODS = frozenset(
    {
        "crm.category.list",
        "crm.dealcategory.stage.list",
        "crm.item.fields",
        "crm.item.list",
        "crm.activity.list",
        "user.get",
    }
)
REQUEST_TIMEOUT_SECONDS = 30
MAX_RETRIES = 3
PAGE_SIZE = 50

PRODUCT_FIELDS = (
    "ufCrm_6A0FE4A52FBF5",
    "ufCrm_1758111877347",
    "ufCrm_1765113071",
)
CLIENT_TYPE_FIELD = "ufCrm_1756973967704"


class BitrixHealthError(RuntimeError):
    pass


class BitrixHealthClient:
    def __init__(
        self,
        webhook_url: Optional[str] = None,
        *,
        session: Any = requests,
        sleep: Any = time.sleep,
    ) -> None:
        url = webhook_url or os.environ.get("BITRIX_WEBHOOK_URL")
        if not url:
            raise BitrixHealthError("BITRIX_WEBHOOK_URL is not configured for CRM Health")
        self.endpoint = normalize_bitrix_webhook_url(url).rstrip("/") + "/"
        self.session = session
        self.sleep = sleep

    def call(self, method: str, params: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        if method not in READ_ONLY_METHODS:
            raise BitrixHealthError(f"CRM Health refuses non-read method: {method}")
        last_error: Optional[Exception] = None
        for attempt in range(MAX_RETRIES):
            try:
                response = self.session.post(
                    self.endpoint + method + ".json",
                    json=dict(params or {}),
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
                data = response.json()
                if data.get("error"):
                    raise BitrixHealthError(data.get("error_description") or data["error"])
                return data
            except BitrixHealthError:
                raise
            except requests.RequestException as exc:
                last_error = exc
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status not in {429, 500, 502, 503, 504} or attempt == MAX_RETRIES - 1:
                    break
                self.sleep(2**attempt)
        raise BitrixHealthError("Bitrix read request failed") from last_error

    def iter_items(self, method: str, params: Mapping[str, Any]) -> Iterator[Dict[str, Any]]:
        start = 0
        while True:
            page_params = dict(params)
            page_params["start"] = start
            data = self.call(method, page_params)
            result = data.get("result") or {}
            items = result.get("items", []) if isinstance(result, dict) else result
            if not isinstance(items, list):
                raise BitrixHealthError(f"Unexpected list response for {method}")
            yield from items
            next_start = data.get("next")
            if next_start is None:
                return
            start = int(next_start)

    def list_categories(self) -> list[Dict[str, Any]]:
        data = self.call("crm.category.list", {"entityTypeId": 2})
        result = data.get("result") or {}
        return list(result.get("categories") or [])

    def list_stages(self, category_id: int) -> list[Dict[str, Any]]:
        return list(self.call("crm.dealcategory.stage.list", {"id": category_id}).get("result") or [])

    def list_deal_fields(self) -> Dict[str, Any]:
        result = self.call("crm.item.fields", {"entityTypeId": 2}).get("result") or {}
        return dict(result.get("fields") or {})

    def list_active_main_sales_deals(self) -> list[Dict[str, Any]]:
        select = [
            "id",
            "assignedById",
            "stageId",
            "categoryId",
            "companyId",
            "contactId",
            "sourceId",
            "movedTime",
            "lastCommunicationTime",
            *PRODUCT_FIELDS,
            CLIENT_TYPE_FIELD,
        ]
        return list(
            self.iter_items(
                "crm.item.list",
                {
                    "entityTypeId": 2,
                    "filter": {"categoryId": MAIN_SALES_CATEGORY_ID, "closed": "N"},
                    "select": select,
                },
            )
        )

    def list_open_activities(self, deal_ids: Sequence[str | int]) -> list[Dict[str, Any]]:
        activities: list[Dict[str, Any]] = []
        ids = [str(item) for item in deal_ids if str(item)]
        for start_index in range(0, len(ids), PAGE_SIZE):
            chunk = ids[start_index : start_index + PAGE_SIZE]
            activities.extend(
                self.iter_items(
                    "crm.activity.list",
                    {
                        "filter": {"OWNER_TYPE_ID": 2, "OWNER_ID": chunk, "COMPLETED": "N"},
                        "select": ["ID", "OWNER_ID", "START_TIME", "DEADLINE", "TYPE_ID", "COMPLETED"],
                    },
                )
            )
        return activities

    def user_activity(self, user_ids: Iterable[str | int]) -> Dict[str, bool]:
        result: Dict[str, bool] = {}
        for user_id in sorted({str(item) for item in user_ids if str(item)}):
            users = self.call("user.get", {"ID": user_id}).get("result") or []
            if users:
                active = users[0].get("ACTIVE", False)
                result[user_id] = active is True or str(active).upper() in {"Y", "TRUE", "1"}
            else:
                result[user_id] = False
        return result


def _first_value(values: Iterable[Any]) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def build_health_snapshots(client: BitrixHealthClient) -> tuple[list[Dict[str, Any]], Dict[str, Any]]:
    """Fetch the minimum facts needed by Health Score and return no client text."""
    categories = client.list_categories()
    main_category = next(
        (item for item in categories if item.get("id") is not None and int(item["id"]) == MAIN_SALES_CATEGORY_ID),
        None,
    )
    if not main_category:
        raise BitrixHealthError("Main sales category is missing")
    stages = client.list_stages(MAIN_SALES_CATEGORY_ID)
    known_stages = {str(item.get("STATUS_ID") or item.get("ID") or "") for item in stages}
    deals = client.list_active_main_sales_deals()
    activities = client.list_open_activities([deal.get("id") for deal in deals])
    users = client.user_activity(deal.get("assignedById") for deal in deals)

    by_deal: dict[str, list[Dict[str, Any]]] = defaultdict(list)
    for activity in activities:
        owner_id = str(activity.get("OWNER_ID") or "")
        if owner_id:
            by_deal[owner_id].append(
                {"due_at": activity.get("DEADLINE") or activity.get("START_TIME"), "type_id": activity.get("TYPE_ID")}
            )

    snapshots = []
    for deal in deals:
        deal_id = str(deal.get("id") or "")
        owner_id = str(deal.get("assignedById") or "")
        stage_id = str(deal.get("stageId") or "")
        snapshots.append(
            {
                "deal_id": deal_id,
                "category_id": int(deal.get("categoryId") or MAIN_SALES_CATEGORY_ID),
                "stage_id": stage_id,
                "stage_known": stage_id in known_stages,
                "responsible_id": owner_id,
                "responsible_active": users.get(owner_id, False),
                "company_id": deal.get("companyId"),
                "contact_id": deal.get("contactId"),
                "source_id": deal.get("sourceId"),
                "client_type": deal.get(CLIENT_TYPE_FIELD),
                "product_or_service": _first_value(deal.get(field) for field in PRODUCT_FIELDS),
                "moved_at": deal.get("movedTime"),
                "last_communication_at": deal.get("lastCommunicationTime"),
                "open_activities": by_deal.get(deal_id, []),
            }
        )
    metadata = {
        "category_id": MAIN_SALES_CATEGORY_ID,
        "category_name": main_category.get("name"),
        "active_deals": len(snapshots),
        "known_stages": len(known_stages),
        "open_activities": len(activities),
    }
    return snapshots, metadata
