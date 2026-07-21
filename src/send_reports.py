"""
Отправка персональных отчётов менеджерам в Bitrix24 в 8:00.
Читает analyses.json и calls_data.json, формирует отчёт за вчерашний день,
отправляет личное сообщение каждому менеджеру через Bitrix24 API.
"""
import json
import os
import sys
import requests
from datetime import datetime, timedelta
from pathlib import Path

# ============================================================
# НАСТРОЙКИ
# ============================================================

# Bitrix24 user ID → имя менеджера
MANAGERS = {
    1286: "Роман",
    2100: "Ирина",
    2196: "Анна",
    # Денис (2212) исключён
}

WEBHOOK_URL = os.environ.get("BITRIX_WEBHOOK_URL", "").rstrip("/")
APP_URL = os.environ.get("APP_URL", "https://sales-analytics-qyf6.onrender.com")

# ============================================================
# ДАННЫЕ
# ============================================================

def load_json(path):
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"⚠ Ошибка чтения {path}: {e}")
        return None

def get_yesterday_range():
    now = datetime.now()
    dow = now.weekday()  # 0=пн, 4=пт
    if dow == 0:    # понедельник → пятница
        days_back = 3
    elif dow == 6:  # воскресенье → пятница
        days_back = 2
    elif dow == 5:  # суббота → пятница
        days_back = 1
    else:
        days_back = 1
    prev = now - timedelta(days=days_back)
    start = prev.replace(hour=0, minute=0, second=0, microsecond=0)
    end = prev.replace(hour=23, minute=59, second=59, microsecond=0)
    MONTHS_RU = ["января","февраля","марта","апреля","мая","июня",
                  "июля","августа","сентября","октября","ноября","декабря"]
    date_label = f"{prev.day} {MONTHS_RU[prev.month-1]} {prev.year}"
    return start, end, date_label

def in_range(c, start, end):
    try:
        d = datetime.fromisoformat(c.get("created", "").replace("Z", ""))
        d = d.replace(tzinfo=None)
        return start <= d <= end
    except Exception:
        return False

# ============================================================
# ФОРМИРОВАНИЕ ОТЧЁТА
# ============================================================

def format_score(score):
    if score is None:
        return "—"
    s = float(score)
    if s >= 8:
        return f"✅ {score}/10"
    elif s >= 6:
        return f"👍 {score}/10"
    elif s >= 4:
        return f"⚠️ {score}/10"
    else:
        return f"🔴 {score}/10"

def build_report(manager_id, manager_name, calls, analyses, date_label):
    """Формирует текст отчёта для одного менеджера за день."""
    # Фильтруем звонки этого менеджера
    start, end, _ = get_yesterday_range()
    my_calls = [
        c for c in calls
        if c.get("manager", {}).get("id") == manager_id
        and in_range(c, start, end)
    ]

    if not my_calls:
        return None  # нет звонков — не отправляем

    # Считаем статистику
    analyzed = []
    not_analyzed = []
    need_callback = []

    for c in my_calls:
        aid = c["activity_id"]
        a_data = analyses.get(aid, {})
        an = a_data.get("analysis") or {}

        # Пропускаем нерелевантные и плохое качество
        if an.get("not_sales") or an.get("poor_audio"):
            continue

        if an.get("overall_score") is not None:
            analyzed.append((c, an))
            # Нужен перезвон: критичный или нет следующего шага
            if an.get("is_critical") or an.get("flags", {}).get("no_next_step"):
                need_callback.append((c, an))
        else:
            not_analyzed.append(c)

    if not analyzed and not not_analyzed:
        return None

    # Средняя оценка
    scores = [float(an.get("overall_score", 0)) for _, an in analyzed if an.get("overall_score")]
    avg = round(sum(scores) / len(scores), 1) if scores else None

    # Лучший и худший звонки
    sorted_analyzed = sorted(analyzed, key=lambda x: float(x[1].get("overall_score", 0) or 0), reverse=True)
    best = sorted_analyzed[0] if sorted_analyzed else None
    worst = sorted_analyzed[-1] if len(sorted_analyzed) > 1 else None

    # Частые слабые места
    weak_points = {}
    for _, an in analyzed:
        for imp in an.get("improvements", []) or []:
            if isinstance(imp, dict) and imp.get("text"):
                text = imp["text"][:80]
                weak_points[text] = weak_points.get(text, 0) + 1
    top_weak = sorted(weak_points.items(), key=lambda x: -x[1])[:3]

    # Частые сильные стороны
    strong_points = {}
    for _, an in analyzed:
        for s in an.get("strengths", []) or []:
            if isinstance(s, dict) and s.get("text"):
                text = s["text"][:80]
                strong_points[text] = strong_points.get(text, 0) + 1
    top_strong = sorted(strong_points.items(), key=lambda x: -x[1])[:2]

    # ---- Собираем текст ----
    lines = []
    lines.append(f"Привет, {manager_name}! 👋")
    lines.append(f"Вот твой отчёт за {date_label}:\n")

    lines.append(f"📊 *Итого:* {len(my_calls)} звонков, проанализировано {len(analyzed)}")
    if avg is not None:
        lines.append(f"⭐ *Средняя оценка:* {format_score(avg)}")
    lines.append("")

    if top_strong:
        lines.append("✅ *Что получилось хорошо:*")
        for text, cnt in top_strong:
            lines.append(f"   — {text}")
        lines.append("")

    if top_weak:
        lines.append("📌 *Над чем поработать:*")
        for text, cnt in top_weak:
            lines.append(f"   — {text}")
        lines.append("")

    if need_callback:
        lines.append(f"📞 *Требуют внимания ({len(need_callback)} звонков):*")
        for c, an in need_callback[:5]:
            client = c.get("client", {}).get("name", "Неизвестный клиент")
            score = an.get("overall_score", "—")
            reason = an.get("critical_reason") or an.get("score_explanation", "")[:60]
            call_url = f"{APP_URL}/calls/{c['activity_id']}"
            lines.append(f"   🔴 {client} — {score}/10")
            if reason:
                lines.append(f"      {reason}")
            lines.append(f"      👉 {call_url}")
        lines.append("")

    if best:
        bc, ban = best
        client = bc.get("client", {}).get("name", "")
        lines.append(f"🏆 *Лучший звонок:* {client} — {format_score(ban.get('overall_score'))}")
        lines.append(f"   {APP_URL}/calls/{bc['activity_id']}")
        lines.append("")

    lines.append(f"🔍 *Все твои звонки:*")
    lines.append(f"   {APP_URL}/managers/{manager_id}")

    return "\n".join(lines)

