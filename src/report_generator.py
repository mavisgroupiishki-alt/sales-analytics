"""
Генератор HTML-отчёта по звонкам.
Создаёт многостраничный сайт: главная, менеджеры, карточки звонков.
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
    if text is None:
        return ""
    return html.escape(str(text))


def format_time(iso_str: str) -> str:
    if not iso_str:
        return "—"
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%H:%M")
    except Exception:
        return iso_str[:5]


def format_datetime(iso_str: str) -> str:
    if not iso_str:
        return "—"
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%d.%m.%Y %H:%M")
    except Exception:
        return iso_str[:16]


def format_date(iso_str: str) -> str:
    months = ["", "января", "февраля", "марта", "апреля", "мая", "июня",
              "июля", "августа", "сентября", "октября", "ноября", "декабря"]
    if not iso_str:
        return "—"
    try:
        dt = datetime.fromisoformat(iso_str)
        return f"{dt.day} {months[dt.month]} {dt.year}"
    except Exception:
        return iso_str[:10]


def manager_initials(name: str) -> str:
    if not name:
        return "??"
    parts = [p for p in name.split() if p]
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    return name[:2].upper()


def manager_color_class(manager_id: int) -> str:
    classes = ["", "b", "c", "d", "e"]
    return classes[manager_id % len(classes)]


def direction_label(direction: str) -> str:
    return {"incoming": "Входящий", "outgoing": "Исходящий"}.get(direction, "Неизвестно")


def crm_link(call: Dict, base_url: str = "https://mavisgroup.bitrix24.by") -> str:
    """Ссылка на сделку/лид/контакт в Битриксе."""
    owner_type = call.get("crm", {}).get("owner_type")
    owner_id = call.get("crm", {}).get("owner_id")
    if not owner_id or owner_type == "unknown":
        return ""
    paths = {
        "deal": f"/crm/deal/details/{owner_id}/",
        "lead": f"/crm/lead/details/{owner_id}/",
        "contact": f"/crm/contact/details/{owner_id}/",
        "company": f"/crm/company/details/{owner_id}/",
    }
    return base_url + paths.get(owner_type, "")


# ============================================================
# АГРЕГАТЫ
# ============================================================
def compute_stats(calls: List[Dict]) -> Dict[str, Any]:
    total = len(calls)
    incoming = sum(1 for c in calls if c.get("direction") == "incoming")
    outgoing = sum(1 for c in calls if c.get("direction") == "outgoing")

    by_manager = defaultdict(lambda: {"count": 0, "in": 0, "out": 0, "name": "", "id": None, "calls": []})
    for c in calls:
        m = c.get("manager", {})
        mid = m.get("id") or 0
        by_manager[mid]["id"] = mid
        by_manager[mid]["name"] = m.get("name", f"User {mid}")
        by_manager[mid]["count"] += 1
        by_manager[mid]["calls"].append(c)
        if c.get("direction") == "incoming":
            by_manager[mid]["in"] += 1
        elif c.get("direction") == "outgoing":
            by_manager[mid]["out"] += 1

    managers_sorted = sorted(by_manager.values(), key=lambda x: -x["count"])
    return {
        "total": total, "incoming": incoming, "outgoing": outgoing,
        "managers_count": len(by_manager), "managers": managers_sorted,
    }


# ============================================================
# ЕДИНЫЙ ШАБЛОН СТРАНИЦЫ
# ============================================================
CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, "Segoe UI", system-ui, sans-serif;
  background: #F4F7FB; color: #1A2940;
  -webkit-font-smoothing: antialiased; line-height: 1.5;
}
a { color: inherit; text-decoration: none; }
.topbar {
  background: #0F2A47; color: #fff;
  padding: 14px 24px;
  display: flex; align-items: center; justify-content: space-between;
  position: sticky; top: 0; z-index: 10;
  box-shadow: 0 2px 8px rgba(15,42,71,0.1);
}
.logo {
  display: flex; align-items: center; gap: 12px;
  font-weight: 700; font-size: 16px;
}
.logo-dot {
  width: 32px; height: 32px;
  background: #F96167; border-radius: 8px;
  display: flex; align-items: center; justify-content: center;
  font-size: 16px;
}
.nav { display: flex; gap: 18px; font-size: 14px; }
.nav a { color: #CADCFC; padding: 6px 2px; border-bottom: 2px solid transparent; }
.nav a.active { color: #fff; border-bottom-color: #F96167; }
.nav a:hover { color: #fff; }
.container { max-width: 1280px; margin: 0 auto; padding: 24px; }
.breadcrumb {
  font-size: 13px; color: #94A3B8; margin-bottom: 16px;
}
.breadcrumb a { color: #1C7293; }
.breadcrumb a:hover { text-decoration: underline; }
.page-head {
  display: flex; justify-content: space-between; align-items: flex-end;
  margin-bottom: 24px; flex-wrap: wrap; gap: 16px;
}
.page-head h1 { font-size: 26px; font-weight: 700; margin-bottom: 4px; }
.page-head .sub { font-size: 14px; color: #475569; }
.pill {
  background: #fff; border: 1px solid #E2E8F0; border-radius: 8px;
  padding: 6px 12px; font-size: 13px; font-weight: 600;
}
.notice {
  background: #FEF3C7; border: 1px solid #FCD34D;
  border-radius: 10px; padding: 14px 18px;
  margin-bottom: 20px; font-size: 13px; color: #92400E;
}
.notice b { font-weight: 700; }
.kpi-row {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 14px; margin-bottom: 24px;
}
.kpi {
  background: #fff; border-radius: 10px; padding: 18px;
  border-left: 4px solid #1C7293;
}
.kpi.accent { border-left-color: #F96167; }
.kpi.green { border-left-color: #10B981; }
.kpi.amber { border-left-color: #F9B248; }
.kpi-label {
  font-size: 10px; color: #475569; text-transform: uppercase;
  letter-spacing: 1px; font-weight: 600; margin-bottom: 6px;
}
.kpi-value { font-size: 28px; font-weight: 700; line-height: 1; }
.kpi-hint { margin-top: 6px; font-size: 11px; color: #94A3B8; }
.grid { display: grid; grid-template-columns: 1.3fr 1fr; gap: 18px; margin-bottom: 20px; }
@media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
.panel {
  background: #fff; border-radius: 12px; border: 1px solid #E2E8F0;
  overflow: hidden;
}
.panel-head {
  padding: 14px 18px; border-bottom: 1px solid #E2E8F0;
  display: flex; justify-content: space-between; align-items: center;
}
.panel-head h3 { font-size: 15px; font-weight: 700; }
.panel-head .hint { font-size: 12px; color: #94A3B8; }
.panel-body { padding: 8px 0; }
.row-link {
  display: block; transition: background 0.1s;
}
.row-link:hover { background: #F4F7FB; }
.manager-row {
  display: grid; grid-template-columns: 28px 1fr auto auto;
  align-items: center; gap: 12px;
  padding: 12px 18px; border-bottom: 1px solid #F4F7FB;
}
.manager-row:last-child { border-bottom: none; }
.rank {
  width: 24px; height: 24px; border-radius: 50%;
  background: #F4F7FB; color: #475569;
  font-weight: 700; font-size: 11px;
  display: flex; align-items: center; justify-content: center;
}
.rank.gold { background: #F9B248; color: #fff; }
.rank.silver { background: #CBD5E1; color: #1A2940; }
.rank.bronze { background: #D4A574; color: #fff; }
.manager-info { display: flex; align-items: center; gap: 10px; min-width: 0; }
.m-avatar {
  width: 34px; height: 34px; border-radius: 50%;
  background: linear-gradient(135deg, #1C7293, #065A82);
  color: #fff; display: flex; align-items: center; justify-content: center;
  font-weight: 600; font-size: 12px; flex-shrink: 0;
}
.m-avatar.b { background: linear-gradient(135deg, #F96167, #F9B248); }
.m-avatar.c { background: linear-gradient(135deg, #10B981, #1C7293); }
.m-avatar.d { background: linear-gradient(135deg, #94A3B8, #475569); }
.m-avatar.e { background: linear-gradient(135deg, #F9B248, #F96167); }
.m-name { font-weight: 600; font-size: 14px; }
.m-stats { font-size: 11px; color: #94A3B8; margin-top: 2px; }
.m-stats b { color: #1A2940; font-weight: 700; }
.m-count {
  font-weight: 700; font-size: 18px; min-width: 36px; text-align: right;
}
.call-row {
  display: grid; grid-template-columns: 28px 1fr auto;
  align-items: center; gap: 12px;
  padding: 11px 18px; border-bottom: 1px solid #F4F7FB;
  font-size: 13px;
}
.call-row:last-child { border-bottom: none; }
.call-direction {
  width: 26px; height: 26px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-size: 14px; font-weight: 700; flex-shrink: 0;
}
.call-direction.in { background: #DCFCE7; color: #10B981; }
.call-direction.out { background: #DBEAFE; color: #1C7293; }
.call-info { min-width: 0; overflow: hidden; }
.call-client {
  font-weight: 600;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.call-meta {
  font-size: 11px; color: #94A3B8;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.call-time {
  font-size: 12px; color: #475569; white-space: nowrap;
}
.empty {
  padding: 36px 18px; text-align: center; color: #94A3B8; font-size: 14px;
}
.footer-note {
  text-align: center; padding: 24px; font-size: 12px; color: #94A3B8;
}
.search-box {
  width: 100%; padding: 10px 14px;
  border: 1px solid #E2E8F0; border-radius: 8px;
  font-size: 14px; background: #fff;
  margin-bottom: 16px;
}
.search-box:focus { outline: 2px solid #1C7293; outline-offset: -1px; }
.filter-row {
  display: flex; gap: 8px; flex-wrap: wrap;
  margin-bottom: 16px;
}
.filter-btn {
  padding: 8px 14px; border-radius: 20px;
  background: #fff; border: 1px solid #E2E8F0;
  font-size: 12px; cursor: pointer;
  font-weight: 600; color: #475569;
}
.filter-btn.active { background: #1C7293; color: #fff; border-color: #1C7293; }

/* Карточка звонка */
.call-card {
  background: #fff; border-radius: 12px; border: 1px solid #E2E8F0;
  overflow: hidden; margin-bottom: 18px;
}
.call-card-head {
  padding: 20px 24px;
  display: grid; grid-template-columns: 1fr auto; gap: 20px; align-items: center;
}
.call-card-head h2 { font-size: 22px; font-weight: 700; margin-bottom: 8px; }
.call-tags { display: flex; gap: 8px; flex-wrap: wrap; font-size: 12px; }
.call-tag {
  background: #F4F7FB; padding: 4px 10px; border-radius: 12px;
  color: #475569;
}
.call-tag.accent { background: #1C7293; color: #fff; font-weight: 600; }
.call-tag.warning { background: #FEE2E2; color: #F96167; font-weight: 600; }
.score-big {
  background: linear-gradient(135deg, #1C7293, #065A82);
  color: #fff; border-radius: 12px;
  padding: 14px 22px; text-align: center; min-width: 140px;
}
.score-big.placeholder { background: linear-gradient(135deg, #94A3B8, #64748B); }
.score-big .num { font-size: 38px; font-weight: 700; line-height: 1; }
.score-big .num .max { font-size: 16px; opacity: 0.7; }
.score-big .label {
  font-size: 10px; text-transform: uppercase; letter-spacing: 1.5px;
  margin-top: 4px; opacity: 0.85;
}
.detail-grid {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px; padding: 0 24px 24px;
}
.detail {
  background: #F4F7FB; border-radius: 8px; padding: 12px 14px;
}
.detail-label {
  font-size: 10px; color: #94A3B8; text-transform: uppercase;
  letter-spacing: 1px; margin-bottom: 4px; font-weight: 600;
}
.detail-value { font-size: 14px; font-weight: 600; }
.detail-value a { color: #1C7293; }
.detail-value a:hover { text-decoration: underline; }
.placeholder-panel {
  background: linear-gradient(135deg, #FEF3C7, #FFF7ED);
  border: 1px dashed #FCD34D;
  border-radius: 12px; padding: 24px;
  margin-bottom: 18px;
}
.placeholder-panel h3 {
  font-size: 16px; font-weight: 700; color: #92400E; margin-bottom: 8px;
  display: flex; align-items: center; gap: 8px;
}
.placeholder-panel p { font-size: 13px; color: #92400E; }
"""


