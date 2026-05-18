"""
Основной модуль работы с Bitrix24.
Забирает звонки за указанный период, скачивает аудиозаписи,
собирает мета-данные (менеджер, клиент, сделка).
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
MIN_CALL_DURATION_SEC = 20
DEFAULT_REQUEST_TIMEOUT = 60


# ============================================================
# КЛИЕНТ БИТРИКСА
# ============================================================
class Bitrix24Client:
    """Клиент для работы с Bitrix24 REST API через webhook."""

    def __init__(self, webhook_url: Optional[str] = None):
        url = webhook_url or os.environ.get("BITRIX_WEBHOOK_URL")
        if not url:
            raise RuntimeError(
                "BITRIX_WEBHOOK_URL не задан. "
                "Добавьте его в переменные окружения или GitHub Secrets."
            )
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
def fetch_calls(
    client: Bitrix24Client,
    date_from: datetime,
    date_to: datetime,
    min_duration_sec: int = MIN_CALL_DURATION_SEC,
) -> List[Dict[str, Any]]:
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
    data = client.call("user.get", {"ID": unique_ids})
    for u in data.get("result", []) or []:
        uid = int(u["ID"])
        users[uid] = {
            "id": uid,
            "name": f"{u.get('NAME', '')} {u.get('LAST_NAME', '')}".strip() or u.get("EMAIL", f"User {uid}"),
            "email": u.get("EMAIL", ""),
        }
    return users


# ============================================================
# СКАЧИВАНИЕ АУДИОФАЙЛА
# ============================================================
def download_audio(client: Bitrix24Client, file_id: int, save_to: Path) -> Path:
    logger.debug(f"Скачиваем файл ID={file_id}")
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

    response = requests.get(download_url, timeout=DEFAULT_REQUEST_TIMEOUT, stream=True)
    response.raise_for_status()
    save_path.parent.mkdir(parents=True, exist_ok=True)
    with open(save_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)
    logger.info(f"Скачано {save_path.stat().st_size} байт в {save_path.name}")
    return save_path


# ============================================================
# НОРМАЛИЗАЦИЯ ЗВОНКА
# ============================================================
def normalize_call(activity: Dict[str, Any], users: Dict[int, Dict]) -> Dict[str, Any]:
    files = activity.get("FILES") or []
    file_info = files[0] if files else None

    comm = (activity.get("COMMUNICATIONS") or [{}])[0]
    settings = comm.get("ENTITY_SETTINGS") or {}
    client_name = " ".join(
        x for x in [settings.get("HONORIFIC"), settings.get("NAME"),
                    settings.get("SECOND_NAME"), settings.get("LAST_NAME")] if x
    ).strip() or settings.get("COMPANY_TITLE", "Неизвестный клиент")
    company = settings.get("COMPANY_TITLE", "")

    manager_id = int(activity.get("RESPONSIBLE_ID") or 0)
    manager = users.get(manager_id, {"id": manager_id, "name": f"User {manager_id}", "email": ""})

    direction_code = int(activity.get("DIRECTION") or 0)
    direction = {1: "incoming", 2: "outgoing"}.get(direction_code, "unknown")

    owner_type_id = int(activity.get("OWNER_TYPE_ID") or 0)
    owner_type = {1: "lead", 2: "deal", 3: "contact", 4: "company"}.get(owner_type_id, "unknown")

    result = {
        "activity_id": str(activity["ID"]),
        "created": activity.get("CREATED"),
        "start_time": activity.get("START_TIME"),
        "end_time": activity.get("END_TIME"),
        "direction": direction,
        "subject": activity.get("SUBJECT", ""),
        "manager": manager,
        "client": {
            "name": client_name,
            "company": company,
            "phone_masked": _mask_phone(comm.get("VALUE", "")),
            "entity_id": comm.get("ENTITY_ID"),
            "entity_type": _entity_type_name(comm.get("ENTITY_TYPE_ID")),
        },
        "crm": {
            "owner_type": owner_type,
            "owner_id": str(activity.get("OWNER_ID") or ""),
        },
        "audio": None,
    }
    if file_info:
        result["audio"] = {
            "file_id": file_info["id"],
            "url": file_info.get("url"),
        }
    return result


def _mask_phone(phone: str) -> str:
    if not phone or len(phone) < 6:
        return "***"
    return phone[:4] + "***" + phone[-4:]


def _entity_type_name(type_id) -> str:
    return {"1": "lead", "3": "contact", "4": "company", "2": "deal"}.get(str(type_id or ""), "unknown")


# ============================================================
# CLI: тестовый запуск
# ============================================================
def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    client = Bitrix24Client()

    # Берём звонки за последние 24 часа (с запасом по часовому поясу)
    now = datetime.now()
    date_from = now - timedelta(hours=24)
    date_to = now + timedelta(hours=3)

    print(f"\nЗагружаем звонки за {date_from:%Y-%m-%d %H:%M} - {date_to:%Y-%m-%d %H:%M}")
    raw_calls = fetch_calls(client, date_from, date_to)
    print(f"   Найдено: {len(raw_calls)} звонков с аудио")

    if not raw_calls:
        print("   Звонков нет.")
        return

    sample = raw_calls[:3]
    print(f"\nПолучаем информацию о менеджерах...")
    manager_ids = [int(c.get("RESPONSIBLE_ID") or 0) for c in sample]
    users = fetch_users(client, manager_ids)
    print(f"   Загружено: {len(users)} менеджеров")
    for uid, u in users.items():
        print(f"   - {uid}: {u['name']}")

    audio_dir = Path("audio_temp")
    audio_dir.mkdir(exist_ok=True)

    print(f"\nСкачиваем аудиозаписи в {audio_dir}/...")
    results = []
    for i, raw in enumerate(sample, 1):
        normalized = normalize_call(raw, users)
        print(f"\n   [{i}/{len(sample)}] Звонок {normalized['activity_id']}:")
        print(f"      Менеджер: {normalized['manager']['name']}")
        print(f"      Клиент: {normalized['client']['name']} ({normalized['client']['company']})")
        print(f"      Тип: {normalized['direction']} | время: {normalized['created']}")

        if normalized["audio"] and normalized["audio"]["file_id"]:
            try:
                path = download_audio(client, normalized["audio"]["file_id"], audio_dir)
                normalized["audio"]["local_path"] = str(path)
                normalized["audio"]["size_bytes"] = path.stat().st_size
                print(f"      Аудио: {path.name} ({path.stat().st_size:,} байт)")
            except Exception as e:
                print(f"      Не удалось скачать аудио: {e}")
                normalized["audio"]["error"] = str(e)

        results.append(normalized)

    out_file = Path("calls_data.json")
    out_file.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nГотово! Данные сохранены в {out_file}")
    print(f"   Размер JSON: {out_file.stat().st_size} байт")
    print(f"   Аудиофайлов: {sum(1 for r in results if r.get('audio', {}).get('local_path'))}")


if __name__ == "__main__":
    main()
