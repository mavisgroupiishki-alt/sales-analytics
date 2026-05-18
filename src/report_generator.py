"""
Генератор HTML-отчёта по звонкам.
Читает calls_data.json и создаёт docs/index.html
в стиле дашборда из презентации.
"""

import json
import html
import logging
from pathlib import Path
from datetime import datetime
from collections import defaultdict
from typing import List, Dict, Any

logger = logging.getLogger(__name__)


# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================
def esc(text: Any) -> str:
    """Безопасно экранируем текст для HTML."""
    if text is None:
        return ""
    return html.escape(str(text))


def format_size(bytes_count: int) -> str:
    """123456 -> '120 КБ'"""
    if not bytes_count:
        return "—"
    if bytes_count < 1024:
        return f"{bytes_count} Б"
    if bytes_count < 1024 * 1024:
        return f"{bytes_count // 1024} КБ"
    return f"{bytes_count / (1024 * 1024):.1f} МБ"


def format_time(iso_str: str) -> str:
    """'2026-05-18T11:35:53+03:00' -> '11:35'"""
    if not iso_str:
        return "—"
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%H:%M")
    except Exception:
        return iso_str[:5]


def format_date(iso_str: str) -> str:
    """'2026-05-18T...' -> '18 мая'"""
    months = ["", "января", "февраля", "марта", "апреля", "мая", "июня",
              "июля", "августа", "сентября", "октября", "ноября", "декабря"]
    if not iso_str:
        return "—"
    try:
        dt = datetime.fromisoformat(iso_str)
        return f"{dt.day} {months[dt.month]}"
    except Exception:
        return iso_str[:10]


def manager_initials(name: str) -> str:
    """'Роман Авсеенко' -> 'РА'"""
    if not name:
        return "??"
    parts = [p for p in name.split() if p]
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    return name[:2].upper()


# ============================================================
# АГРЕГАТЫ
# ============================================================
def compute_stats(calls: List[Dict]) -> Dict[str, Any]:
    """Считаем общие метрики по списку звонков."""
    total = len(calls)
    incoming = sum(1 for c in calls if c.get("direction") == "incoming")
    outgoing = sum(1 for c in calls if c.get("direction") == "outgoing")

    by_manager = defaultdict(lambda: {"count": 0, "in": 0, "out": 0, "name": "", "id": None})
    for c in calls:
        m = c.get("manager", {})
        mid = m.get("id") or 0
        by_manager[mid]["id"] = mid
        by_manager[mid]["name"] = m.get("name", f"User {mid}")
        by_manager[mid]["count"] += 1
        if c.get("direction") == "incoming":
            by_manager[mid]["in"] += 1
        elif c.get("direction") == "outgoing":
            by_manager[mid]["out"] += 1

    managers_sorted = sorted(by_manager.values(), key=lambda x: -x["count"])

    return {
        "total": total,
        "incoming": incoming,
        "outgoing": outgoing,
        "managers_count": len(by_manager),
        "managers": managers_sorted,
    }


