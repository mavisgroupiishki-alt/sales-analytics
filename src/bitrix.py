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
    logger.info(f"Получено {len(activities)} звонков от Bitrix24")
    filtered = [a for a in activities if a.get("FILES")]
    logger.info(f"С аудио: {len(filtered)}")
    return filtered


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
                users[uid] = {
                    "id": uid,
                    "name": f"{u.get('NAME', '')} {u.get('LAST_NAME', '')}".strip() or u.get("EMAIL", f"User {uid}"),
                    "email": u.get("EMAIL", ""),
                    "photo_url": u.get("PERSONAL_PHOTO") or "",
                }
        except Exception as e:
            logger.warning(f"Не удалось получить пользователя {uid}: {e}")
            users[uid] = {"id": uid, "name": f"User {uid}", "email": "", "photo_url": ""}
    return users


def download_user_avatars(users: Dict[int, Dict], avatars_dir: Path) -> None:
    avatars_dir.mkdir(parents=True, exist_ok=True)
    for uid, u in users.items():
        photo_url = u.get("photo_url")
        if not photo_url:
            logger.info(f"   - {uid}: {u['name']} (нет фото)")
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
                logger.info(f"   - {uid}: {u['name']} → {avatar_path.name}")
        except Exception as e:
            logger.warning(f"   - {uid}: ошибка фото: {e}")


def download_audio(client: Bitrix24Client, file_id: int, save_to: Path) -> Path:
    meta = client.call("disk.file.get", {"id": file_id})
    file_data = meta.get("result")
    if not file_data:
        raise RuntimeError(f"Файл {file_id} не найден")
    download_url = file_data.get("DOWNLOAD_URL")
    if not download_url:
        raise RuntimeError(f"Нет DOWNLOAD_URL для {file_id}")

    file_name = file_data.get("NAME", f"call_{file_id}.mp3")
    safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", file_name)
    save_path = save_to / f"{file_id}_{safe_name}"

    response = requests.get(download_url, timeout=DEFAULT_REQUEST_TIMEOUT, stream=True)
    response.raise_for_status()
    save_path.parent.mkdir(parents=True, exist_ok=True)
    with open(save_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)
    return save_path