def page_template(title: str, body: str, active_nav: str = "dashboard", generated_at: str = "") -> str:
    """Общий шаблон страницы с топбаром и навигацией."""
    nav_items = [
        ("dashboard", "index.html", "Сводка"),
        ("managers", "managers.html", "Менеджеры"),
        ("calls", "all-calls.html", "Все звонки"),
        ("triggers", "triggers.html", "Триггеры"),
    ]
    nav_html = ""
    for key, href, label in nav_items:
        active = " active" if key == active_nav else ""
        nav_html += f'<a class="{active.strip()}" href="{href}">{label}</a>'

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{esc(title)} — AI Sales Analytics</title>
  <style>{CSS}</style>
</head>
<body>
  <div class="topbar">
    <a href="index.html" class="logo">
      <div class="logo-dot">📞</div>
      <span>AI Sales Analytics</span>
    </a>
    <div class="nav">{nav_html}</div>
    <div style="font-size:12px; opacity:0.8;">{esc(generated_at[:16].replace('T', ' '))}</div>
  </div>
  <div class="container">
    {body}
    <div class="footer-note">
      Сгенерировано автоматически · Источник: Bitrix24 · Запуск: GitHub Actions
    </div>
  </div>
</body>
</html>"""


NOTICE_NO_AI = """
<div class="notice">
  <b>⏳ ИИ-анализ временно недоступен.</b>
  Сейчас отображается базовая статистика по звонкам.
  Когда будет подключён Claude API, появятся: оценки качества по 17 критериям,
  рекомендации менеджерам, триггеры «Требует внимания», транскрипты и поиск.
