"""
Основной модуль работы с Bitrix24.
Забирает звонки за сутки, скачивает аудио,
вычисляет длительность из START_TIME / END_TIME / DURATION.
"""

import os
import re
import json
import logging
import requests
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any

logger = logging.getLogger(__name__)

ACTIVITY_TYPE_CALL = 2
DEFAULT_REQUEST_TIMEOUT = 60
MIN_AUDIO_SIZE_BYTES = 10_000
DEFAULT_AUDIO_DOWNLOAD_LIMIT = 0
MIN_DURATION_SEC = 40  # ТЗ: анализируем только звонки ≥ 40 секунд

ALLOWED_MANAGERS = [
    "Роман Авсеенко",
    "Екатерина Халько",
    "Ирина Богомольцева",
]


class Bitrix24Client:
    def __init__(self, webhook_url: Optional[str] = None):
        url = webhook_url or os.environ.get("BITRIX_WEBHOOK_URL")
        if not url:
            raise RuntimeError("BITRIX_WEBHOOK_URL не задан.")
        self.webhook = url.rstrip("/") + "/"

    def call(self, method: str, params: dict = None) -> dict:
        response = requests.post(self.webhook + method, json=params or {}, timeout=DEFAULT_REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        if "error" in data:
            raise RuntimeError(f"Bitrix24 API error {method}: {data.get('error_description', data['error'])}")
        return data

    def call_all(self, method: str, params: dict = None) -> list:
        all_items = []
        start = 0
        params = dict(params or {})
        while True:
            params["start"] = start
            data = self.call(method, params)
            items = data.get("result", [])
            if isinstance(items, dict):
                items = list(items.values())
            all_items.extend(items)
            next_start = data.get("next")
            if next_start is None:
                break
            start = next_start
        return all_items


def fetch_calls(client: Bitrix24Client, date_from: datetime, date_to: datetime) -> List[Dict[str, Any]]:
    logger.info(f"Загружаем звонки с {date_from} по {date_to}")
    activities = client.call_all(
        "crm.activity.list",
        {
            "filter": {
                "TYPE_ID": ACTIVITY_TYPE_CALL,
                "COMPLETED": "Y",
                ">=CREATED": date_from.strftime("%Y-%m-%dT%H:%M:%S"),
                "<=CREATED": date_to.strftime("%Y-%m-%dT%H:%M:%S"),
            },
            "select": ["*", "COMMUNICATIONS"],
            "order": {"CREATED": "ASC"},
        },
    )
    logger.info(f"Получено {