def compute_duration_sec(activity: Dict[str, Any]) -> Optional[int]:
    """Считает длительность звонка из доступных полей."""
    # Способ 1: явное поле DURATION (от телефонии)
    duration = activity.get("DURATION")
    if duration:
        try:
            d = int(duration)
            if d > 0:
                return d
        except (TypeError, ValueError):
            pass

    # Способ 2: START_TIME + END_TIME
    start = activity.get("START_TIME")
    end = activity.get("END_TIME")
    if start and end:
        try:
            t1 = datetime.fromisoformat(start.replace("Z", "+00:00") if "Z" in start else start)
            t2 = datetime.fromisoformat(end.replace("Z", "+00:00") if "Z" in end else end)
            d = int((t2 - t1).total_seconds())
            if d > 0:
                return d
        except Exception:
            pass

    return None


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
    manager_data = users.get(manager_id, {"id": manager_id, "name": f"User {manager_id}"})
    manager = {
        "id": manager_data["id"],
        "name": manager_data["name"],
        "email": manager_data.get("email", ""),
        "avatar_file": manager_data.get("avatar_file", ""),
    }

    direction_code = int(activity.get("DIRECTION") or 0)
    direction = {1: "incoming", 2: "outgoing"}.get(direction_code, "unknown")
    owner_type_id = int(activity.get("OWNER_TYPE_ID") or 0)
    owner_type = {1: "lead", 2: "deal", 3: "contact", 4: "company"}.get(owner_type_id, "unknown")

    duration_sec = compute_duration_sec(activity)

    result = {
        "activity_id": str(activity["ID"]),
        "created": activity.get("CREATED"),
        "duration_sec": duration_sec,
        "direction": direction,
        "subject": activity.get("SUBJECT", ""),
        "manager": manager,
        "client": {
            "name": client_name,
            "company": company,
            "phone_masked": _mask_phone(comm.get("VALUE", "")),
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


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    client = Bitrix24Client()
    now = datetime.now()
    date_from = now - timedelta(hours=24)
    date_to = now + timedelta(hours=3)

    print(f"\nЗагружаем звонки за {date_from:%Y-%m-%d %H:%M} - {date_to:%Y-%m-%d %H:%M}")
    raw_calls = fetch_calls(client, date_from, date_to)
    print(f"   Найдено: {len(raw_calls)} звонков с аудио")

    if not raw_calls:
        Path("calls_data.json").write_text("[]", encoding="utf-8")
        return

    print(f"\nИнфо о менеджерах...")
    manager_ids = list(set(int(c.get("RESPONSIBLE_ID") or 0) for c in raw_calls))
    users = fetch_users(client, manager_ids)
    print(f"   Загружено: {len(users)}")

    print(f"\nФото менеджеров...")
    download_user_avatars(users, Path("docs") / "avatars")

    print(f"\nНормализуем...")
    all_results = [normalize_call(raw, users) for raw in raw_calls]

    if ALLOWED_MANAGERS:
        allowed_lower = [name.lower().strip() for name in ALLOWED_MANAGERS]
        before = len(all_results)
        results = [r for r in all_results if r["manager"]["name"].lower().strip() in allowed_lower]
        print(f"   Фильтр по менеджерам: {before} → {len(results)}")
    else:
        results = all_results

    # Статистика по длительности
    with_duration = [r for r in results if r.get("duration_sec") is not None]
    long_enough = [r for r in results if r.get("duration_sec") and r["duration_sec"] >= MIN_DURATION_SEC]
    print(f"\nДлительность:")
    print(f"   С известной длительностью: {len(with_duration)}/{len(results)}")
    print(f"   ≥ {MIN_DURATION_SEC} сек: {len(long_enough)}")

    # Скачивание аудио
    audio_limit = int(os.environ.get("DOWNLOAD_AUDIO_COUNT", DEFAULT_AUDIO_DOWNLOAD_LIMIT))
    print(f"\nDOWNLOAD_AUDIO_COUNT = {audio_limit}")

    if audio_limit > 0:
        audio_dir = Path("audio_temp")
        audio_dir.mkdir(exist_ok=True)

        # Кандидаты: длинные (≥ 40 сек) + неизвестной длительности (пусть Whisper определит)
        candidates = [
            r for r in results
            if r.get("audio") and r["audio"].get("file_id")
            and (r.get("duration_sec") is None or r["duration_sec"] >= MIN_DURATION_SEC)
        ]
        candidates.sort(key=lambda x: x.get("created", ""), reverse=True)

        # Если 0 (special meaning) — скачиваем все
        to_download = candidates if audio_limit == -1 else candidates[:audio_limit]
        print(f"\nКандидатов: {len(candidates)}, скачиваем: {len(to_download)}")

        downloaded = 0
        for i, call in enumerate(to_download, 1):
            file_id = call["audio"]["file_id"]
            dur = call.get("duration_sec")
            dur_str = f"{dur} сек" if dur else "неизв."
            print(f"\n[{i}/{len(to_download)}] {call['activity_id']} ({call['manager']['name']}, {dur_str}):")
            try:
                path = download_audio(client, file_id, audio_dir)
                size = path.stat().st_size
                if size < MIN_AUDIO_SIZE_BYTES:
                    print(f"   ⚠ Слишком маленький ({size} б), пропускаем")
                    path.unlink()
                    continue
                call["audio"]["local_path"] = str(path)
                call["audio"]["size_bytes"] = size
                downloaded += 1
                print(f"   ✅ {path.name} ({size:,} б)")
            except Exception as e:
                print(f"   ❌ {e}")
                call["audio"]["error"] = str(e)

        print(f"\nИтого скачано: {downloaded}/{len(to_download)}")

    out_file = Path("calls_data.json")
    out_file.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nГотово! Всего звонков: {len(results)}, JSON: {out_file.stat().st_size:,} б")


if __name__ == "__main__":
    main()
