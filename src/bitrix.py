"""
Основной модуль работы с Bitrix24.
Забирает звонки за указанный период, скачивает аудиозаписи (опционально),
собирает мета-данные (менеджер, клиент, сделка) + фото менеджеров.
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


# ============================================================
# КОНСТАНТЫ
# ============================================================
ACTIVITY_TYPE_CALL = 2
DIRECTION_INCOMING = 1
DIRECTION_OUTGOING = 2
DEFAULT_REQUEST_TIMEOUT = 60
MIN_AUDIO_SIZE_BYTES = 10_000

DEFAULT_AUDIO_DOWNLOAD_LIMIT = 0

ALLOWED_MANAGERS = [
    "Роман Авсеенко",
    "Екатерина Халько",
    "Ирина Богомольцева",
]


# ============================================================
# КЛИЕНТ БИТРИКСА
# ============================================================
class Bitrix24Client:
    def __init__(self, webhook_url: Optional[str] = None):
        url = webhook_url or os.environ.get("BITRIX_WEBHOOK_URL")
        if not url:
            raise RuntimeError("BITRIX_WEBHOOK_URL не задан.")
        self.webhook = url.rstrip("/") + "/"

    def call(self, method: str, params: dict = None) -> dict:
        response = requests.post(
            self.webhook + method,
            json=params or {},
            timeout=DEFAULT_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        if "error" in data:
            raise RuntimeError(
                f"Bitrix24 API error в методе {method}: "
                f"{data.get('error_description', data['error'])}"
            )
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


# ============================================================
# ЗАБОР ЗВОНКОВ
# ============================================================
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
    logger.info(f"Получено {len(activities)} звонков от Bitrix24")

    filtered = []
    for a in activities:
        files = a.get("FILES") or []
        if not files:
            continue
        filtered.append(a)
    logger.info(f"С прикреплёнными аудио: {len(filtered)}")
    return filtered


# ============================================================
# ИНФОРМАЦИЯ О МЕНЕДЖЕРАХ
# ============================================================
def fetch_users(client: Bitrix24Client, user_ids: List[int]) -> Dict[int, Dict]:
    if not user_ids:
        return {}
    unique_ids = list(set(int(uid) for uid in user_ids if uid))
    users = {}
    for uid in unique_ids:
        try:
            data = client.call("user.get", {"ID": uid})
            result = data.get("result", [])
            if result:
                u = result[0]
                photo_url = u.get("PERSONAL_PHOTO") or ""
                users[uid] = {
                    "id": uid,
                    "name": f"{u.get('NAME', '')} {u.get('LAST_NAME', '')}".strip() or u.get("EMAIL", f"User {uid}"),
                    "email": u.get("EMAIL", ""),
                    "photo_url": photo_url,
                }
        except Exception as e:
            logger.warning(f"Не удалось получить пользователя {uid}: {e}")
            users[uid] = {"id": uid, "name": f"User {uid}", "email": "", "photo_url": ""}
    return users


# ============================================================
# СКАЧИВАНИЕ ФОТО МЕНЕДЖЕРОВ
# ============================================================
def download_user_avatars(users: Dict[int, Dict], avatars_dir: Path) -> None:
    avatars_dir.mkdir(parents=True, exist_ok=True)
    for uid, u in users.items():
        photo_url = u.get("photo_url")
        if not photo_url:
            logger.info(f"   - {uid}: {u['name']} (фото нет в Битриксе)")
            continue
        try:
            r = requests.get(photo_url, timeout=DEFAULT_REQUEST_TIMEOUT)
            if r.status_code == 200 and len(r.content) > 100:
                ext = ".jpg"
                ct = r.headers.get("Content-Type", "").lower()
                if "png" in ct:
                    ext = ".png"
                elif "webp" in ct:
                    ext = ".webp"
                avatar_path = avatars_dir / f"{uid}{ext}"
                avatar_path.write_bytes(r.content)
                u["avatar_file"] = avatar_path.name
                logger.info(f"   - {uid}: {u['name']} → {avatar_path.name} ({len(r.content):,} байт)")
            else:
                logger.info(f"   - {uid}: {u['name']} (фото не скачалось, статус {r.status_code})")
        except Exception as e:
            logger.warning(f"   - {uid}: ошибка скачивания фото: {e}")


# ============================================================
# СКАЧИВАНИЕ АУДИОФАЙЛА
# ============================================================
def download_audio(client: Bitrix24Client, file_id: int, save_to: Path) -> Path:
    logger.info(f"   → Запрашиваем disk.file.get для file_id={file_id}")
    meta = client.call("disk.file.get", {"id": file_id})
    file_data = meta.get("result")
    if not file_data:
        raise RuntimeError(f"Файл {file_id} не найден в Bitrix24 Disk")

    download_url = file_data.get("DOWNLOAD_URL")
    if not download_url:
        raise RuntimeError(f"Нет DOWNLOAD_URL для файла {file_id}")

    file_name = file_data.get("NAME", f"call_{file_id}.mp3")
    safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", file_name)
    save_path = save_to / f"{file_id}_{safe_name}"

    logger.info(f"   → Скачиваем файл {file_name}")
    response = requests.get(download_url, timeout=DEFAULT_REQUEST_TIMEOUT, stream=True)
    response.raise_for_status()
    save_path.parent.mkdir(parents=True, exist_ok=True)
    with open(save_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)
    logger.info(f"   → Сохранено {save_path.stat().st_size:,} байт")
    return