# ============================================================
# ОТПРАВКА В BITRIX
# ============================================================

def send_message(user_id, text, date_label):
    """Создаёт задачу менеджеру в Bitrix24 с текстом отчёта."""
    if not WEBHOOK_URL:
        print(f"⚠ BITRIX_WEBHOOK_URL не задан")
        return False

    url = f"{WEBHOOK_URL}/tasks.task.add"
    payload = {
        "fields": {
            "TITLE": f"📊 Отчёт ИИгорь за {date_label}",
            "DESCRIPTION": text,
            "RESPONSIBLE_ID": user_id,
            "CREATED_BY": user_id,
            "PRIORITY": "1",  # обычный приоритет
            "ALLOW_CHANGE_DEADLINE": "Y",
            "ALLOW_TIME_TRACKING": "N",
            "TASK_CONTROL": "N",
            "GROUP_ID": 0,
        }
    }
    try:
        r = requests.post(url, json=payload, timeout=30)
        data = r.json()
        if data.get("result") and data["result"].get("task"):
            task_id = data["result"]["task"].get("id")
            print(f"✅ Задача создана для пользователя {user_id} (task ID: {task_id})")
            return True
        error = data.get("error_description") or data.get("error") or str(data)
        print(f"❌ Ошибка создания задачи для {user_id}: {error}")
        return False
    except Exception as e:
        print(f"❌ Ошибка запроса для {user_id}: {e}")
        return False

# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 60)
    print("Отправка ежедневных отчётов менеджерам в Bitrix24")
    print("=" * 60)

    calls = load_json("calls_data.json")
    analyses = load_json("analyses.json")

    if not calls:
        print("⚠ calls_data.json пустой или не найден")
        sys.exit(1)
    if not analyses:
        print("⚠ analyses.json пустой или не найден")
        sys.exit(1)

    print(f"Загружено: {len(calls)} звонков, {len(analyses)} анализов")

    _, _, date_label = get_yesterday_range()
    print(f"Отчёт за: {date_label}\n")

    sent = 0
    for user_id, name in MANAGERS.items():
        print(f"\n--- {name} (ID {user_id}) ---")
        text = build_report(user_id, name, calls, analyses, date_label)
        if text is None:
            print(f"   Нет звонков за {date_label} — пропускаем")
            continue
        print(f"   Сообщение готово ({len(text)} символов)")
        if send_message(user_id, text, date_label):
            sent += 1

    print(f"\n✅ Отправлено {sent}/{len(MANAGERS)} отчётов")

if __name__ == "__main__":
    main()
