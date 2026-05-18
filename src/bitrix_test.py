"""
Тестовый скрипт: проверяем подключение к Bitrix24
и смотрим структуру звонков.
Запускается через GitHub Actions, ничего не меняет в Битриксе.
"""

import os
import json
import requests


def get_webhook_url() -> str:
    """Читаем URL вебхука из переменной окружения GitHub Secrets."""
    url = os.environ.get("BITRIX_WEBHOOK_URL")
    if not url:
        raise RuntimeError(
            "Не найден BITRIX_WEBHOOK_URL. "
            "Проверьте, что секрет добавлен в GitHub Settings -> Secrets."
        )
    if not url.endswith("/"):
        url += "/"
    return url


def call_bitrix(method: str, params: dict = None) -> dict:
    """Вызывает метод Bitrix24 REST API."""
    webhook = get_webhook_url()
    response = requests.post(webhook + method, json=params or {}, timeout=30)
    response.raise_for_status()
    return response.json()


def mask_phone(phone: str) -> str:
    """Маскируем телефон для безопасных логов."""
    if not phone or len(phone) < 6:
        return "***"
    return phone[:4] + "*" * (len(phone) - 6) + phone[-2:]


def main():
    print("=" * 60)
    print("Тест подключения к Bitrix24")
    print("=" * 60)

    # Шаг 1: проверяем, что вебхук работает
    print("\n[1/3] Запрашиваем информацию о портале...")
    try:
        info = call_bitrix("app.info")
        print("   OK - подключение работает")
    except Exception as e:
        print(f"   ОШИБКА: {e}")
        raise

    # Шаг 2: получаем список последних звонков
    print("\n[2/3] Запрашиваем последние 5 звонков (длительность > 20 сек)...")
    calls = call_bitrix(
        "voximplant.statistic.get",
        {
            "ORDER": {"CALL_START_DATE": "DESC"},
            "FILTER": {">CALL_DURATION": 20},
            "SELECT": ["*"],
            "start": 0,
        },
    )

    if "error" in calls:
        print(f"   ОШИБКА от Битрикса: {calls.get('error_description', calls['error'])}")
        return

    if "result" not in calls or not calls["result"]:
        print("   Звонков не найдено. Возможные причины:")
        print("      - В Битриксе пока нет записанных звонков")
        print("      - У вебхука нет права telephony")
        print("      - Все звонки короче 20 секунд")
        return

    call_list = calls["result"][:5]
    print(f"   OK - найдено {len(call_list)} звонков")

    # Шаг 3: показываем структуру первого звонка
    print("\n[3/3] Структура первого звонка (поля):")
    print("-" * 60)
    first_call = call_list[0]
    # Маскируем номера телефонов в логе
    safe_call = dict(first_call)
    if "PHONE_NUMBER" in safe_call:
        safe_call["PHONE_NUMBER"] = mask_phone(str(safe_call["PHONE_NUMBER"]))
    print(json.dumps(safe_call, indent=2, ensure_ascii=False, default=str))
    print("-" * 60)

    print("\nКраткая сводка по 5 звонкам:")
    for i, call in enumerate(call_list, 1):
        duration = call.get("CALL_DURATION", "?")
        call_type = call.get("CALL_TYPE", "?")
        phone = mask_phone(str(call.get("PHONE_NUMBER", "")))
        date = call.get("CALL_START_DATE", "?")
        has_record = "ЕСТЬ" if call.get("CALL_RECORD_URL") else "НЕТ"
        print(
            f"   {i}. {date} | тип:{call_type} | {duration}с | "
            f"тел:{phone} | запись:{has_record}"
        )

    print("\nТест пройден успешно!")


if __name__ == "__main__":
    main()
