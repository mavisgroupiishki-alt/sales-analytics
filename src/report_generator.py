"""
Генератор HTML-отчёта по звонкам Mavis Group.
Создаёт многостраничный сайт: главная, менеджеры, карточки звонков.
Корпоративный дизайн в гамме логотипа Mavis Group.
"""

import json
import html
import logging
from pathlib import Path
from datetime import datetime
from collections import defaultdict
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

COMPANY_NAME = "Mavis Group"


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


def manager_avatar_html(manager: Dict, base_path: str = "") -> str:
    """Возвращает содержимое аватарки: <img> если есть фото, иначе инициалы."""
    avatar_file = manager.get("avatar_file") or ""
    name = manager.get("name", "")
    initials = manager_initials(name)
    if avatar_file:
        src = f"{base_path}avatars/{avatar_file}"
        return f'<img src="{esc(src)}" alt="{esc(initials)}">'
    return esc(initials)


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

    by_manager = defaultdict(lambda: {"count": 0, "in": 0, "out": 0, "name": "", "id": None, "calls": [], "avatar_file": ""})
    for c in calls:
        m = c.get("manager", {})
        mid = m.get("id") or 0
        by_manager[mid]["id"] = mid
        by_manager[mid]["name"] = m.get("name", f"User {mid}")
        by_manager[mid]["avatar_file"] = m.get("avatar_file", "")
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
# CSS — корпоративный стиль Mavis Group
# ============================================================
CSS = """
:root {
  --brand-dark: #3D2E1F;
  --brand-medium: #6B5544;
  --brand-light: #A0826D;
  --brand-cream: #FAF7F2;
  --brand-paper: #F2EBE0;
  --brand-gold: #C9A961;
  --brand-olive: #7C8B6F;
  --brand-terracotta: #B86B4F;
  --text-primary: #2A1F15;
  --text-secondary: #6B5544;
  --text-muted: #A39686;
  --border: #E8DFD0;
  --border-soft: #F0E8DA;
}

* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: "Georgia", "Garamond", -apple-system, "Segoe UI", system-ui, serif;
  background: var(--brand-cream);
  color: var(--text-primary);
  -webkit-font-smoothing: antialiased;
  line-height: 1.55;
}
a { color: inherit; text-decoration: none; }

/* ============================================
   ТОПБАР
   ============================================ */
.topbar {
  background: var(--brand-dark);
  color: #fff;
  padding: 10px 24px;
  display: flex; align-items: center; gap: 20px;
  position: sticky; top: 0; z-index: 10;
  box-shadow: 0 2px 12px rgba(61,46,31,0.25);
  border-bottom: 3px solid var(--brand-gold);
  overflow: hidden;
}
.logo {
  display: flex; align-items: center; gap: 14px;
  flex-shrink: 0;
}
.logo-img {
  height: 48px; width: auto;
  display: block;
  flex-shrink: 0;
}
.logo-text {
  display: flex; flex-direction: column;
  font-family: "Georgia", "Garamond", serif;
  line-height: 1.1;
}
.logo-text .brand {
  font-size: 17px; font-weight: 700;
  letter-spacing: 1px;
  color: #fff;
}
.logo-text .sub {
  font-size: 10px;
  letter-spacing: 3px;
  color: var(--brand-gold);
  text-transform: uppercase;
  margin-top: 2px;
}
.logo .short-name { display: none; }

.nav {
  display: flex; gap: 22px;
  font-size: 13px;
  flex: 1;
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
  scrollbar-width: none;
  font-family: -apple-system, "Segoe UI", system-ui, sans-serif;
}
.nav::-webkit-scrollbar { display: none; }
.nav a {
  color: rgba(255,255,255,0.75); padding: 4px 2px;
  border-bottom: 2px solid transparent;
  white-space: nowrap; flex-shrink: 0;
  transition: color 0.15s;
  letter-spacing: 0.3px;
}
.nav a.active {
  color: #fff;
  border-bottom-color: var(--brand-gold);
}
.nav a:hover { color: #fff; }
.timestamp {
  font-size: 11px;
  color: rgba(255,255,255,0.5);
  white-space: nowrap; flex-shrink: 0;
  font-family: -apple-system, "Segoe UI", system-ui, sans-serif;
  letter-spacing: 0.5px;
}

/* ============================================
   КОНТЕЙНЕР
   ============================================ */
.container { max-width: 1280px; margin: 0 auto; padding: 24px 24px 40px; }

.breadcrumb {
  font-size: 12px;
  color: var(--text-muted);
  margin-bottom: 16px;
  font-family: -apple-system, "Segoe UI", system-ui, sans-serif;
  letter-spacing: 0.3px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.breadcrumb a {
  color: var(--brand-medium);
  border-bottom: 1px dotted var(--border);
}
.breadcrumb a:hover { color: var(--brand-dark); border-bottom-style: solid; }

.page-head {
  display: flex; justify-content: space-between; align-items: flex-end;
  margin-bottom: 24px; flex-wrap: wrap; gap: 14px;
  padding-bottom: 18px;
  border-bottom: 1px solid var(--border);
}
.page-head h1 {
  font-family: "Georgia", "Garamond", serif;
  font-size: 28px;
  font-weight: 400;
  margin-bottom: 6px;
  color: var(--brand-dark);
  letter-spacing: 0.3px;
}
.page-head .sub {
  font-size: 13px;
  color: var(--text-secondary);
  font-family: -apple-system, "Segoe UI", system-ui, sans-serif;
}
.pill {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 8px 14px;
  font-size: 13px;
  font-weight: 600;
  color: var(--brand-dark);
  font-family: -apple-system, "Segoe UI", system-ui, sans-serif;
  box-shadow: 0 1px 0 rgba(61,46,31,0.04);
}

/* ============================================
   УВЕДОМЛЕНИЕ
   ============================================ */
.notice {
  background: linear-gradient(135deg, var(--brand-paper), #F8F1E3);
  border: 1px solid var(--brand-gold);
  border-left: 4px solid var(--brand-gold);
  border-radius: 4px;
  padding: 14px 18px;
  margin-bottom: 22px;
  font-size: 13px;
  color: var(--brand-dark);
  line-height: 1.6;
  font-family: -apple-system, "Segoe UI", system-ui, sans-serif;
}
.notice b { font-weight: 700; }

/* ============================================
   KPI
   ============================================ */
.kpi-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: 12px;
  margin-bottom: 24px;
}
.kpi {
  background: #fff;
  border-radius: 6px;
  padding: 16px 18px;
  border: 1px solid var(--border);
  border-top: 3px solid var(--brand-medium);
  box-shadow: 0 1px 2px rgba(61,46,31,0.03);
}
.kpi.accent { border-top-color: var(--brand-terracotta); }
.kpi.green { border-top-color: var(--brand-olive); }
.kpi.amber { border-top-color: var(--brand-gold); }
.kpi-label {
  font-size: 10px;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 1.5px;
  font-weight: 600;
  margin-bottom: 8px;
  font-family: -apple-system, "Segoe UI", system-ui, sans-serif;
}
.kpi-value {
  font-family: "Georgia", "Garamond", serif;
  font-size: 30px;
  font-weight: 400;
  line-height: 1;
  color: var(--brand-dark);
}
.kpi-hint {
  margin-top: 6px;
  font-size: 11px;
  color: var(--text-muted);
  font-family: -apple-system, "Segoe UI", system-ui, sans-serif;
}

/* ============================================
   ГРИД
   ============================================ */
.grid {
  display: grid;
  grid-template-columns: 1.3fr 1fr;
  gap: 18px;
  margin-bottom: 22px;
}
@media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }

/* ============================================
   ПАНЕЛИ
   ============================================ */
.panel {
  background: #fff;
  border-radius: 6px;
  border: 1px solid var(--border);
  overflow: hidden;
  box-shadow: 0 1px 2px rgba(61,46,31,0.03);
}
.panel-head {
  padding: 14px 18px;
  border-bottom: 1px solid var(--border-soft);
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: linear-gradient(180deg, #FFFCF7, #fff);
}
.panel-head h3 {
  font-family: "Georgia", "Garamond", serif;
  font-size: 16px;
  font-weight: 600;
  color: var(--brand-dark);
  letter-spacing: 0.3px;
}
.panel-head .hint {
  font-size: 12px;
  color: var(--text-muted);
  font-family: -apple-system, "Segoe UI", system-ui, sans-serif;
}

/* ============================================
   СТРОКИ
   ============================================ */
.row-link {
  display: block;
  transition: background 0.15s;
  font-family: -apple-system, "Segoe UI", system-ui, sans-serif;
}
.row-link:hover { background: var(--brand-paper); }

.manager-row {
  display: grid;
  grid-template-columns: 26px 1fr auto;
  align-items: center;
  gap: 12px;
  padding: 12px 18px;
  border-bottom: 1px solid var(--border-soft);
}
.manager-row:last-child { border-bottom: none; }
.rank {
  width: 22px; height: 22px;
  border-radius: 50%;
  background: var(--brand-paper);
  color: var(--text-secondary);
  font-weight: 700;
  font-size: 11px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.rank.gold { background: var(--brand-gold); color: #fff; }
.rank.silver { background: #BFB5A8; color: #fff; }
.rank.bronze { background: var(--brand-terracotta); color: #fff; }

.manager-info { display: flex; align-items: center; gap: 12px; min-width: 0; }
.m-avatar {
  width: 38px; height: 38px;
  border-radius: 50%;
  background: linear-gradient(135deg, var(--brand-medium), var(--brand-dark));
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 600;
  font-size: 12px;
  flex-shrink: 0;
  overflow: hidden;
  border: 2px solid #fff;
  box-shadow: 0 0 0 1px var(--border);
}
.m-avatar img {
  width: 100%; height: 100%;
  border-radius: 50%;
  object-fit: cover;
  display: block;
}
.m-avatar.b { background: linear-gradient(135deg, var(--brand-terracotta), var(--brand-light)); }
.m-avatar.c { background: linear-gradient(135deg, var(--brand-olive), var(--brand-medium)); }
.m-avatar.d { background: linear-gradient(135deg, #8B7E6F, var(--brand-medium)); }
.m-avatar.e { background: linear-gradient(135deg, var(--brand-gold), var(--brand-terracotta)); }

.m-name {
  font-weight: 600;
  font-size: 14px;
  color: var(--brand-dark);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.m-stats { font-size: 11px; color: var(--text-muted); margin-top: 2px; }
.m-stats b { color: var(--brand-dark); font-weight: 700; }
.m-count {
  font-family: "Georgia", "Garamond", serif;
  font-weight: 600;
  font-size: 20px;
  min-width: 36px;
  text-align: right;
  color: var(--brand-dark);
}

/* ============================================
   СТРОКА ЗВОНКА
   ============================================ */
.call-row {
  display: grid;
  grid-template-columns: 28px 1fr auto;
  align-items: center;
  gap: 12px;
  padding: 11px 18px;
  border-bottom: 1px solid var(--border-soft);
  font-size: 13px;
}
.call-row:last-child { border-bottom: none; }
.call-direction {
  width: 26px; height: 26px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  font-weight: 700;
  flex-shrink: 0;
}
.call-direction.in {
  background: rgba(124,139,111,0.15);
  color: var(--brand-olive);
}
.call-direction.out {
  background: rgba(184,107,79,0.15);
  color: var(--brand-terracotta);
}
.call-info { min-width: 0; overflow: hidden; }
.call-client {
  font-weight: 600;
  color: var(--brand-dark);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.call-meta {
  font-size: 11px;
  color: var(--text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.call-time {
  font-size: 12px;
  color: var(--text-secondary);
  white-space: nowrap;
  font-weight: 600;
}

/* ============================================
   ПУСТЫЕ СОСТОЯНИЯ
   ============================================ */
.empty {
  padding: 36px 18px;
  text-align: center;
  color: var(--text-muted);
  font-size: 14px;
  font-style: italic;
}

/* ============================================
   ПОИСК И ФИЛЬТРЫ
   ============================================ */
.search-box {
  width: 100%;
  padding: 11px 16px;
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 14px;
  background: #fff;
  margin-bottom: 14px;
  font-family: -apple-system, "Segoe UI", system-ui, sans-serif;
  color: var(--brand-dark);
}
.search-box:focus {
  outline: 2px solid var(--brand-gold);
  outline-offset: -1px;
}
.filter-row {
  display: flex; gap: 8px; flex-wrap: wrap;
  margin-bottom: 16px;
}
.filter-btn {
  padding: 7px 14px;
  border-radius: 20px;
  background: #fff;
  border: 1px solid var(--border);
  font-size: 12px;
  cursor: pointer;
  font-weight: 600;
  color: var(--text-secondary);
  font-family: -apple-system, "Segoe UI", system-ui, sans-serif;
  transition: all 0.15s;
}
.filter-btn:hover { border-color: var(--brand-medium); }
.filter-btn.active {
  background: var(--brand-dark);
  color: #fff;
  border-color: var(--brand-dark);
}

/* ============================================
   КАРТОЧКА ЗВОНКА
   ============================================ */
.call-card {
  background: #fff;
  border-radius: 6px;
  border: 1px solid var(--border);
  overflow: hidden;
  margin-bottom: 18px;
  box-shadow: 0 1px 2px rgba(61,46,31,0.03);
}
.call-card-head {
  padding: 22px 24px;
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 18px;
  align-items: center;
  background: linear-gradient(180deg, #FFFCF7, #fff);
  border-bottom: 1px solid var(--border-soft);
}
.call-card-head h2 {
  font-family: "Georgia", "Garamond", serif;
  font-size: 22px;
  font-weight: 400;
  margin-bottom: 10px;
  color: var(--brand-dark);
}
.call-tags {
  display: flex; gap: 6px;
  flex-wrap: wrap;
  font-size: 12px;
  font-family: -apple-system, "Segoe UI", system-ui, sans-serif;
}
.call-tag {
  background: var(--brand-paper);
  padding: 4px 10px;
  border-radius: 12px;
  color: var(--brand-medium);
  border: 1px solid var(--border);
}
.call-tag.accent {
  background: var(--brand-dark);
  color: #fff;
  font-weight: 600;
  border-color: var(--brand-dark);
}
.call-tag.warning {
  background: var(--brand-terracotta);
  color: #fff;
  font-weight: 600;
  border-color: var(--brand-terracotta);
}

.score-big {
  background: linear-gradient(135deg, var(--brand-dark), var(--brand-medium));
  color: #fff;
  border-radius: 6px;
  padding: 14px 22px;
  text-align: center;
  min-width: 130px;
  border: 1px solid var(--brand-dark);
  position: relative;
}
.score-big.placeholder {
  background: linear-gradient(135deg, #BFB5A8, var(--text-muted));
  border-color: #BFB5A8;
}
.score-big .num {
  font-family: "Georgia", "Garamond", serif;
  font-size: 36px;
  font-weight: 400;
  line-height: 1;
}
.score-big .num .max { font-size: 16px; opacity: 0.7; }
.score-big .label {
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 1.5px;
  margin-top: 4px;
  opacity: 0.85;
  font-family: -apple-system, "Segoe UI", system-ui, sans-serif;
}

.detail-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
  gap: 12px;
  padding: 20px 24px 24px;
}
.detail {
  background: var(--brand-cream);
  border: 1px solid var(--border-soft);
  border-radius: 4px;
  padding: 11px 14px;
}
.detail-label {
  font-size: 10px;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 1px;
  margin-bottom: 4px;
  font-weight: 600;
  font-family: -apple-system, "Segoe UI", system-ui, sans-serif;
}
.detail-value {
  font-size: 13px;
  font-weight: 600;
  color: var(--brand-dark);
  word-wrap: break-word;
  font-family: -apple-system, "Segoe UI", system-ui, sans-serif;
}
.detail-value a { color: var(--brand-medium); border-bottom: 1px dotted var(--border); }
.detail-value a:hover { color: var(--brand-dark); border-bottom-style: solid; }

/* ============================================
   PLACEHOLDER ПАНЕЛЬ
   ============================================ */
.placeholder-panel {
  background: linear-gradient(135deg, var(--brand-paper), #F8F1E3);
  border: 1px dashed var(--brand-gold);
  border-radius: 6px;
  padding: 20px 22px;
  margin-bottom: 16px;
}
.placeholder-panel h3 {
  font-family: "Georgia", "Garamond", serif;
  font-size: 16px;
  font-weight: 600;
  color: var(--brand-dark);
  margin-bottom: 8px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.placeholder-panel p {
  font-size: 13px;
  color: var(--brand-medium);
  font-family: -apple-system, "Segoe UI", system-ui, sans-serif;
  line-height: 1.6;
}

/* ============================================
   ФУТЕР
   ============================================ */
.footer-note {
  text-align: center;
  padding: 36px 16px 16px;
  font-size: 12px;
  color: var(--text-muted);
  font-family: -apple-system, "Segoe UI", system-ui, sans-serif;
  border-top: 1px solid var(--border);
  margin-top: 40px;
}
.footer-brand {
  font-family: "Georgia", "Garamond", serif;
  font-size: 14px;
  font-weight: 600;
  color: var(--brand-dark);
  letter-spacing: 1.5px;
  margin-bottom: 6px;
}
.footer-divider {
  display: inline-block;
  width: 30px;
  height: 1px;
  background: var(--brand-gold);
  margin: 8px auto;
  vertical-align: middle;
}

/* ============================================
   МОБИЛЬНАЯ АДАПТАЦИЯ (≤ 640px)
   ============================================ */
@media (max-width: 640px) {
  .topbar { padding: 10px 14px; gap: 12px; }
  .logo-img { height: 40px; }
  .logo-text .brand { font-size: 14px; }
  .logo-text .sub { font-size: 9px; letter-spacing: 2px; }
  .timestamp { display: none; }
  .nav { gap: 14px; font-size: 13px; }

  .container { padding: 16px 14px 24px; }
  .page-head { padding-bottom: 14px; margin-bottom: 18px; }
  .page-head h1 { font-size: 22px; }
  .page-head .sub { font-size: 12px; }

  .kpi-row { grid-template-columns: 1fr 1fr; gap: 8px; }
  .kpi { padding: 13px 14px; }
  .kpi-value { font-size: 24px; }
  .kpi-label { font-size: 9px; }

  .panel-head { padding: 12px 14px; }
  .panel-head h3 { font-size: 15px; }
  .manager-row, .call-row { padding: 11px 14px; }
  .m-name { font-size: 13px; }
  .m-count { font-size: 18px; }

  .call-card-head {
    grid-template-columns: 1fr;
    gap: 14px;
    padding: 18px;
  }
  .call-card-head h2 { font-size: 19px; }
  .score-big {
    justify-self: start;
    min-width: auto;
    padding: 12px 18px;
  }
  .score-big .num { font-size: 28px; }
  .detail-grid {
    grid-template-columns: 1fr;
    padding: 16px 18px 18px;
    gap: 10px;
  }
}

@media (max-width: 380px) {
  .kpi-row { grid-template-columns: 1fr; }
  .nav { font-size: 12px; gap: 12px; }
  .logo-text .brand { font-size: 13px; }
}
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

    year = datetime.now().year

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{esc(title)} — {esc(COMPANY_NAME)} Sales Analytics</title>
  <style>{CSS}</style>
</head>
<body>
  <div class="topbar">
    <a href="index.html" class="logo">
      <img class="logo-img" src="logo.png" alt="{esc(COMPANY_NAME)}">
      <div class="logo-text">
        <span class="brand">{esc(COMPANY_NAME)}</span>
        <span class="sub">Sales Analytics</span>
      </div>
    </a>
    <div class="nav">{nav_html}</div>
    <div class="timestamp">обновлено {esc(generated_at[:16].replace('T', ' '))}</div>
  </div>
  <div class="container">
    {body}
  </div>
  <div class="footer-note">
    <div class="footer-brand">{esc(COMPANY_NAME).upper()}</div>
    <div class="footer-divider"></div>
    <div>Sales Analytics · Автоматический анализ звонков отдела продаж</div>
    <div style="margin-top:6px; font-size:11px;">
      Источник данных: Bitrix24 · Запуск: GitHub Actions · © {year}
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

    managers_html = ""
    for i, m in enumerate(stats["managers"][:10], 1):
        rank_class = ["", "gold", "silver", "bronze"][min(i, 3)] if i <= 3 else ""
        avatar_class = manager_color_class(m["id"])
        managers_html += f"""
        <a href="managers/{m['id']}.html" class="row-link">
          <div class="manager-row">
            <div class="rank {rank_class}">{i}</div>
            <div class="manager-info">
              <div class="m-avatar {avatar_class}">{manager_avatar_html(m)}</div>
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

    recent = sorted(calls, key=lambda x: x.get("created", ""), reverse=True)[:10]
    calls_html = "".join(render_call_row(c) for c in recent) or '<div class="empty">Звонков пока нет</div>'

    body = f"""
    <div class="page-head">
      <div>
        <h1>Сводка звонков</h1>
        <div class="sub">{COMPANY_NAME} · {stats['total']} звонков · {stats['managers_count']} менеджеров</div>
      </div>
      <div class="pill">📅 {today}</div>
    </div>
    {NOTICE_NO_AI}
    {kpi_html}
    <div class="grid">
      <div class="panel">
        <div class="panel-head">
          <h3>Рейтинг менеджеров</h3>
          <a href="managers.html" class="hint" style="color:var(--brand-medium);">все →</a>
        </div>
        {managers_html}
      </div>
      <div class="panel">
        <div class="panel-head">
          <h3>Последние звонки</h3>
          <a href="all-calls.html" class="hint" style="color:var(--brand-medium);">все →</a>
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
              <div class="m-avatar {avatar_class}">{manager_avatar_html(m)}</div>
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
        <h1>Менеджеры отдела продаж</h1>
        <div class="sub">{COMPANY_NAME} · {stats['managers_count']} человек · {stats['total']} звонков</div>
      </div>
    </div>
    <div class="panel">
      <div class="panel-head">
        <h3>Все менеджеры</h3>
        <span class="hint">сортировка по количеству звонков</span>
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
      <div style="display:flex; align-items:center; gap:18px;">
        <div class="m-avatar {avatar_class}" style="width:64px; height:64px; font-size:20px;">
          {manager_avatar_html(manager, base_path="../")}
        </div>
        <div>
          <h1>{esc(manager['name'])}</h1>
          <div class="sub">{COMPANY_NAME} · {manager['count']} звонков · ID {manager['id']}</div>
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
      <p>Средняя оценка по 17 критериям, главные ошибки менеджера, рекомендации по обучению, сравнение с лучшими в отделе.</p>
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
          <div class="detail-label">Менеджер {esc(COMPANY_NAME)}</div>
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
        <div class="sub">{COMPANY_NAME} · {len(calls)} штук за последние сутки</div>
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
    body = f"""
    <div class="breadcrumb"><a href="index.html">Главная</a> › Триггеры</div>
    <div class="page-head">
      <div>
        <h1>Триггеры «Требует внимания»</h1>
        <div class="sub">{COMPANY_NAME} · звонки с критическими ошибками</div>
      </div>
    </div>
    <div class="placeholder-panel">
      <h3>⏳ Раздел появится после Claude API</h3>
      <p>Здесь будут отображаться звонки с критическими ошибками:</p>
      <ul style="margin-top:10px; padding-left:20px; font-size:13px; color:var(--brand-medium);">
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
        html_content = render_manager_page(m, generated_at)
        html_content = html_content.replace('href="index.html"', 'href="../index.html"', 1)
        html_content = html_content.replace('href="managers.html"', 'href="../managers.html"')
        html_content = html_content.replace('href="all-calls.html"', 'href="../all-calls.html"')
        html_content = html_content.replace('href="triggers.html"', 'href="../triggers.html"')
        html_content = html_content.replace('src="logo.png"', 'src="../logo.png"')
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
        html_content = html_content.replace('src="logo.png"', 'src="../logo.png"')
        page_path.write_text(html_content, encoding="utf-8")
    logger.info(f"Создано карточек звонков: {len(calls)}")

    # Триггеры (заглушка)
    (out_dir / "triggers.html").write_text(
        render_triggers_page(generated_at), encoding="utf-8")
    logger.info("Создан: triggers.html")

    # .nojekyll
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")

    print(f"\n✅ Сайт сгенерирован в {out_dir}/")
    print(f"   Главная: index.html")
    print(f"   Список менеджеров: managers.html ({stats['managers_count']} человек)")
    print(f"   Страниц менеджеров: {len(stats['managers'])}")
    print(f"   Карточек звонков: {len(calls)}")


if __name__ == "__main__":
    generate()
