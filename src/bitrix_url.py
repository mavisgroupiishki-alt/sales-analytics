"""Безопасная работа с URL входящего webhook Bitrix24.

В переменную BITRIX_WEBHOOK_URL иногда по ошибке копируют URL из генератора
запросов вместе с названием метода, например ``.../profile.json``. Если затем
просто дописать другой метод, получится невалидный адрес вида
``.../profile.json/disk.file.get``.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def normalize_bitrix_webhook_url(raw_url: str) -> str:
    """Возвращает базовый URL webhook без названия REST-метода.

    Поддерживаются классический формат ``/rest/<user>/<token>`` и формат
    ``/rest/api/<user>/<token>``. Любой хвост после токена отбрасывается.
    """
    if not raw_url or not raw_url.strip():
        raise ValueError("BITRIX_WEBHOOK_URL не задан")

    parsed = urlsplit(raw_url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("BITRIX_WEBHOOK_URL должен быть абсолютным HTTP(S)-адресом")

    parts = [part for part in parsed.path.split("/") if part]
    try:
        rest_index = parts.index("rest")
    except ValueError as exc:
        raise ValueError("В BITRIX_WEBHOOK_URL отсутствует сегмент /rest/") from exc

    cursor = rest_index + 1
    if cursor < len(parts) and parts[cursor] == "api":
        cursor += 1

    # После /rest[/api]/ должны идти ID пользователя и секрет webhook.
    if len(parts) < cursor + 2:
        raise ValueError("BITRIX_WEBHOOK_URL не содержит ID пользователя и секрет webhook")

    base_parts = parts[: cursor + 2]
    base_path = "/" + "/".join(base_parts)
    return urlunsplit((parsed.scheme, parsed.netloc, base_path, "", ""))


def build_bitrix_method_url(raw_webhook_url: str, method: str) -> str:
    """Собирает URL конкретного REST-метода из любого варианта webhook URL."""
    method = (method or "").strip().strip("/")
    if not method:
        raise ValueError("Не указан метод Bitrix24")
    return f"{normalize_bitrix_webhook_url(raw_webhook_url)}/{method}"


def extract_webhook_token(raw_webhook_url: str) -> str:
    """Извлекает секрет webhook для старых crm_show_file.php ссылок."""
    base = normalize_bitrix_webhook_url(raw_webhook_url)
    return urlsplit(base).path.rstrip("/").split("/")[-1]


def add_webhook_auth_to_file_url(file_url: str, raw_webhook_url: str) -> str:
    """Заполняет пустой параметр auth в прямой ссылке файла Bitrix24.

    Существующий непустой auth не перезаписывается.
    """
    if not file_url:
        return ""

    parsed = urlsplit(file_url)
    params = parse_qsl(parsed.query, keep_blank_values=True)
    token = extract_webhook_token(raw_webhook_url)

    found_auth = False
    updated = []
    for key, value in params:
        if key == "auth":
            found_auth = True
            updated.append((key, value or token))
        else:
            updated.append((key, value))
    if not found_auth:
        updated.append(("auth", token))

    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(updated), parsed.fragment))


def safe_webhook_label(raw_webhook_url: str) -> str:
    """Возвращает безопасное представление URL без секрета для логов."""
    try:
        parsed = urlsplit(normalize_bitrix_webhook_url(raw_webhook_url))
        parts = parsed.path.rstrip("/").split("/")
        parts[-1] = "***"
        return urlunsplit((parsed.scheme, parsed.netloc, "/".join(parts), "", ""))
    except Exception:
        return "<invalid BITRIX_WEBHOOK_URL>"