</div>
"""


# ============================================================
# КОМПОНЕНТЫ
# ============================================================
def render_call_row(c: Dict, show_manager: bool = True) -> str:
    """Одна строка в списке звонков (кликабельная)."""
    direction = c.get("direction", "")
    dir_icon = "↓" if direction == "incoming" else "↑"
    dir_class = "in" if direction == "incoming" else "out"
    client_name = c.get("client", {}).get("name", "Неизвестно")
    company = c.get("client", {}).get("company", "")
    manager = c.get("manager", {}).get("name", "") if show_manager else ""

    meta_parts = []
    if company:
        meta_parts.append(esc(company))
    if manager:
        meta_parts.append(f"менеджер: {esc(manager)}")
    meta = " · ".join(meta_parts)

    return f"""
    <a href="calls/{esc(c['activity_id'])}.html" class="row-link">
      <div class="call-row">
        <div class="call-direction {dir_class}">{dir_icon}</div>
        <div class="call-info">
          <div class="call-client">{esc(client_name)}</div>
          <div class="call-meta">{meta}</div>
        </div>
        <div class="call-time">{format_time(c.get('created', ''))}</div>
      </div>
    </a>
    """


# ============================================================
# СТРАНИЦЫ
# ============================================================
def render_index(calls: List[Dict], stats: Dict, generated_at: str) -> str:
    """Главная — сводка."""
    today = format_date(generated_at)

    kpi_html = f"""
    <div class="kpi-row">
      <div class="kpi">
        <div class="kpi-label">Всего звонков</div>
        <div class="kpi-value">{stats['total']}</div>
        <div class="kpi-hint">за последние сутки</div>
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
    </div>
    """

    # Топ менеджеров
    managers_html = ""
    for i, m in enumerate(stats["managers"][:10], 1):
        rank_class = ["", "gold", "silver", "bronze"][min(i, 3)] if i <= 3 else ""
        avatar_class = manager_color_class(m["id"])
        managers_html += f"""
        <a href="managers/{m['id']}.html" class="row-link">
          <div class="manager-row">
            <div class="rank {rank_class}">{i}</div>
            <div class="manager-info">
              <div class="m-avatar {avatar_class}">{esc(manager_initials(m['name']))}</div>
              <div>
                <div class="m-name">{esc(m['name'])}</div>
                <div class="m-stats">входящих: <b>{m['in']}</b> · исходящих: <b>{m['out']}</b></div>
              </div>
            </div>
            <div class="m-count">{m['count']}</div>
          </div>
        </a>
        """
    if not managers_html:
        managers_html = '<div class="empty">Сегодня пока не было звонков</div>'

    # Последние 10 звонков
    recent = sorted(calls, key=lambda x: x.get("created", ""), reverse=True)[:10]
    calls_html = "".join(render_call_row(c) for c in recent) or '<div class="empty">Звонков пока нет</div>'

    body = f"""
    <div class="page-head">
      <div>
        <h1>Сводка звонков</h1>
        <div class="sub">{stats['total']} звонков · {stats['managers_count']} менеджеров</div>
      </div>
      <div class="pill">📅 {today}</div>
    </div>
    {NOTICE_NO_AI}
    {kpi_html}
    <div class="grid">
      <div class="panel">
        <div class="panel-head">
          <h3>Рейтинг менеджеров</h3>
          <a href="managers.html" class="hint" style="color:#1C7293;">все →</a>
        </div>
        {managers_html}
      </div>
      <div class="panel">
        <div class="panel-head">
          <h3>Последние звонки</h3>
          <a href="all-calls.html" class="hint" style="color:#1C7293;">все →</a>
        </div>
        {calls_html}
      </div>
    </div>
    """
    return page_template("Сводка", body, "dashboard", generated_at)


def render_managers_list(stats: Dict, generated_at: str) -> str:
    """Страница со списком всех менеджеров."""
    rows_html = ""
    for i, m in enumerate(stats["managers"], 1):
        rank_class = ["", "gold", "silver", "bronze"][min(i, 3)] if i <= 3 else ""
        avatar_class = manager_color_class(m["id"])
        rows_html += f"""
        <a href="managers/{m['id']}.html" class="row-link">
          <div class="manager-row">
            <div class="rank {rank_class}">{i}</div>
            <div class="manager-info">
              <div class="m-avatar {avatar_class}">{esc(manager_initials(m['name']))}</div>
              <div>
                <div class="m-name">{esc(m['name'])}</div>
                <div class="m-stats">входящих: <b>{m['in']}</b> · исходящих: <b>{m['out']}</b></div>
              </div>
            </div>
            <div class="m-count">{m['count']}</div>
          </div>
        </a>
        """
    if not rows_html:
        rows_html = '<div class="empty">Менеджеров нет</div>'

    body = f"""
    <div class="breadcrumb"><a href="index.html">Главная</a> › Менеджеры</div>
    <div class="page-head">
      <div>
        <h1>Менеджеры</h1>
        <div class="sub">{stats['managers_count']} человек · {stats['total']} звонков</div>
      </div>
    </div>
    <div class="panel">
      <div class="panel-head">
        <h3>Все менеджеры</h3>
        <span class="hint">сортировка по количеству</span>
      </div>
      {rows_html}
    </div>
    """
    return page_template("Менеджеры", body, "managers", generated_at)


def render_manager_page(manager: Dict, generated_at: str) -> str:
    """Страница одного менеджера."""
    calls = sorted(manager["calls"], key=lambda x: x.get("created", ""), reverse=True)
    calls_html = "".join(render_call_row(c, show_manager=False) for c in calls)
    if not calls_html:
        calls_html = '<div class="empty">Звонков нет</div>'

    avatar_class = manager_color_class(manager["id"])

    body = f"""
    <div class="breadcrumb">
      <a href="../index.html">Главная</a> ›
      <a href="../managers.html">Менеджеры</a> ›
      {esc(manager['name'])}
    </div>
    <div class="page-head">
      <div style="display:flex; align-items:center; gap:16px;">
        <div class="m-avatar {avatar_class}" style="width:56px; height:56px; font-size:18px;">
          {esc(manager_initials(manager['name']))}
        </div>
        <div>
          <h1>{esc(manager['name'])}</h1>
          <div class="sub">{manager['count']} звонков · ID {manager['id']}</div>
        </div>
      </div>
    </div>
    <div class="kpi-row">
      <div class="kpi">
        <div class="kpi-label">Всего звонков</div>
        <div class="kpi-value">{manager['count']}</div>
      </div>
      <div class="kpi green">
        <div class="kpi-label">Входящих</div>
        <div class="kpi-value">{manager['in']}</div>
      </div>
      <div class="kpi accent">
        <div class="kpi-label">Исходящих</div>
        <div class="kpi-value">{manager['out']}</div>
      </div>
    </div>
    <div class="placeholder-panel">
      <h3>💡 Здесь появится после Claude API</h3>
      <p>Средняя оценка по 17 критериям, главные ошибки менеджера, рекомендации по обучению, сравнение с лучшими.</p>
    </div>
    <div class="panel">
      <div class="panel-head">
        <h3>Все звонки</h3>
        <span class="hint">{len(calls)} штук</span>
      </div>
      {calls_html}
    </div>
    """
    return page_template(manager["name"], body, "managers", generated_at)


def render_call_page(call: Dict, all_calls: List[Dict], generated_at: str) -> str:
    """Карточка одного звонка."""
    direction = call.get("direction", "")
    dir_label = direction_label(direction)
    client = call.get("client", {})
    manager = call.get("manager", {})
    audio = call.get("audio") or {}

    bitrix_link = crm_link(call)
    crm_link_html = ""
    if bitrix_link:
        owner_type_label = {
            "deal": "сделка", "lead": "лид",
            "contact": "контакт", "company": "компания"
        }.get(call.get("crm", {}).get("owner_type", ""), "запись")
        crm_link_html = f'<a href="{esc(bitrix_link)}" target="_blank">{owner_type_label} №{esc(call.get("crm", {}).get("owner_id", ""))}</a>'

    tags_html = f'<span class="call-tag accent">{esc(dir_label)}</span>'
    if call.get("crm", {}).get("owner_type") != "unknown":
        tags_html += f'<span class="call-tag">CRM: {esc(call.get("crm", {}).get("owner_type", ""))}</span>'

    avatar_class = manager_color_class(manager.get("id", 0))

    body = f"""
    <div class="breadcrumb">
      <a href="../index.html">Главная</a> ›
      <a href="../all-calls.html">Все звонки</a> ›
      Звонок №{esc(call['activity_id'])}
    </div>
    <div class="call-card">
      <div class="call-card-head">
        <div>
          <h2>{esc(client.get('name', 'Неизвестный клиент'))}</h2>
          <div class="call-tags">{tags_html}</div>
        </div>
        <div class="score-big placeholder">
          <div class="num">—<span class="max">/10</span></div>
          <div class="label">оценка скоро</div>
        </div>
      </div>
      <div class="detail-grid">
        <div class="detail">
          <div class="detail-label">Менеджер</div>
          <div class="detail-value">
            <a href="../managers/{manager.get('id', 0)}.html">{esc(manager.get('name', ''))}</a>
          </div>
        </div>
        <div class="detail">
          <div class="detail-label">Компания клиента</div>
          <div class="detail-value">{esc(client.get('company') or '—')}</div>
        </div>
        <div class="detail">
          <div class="detail-label">Телефон</div>
          <div class="detail-value">{esc(client.get('phone_masked') or '—')}</div>
        </div>
        <div class="detail">
          <div class="detail-label">Время звонка</div>
          <div class="detail-value">{format_datetime(call.get('created', ''))}</div>
        </div>
        <div class="detail">
          <div class="detail-label">Направление</div>
          <div class="detail-value">{esc(dir_label)}</div>
        </div>
        <div class="detail">
          <div class="detail-label">Связано в CRM</div>
          <div class="detail-value">{crm_link_html or '—'}</div>
        </div>
      </div>
    </div>

    <div class="placeholder-panel">
      <h3>📝 Транскрипт звонка</h3>
      <p>Появится после подключения Claude API. Полный текст разговора с разделением на спикеров и таймкодами.</p>
    </div>

    <div class="placeholder-panel">
      <h3>📊 Оценка по 17 критериям</h3>
      <p>Появится после подключения Claude API. Детальная оценка с цветовой подсветкой, рекомендациями менеджеру и цитатами из разговора.</p>
    </div>

    <div class="placeholder-panel">
      <h3>⚠️ Триггеры «Требует внимания»</h3>
      <p>Появится после подключения Claude API. Не отработанные возражения, упущенные допродажи, незафиксированные следующие шаги.</p>
    </div>
    """
    return page_template(f"Звонок №{call['activity_id']}", body, "calls", generated_at)


def render_all_calls(calls: List[Dict], generated_at: str) -> str:
    """Страница со всеми звонками + клиентский фильтр."""
    sorted_calls = sorted(calls, key=lambda x: x.get("created", ""), reverse=True)
    rows = "".join(render_call_row(c) for c in sorted_calls)
    if not rows:
        rows = '<div class="empty">Звонков нет</div>'

    body = f"""
    <div class="breadcrumb"><a href="index.html">Главная</a> › Все звонки</div>
    <div class="page-head">
      <div>
        <h1>Все звонки</h1>
        <div class="sub">{len(calls)} штук за последние сутки</div>
      </div>
    </div>
    <input type="text" class="search-box" id="searchInput"
           placeholder="🔍 Поиск по клиенту или компании..." oninput="filterCalls()">
    <div class="filter-row">
      <button class="filter-btn active" onclick="setFilter('all', this)">Все</button>
      <button class="filter-btn" onclick="setFilter('incoming', this)">Входящие</button>
      <button class="filter-btn" onclick="setFilter('outgoing', this)">Исходящие</button>
    </div>
    <div class="panel" id="callsPanel">
      <div class="panel-head">
        <h3>Список звонков</h3>
        <span class="hint" id="visibleCount">{len(calls)} видно</span>
      </div>
      <div id="callsList">{rows}</div>
    </div>
    <script>
      var currentFilter = 'all';
      function setFilter(filter, btn) {{
        currentFilter = filter;
        document.querySelectorAll('.filter-btn').forEach(function(b){{ b.classList.remove('active'); }});
        btn.classList.add('active');
        filterCalls();
      }}
      function filterCalls() {{
        var q = document.getElementById('searchInput').value.toLowerCase();
        var rows = document.querySelectorAll('#callsList .row-link');
        var visible = 0;
        rows.forEach(function(row) {{
          var text = row.textContent.toLowerCase();
          var dirClass = row.querySelector('.call-direction').classList;
          var matchesText = !q || text.indexOf(q) !== -1;
          var matchesFilter = currentFilter === 'all' ||
            (currentFilter === 'incoming' && dirClass.contains('in')) ||
            (currentFilter === 'outgoing' && dirClass.contains('out'));
          var show = matchesText && matchesFilter;
          row.style.display = show ? '' : 'none';
          if (show) visible++;
        }});
        document.getElementById('visibleCount').textContent = visible + ' видно';
      }}
    </script>
    """
    return page_template("Все звонки", body, "calls", generated_at)


def render_triggers_page(generated_at: str) -> str:
    """Заглушка под триггеры."""
    body = """
    <div class="breadcrumb"><a href="index.html">Главная</a> › Триггеры</div>
    <div class="page-head">
      <div>
        <h1>Триггеры «Требует внимания»</h1>
        <div class="sub">Звонки, где менеджер допустил критические ошибки</div>
      </div>
    </div>
    <div class="placeholder-panel">
      <h3>⏳ Раздел появится после Claude API</h3>
      <p>Здесь будут отображаться звонки с критическими ошибками:</p>
      <ul style="margin-top:10px; padding-left:20px; font-size:13px; color:#92400E;">
        <li>Не отработано возражение «дорого»</li>
        <li>Не зафиксирован следующий шаг</li>
        <li>Упущена возможность допродажи</li>
        <li>Не выявлены потребности клиента</li>
        <li>Не предложен следующий этап воронки</li>
        <li>...и ещё 9 правил из ТЗ</li>
      </ul>
    </div>
    """
    return page_template("Триггеры", body, "triggers", generated_at)


# ============================================================
# ГЛАВНАЯ ФУНКЦИЯ
# ============================================================
def generate(calls_json_path: str = "calls_data.json", output_dir: str = "docs"):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    json_path = Path(calls_json_path)
    out_dir = Path(output_dir)
    out_dir.mkdir(exist_ok=True)
    (out_dir / "managers").mkdir(exist_ok=True)
    (out_dir / "calls").mkdir(exist_ok=True)

    if not json_path.exists():
        logger.warning(f"Файл {json_path} не найден")
        calls = []
    else:
        calls = json.loads(json_path.read_text(encoding="utf-8"))

    logger.info(f"Загружено звонков: {len(calls)}")
    stats = compute_stats(calls)
    generated_at = datetime.now().isoformat()

    # Главная
    (out_dir / "index.html").write_text(
        render_index(calls, stats, generated_at), encoding="utf-8")
    logger.info("Создан: index.html")

    # Все менеджеры (список)
    (out_dir / "managers.html").write_text(
        render_managers_list(stats, generated_at), encoding="utf-8")
    logger.info("Создан: managers.html")

    # Страница каждого менеджера
    for m in stats["managers"]:
        page_path = out_dir / "managers" / f"{m['id']}.html"
        # Поправим относительные ссылки для подпапки
        html_content = render_manager_page(m, generated_at)
        # Исправляем относительные ссылки топбара (они на 1 уровень выше)
        html_content = html_content.replace('href="index.html"', 'href="../index.html"', 1)
        html_content = html_content.replace('href="managers.html"', 'href="../managers.html"')
        html_content = html_content.replace('href="all-calls.html"', 'href="../all-calls.html"')
        html_content = html_content.replace('href="triggers.html"', 'href="../triggers.html"')
        page_path.write_text(html_content, encoding="utf-8")
    logger.info(f"Создано страниц менеджеров: {len(stats['managers'])}")

    # Все звонки
    (out_dir / "all-calls.html").write_text(
        render_all_calls(calls, generated_at), encoding="utf-8")
    logger.info("Создан: all-calls.html")

    # Карточка каждого звонка
    for call in calls:
        page_path = out_dir / "calls" / f"{call['activity_id']}.html"
        html_content = render_call_page(call, calls, generated_at)
        html_content = html_content.replace('href="index.html"', 'href="../index.html"', 1)
        html_content = html_content.replace('href="managers.html"', 'href="../managers.html"')
        html_content = html_content.replace('href="all-calls.html"', 'href="../all-calls.html"')
        html_content = html_content.replace('href="triggers.html"', 'href="../triggers.html"')
        page_path.write_text(html_content, encoding="utf-8")
    logger.info(f"Создано карточек звонков: {len(calls)}")

    # Триггеры (заглушка)
    (out_dir / "triggers.html").write_text(
        render_triggers_page(generated_at), encoding="utf-8")
    logger.info("Создан: triggers.html")

    # .nojekyll — отключает Jekyll на GitHub Pages
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")

    print(f"\n✅ Сайт сгенерирован в {out_dir}/")
    print(f"   Главная: index.html")
    print(f"   Список менеджеров: managers.html ({stats['managers_count']} человек)")
    print(f"   Страниц менеджеров: {len(stats['managers'])}")
    print(f"   Карточек звонков: {len(calls)}")


if __name__ == "__main__":
    generate()
