"""
Расширенный тест: смотрим, что вообще есть в Bitrix24
по звонкам и какая телефония подключена.
"""

import os
import json
import requests
from datetime import datetime, timedelta


def get_webhook_url() -> str:
    url = os.environ.get("BITRIX_WEBHOOK_URL")
    if not url:
        raise RuntimeError("Не найден BITRIX_WEBHOOK_URL")
    if not url.endswith("/"):
        url += "/"
    return url


def call_bitrix(method: str, params: dict = None) -> dict:
    webhook = get_webhook_url()
    response = requests.post(webhook + method, json=params or {}, timeout=30)
    response.raise_for_status()
    return response.json()


def mask_phone(phone: str) -> str:
    if not phone or len(phone) < 6:
        return "***"
    return phone[:4] + "*" * (len(phone) - 6) + phone[-2:]


def main():
    print("=" * 60)
    print("Расширенный тест Bitrix24")
    print("=" * 60)

    # ============================================================
    # 1. Сколько всего звонков в системе
    # ============================================================
    print("\n[1] Всего звонков в системе...")
    total = call_bitrix(
        "voximplant.statistic.get",
        {"FILTER": {">CALL_DURATION": 0}, "start": -1, "SELECT": ["ID"]},
    )
    if "total" in total:
        print(f"   Всего звонков: {total['total']}")
    if total.get("result"):
        print(f"   Получено в выборке: {len(total['result'])}")

    # ============================================================
    # 2. Самый свежий звонок
    # ============================================================
    print("\n[2] Самый свежий звонок в системе...")
    latest = call_bitrix(
        "voximplant.statistic.get",
        {
            "ORDER": {"CALL_START_DATE": "DESC"},
            "FILTER": {">CALL_DURATION": 0},
            "SELECT": ["ID", "CALL_START_DATE", "CALL_DURATION",
                       "CALL_RECORD_URL", "RECORD_FILE_ID", "PORTAL_NUMBER"],
            "start": 0,
        },
    )
    if latest.get("result"):
        c = latest["result"][0]
        print(f"   Дата: {c.get('CALL_START_DATE')}")
        print(f"   Длительность: {c.get('CALL_DURATION')} сек")
        print(f"   PORTAL_NUMBER: {c.get('PORTAL_NUMBER')}")
        print(f"   CALL_RECORD_URL: {c.get('CALL_RECORD_URL')}")
        print(f"   RECORD_FILE_ID: {c.get('RECORD_FILE_ID')}")

    # ============================================================
    # 3. Звонки за последний месяц
    # ============================================================
    print("\n[3] Звонки за последние 30 дней...")
    month_ago = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S")
    recent = call_bitrix(
        "voximplant.statistic.get",
        {
            "ORDER": {"CALL_START_DATE": "DESC"},
            "FILTER": {">=CALL_START_DATE": month_ago, ">CALL_DURATION": 0},
            "SELECT": ["ID", "CALL_START_DATE", "CALL_DURATION", "CALL_RECORD_URL"],
            "start": -1,
        },
    )
    count_30d = recent.get("total", 0)
    print(f"   Звонков за 30 дней: {count_30d}")
    if recent.get("result"):
        with_record = sum(1 for c in recent["result"] if c.get("CALL_RECORD_URL"))
        print(f"   Из них с CALL_RECORD_URL: {with_record}")

    # ============================================================
    # 4. Какие приложения телефонии подключены
    # ============================================================
    print("\n[4] Подключенные приложения телефонии (REST APPS)...")
    try:
        apps = call_bitrix("telephony.externalLine.get", {})
        if apps.get("result"):
            for line in apps["result"]:
                print(f"   - Линия: {line}")
        else:
            print(f"   Внешние линии: {json.dumps(apps, ensure_ascii=False)[:200]}")
    except Exception as e:
        print(f"   Не удалось получить внешние линии: {e}")

    # ============================================================
    # 5. Уникальные PORTAL_NUMBER (откуда идут звонки)
    # ============================================================
    print("\n[5] Источники звонков (последние 100)...")
    sample = call_bitrix(
        "voximplant.statistic.get",
        {
            "ORDER": {"CALL_START_DATE": "DESC"},
            "FILTER": {">CALL_DURATION": 0},
            "SELECT": ["PORTAL_NUMBER", "CALL_START_DATE"],
            "start": 0,
        },
    )
    if sample.get("result"):
        sources = {}
        for c in sample["result"]:
            src = c.get("PORTAL_NUMBER", "(unknown)")
            sources[src] = sources.get(src, 0) + 1
        for src, cnt in sorted(sources.items(), key=lambda x: -x[1]):
            print(f"   {cnt:3d} звонков от: {src}")

    # ============================================================
    # 6. CRM Activities - может звонки логируются как дела CRM
    # ============================================================
    print("\n[6] Звонки в CRM Activities (call) за последние 7 дней...")
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")
    try:
        activities = call_bitrix(
            "crm.activity.list",
            {
                "filter": {
                    "TYPE_ID": 2,  # 2 = звонок
                    ">=CREATED": week_ago,
                },
                "select": ["ID", "SUBJECT", "CREATED", "RESPONSIBLE_ID", "SETTINGS"],
                "order": {"CREATED": "DESC"},
                "start": 0,
            },
        )
        if activities.get("result"):
            print(f"   Найдено активностей-звонков: {len(activities['result'])}")
            for a in activities["result"][:3]:
                print(f"   - {a.get('CREATED')} | {a.get('SUBJECT')} | менеджер: {a.get('RESPONSIBLE_ID')}")
        else:
            print(f"   Активностей-звонков нет за неделю")
    except Exception as e:
        print(f"   Ошибка: {e}")

    print("\n" + "=" * 60)
    print("Тест завершён. Передайте этот вывод Claude.")
    print("=" * 60)


if __name__ == "__main__":
    main()
