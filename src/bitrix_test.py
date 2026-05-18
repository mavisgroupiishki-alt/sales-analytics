"""
Глубокая проверка: смотрим Activities-звонки
и ищем прикреплённые аудиозаписи.
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


def mask_phone(text: str) -> str:
    """Маскируем номера в текстах вида '+375 29 666-80-07' """
    import re
    if not text:
        return text
    return re.sub(r'(\+?\d[\d\s\-]{6,})', lambda m: m.group(0)[:4] + '***' + m.group(0)[-4:], str(text))


def main():
    print("=" * 60)
    print("Глубокая проверка звонков через CRM Activities")
    print("=" * 60)

    # ============================================================
    # 1. Получаем последние 10 звонков-активностей
    # ============================================================
    print("\n[1] Последние 10 звонков в CRM Activities...")
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")
    activities = call_bitrix(
        "crm.activity.list",
        {
            "filter": {
                "TYPE_ID": 2,
                ">=CREATED": week_ago,
            },
            "select": ["*", "COMMUNICATIONS"],
            "order": {"CREATED": "DESC"},
            "start": 0,
        },
    )

    if not activities.get("result"):
        print("   Активностей-звонков не найдено за неделю")
        return

    acts = activities["result"][:10]
    print(f"   Получено: {len(acts)} звонков")

    # ============================================================
    # 2. Структура первого звонка (полная, со всеми полями)
    # ============================================================
    print("\n[2] Полная структура первого звонка:")
    print("-" * 60)
    first = dict(acts[0])
    if "SUBJECT" in first:
        first["SUBJECT"] = mask_phone(first["SUBJECT"])
    if "DESCRIPTION" in first and first["DESCRIPTION"]:
        first["DESCRIPTION"] = mask_phone(first["DESCRIPTION"])
    if "COMMUNICATIONS" in first and first["COMMUNICATIONS"]:
        for c in first["COMMUNICATIONS"]:
            if "VALUE" in c:
                c["VALUE"] = mask_phone(c["VALUE"])
    print(json.dumps(first, indent=2, ensure_ascii=False, default=str))
    print("-" * 60)

    # ============================================================
    # 3. Получаем детали через crm.activity.get для первой
    # ============================================================
    print("\n[3] Детали первого звонка через crm.activity.get...")
    activity_id = acts[0].get("ID")
    if activity_id:
        details = call_bitrix("crm.activity.get", {"id": activity_id})
        if details.get("result"):
            d = dict(details["result"])
            # Маскируем
            if "SUBJECT" in d:
                d["SUBJECT"] = mask_phone(d["SUBJECT"])
            print(json.dumps(d, indent=2, ensure_ascii=False, default=str)[:3000])
        else:
            print(f"   Не удалось: {details}")

    # ============================================================
    # 4. Ищем файлы, прикреплённые к звонку
    # ============================================================
    print("\n[4] Файлы, прикреплённые к звонку...")
    if activity_id:
        try:
            files = call_bitrix("crm.activity.binding.list", {"activityId": activity_id})
            print(f"   Bindings: {json.dumps(files, ensure_ascii=False)[:500]}")
        except Exception as e:
            print(f"   crm.activity.binding не сработал: {e}")

    # ============================================================
    # 5. Краткая сводка по 10 звонкам
    # ============================================================
    print("\n[5] Сводка по 10 последним звонкам:")
    for i, a in enumerate(acts[:10], 1):
        created = a.get("CREATED", "?")
        subject = mask_phone(a.get("SUBJECT", "?"))
        responsible = a.get("RESPONSIBLE_ID", "?")
        duration = a.get("END_TIME", "?")
        files_count = len(a.get("FILES", []) or [])
        has_settings = "ДА" if a.get("SETTINGS") else "НЕТ"
        print(f"   {i}. {created} | {subject[:50]}")
        print(f"      менеджер: {responsible} | файлов: {files_count} | settings: {has_settings}")

    # ============================================================
    # 6. Если у первого звонка есть FILES — смотрим их
    # ============================================================
    print("\n[6] Файлы из поля FILES первого звонка...")
    first_files = acts[0].get("FILES") or []
    if first_files:
        print(f"   Найдено файлов: {len(first_files)}")
        for f in first_files[:3]:
            print(f"   {json.dumps(f, ensure_ascii=False, default=str)}")
    else:
        print("   Поле FILES пустое")

    # ============================================================
    # 7. SETTINGS - часто там хранятся данные о звонке/записи
    # ============================================================
    print("\n[7] SETTINGS первого звонка...")
    settings = acts[0].get("SETTINGS")
    if settings:
        if isinstance(settings, str):
            try:
                settings = json.loads(settings)
            except:
                pass
        print(f"   {json.dumps(settings, indent=2, ensure_ascii=False)[:1500]}")
    else:
        print("   SETTINGS пусто")

    print("\n" + "=" * 60)
    print("Передайте вывод Claude — найдём, где записи.")
    print("=" * 60)


if __name__ == "__main__":
    main()