# ============================================================
# CSS — единый для всех страниц
# ============================================================
CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, "Segoe UI", system-ui, sans-serif;
  background: #F4F7FB;
  color: #1A2940;
  -webkit-font-smoothing: antialiased;
  line-height: 1.5;
}
.topbar {
  background: #0F2A47;
  color: #fff;
  padding: 16px 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  position: sticky;
  top: 0;
  z-index: 10;
  box-shadow: 0 2px 8px rgba(15,42,71,0.1);
}
.logo { display: flex; align-items: center; gap: 12px; font-weight: 700; font-size: 17px; }
.logo-dot {
  width: 32px; height: 32px;
  background: #F96167; border-radius: 8px;
  display: flex; align-items: center; justify-content: center;
  font-size: 16px;
}
.container { max-width: 1280px; margin: 0 auto; padding: 24px; }
.page-head {
  display: flex; justify-content: space-between; align-items: flex-end;
  margin-bottom: 24px; flex-wrap: wrap; gap: 16px;
}
.page-head h1 { font-size: 28px; font-weight: 700; margin-bottom: 4px; }
.page-head .sub { font-size: 14px; color: #475569; }
.date-pill {
  background: #fff; border: 1px solid #E2E8F0; border-radius: 8px;
  padding: 8px 14px; font-size: 13px; font-weight: 600;
}
.kpi-row {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 16px; margin-bottom: 24px;
}
.kpi {
  background: #fff; border-radius: 10px; padding: 20px;
  border-left: 4px solid #1C7293;
}
.kpi.accent { border-left-color: #F96167; }
.kpi.green { border-left-color: #10B981; }
.kpi.amber { border-left-color: #F9B248; }
.kpi-label {
  font-size: 11px; color: #475569; text-transform: uppercase;
  letter-spacing: 1px; font-weight: 600; margin-bottom: 8px;
}
.kpi-value { font-size: 30px; font-weight: 700; line-height: 1; }
.kpi-hint { margin-top: 8px; font-size: 12px; color: #94A3B8; }
.grid {
  display: grid; grid-template-columns: 1.4fr 1fr; gap: 20px;
  margin-bottom: 24px;
}
@media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
.panel {
  background: #fff; border-radius: 12px; border: 1px solid #E2E8F0;
  overflow: hidden;
}
.panel-head {
  padding: 14px 20px; border-bottom: 1px solid #E2E8F0;
  display: flex; justify-content: space-between; align-items: center;
}
.panel-head h3 { font-size: 15px; font-weight: 700; }
.panel-head .hint { font-size: 12px; color: #94A3B8; }
.manager-row {
  display: grid; grid-template-columns: 32px 1fr auto auto;
  align-items: center; gap: 14px;
  padding: 12px 20px; border-bottom: 1px solid #F4F7FB;
}
.manager-row:last-child { border-bottom: none; }
.rank {
  width: 26px; height: 26px; border-radius: 50%;
  background: #F4F7FB; color: #475569;
  font-weight: 700; font-size: 12px;
  display: flex; align-items: center; justify-content: center;
}
.rank.gold { background: #F9B248; color: #fff; }
.rank.silver { background: #CBD5E1; color: #1A2940; }
.rank.bronze { background: #D4A574; color: #fff; }
.manager-info { display: flex; align-items: center; gap: 12px; min-width: 0; }
.m-avatar {
  width: 36px; height: 36px; border-radius: 50%;
  background: linear-gradient(135deg, #1C7293, #065A82);
  color: #fff; display: flex; align-items: center; justify-content: center;
  font-weight: 600; font-size: 12px; flex-shrink: 0;
}
.m-avatar.b { background: linear-gradient(135deg, #F96167, #F9B248); }
.m-avatar.c { background: linear-gradient(135deg, #10B981, #1C7293); }
.m-avatar.d { background: linear-gradient(135deg, #94A3B8, #475569); }
.m-avatar.e { background: linear-gradient(135deg, #F9B248, #F96167); }
.m-name { font-weight: 600; font-size: 14px; }
.m-stats { font-size: 11px; color: #94A3B8; }
.m-count {
  font-weight: 700; font-size: 16px; min-width: 30px; text-align: right;
}
.m-bars { display: flex; gap: 4px; font-size: 11px; color: #94A3B8; }
.m-bars b { color: #1A2940; font-weight: 700; }
.call-row {
  display: grid; grid-template-columns: auto 1fr auto;
  align-items: center; gap: 14px;
  padding: 12px 20px; border-bottom: 1px solid #F4F7FB;
  font-size: 13px;
}
.call-row:last-child { border-bottom: none; }
.call-direction {
  width: 28px; height: 28px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-size: 14px; flex-shrink: 0;
}
.call-direction.in { background: #DCFCE7; color: #10B981; }
.call-direction.out { background: #DBEAFE; color: #1C7293; }
.call-info { min-width: 0; }
.call-client { font-weight: 600; }
.call-meta { font-size: 11px; color: #94A3B8; }
.call-time { font-size: 12px; color: #475569; white-space: nowrap; }
.notice {
  background: #FEF3C7; border: 1px solid #FCD34D;
  border-radius: 12px; padding: 16px 20px;
  margin-bottom: 24px;
  font-size: 13px; color: #92400E;
}
.notice b { font-weight: 700; }
.footer-note {
  text-align: center; padding: 24px; font-size: 12px; color: #94A3B8;
}
.empty {
  padding: 40px 20px; text-align: center; color: #94A3B8; font-size: 14px;
}
"""

# ============================================================
# ГЛАВНАЯ СТРАНИЦА
# ============================================================
def render_index(calls: List[Dict], stats: Dict, generated_at: str) -> str:
    """Главная страница: сводка дня."""
    today = format_date(generated_at)

    # KPI блок
    kpi_html = f"""
    <div class="kpi">
      <div class="kpi-label">Всего звонков</div>
      <div class="kpi-value">{stats['total']}</div>
      <div class="kpi-hint">за сегодняшний день</div>
    </div>
    <div class="kpi green">
      <div class="kpi-label">Входящих</div>
      <div class="kpi-value">{stats['incoming']}</div>
      <div class="kpi-hint">от клиентов</div>
    </div>
    <div class="kpi accent">
      <div class="kpi-label">Исходящих</div>
      <div class="kpi-value">{stats['outgoing']}</div>
      <div class="kpi-hint">к клиентам</div>
    </div>
    <div class="kpi amber">
      <div class="kpi-label">Менеджеров</div>
      <div class="kpi-value">{stats['managers_count']}</div>
      <div class="kpi-hint">говорили сегодня</div>
    </div>
    """

    # Список менеджеров
    avatars = ["", "b", "c", "d", "e"]
    managers_html = ""
    for i, m in enumerate(stats["managers"][:10], 1):
        rank_class = ""
        if i == 1: rank_class = "gold"
        elif i == 2: rank_class = "silver"
        elif i == 3: rank_class = "bronze"

        avatar_class = avatars[i % len(avatars)]
        managers_html += f"""
        <div class="manager-row">
          <div class="rank {rank_class}">{i}</div>
          <div class="manager-info">
            <div class="m-avatar {avatar_class}">{esc(manager_initials(m['name']))}</div>
            <div>
              <div class="m-name">{esc(m['name'])}</div>
              <div class="m-stats">
                <span class="m-bars">входящих: <b>{m['in']}</b> · исходящих: <b>{m['out']}</b></span>
              </div>
            </div>
          </div>
          <div class="m-count">{m['count']}</div>
        </div>
        """

    if not managers_html:
        managers_html = '<div class="empty">Сегодня пока не было звонков</div>'

    # Последние звонки
    recent = sorted(calls, key=lambda x: x.get("created", ""), reverse=True)[:10]
    calls_html = ""
    for c in recent:
        direction = c.get("direction", "")
        dir_icon = "↓" if direction == "incoming" else "↑"
        dir_class = "in" if direction == "incoming" else "out"
        client_name = c.get("client", {}).get("name", "Неизвестно")
        company = c.get("client", {}).get("company", "")
        manager = c.get("manager", {}).get("name", "")

        calls_html += f"""
        <div class="call-row">
          <div class="call-direction {dir_class}">{dir_icon}</div>
          <div class="call-info">
            <div class="call-client">{esc(client_name)}</div>
            <div class="call-meta">{esc(company)} · менеджер: {esc(manager)}</div>
          </div>
          <div class="call-time">{format_time(c.get('created', ''))}</div>
        </div>
        """

    if not calls_html:
        calls_html = '<div class="empty">Звонков пока нет</div>'

    # Уведомление об отсутствии ИИ-анализа
    notice_html = """
    <div class="notice">
      <b>⏳ ИИ-анализ временно недоступен.</b>
      Сейчас отображается базовая статистика по звонкам.
      Когда будет подключён Claude API, появятся: оценки качества по 17 критериям,
      рекомендации менеджерам, триггеры «Требует внимания» и список упущенных возможностей.
    </div>
    """

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AI Sales Analytics — Сводка дня</title>
  <style>{CSS}</style>
</head>
<body>
  <div class="topbar">
    <div class="logo">
      <div class="logo-dot">📞</div>
      <span>AI Sales Analytics</span>
    </div>
    <div style="font-size:12px; opacity:0.8;">обновлено {esc(generated_at[:16].replace('T', ' '))}</div>
  </div>

  <div class="container">
    <div class="page-head">
      <div>
        <h1>Сводка звонков</h1>
        <div class="sub">{stats['total']} звонков · {stats['managers_count']} менеджеров</div>
      </div>
      <div class="date-pill">📅 {today}</div>
    </div>

    {notice_html}

    <div class="kpi-row">
      {kpi_html}
    </div>

    <div class="grid">
      <div class="panel">
        <div class="panel-head">
          <h3>Рейтинг менеджеров</h3>
          <span class="hint">по количеству звонков</span>
        </div>
        {managers_html}
      </div>

      <div class="panel">
        <div class="panel-head">
          <h3>Последние звонки</h3>
          <span class="hint">10 свежих</span>
        </div>
        {calls_html}
      </div>
    </div>

    <div class="footer-note">
      Сгенерировано автоматически · Источник: Bitrix24 · Запуск: GitHub Actions
    </div>
  </div>
</body>
</html>
"""


# ============================================================
# ГЛАВНАЯ ФУНКЦИЯ
# ============================================================
def generate(calls_json_path: str = "calls_data.json", output_dir: str = "docs"):
    """Читает JSON и генерирует HTML-отчёт."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    json_path = Path(calls_json_path)
    out_dir = Path(output_dir)
    out_dir.mkdir(exist_ok=True)

    if not json_path.exists():
        logger.warning(f"Файл {json_path} не найден — создаю пустой отчёт")
        calls = []
    else:
        calls = json.loads(json_path.read_text(encoding="utf-8"))

    logger.info(f"Загружено звонков: {len(calls)}")

    stats = compute_stats(calls)
    generated_at = datetime.now().isoformat()

    index_html = render_index(calls, stats, generated_at)
    index_path = out_dir / "index.html"
    index_path.write_text(index_html, encoding="utf-8")
    logger.info(f"Создан: {index_path} ({index_path.stat().st_size:,} байт)")

    # .nojekyll — отключает Jekyll на GitHub Pages
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")

    print(f"\n✅ Отчёт сгенерирован")
    print(f"   Файл: {index_path}")
    print(f"   Звонков: {stats['total']}")
    print(f"   Менеджеров: {stats['managers_count']}")


if __name__ == "__main__":
    generate()
