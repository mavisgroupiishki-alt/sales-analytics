"""
Основной модуль работы с Bitrix24.
Забирает звонки за сутки, скачивает аудио,
вычисляет длительность из START_TIME / END_TIME / DURATION.

ФИКС менеджеров (28.05.2026):
В звонках через Asterisk-интеграцию Bitrix24 автоматически подставляет
RESPONSIBLE_ID как "ответственного за компанию", что не соответствует реальному
звонящему. Теперь приоритет: AUTHOR_ID (тот, кто реально создал звонок).
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

# Имена менеджеров для фильтрации (приведём к lower при сравнении)
ALLOWED_MANAGERS = [
    "Роман Авсеенко",
    "Екатерина Халько",
    "Ирина Богомольцева",
]

# ID менеджеров для надёжной фильтрации (если имя в Bitrix отличается)
# По данным из логов: Роман=1286, Екатерина=2154, Ирина=2100
ALLOWED_MANAGER_IDS = [1286, 2154, 2100]


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


def determine_real_manager_id(activity: Dict[str, Any]) -> int:
    """
    Определяет ID реального менеджера, который вёл звонок.
    
    Приоритет (от самого надёжного к менее надёжному):
    1. AUTHOR_ID — кто инициировал/создал звонок (обычно реальный звонящий)
    2. CREATED_BY_ID / CREATED_BY — кто создал запись активности
    3. RESPONSIBLE_ID — ответственный (часто подменяется автоматически на ответственного за компанию)
    
    Если хотя бы один из приоритетных полей совпадает с разрешённым менеджером — используем его.
    """
    author_id = activity.get("AUTHOR_ID")
    created_by_id = activity.get("CREATED_BY_ID") or activity.get("CREATED_BY")
    responsible_id = activity.get("RESPONSIBLE_ID")

    # Приводим к int
    def to_int(val):
        try:
            return int(val) if val else 0
        except (TypeError, ValueError):
            return 0

    author_id = to_int(author_id)
    created_by_id = to_int(created_by_id)
    responsible_id = to_int(responsible_id)

    # Приоритет 1: AUTHOR_ID, если он в списке наших менеджеров
    if author_id and author_id in ALLOWED_MANAGER_IDS:
        return author_id
    # Приоритет 2: CREATED_BY_ID
    if created_by_id and created_by_id in ALLOWED_MANAGER_IDS:
        return created_by_id
    # Приоритет 3: AUTHOR_ID даже если не в списке (для логирования)
    if author_id:
        return author_id
    # Запасной: RESPONSIBLE_ID
    return responsible_id


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

    # ⭐ ФИКС: определяем реального менеджера через AUTHOR_ID
    manager_id = determine_real_manager_id(activity)
    manager_data = users.get(manager_id, {"id": manager_id, "name": f"User {manager_id}"})
    manager = {
        "id": manager_data["id"],
        "name": manager_data["name"],
        "email": manager_data.get("email", ""),
        "avatar_file": manager_data.get("avatar_file", ""),
    }

    # Сохраняем для диагностики все поля «ответственных»
    responsibility_debug = {
        "author_id": int(activity.get("AUTHOR_ID") or 0),
        "created_by_id": int(activity.get("CREATED_BY_ID") or activity.get("CREATED_BY") or 0),
        "responsible_id": int(activity.get("RESPONSIBLE_ID") or 0),
        "used_field": "author" if int(activity.get("AUTHOR_ID") or 0) == manager_id else (
            "created_by" if int(activity.get("CREATED_BY_ID") or activity.get("CREATED_BY") or 0) == manager_id else "responsible"
        ),
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
        "manager_debug": responsibility_debug,
        "client": {
            "name": client_name,
            "company": company,
            "phone_masked": _mask_phone(comm.get("VALUE", "")),
        },
        "crm": {
            "owner_type": owner_type,
            "owner_id": str(activity.get("OWNER_ID") or ""),
            "stage_name": "",  # заполняется ниже через доп. запрос
            "stage_id": "",
            "has_next_activity": False,
            "next_activity_date": "",
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


def enrich_with_deal_info(client: "Bitrix24Client", calls: list) -> list:
    """Обогащает звонки данными о статусе сделки и наличии следующего дела."""
    # Группируем по типу CRM объекта
    deal_ids = [c["crm"]["owner_id"] for c in calls if c["crm"]["owner_type"] == "deal" and c["crm"]["owner_id"]]
    lead_ids = [c["crm"]["owner_id"] for c in calls if c["crm"]["owner_type"] == "lead" and c["crm"]["owner_id"]]

    deal_stages = {}
    deal_activities = {}

    # Получаем статусы сделок
    if deal_ids:
        try:
            resp = client._post("crm.deal.list", {
                "filter": {"ID": deal_ids},
                "select": ["ID", "STAGE_ID", "STAGE_NAME"],
            })
            for d in (resp.get("result") or []):
                deal_stages[str(d["ID"])] = {
                    "stage_id": d.get("STAGE_ID", ""),
                    "stage_name": d.get("STAGE_NAME") or d.get("STAGE_ID", ""),
                }
        except Exception as e:
            logger.warning(f"Не удалось получить статусы сделок: {e}")

    # Проверяем наличие дел (следующий контакт)
    if deal_ids:
        try:
            from datetime import datetime
            now_str = datetime.now().strftime("%Y-%m-%d")
            resp = client._post("crm.activity.list", {
                "filter": {
                    "OWNER_TYPE_ID": 2,
                    "OWNER_ID": deal_ids,
                    "COMPLETED": "N",
                    ">=START_TIME": now_str,
                },
                "select": ["ID", "OWNER_ID", "START_TIME"],
            })
            for a in (resp.get("result") or []):
                oid = str(a.get("OWNER_ID",""))
                if oid not in deal_activities:
                    deal_activities[oid] = a.get("START_TIME","")
        except Exception as e:
            logger.warning(f"Не удалось получить дела: {e}")

    # Применяем к звонкам
    for c in calls:
        oid = c["crm"]["owner_id"]
        otype = c["crm"]["owner_type"]
        if otype == "deal" and oid in deal_stages:
            c["crm"]["stage_id"] = deal_stages[oid]["stage_id"]
            c["crm"]["stage_name"] = deal_stages[oid]["stage_name"]
        if otype == "deal" and oid in deal_activities:
            c["crm"]["has_next_activity"] = True
            c["crm"]["next_activity_date"] = deal_activities[oid]

    return calls



def enrich_with_deal_info(client, calls):
    """Обогащает звонки данными о статусе сделки и наличии следующего дела."""
    deal_ids = list(set(c["crm"]["owner_id"] for c in calls if c["crm"]["owner_type"] == "deal" and c["crm"]["owner_id"]))
    if not deal_ids:
        return calls

    deal_stages = {}
    deal_activities = {}

    try:
        resp = client._post("crm.deal.list", {
            "filter": {"ID": deal_ids},
            "select": ["ID", "STAGE_ID"],
        })
        for d in (resp.get("result") or []):
            deal_stages[str(d["ID"])] = d.get("STAGE_ID", "")
    except Exception as e:
        logger.warning(f"Не удалось получить статусы сделок: {e}")

    try:
        from datetime import datetime as _dt
        resp = client._post("crm.activity.list", {
            "filter": {"OWNER_TYPE_ID": 2, "OWNER_ID": deal_ids, "COMPLETED": "N"},
            "select": ["ID", "OWNER_ID", "START_TIME"],
        })
        for a in (resp.get("result") or []):
            oid = str(a.get("OWNER_ID", ""))
            if oid not in deal_activities:
                deal_activities[oid] = a.get("START_TIME", "")
    except Exception as e:
        logger.warning(f"Не удалось получить дела: {e}")

    for c in calls:
        oid = c["crm"]["owner_id"]
        if c["crm"]["owner_type"] == "deal":
            stage = deal_stages.get(oid, "")
            c["crm"]["stage_id"] = stage
            # Человекочитаемое имя стадии Bitrix (стандартные)
            stage_labels = {
                "NEW": "Новая", "PREPARATION": "Подготовка КП", "PREPAYMENT_INVOICE": "Счёт на предоплату",
                "EXECUTING": "В работе", "FINAL_INVOICE": "Финальный счёт",
                "WON": "Сделка успешна", "LOSE": "Сделка провалена", "APOLOGY": "Анализ причин отказа",
            }
            c["crm"]["stage_name"] = stage_labels.get(stage, stage)
        if oid in deal_activities:
            c["crm"]["has_next_activity"] = True
            c["crm"]["next_activity_date"] = deal_activities[oid]

    return calls

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

    # Собираем ВСЕ возможные ID менеджеров (AUTHOR + CREATED_BY + RESPONSIBLE)
    print(f"\nИнфо о менеджерах...")
    all_manager_ids = set()
    for c in raw_calls:
        for field in ("AUTHOR_ID", "CREATED_BY_ID", "CREATED_BY", "RESPONSIBLE_ID"):
            uid = c.get(field)
            if uid:
                try:
                    all_manager_ids.add(int(uid))
                except (TypeError, ValueError):
                    pass
    users = fetch_users(client, list(all_manager_ids))
    print(f"   Загружено пользователей: {len(users)}")

    print(f"\nФото менеджеров...")
    download_user_avatars(users, Path("docs") / "avatars")

    print(f"\nНормализуем...")
    all_results = [normalize_call(raw, users) for raw in raw_calls]

    # ====== ДИАГНОСТИКА: сколько звонков имеют расхождения ======
    print(f"\nДиагностика полей ответственности:")
    diff_count = 0
    for r in all_results:
        d = r.get("manager_debug", {})
        if d.get("author_id") and d.get("responsible_id") and d["author_id"] != d["responsible_id"]:
            diff_count += 1
    print(f"   Звонков с AUTHOR_ID != RESPONSIBLE_ID: {diff_count} из {len(all_results)}")
    if diff_count > 0:
        print(f"   ⚠️  Это значит, что Bitrix подменял ответственного автоматически.")
        print(f"   Теперь берём AUTHOR_ID (реального звонящего).")

    # ====== ФИЛЬТР: по ID + по имени (двойная защита) ======
    allowed_lower = [name.lower().strip() for name in ALLOWED_MANAGERS]
    before = len(all_results)
    results = [
        r for r in all_results
        if r["manager"]["id"] in ALLOWED_MANAGER_IDS
        or r["manager"]["name"].lower().strip() in allowed_lower
    ]
    print(f"\n   Фильтр по менеджерам: {before} → {len(results)} звонков")

    # Покажем, кого отфильтровали
    filtered_out = [r for r in all_results if r not in results]
    if filtered_out:
        excluded_managers = {}
        for r in filtered_out:
            m = r["manager"]["name"]
            excluded_managers[m] = excluded_managers.get(m, 0) + 1
        print(f"   Исключены звонки от:")
        for m, cnt in sorted(excluded_managers.items(), key=lambda x: -x[1]):
            print(f"      - {m}: {cnt}")

    # Кто остался — для проверки
    print(f"\n   Распределение по нашим менеджерам:")
    by_mgr = {}
    for r in results:
        m = r["manager"]["name"]
        by_mgr[m] = by_mgr.get(m, 0) + 1
    for m, cnt in sorted(by_mgr.items(), key=lambda x: -x[1]):
        print(f"      ✓ {m}: {cnt}")

    # Статистика по длительности
    with_duration = [r for r in results if r.get("duration_sec") is not None]
    long_enough = [r for r in results if r.get("duration_sec") and r["duration_sec"] >= MIN_DURATION_SEC]
    print(f"\nДлительность:")
    print(f"   С известной длительностью: {len(with_duration)}/{len(results)}")
    print(f"   ≥ {MIN_DURATION_SEC} сек: {len(long_enough)}")

    # Скачивание аудио
    audio_limit = int(os.environ.get("DOWNLOAD_AUDIO_COUNT", DEFAULT_AUDIO_DOWNLOAD_LIMIT))
    print(f"\nDOWNLOAD_AUDIO_COUNT = {audio_limit}")

    if audio_limit > 0 or audio_limit == -1:
        audio_dir = Path("audio_temp")
        audio_dir.mkdir(exist_ok=True)

        # Кандидаты: длинные (≥ 40 сек) + неизвестной длительности
        candidates = [
            r for r in results
            if r.get("audio") and r["audio"].get("file_id")
            and (r.get("duration_sec") is None or r["duration_sec"] >= MIN_DURATION_SEC)
        ]
        candidates.sort(key=lambda x: x.get("created", ""), reverse=True)

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

    # Обогащаем данными о статусе сделки и делах
    print("\nПолучаем статусы сделок и дела...")
    results = enrich_with_deal_info(client, results)

    out_file = Path("calls_data.json")
    out_file.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nГотово! Всего звонков: {len(results)}, JSON: {out_file.stat().st_size:,} б")



def send_manager_notifications(calls_with_analyses: list, webhook_url: str = None) -> None:
    """
    Отправляет менеджеру уведомление в Bitrix24 с оценкой его звонка.
    Вызывается из claude_analyzer.py после анализа.
    """
    import os, requests as _req, json as _json

    url = webhook_url or os.environ.get("BITRIX_WEBHOOK_URL", "")
    if not url:
        return

    for call, analysis in calls_with_analyses:
        manager_id = call.get("manager", {}).get("id")
        if not manager_id:
            continue

        score = analysis.get("overall_score")
        if score is None:
            continue

        client_name = call.get("client", {}).get("name", "клиент")
        recommendation = analysis.get("recommendation", "")
        is_crit = analysis.get("is_critical", False)
        activity_id = call.get("activity_id", "")

        emoji = "🔴" if is_crit else ("🟡" if float(score) < 7 else "🟢")
        msg = (
            f"{emoji} *Разбор звонка: {client_name}*\n"
            f"Оценка: *{score}/10*\n"
        )
        if recommendation:
            msg += f"💡 {recommendation}\n"
        if is_crit:
            msg += f"⚠️ {analysis.get('critical_reason', 'Требует внимания')}\n"

        try:
            _req.post(url.rstrip('/') + '/im.message.add', json={
                "DIALOG_ID": f"U{manager_id}",
                "MESSAGE": msg,
            }, timeout=10)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Не удалось отправить уведомление менеджеру {manager_id}: {e}")


if __name__ == "__main__":
    main()
