"""
Финальный тест: скачиваем аудиозапись звонка и проверяем,
что это реальный аудиофайл.
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


def main():
    print("=" * 60)
    print("Финальный тест: скачивание аудиозаписи звонка")
    print("=" * 60)

    # ============================================================
    # 1. Берём последний звонок с файлом
    # ============================================================
    print("\n[1] Ищем последний звонок с прикреплённым файлом...")
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")
    activities = call_bitrix(
        "crm.activity.list",
        {
            "filter": {"TYPE_ID": 2, ">=CREATED": week_ago},
            "select": ["ID", "CREATED", "FILES", "RESPONSIBLE_ID", "DIRECTION"],
            "order": {"CREATED": "DESC"},
            "start": 0,
        },
    )

    target = None
    for a in activities.get("result", []):
        if a.get("FILES"):
            target = a
            break

    if not target:
        print("   Не нашли звонок с файлом")
        return

    print(f"   Нашли звонок ID={target['ID']}")
    print(f"   Создан: {target['CREATED']}")
    print(f"   Менеджер: {target['RESPONSIBLE_ID']}")
    print(f"   Направление: {target.get('DIRECTION')} (1=входящий, 2=исходящий)")
    print(f"   Файлов: {len(target['FILES'])}")

    file_info = target["FILES"][0]
    file_id = file_info["id"]
    file_url = file_info["url"]
    print(f"   File ID: {file_id}")
    print(f"   File URL: {file_url}")

    # ============================================================
    # 2. Пробуем скачать через прямой URL с auth-токеном из webhook
    # ============================================================
    print("\n[2] Пробуем способ 1: прямой URL + добавляем токен из webhook...")
    webhook = get_webhook_url()
    # извлекаем токен из webhook URL
    # формат: https://mavisgroup.bitrix24.by/rest/2110/TOKEN/
    parts = webhook.rstrip("/").split("/")
    token = parts[-1]
    user_id = parts[-2]

    auth_url = file_url + token
    print(f"   Запрос (токен скрыт): {file_url}***")

    try:
        r = requests.get(auth_url, timeout=60, allow_redirects=True)
        print(f"   HTTP статус: {r.status_code}")
        print(f"   Content-Type: {r.headers.get('Content-Type')}")
        print(f"   Размер ответа: {len(r.content)} байт")
        if r.status_code == 200:
            ct = r.headers.get("Content-Type", "")
            if "audio" in ct or "octet-stream" in ct or "mpeg" in ct:
                print(f"   ✅ ПОЛУЧИЛИ АУДИО! Тип: {ct}")
                # Сохраним первые байты, чтобы посмотреть сигнатуру
                first_bytes = r.content[:16].hex()
                print(f"   Первые 16 байт (hex): {first_bytes}")
                # MP3 начинается с ID3 или FFFB
                # WAV начинается с RIFF
                if r.content[:3] == b"ID3" or r.content[:2] == b"\xff\xfb" or r.content[:2] == b"\xff\xf3":
                    print(f"   ✅ Это MP3 файл!")
                elif r.content[:4] == b"RIFF":
                    print(f"   ✅ Это WAV файл!")
                else:
                    print(f"   ⚠️ Неизвестный формат, но точно бинарный файл")
                return
            else:
                print(f"   ⚠️ Получили не аудио. Первые 300 символов:")
                print(f"   {r.text[:300]}")
    except Exception as e:
        print(f"   Ошибка: {e}")

    # ============================================================
    # 3. Пробуем через disk API
    # ============================================================
    print("\n[3] Пробуем способ 2: disk.file.get...")
    try:
        result = call_bitrix("disk.file.get", {"id": file_id})
        print(f"   Ответ: {json.dumps(result, ensure_ascii=False, default=str)[:500]}")
        if result.get("result") and result["result"].get("DOWNLOAD_URL"):
            dl_url = result["result"]["DOWNLOAD_URL"]
            print(f"   DOWNLOAD_URL получен")
            r = requests.get(dl_url, timeout=60)
            print(f"   Статус скачивания: {r.status_code}, размер: {len(r.content)} байт")
    except Exception as e:
        print(f"   Не сработало: {e}")

    # ============================================================
    # 4. Пробуем через voximplant.url.get
    # ============================================================
    print("\n[4] Пробуем способ 3: voximplant.statistic.get по ORIGIN_ID...")
    # ORIGIN_ID = VI_externalCall.HASH.TIMESTAMP — извлечём CALL_ID
    origin = target.get("ORIGIN_ID", "")
    print(f"   ORIGIN_ID: {origin}")

    print("\n" + "=" * 60)
    print("Тест завершён. Передайте результат Claude.")
    print("=" * 60)


if __name__ == "__main__":
    main()
