"""
Генератор HTML-отчёта по звонкам Mavis Group.
Реализует требования ТЗ:
- 17 критериев с весами
- Двухосевая классификация
- Сильные/слабые стороны, основная проблема
- Низкие баллы с цитатами и рекомендациями
- 20 триггеров
- Дата следующего контакта
- Сопоставление со скриптами по этапам
- Сводный отчёт РОПа + дневной отчёт менеджера
- Ручная правка через manual_corrections.json
"""

import json
import html
import logging
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict, Counter
from typing import List, Dict, Any, Tuple, Optional

logger = logging.getLogger(__name__)
COMPANY_NAME = "Mavis Group"

# ============================================================
# УТИЛИТЫ
# ============================================================

def esc(text: Any) -> str:
    if text is None:
        return ""
    return html.escape(str(text))


def format_time(iso_str: str) -> str:
    if not iso_str:
        return "—"
    try:
        return datetime.fromisoformat(iso_str).strftime("%H:%M")
    except Exception:
        return iso_str[:5]


def format_datetime(iso_str: str) -> str:
    if not iso_str:
        return "—"
    try:
        return datetime.fromisoformat(iso_str).strftime("%d.%m.%Y %H:%M")
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


def format_duration(seconds) -> str:
    if not seconds:
        return "—"
    try:
        s = int(seconds)
    except (TypeError, ValueError):
        return "—"
    if s < 60:
        return f"{s} сек"
    m = s // 60
    rem = s % 60
    if rem == 0:
        return f"{m} мин"
    return f"{m} мин {rem} сек"


def manager_initials(name: str) -> str:
    if not name:
        return "??"
    parts = [p for p in name.split() if p]
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    return name[:2].upper()


def manager_avatar_html(manager: Dict, base_path: str = "") -> str:
    avatar_file = manager.get("avatar_file") or ""
    name = manager.get("name", "")
    initials = manager_initials(name)
    if avatar_file:
        return f'<img src="{esc(base_path)}avatars/{esc(avatar_file)}" alt="{esc(initials)}">'
    return esc(initials)


def manager_color_class(manager_id: int) -> str:
    classes = ["", "b", "c", "d", "e"]
    return classes[manager_id % len(classes)]


def direction_label(direction: str) -> str:
    return {"incoming": "Входящий", "outgoing": "Исходящий"}.get(direction, "Неизвестно")


def crm_link(call: Dict, base_url: str = "https://mavisgroup.bitrix24.by") -> str:
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


def score_color(score) -> str:
    try:
        s = float(score)
    except (TypeError, ValueError):
        return "mid"
    if s >= 7.5:
        return "high"
    if s >= 5.0:
        return "mid"
    return "low"


# ============================================================
# РУЧНАЯ ПРАВКА ОЦЕНОК
# ============================================================

def apply_manual_corrections(analyses: Dict, corrections: Dict) -> Dict:
    """Применяет ручные правки к анализам по activity_id."""
    if not corrections:
        return analyses
    for activity_id, correction in corrections.items():
        if activity_id in analyses:
            ad = analyses[activity_id].get("analysis") or {}
            if "overall_score" in correction:
                ad["overall_score"] = correction["overall_score"]
                ad["score_corrected"] = True
                ad["original_score"] = ad.get("original_score", ad.get("overall_score"))
            if "comment" in correction:
                ad["correction_comment"] = correction["comment"]
            if "is_critical" in correction:
                ad["is_critical"] = correction["is_critical"]
    return analyses


# ============================================================
# СТАТИСТИКА
# ============================================================

def compute_stats(calls: List[Dict], analyses: Dict) -> Dict[str, Any]:
    total = len(calls)
    incoming = sum(1 for c in calls if c.get("direction") == "incoming")
    outgoing = sum(1 for c in calls if c.get("direction") == "outgoing")
    critical = sum(
        1 for a in analyses.values()
        if (a.get("analysis") or {}).get("is_critical")
    )

    by_manager = defaultdict(lambda: {
        "count": 0, "in": 0, "out": 0, "name": "", "id": None,
        "calls": [], "avatar_file": "",
        "scores": [], "critical": 0,
    })
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
        a = analyses.get(c["activity_id"])
        if a:
            sc = (a.get("analysis") or {}).get("overall_score")
            if sc is not None:
                try:
                    by_manager[mid]["scores"].append(float(sc))
                except (TypeError, ValueError):
                    pass
            if (a.get("analysis") or {}).get("is_critical"):
                by_manager[mid]["critical"] += 1

    for m in by_manager.values():
        if m["scores"]:
            m["avg_score"] = round(sum(m["scores"]) / len(m["scores"]), 1)
            m["analyzed"] = len(m["scores"])
        else:
            m["avg_score"] = None
            m["analyzed"] = 0

    managers_by_quality = sorted(
        by_manager.values(),
        key=lambda x: (-(x["avg_score"] or 0), -x["count"])
    )
    return {
        "total": total, "incoming": incoming, "outgoing": outgoing,
        "managers_count": len(by_manager),
        "managers": managers_by_quality,
        "critical_count": critical,
    }


def compute_manager_daily_report(manager: Dict, analyses: Dict) -> Dict:
    """Дневной отчёт по менеджеру: лучший/худший звонок, слабые критерии, частые триггеры."""
    manager_analyses = []
    for c in manager["calls"]:
        a = analyses.get(c["activity_id"])
        if a:
            manager_analyses.append((c, a))

    if not manager_analyses:
        return {"has_data": False}

    # Лучший и худший звонок
    sorted_by_score = sorted(
        manager_analyses,
        key=lambda x: (x[1].get("analysis") or {}).get("overall_score") or 0
    )
    worst = sorted_by_score[0] if sorted_by_score else None
    best = sorted_by_score[-1] if sorted_by_score else None

    # Слабые критерии (средняя оценка по критериям)
    criterion_scores = defaultdict(list)
    for _, a in manager_analyses:
        scores = (a.get("analysis") or {}).get("scores", {}) or {}
        for crit, val in scores.items():
            try:
                criterion_scores[crit].append(float(val))
            except (TypeError, ValueError):
                pass
    weak_criteria = []
    for crit, vals in criterion_scores.items():
        avg = sum(vals) / len(vals)
        if avg < 6.0:
            weak_criteria.append((crit, round(avg, 1)))
    weak_criteria.sort(key=lambda x: x[1])

    # Частые триггеры
    trigger_counter = Counter()
    for _, a in manager_analyses:
        for t in (a.get("analysis") or {}).get("triggers", []) or []:
            trigger_counter[t.get("name", "")] += 1
    top_triggers = trigger_counter.most_common(5)

    return {
        "has_data": True,
        "analyzed": len(manager_analyses),
        "best": best,
        "worst": worst,
        "weak_criteria": weak_criteria[:5],
        "top_triggers": top_triggers,
    }


# ============================================================
# CSS
# ============================================================
CSS = """
:root {
  --brand-dark: #3D2E1F; --brand-medium: #6B5544; --brand-light: #A0826D;
  --brand-cream: #FAF7F2; --brand-paper: #F2EBE0;
  --brand-gold: #C9A961; --brand-olive: #7C8B6F; --brand-terracotta: #B86B4F;
  --text-primary: #2A1F15; --text-secondary: #6B5544; --text-muted: #A39686;
  --border: #E8DFD0; --border-soft: #F0E8DA;
  --red: #A0432B; --red-soft: #FFE5DC;
  --blue: #4A6B8A; --blue-soft: #DCE8F4;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: "Georgia", "Garamond", -apple-system, "Segoe UI", system-ui, serif;
  background: var(--brand-cream); color: var(--text-primary);
  -webkit-font-smoothing: antialiased; line-height: 1.55;
}
a { color: inherit; text-decoration: none; }
.topbar {
  background: var(--brand-dark); color: #fff;
  padding: 10px 24px;
  display: flex; align-items: center; gap: 20px;
  position: sticky; top: 0; z-index: 10;
  box-shadow: 0 2px 12px rgba(61,46,31,0.25);
  border-bottom: 3px solid var(--brand-gold);
  overflow: hidden;
}
.logo { display: flex; align-items: center; gap: 14px; flex-shrink: 0; }
.logo-img { height: 48px; width: auto; display: block; flex-shrink: 0; background: transparent; border-radius: 50%; }
.logo-text { display: flex; flex-direction: column; line-height: 1.1; }
.logo-text .brand { font-size: 17px; font-weight: 700; letter-spacing: 1px; color: #fff; }
.logo-text .sub { font-size: 10px; letter-spacing: 3px; color: var(--brand-gold); text-transform: uppercase; margin-top: 2px; }
.nav {
  display: flex; gap: 22px; font-size: 13px; flex: 1;
  overflow-x: auto; -webkit-overflow-scrolling: touch; scrollbar-width: none;
  font-family: -apple-system, sans-serif;
}
.nav::-webkit-scrollbar { display: none; }
.nav a { color: rgba(255,255,255,0.75); padding: 4px 2px; border-bottom: 2px solid transparent; white-space: nowrap; flex-shrink: 0; }
.nav a.active { color: #fff; border-bottom-color: var(--brand-gold); }
.nav a:hover { color: #fff; }
.nav a.critical-link { color: var(--red-soft); }
.nav a.critical-link::after { content: ""; width: 6px; height: 6px; background: var(--red); border-radius: 50%; display: inline-block; margin-left: 5px; vertical-align: middle; }
.timestamp { font-size: 11px; color: rgba(255,255,255,0.5); white-space: nowrap; flex-shrink: 0; }
.container { max-width: 1280px; margin: 0 auto; padding: 24px 24px 40px; }
.breadcrumb { font-size: 12px; color: var(--text-muted); margin-bottom: 16px; font-family: -apple-system, sans-serif; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.breadcrumb a { color: var(--brand-medium); border-bottom: 1px dotted var(--border); }
.breadcrumb a:hover { color: var(--brand-dark); border-bottom-style: solid; }
.page-head { display: flex; justify-content: space-between; align-items: flex-end; margin-bottom: 24px; flex-wrap: wrap; gap: 14px; padding-bottom: 18px; border-bottom: 1px solid var(--border); }
.page-head h1 { font-size: 28px; font-weight: 400; margin-bottom: 6px; color: var(--brand-dark); }
.page-head .sub { font-size: 13px; color: var(--text-secondary); font-family: -apple-system, sans-serif; }
.pill { background: #fff; border: 1px solid var(--border); border-radius: 4px; padding: 8px 14px; font-size: 13px; font-weight: 600; color: var(--brand-dark); font-family: -apple-system, sans-serif; }
.notice { background: linear-gradient(135deg, var(--brand-paper), #F8F1E3); border: 1px solid var(--brand-gold); border-left: 4px solid var(--brand-gold); border-radius: 4px; padding: 14px 18px; margin-bottom: 22px; font-size: 13px; color: var(--brand-dark); font-family: -apple-system, sans-serif; }
.notice b { font-weight: 700; }
.notice.success { background: linear-gradient(135deg, #E8F0E0, #F0F4E8); border-color: var(--brand-olive); border-left-color: var(--brand-olive); color: #2A3A1F; }
.notice.danger { background: linear-gradient(135deg, var(--red-soft), #FFEEE5); border-color: var(--red); border-left-color: var(--red); color: var(--red); }
.kpi-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; margin-bottom: 24px; }
.kpi { background: #fff; border-radius: 6px; padding: 16px 18px; border: 1px solid var(--border); border-top: 3px solid var(--brand-medium); }
.kpi.accent { border-top-color: var(--brand-terracotta); }
.kpi.green { border-top-color: var(--brand-olive); }
.kpi.amber { border-top-color: var(--brand-gold); }
.kpi.red { border-top-color: var(--red); }
.kpi.blue { border-top-color: var(--blue); }
.kpi-label { font-size: 10px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 1.5px; font-weight: 600; margin-bottom: 8px; font-family: -apple-system, sans-serif; }
.kpi-value { font-size: 30px; font-weight: 400; line-height: 1; color: var(--brand-dark); }
.kpi-hint { margin-top: 6px; font-size: 11px; color: var(--text-muted); font-family: -apple-system, sans-serif; }
.grid { display: grid; grid-template-columns: 1.3fr 1fr; gap: 18px; margin-bottom: 22px; }
@media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
.panel { background: #fff; border-radius: 6px; border: 1px solid var(--border); overflow: hidden; margin-bottom: 18px; }
.panel-head { padding: 14px 18px; border-bottom: 1px solid var(--border-soft); display: flex; justify-content: space-between; align-items: center; background: linear-gradient(180deg, #FFFCF7, #fff); }
.panel-head h3 { font-size: 16px; font-weight: 600; color: var(--brand-dark); }
.panel-head .hint { font-size: 12px; color: var(--text-muted); font-family: -apple-system, sans-serif; }
.row-link { display: block; transition: background 0.15s; font-family: -apple-system, sans-serif; }
.row-link:hover { background: var(--brand-paper); }
.row-link.critical { background: linear-gradient(90deg, var(--red-soft), transparent 60%); }
.manager-row { display: grid; grid-template-columns: 26px 1fr auto auto; align-items: center; gap: 12px; padding: 12px 18px; border-bottom: 1px solid var(--border-soft); }
.manager-row:last-child { border-bottom: none; }
.rank { width: 22px; height: 22px; border-radius: 50%; background: var(--brand-paper); color: var(--text-secondary); font-weight: 700; font-size: 11px; display: flex; align-items: center; justify-content: center; }
.rank.gold { background: var(--brand-gold); color: #fff; }
.rank.silver { background: #BFB5A8; color: #fff; }
.rank.bronze { background: var(--brand-terracotta); color: #fff; }
.manager-info { display: flex; align-items: center; gap: 12px; min-width: 0; }
.m-avatar { width: 38px; height: 38px; border-radius: 50%; background: linear-gradient(135deg, var(--brand-medium), var(--brand-dark)); color: #fff; display: flex; align-items: center; justify-content: center; font-weight: 600; font-size: 12px; flex-shrink: 0; overflow: hidden; border: 2px solid #fff; box-shadow: 0 0 0 1px var(--border); }
.m-avatar img { width: 100%; height: 100%; border-radius: 50%; object-fit: cover; display: block; }
.m-avatar.b { background: linear-gradient(135deg, var(--brand-terracotta), var(--brand-light)); }
.m-avatar.c { background: linear-gradient(135deg, var(--brand-olive), var(--brand-medium)); }
.m-avatar.d { background: linear-gradient(135deg, #8B7E6F, var(--brand-medium)); }
.m-avatar.e { background: linear-gradient(135deg, var(--brand-gold), var(--brand-terracotta)); }
.m-name { font-weight: 600; font-size: 14px; color: var(--brand-dark); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.m-stats { font-size: 11px; color: var(--text-muted); margin-top: 2px; }
.m-stats b { color: var(--brand-dark); }
.m-avg { font-family: "Georgia", serif; font-weight: 700; font-size: 18px; padding: 3px 10px; border-radius: 4px; min-width: 50px; text-align: center; }
.m-avg.high { background: rgba(124,139,111,0.15); color: var(--brand-olive); }
.m-avg.mid { background: rgba(201,169,97,0.18); color: var(--brand-gold); }
.m-avg.low { background: rgba(184,107,79,0.15); color: var(--brand-terracotta); }
.m-avg.empty { background: var(--brand-paper); color: var(--text-muted); font-size: 11px; font-weight: 600; }
.m-count { font-weight: 600; font-size: 18px; min-width: 36px; text-align: right; color: var(--text-secondary); font-family: "Georgia", serif; }
.call-row { display: grid; grid-template-columns: 28px 1fr auto auto auto; align-items: center; gap: 10px; padding: 11px 18px; border-bottom: 1px solid var(--border-soft); font-size: 13px; }
.call-row:last-child { border-bottom: none; }
.call-direction { width: 26px; height: 26px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 14px; font-weight: 700; flex-shrink: 0; }
.call-direction.in { background: rgba(124,139,111,0.15); color: var(--brand-olive); }
.call-direction.out { background: rgba(184,107,79,0.15); color: var(--brand-terracotta); }
.call-info { min-width: 0; overflow: hidden; }
.call-client { font-weight: 600; color: var(--brand-dark); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.call-meta { font-size: 11px; color: var(--text-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.call-duration { font-size: 11px; color: var(--text-muted); font-weight: 600; white-space: nowrap; padding: 2px 7px; background: var(--brand-paper); border-radius: 10px; }
.call-time { font-size: 12px; color: var(--text-secondary); white-space: nowrap; font-weight: 600; }
.mini-score { font-family: "Georgia", serif; font-size: 14px; font-weight: 700; padding: 3px 8px; border-radius: 4px; min-width: 36px; text-align: center; }
.mini-score.high { background: rgba(124,139,111,0.15); color: var(--brand-olive); }
.mini-score.mid { background: rgba(201,169,97,0.18); color: var(--brand-gold); }
.mini-score.low { background: rgba(184,107,79,0.15); color: var(--brand-terracotta); }
.mini-score.crit { background: var(--red); color: #fff; }
.empty { padding: 36px 18px; text-align: center; color: var(--text-muted); font-size: 14px; font-style: italic; }
.footer-note { text-align: center; padding: 36px 16px 16px; font-size: 12px; color: var(--text-muted); font-family: -apple-system, sans-serif; border-top: 1px solid var(--border); margin-top: 40px; }
.footer-brand { font-family: "Georgia", serif; font-size: 14px; font-weight: 600; color: var(--brand-dark); letter-spacing: 1.5px; margin-bottom: 6px; }
.footer-divider { display: inline-block; width: 30px; height: 1px; background: var(--brand-gold); margin: 8px auto; vertical-align: middle; }
.search-box { width: 100%; padding: 11px 16px; border: 1px solid var(--border); border-radius: 6px; font-size: 14px; background: #fff; margin-bottom: 14px; font-family: -apple-system, sans-serif; }
.filter-row { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 16px; }
.filter-btn { padding: 7px 14px; border-radius: 20px; background: #fff; border: 1px solid var(--border); font-size: 12px; cursor: pointer; font-weight: 600; color: var(--text-secondary); font-family: -apple-system, sans-serif; }
.filter-btn.active { background: var(--brand-dark); color: #fff; border-color: var(--brand-dark); }
.call-card { background: #fff; border-radius: 6px; border: 1px solid var(--border); overflow: hidden; margin-bottom: 18px; }
.call-card.critical { border-color: var(--red); box-shadow: 0 0 0 1px var(--red), 0 4px 12px rgba(160,67,43,0.1); }
.call-card-head { padding: 22px 24px; display: grid; grid-template-columns: 1fr auto; gap: 18px; align-items: center; background: linear-gradient(180deg, #FFFCF7, #fff); border-bottom: 1px solid var(--border-soft); }
.call-card.critical .call-card-head { background: linear-gradient(180deg, var(--red-soft), #fff); }
.call-card-head h2 { font-size: 22px; font-weight: 400; margin-bottom: 10px; color: var(--brand-dark); }
.critical-badge { display: inline-block; background: var(--red); color: #fff; padding: 3px 10px; border-radius: 4px; font-size: 11px; font-weight: 700; margin-bottom: 10px; font-family: -apple-system, sans-serif; letter-spacing: 0.5px; }
.call-tags { display: flex; gap: 6px; flex-wrap: wrap; font-size: 12px; font-family: -apple-system, sans-serif; }
.call-tag { background: var(--brand-paper); padding: 4px 10px; border-radius: 12px; color: var(--brand-medium); border: 1px solid var(--border); }
.call-tag.accent { background: var(--brand-dark); color: #fff; font-weight: 600; border-color: var(--brand-dark); }
.call-tag.goal { background: var(--blue-soft); color: var(--blue); border-color: var(--blue); }
.call-tag.warning { background: var(--brand-terracotta); color: #fff; font-weight: 600; border-color: var(--brand-terracotta); }
.score-big { border-radius: 6px; padding: 14px 22px; text-align: center; min-width: 130px; color: #fff; position: relative; }
.score-big.high { background: linear-gradient(135deg, var(--brand-olive), #6B7B5F); }
.score-big.mid { background: linear-gradient(135deg, var(--brand-gold), #B89A55); }
.score-big.low { background: linear-gradient(135deg, var(--brand-terracotta), #A65E45); }
.score-big.crit { background: linear-gradient(135deg, var(--red), #7D2818); }
.score-big.placeholder { background: linear-gradient(135deg, #BFB5A8, var(--text-muted)); }
.score-big .num { font-family: "Georgia", serif; font-size: 36px; font-weight: 400; line-height: 1; }
.score-big .num .max { font-size: 16px; opacity: 0.7; }
.score-big .label { font-size: 10px; text-transform: uppercase; letter-spacing: 1.5px; margin-top: 4px; opacity: 0.85; font-family: -apple-system, sans-serif; }
.score-big .edit-btn { display: block; margin: 8px auto 0; background: rgba(255,255,255,0.2); border: 1px solid rgba(255,255,255,0.3); color: #fff; padding: 3px 10px; border-radius: 4px; font-size: 11px; cursor: pointer; font-family: -apple-system, sans-serif; }
.score-big .edit-btn:hover { background: rgba(255,255,255,0.3); }
.score-corrected-mark { position: absolute; top: 4px; right: 6px; font-size: 10px; background: rgba(255,255,255,0.3); padding: 1px 5px; border-radius: 3px; }
.detail-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; padding: 20px 24px 24px; }
.detail { background: var(--brand-cream); border: 1px solid var(--border-soft); border-radius: 4px; padding: 11px 14px; }
.detail-label { font-size: 10px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 1px; margin-bottom: 4px; font-weight: 600; font-family: -apple-system, sans-serif; }
.detail-value { font-size: 13px; font-weight: 600; color: var(--brand-dark); font-family: -apple-system, sans-serif; }
.detail-value a { color: var(--brand-medium); border-bottom: 1px dotted var(--border); }
.audio-player { background: var(--brand-paper); border: 1px solid var(--brand-gold); border-radius: 6px; padding: 16px 20px; margin-bottom: 16px; display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
.audio-player audio { flex: 1; min-width: 240px; height: 38px; }
.audio-player .bitrix-link { font-family: -apple-system, sans-serif; font-size: 12px; color: var(--brand-dark); text-decoration: underline; padding: 6px 12px; background: #fff; border-radius: 4px; border: 1px solid var(--border); }
.audio-missing { background: var(--brand-paper); border: 1px dashed var(--border); border-radius: 6px; padding: 14px 18px; margin-bottom: 16px; font-family: -apple-system, sans-serif; font-size: 13px; color: var(--text-secondary); }
.ai-summary { background: linear-gradient(135deg, #FFF8E8, #FFF4DC); border: 1px solid var(--brand-gold); border-radius: 6px; padding: 18px 22px; margin-bottom: 16px; font-family: -apple-system, sans-serif; }
.ai-summary .label { font-size: 10px; text-transform: uppercase; letter-spacing: 1.5px; color: var(--brand-dark); font-weight: 700; margin-bottom: 8px; }
.ai-summary .text { font-size: 14px; color: var(--brand-dark); line-height: 1.6; }
.main-problem { background: linear-gradient(135deg, var(--red-soft), #FFEEE5); border: 1px solid var(--red); border-radius: 6px; padding: 18px 22px; margin-bottom: 16px; font-family: -apple-system, sans-serif; }
.main-problem .label { font-size: 10px; text-transform: uppercase; letter-spacing: 1.5px; color: var(--red); font-weight: 700; margin-bottom: 8px; }
.main-problem .text { font-size: 14px; color: var(--red); line-height: 1.6; font-weight: 600; }
.next-contact { background: linear-gradient(135deg, var(--blue-soft), #EAF2FA); border: 1px solid var(--blue); border-radius: 6px; padding: 14px 20px; margin-bottom: 16px; font-family: -apple-system, sans-serif; }
.next-contact .label { font-size: 10px; text-transform: uppercase; letter-spacing: 1.5px; color: var(--blue); font-weight: 700; margin-bottom: 6px; }
.next-contact .text { font-size: 14px; color: var(--blue); font-weight: 600; }
.strengths-weaknesses { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-bottom: 16px; }
@media (max-width: 700px) { .strengths-weaknesses { grid-template-columns: 1fr; } }
.sw-box { background: #fff; border: 1px solid var(--border); border-radius: 6px; padding: 16px 18px; font-family: -apple-system, sans-serif; }
.sw-box.strong { border-left: 3px solid var(--brand-olive); }
.sw-box.weak { border-left: 3px solid var(--brand-terracotta); }
.sw-box .label { font-size: 10px; text-transform: uppercase; letter-spacing: 1.5px; font-weight: 700; margin-bottom: 8px; }
.sw-box.strong .label { color: var(--brand-olive); }
.sw-box.weak .label { color: var(--brand-terracotta); }
.sw-box ul { list-style: none; padding: 0; }
.sw-box li { font-size: 13px; color: var(--brand-dark); padding: 4px 0; border-bottom: 1px solid var(--border-soft); }
.sw-box li:last-child { border-bottom: none; }
.sw-box li::before { content: "•  "; color: var(--brand-light); font-weight: 700; }
.ai-key-quote { background: var(--brand-paper); border-left: 3px solid var(--brand-medium); padding: 12px 16px; margin-top: 12px; font-style: italic; font-size: 14px; color: var(--brand-dark); }
.ai-key-quote::before { content: "« "; color: var(--brand-medium); }
.ai-key-quote::after { content: " »"; color: var(--brand-medium); }
.ai-key-quote .meta { display: block; font-style: normal; font-size: 11px; color: var(--text-muted); margin-top: 6px; font-family: -apple-system, sans-serif; }
.transcript-panel { background: #fff; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; margin-bottom: 16px; }
.transcript-head { padding: 14px 20px; border-bottom: 1px solid var(--border-soft); background: linear-gradient(180deg, #FFFCF7, #fff); display: flex; justify-content: space-between; align-items: center; }
.transcript-head h3 { font-size: 16px; font-weight: 600; color: var(--brand-dark); }
.transcript-body { padding: 6px 0; max-height: 500px; overflow-y: auto; font-family: -apple-system, sans-serif; }
.msg { padding: 10px 20px; border-bottom: 1px solid var(--border-soft); display: grid; grid-template-columns: 60px 1fr; gap: 12px; }
.msg:last-child { border-bottom: none; }
.msg-time { font-size: 11px; color: var(--text-muted); font-weight: 600; padding-top: 2px; }
.msg-body { min-width: 0; }
.msg-who { font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 3px; }
.msg-who.client { color: var(--brand-olive); }
.msg-who.manager { color: var(--brand-terracotta); }
.msg-text { font-size: 13px; color: var(--brand-dark); line-height: 1.55; }
.scores-panel { background: #fff; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; margin-bottom: 16px; }
.score-row { display: grid; grid-template-columns: 1fr 110px 50px 32px; align-items: center; gap: 14px; padding: 9px 20px; border-bottom: 1px solid var(--border-soft); font-family: -apple-system, sans-serif; font-size: 13px; }
.score-row:last-child { border-bottom: none; }
.score-name { color: var(--brand-dark); }
.score-bar { height: 6px; background: var(--brand-paper); border-radius: 3px; overflow: hidden; }
.score-fill { height: 100%; }
.score-fill.high { background: var(--brand-olive); }
.score-fill.mid { background: var(--brand-gold); }
.score-fill.low { background: var(--brand-terracotta); }
.score-val { font-family: "Georgia", serif; font-weight: 700; font-size: 15px; text-align: right; }
.score-val.high { color: var(--brand-olive); }
.score-val.mid { color: var(--brand-gold); }
.score-val.low { color: var(--brand-terracotta); }
.score-weight { font-size: 10px; color: var(--text-muted); text-align: right; font-variant-numeric: tabular-nums; }
.low-score-block { background: var(--red-soft); border: 1px solid var(--red); border-radius: 6px; padding: 14px 18px; margin-bottom: 12px; font-family: -apple-system, sans-serif; }
.low-score-block .lsh { font-weight: 700; color: var(--red); margin-bottom: 6px; font-size: 13px; }
.low-score-block .lsq { font-style: italic; padding: 8px 12px; background: #fff; border-left: 2px solid var(--red); margin: 8px 0; font-size: 13px; }
.low-score-block .lsr { font-size: 13px; color: var(--brand-dark); margin-top: 6px; }
.low-score-block .lsr b { color: var(--red); }
.trigger-list { background: #fff; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; margin-bottom: 16px; }
.trigger-row { padding: 12px 20px; display: flex; gap: 12px; align-items: flex-start; border-bottom: 1px solid var(--border-soft); }
.trigger-row:last-child { border-bottom: none; }
.trigger-marker { width: 6px; height: 6px; border-radius: 50%; background: var(--brand-terracotta); margin-top: 7px; flex-shrink: 0; }
.trigger-text { font-family: -apple-system, sans-serif; font-size: 13px; color: var(--brand-dark); flex: 1; }
.trigger-text .sub { font-size: 11px; color: var(--text-muted); margin-top: 3px; }
.trigger-text .timecode { display: inline-block; font-size: 10px; color: var(--brand-medium); background: var(--brand-paper); padding: 1px 6px; border-radius: 3px; margin-left: 6px; }
.recommendation { background: linear-gradient(135deg, #FFF8E8, #FFF4DC); border: 1px solid var(--brand-gold); border-radius: 6px; padding: 18px 22px; margin-bottom: 16px; font-family: -apple-system, sans-serif; }
.recommendation .label { font-size: 10px; text-transform: uppercase; letter-spacing: 1.5px; color: var(--brand-dark); font-weight: 700; margin-bottom: 8px; }
.recommendation .text { font-size: 14px; color: var(--brand-dark); line-height: 1.6; }
.scripts-alignment { background: #fff; border: 1px solid var(--border); border-radius: 6px; padding: 16px 20px; margin-bottom: 16px; font-family: -apple-system, sans-serif; }
.scripts-alignment .label { font-size: 10px; text-transform: uppercase; letter-spacing: 1.5px; color: var(--brand-dark); font-weight: 700; margin-bottom: 10px; }
.sa-row { display: grid; grid-template-columns: 1fr auto; gap: 12px; padding: 6px 0; border-bottom: 1px solid var(--border-soft); font-size: 13px; align-items: center; }
.sa-row:last-child { border-bottom: none; }
.sa-stage { color: var(--brand-dark); }
.sa-status { font-size: 11px; font-weight: 700; padding: 3px 10px; border-radius: 10px; }
.sa-status.full { background: rgba(124,139,111,0.15); color: var(--brand-olive); }
.sa-status.partial { background: rgba(201,169,97,0.18); color: var(--brand-gold); }
.sa-status.miss { background: rgba(184,107,79,0.15); color: var(--brand-terracotta); }
.scripts-used { background: var(--brand-paper); border: 1px solid var(--border); border-radius: 6px; padding: 12px 18px; margin-bottom: 16px; font-family: -apple-system, sans-serif; font-size: 12px; color: var(--text-secondary); }
.scripts-used b { color: var(--brand-dark); }
.scripts-used .tag { display: inline-block; background: #fff; border: 1px solid var(--border); padding: 2px 8px; margin: 3px 4px 0 0; border-radius: 10px; font-size: 11px; }
.placeholder-panel { background: linear-gradient(135deg, var(--brand-paper), #F8F1E3); border: 1px dashed var(--brand-gold); border-radius: 6px; padding: 20px 22px; margin-bottom: 16px; }
.placeholder-panel h3 { font-size: 16px; font-weight: 600; color: var(--brand-dark); margin-bottom: 8px; }
.placeholder-panel p { font-size: 13px; color: var(--brand-medium); font-family: -apple-system, sans-serif; }
.correction-note { background: var(--blue-soft); border: 1px solid var(--blue); border-radius: 6px; padding: 12px 18px; margin-bottom: 14px; font-family: -apple-system, sans-serif; font-size: 13px; color: var(--blue); }
.correction-note b { font-weight: 700; }
.modal-backdrop { display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(61,46,31,0.6); z-index: 9999; align-items: center; justify-content: center; padding: 20px; }
.modal-backdrop.active { display: flex; }
.modal { background: #fff; border-radius: 8px; padding: 24px; max-width: 540px; width: 100%; box-shadow: 0 8px 32px rgba(61,46,31,0.3); font-family: -apple-system, sans-serif; max-height: 90vh; overflow-y: auto; }
.modal h3 { font-size: 18px; margin-bottom: 14px; color: var(--brand-dark); }
.modal label { display: block; font-size: 12px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 1px; margin-bottom: 4px; font-weight: 600; margin-top: 12px; }
.modal input, .modal textarea { width: 100%; padding: 10px 12px; border: 1px solid var(--border); border-radius: 4px; font-size: 14px; font-family: inherit; }
.modal textarea { min-height: 80px; resize: vertical; }
.modal .actions { display: flex; gap: 10px; margin-top: 18px; justify-content: flex-end; }
.modal button { padding: 9px 18px; border-radius: 4px; font-size: 13px; cursor: pointer; font-weight: 600; border: none; font-family: inherit; }
.modal .btn-cancel { background: var(--brand-paper); color: var(--brand-dark); }
.modal .btn-save { background: var(--brand-dark); color: #fff; }
.modal .instruction { background: var(--brand-paper); border: 1px solid var(--brand-gold); border-radius: 4px; padding: 10px 14px; font-size: 12px; color: var(--brand-dark); margin-top: 12px; line-height: 1.5; }
.modal .instruction code { background: #fff; padding: 1px 5px; border-radius: 3px; font-family: monospace; font-size: 11px; word-break: break-all; }
.daily-report { background: linear-gradient(135deg, #FFFBF0, #FFF6E0); border: 1px solid var(--brand-gold); border-radius: 6px; padding: 20px 22px; margin-bottom: 18px; font-family: -apple-system, sans-serif; }
.daily-report h3 { font-size: 18px; color: var(--brand-dark); margin-bottom: 12px; font-family: "Georgia", serif; font-weight: 400; }
.dr-block { margin-bottom: 14px; }
.dr-block .label { font-size: 11px; text-transform: uppercase; letter-spacing: 1px; color: var(--text-muted); font-weight: 700; margin-bottom: 6px; }
.dr-list { font-size: 13px; color: var(--brand-dark); }
.dr-list-item { padding: 4px 0; border-bottom: 1px dashed var(--border); }
.dr-list-item:last-child { border-bottom: none; }
.dr-callref { padding: 8px 12px; background: #fff; border-radius: 4px; font-size: 13px; margin-top: 4px; }
.dr-callref a { color: var(--brand-medium); border-bottom: 1px dotted var(--border); }
.dr-callref .sc { display: inline-block; font-family: "Georgia", serif; font-weight: 700; padding: 1px 7px; border-radius: 3px; margin-right: 6px; font-size: 12px; }
.rop-section { background: #fff; border: 1px solid var(--border); border-radius: 6px; padding: 18px 22px; margin-bottom: 16px; font-family: -apple-system, sans-serif; }
.rop-section h3 { font-size: 16px; color: var(--brand-dark); margin-bottom: 12px; }
.rop-section .label { font-size: 11px; text-transform: uppercase; letter-spacing: 1px; color: var(--text-muted); font-weight: 700; margin-bottom: 8px; }
.rop-list-item { padding: 6px 0; font-size: 13px; color: var(--brand-dark); border-bottom: 1px solid var(--border-soft); display: grid; grid-template-columns: 1fr auto; gap: 12px; }
.rop-list-item:last-child { border-bottom: none; }
.rop-list-item .count { font-weight: 700; color: var(--brand-terracotta); }
@media (max-width: 640px) {
  .topbar { padding: 10px 14px; gap: 12px; }
  .logo-img { height: 40px; }
  .logo-text .brand { font-size: 14px; }
  .timestamp { display: none; }
  .container { padding: 16px 14px 24px; }
  .page-head h1 { font-size: 22px; }
  .kpi-row { grid-template-columns: 1fr 1fr; gap: 8px; }
  .call-card-head { grid-template-columns: 1fr; gap: 14px; padding: 18px; }
  .call-card-head h2 { font-size: 19px; }
  .score-big { justify-self: start; min-width: auto; padding: 12px 18px; }
  .score-big .num { font-size: 28px; }
  .detail-grid { grid-template-columns: 1fr; padding: 16px 18px 18px; gap: 10px; }
  .score-row { grid-template-columns: 1fr 80px 40px 28px; padding: 9px 14px; }
  .call-row { grid-template-columns: 28px 1fr auto auto; }
  .call-row .call-time { display: none; }
  .manager-row { grid-template-columns: 26px 1fr auto; }
  .manager-row .m-count { display: none; }
  .msg { grid-template-columns: 45px 1fr; gap: 8px; }
}
@media (max-width: 380px) { .kpi-row { grid-template-columns: 1fr; } }

/* Фильтры по периоду */
.period-tabs { display: flex; gap: 6px; margin-bottom: 20px; }
.period-tab { padding: 7px 16px; border-radius: 20px; background: #fff; border: 1px solid var(--border); font-size: 13px; cursor: pointer; font-weight: 600; color: var(--text-secondary); font-family: -apple-system, sans-serif; transition: all 0.15s; }
.period-tab.active { background: var(--brand-dark); color: #fff; border-color: var(--brand-dark); }
.period-tab:hover:not(.active) { background: var(--brand-paper); }

/* Расширенные фильтры */
.filter-bar { background: #fff; border: 1px solid var(--border); border-radius: 6px; padding: 14px 18px; margin-bottom: 18px; display: flex; gap: 12px; flex-wrap: wrap; align-items: center; font-family: -apple-system, sans-serif; }
.filter-bar select { padding: 7px 12px; border: 1px solid var(--border); border-radius: 4px; font-size: 13px; color: var(--brand-dark); background: var(--brand-cream); font-family: inherit; cursor: pointer; }
.filter-bar label { font-size: 12px; color: var(--text-muted); font-weight: 600; margin-right: -6px; }
.filter-clear { padding: 7px 14px; background: none; border: 1px solid var(--border); border-radius: 4px; font-size: 12px; color: var(--text-muted); cursor: pointer; font-family: inherit; }
.filter-clear:hover { color: var(--brand-dark); border-color: var(--brand-medium); }

/* Поиск по транскрипту */
.transcript-search { padding: 8px 14px; border: 1px solid var(--border); border-radius: 4px; font-size: 13px; font-family: -apple-system, sans-serif; width: 100%; margin-bottom: 8px; }
.msg.highlight { background: rgba(201,169,97,0.15); }
.msg .highlight-text { background: rgba(201,169,97,0.4); border-radius: 2px; padding: 0 2px; }

/* Кликабельные таймкоды */
.tc-link { display: inline-block; font-size: 10px; color: var(--brand-medium); background: var(--brand-paper); padding: 1px 6px; border-radius: 3px; cursor: pointer; border: 1px solid var(--border); transition: all 0.15s; font-family: -apple-system, sans-serif; }
.tc-link:hover { background: var(--brand-dark); color: #fff; border-color: var(--brand-dark); }

/* Статус сделки */
.deal-status { display: inline-block; font-size: 11px; padding: 3px 10px; border-radius: 10px; font-weight: 600; font-family: -apple-system, sans-serif; }
.deal-status.won { background: rgba(124,139,111,0.2); color: var(--brand-olive); }
.deal-status.lost { background: rgba(160,67,43,0.15); color: var(--red); }
.deal-status.work { background: rgba(201,169,97,0.2); color: var(--brand-gold); }
.deal-status.new { background: var(--blue-soft); color: var(--blue); }
"""


# ============================================================
# ШАБЛОН СТРАНИЦЫ
# ============================================================

def page_template(title: str, body: str, active_nav: str, generated_at: str,
                  critical_count: int = 0, base_path: str = "", extra_head: str = "") -> str:
    nav_items = [
        ("dashboard", "index.html", "Сводка"),
        ("rop", "rop-report.html", "Отчёт РОПа"),
        ("managers", "managers.html", "Менеджеры"),
        ("compare", "compare.html", "Сравнение"),
        ("calls", "all-calls.html", "Все звонки"),
        ("critical", "critical.html", f"Срочно{' (' + str(critical_count) + ')' if critical_count else ''}"),
        ("triggers", "triggers.html", "Триггеры"),
        ("objections", "objections.html", "Возражения"),
        ("best", "best-calls.html", "Лучшие звонки"),
    ]
    nav_html = ""
    for key, href, label in nav_items:
        classes = []
        if key == active_nav:
            classes.append("active")
        if key == "critical" and critical_count > 0:
            classes.append("critical-link")
        cls = ' class="' + ' '.join(classes) + '"' if classes else ""
        nav_html += '<a' + cls + ' href="' + base_path + href + '">' + esc(label) + '</a>'
    year = datetime.now().year
    safe_ts = esc(generated_at[:16].replace('T', ' '))
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{esc(title)} — {esc(COMPANY_NAME)} Sales Analytics</title>
  <style>{CSS}</style>
  {extra_head}
</head>
<body>
  <div class="topbar">
    <a href="{base_path}index.html" class="logo">
      <img class="logo-img" src="{base_path}logo.png" alt="{esc(COMPANY_NAME)}" onerror="this.style.display='none'">
      <div class="logo-text">
        <span class="brand">{esc(COMPANY_NAME)}</span>
        <span class="sub">Sales Analytics</span>
      </div>
    </a>
    <div class="nav">{nav_html}</div>
    <div class="timestamp">обновлено {safe_ts}</div>
  </div>
  <div class="container">{body}</div>
  <div class="footer-note">
    <div class="footer-brand">{esc(COMPANY_NAME).upper()}</div>
    <div class="footer-divider"></div>
    <div>Sales Analytics · Автоматический анализ звонков отдела продаж</div>
    <div style="margin-top:6px; font-size:11px;">Bitrix24 · GitHub Actions · BitrixGPT · © {year}</div>
  </div>
</body>
</html>"""


# ============================================================
# СТРОКА ЗВОНКА
# ============================================================

def render_call_row(c: Dict, show_manager: bool = True, analysis: Dict = None,
                    base_path: str = "") -> str:
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
        meta_parts.append("менеджер: " + esc(manager))
    meta = " · ".join(meta_parts)

    duration_html = ""
    dur = c.get("duration_sec")
    if dur:
        duration_html = '<div class="call-duration">' + format_duration(dur) + '</div>'

    score_html = ""
    row_classes = ["row-link"]
    if analysis:
        an = analysis.get("analysis") or {}
        score = an.get("overall_score")
        if score is not None:
            cls = "crit" if an.get("is_critical") else score_color(score)
            score_html = '<div class="mini-score ' + cls + '">' + esc(score) + '</div>'
        if an.get("is_critical"):
            row_classes.append("critical")

    activity_id = esc(c['activity_id'])
    return ('<a href="' + base_path + 'calls/' + activity_id + '.html" class="' + ' '.join(row_classes) + '">'
            '<div class="call-row">'
            '<div class="call-direction ' + dir_class + '">' + dir_icon + '</div>'
            '<div class="call-info">'
            '<div class="call-client">' + esc(client_name) + '</div>'
            '<div class="call-meta">' + meta + '</div>'
            '</div>'
            + duration_html +
            '<div class="call-time">' + format_time(c.get('created', '')) + '</div>'
            + score_html +
            '</div></a>')


# ============================================================
# ГЛАВНАЯ СТРАНИЦА
# ============================================================

def render_index(calls: List[Dict], stats: Dict, analyses: Dict, generated_at: str) -> str:
    today = format_date(generated_at)
    # Считаем только звонки текущего дня которые проанализированы
    analyzed_today = sum(1 for c in calls if c["activity_id"] in analyses)
    critical_today = sum(
        1 for c in calls
        if c["activity_id"] in analyses
        and (analyses[c["activity_id"]].get("analysis") or {}).get("is_critical")
    )
    critical_count = critical_today
    analyzed_count = analyzed_today

    kpi_html = ('<div class="kpi-row">'
                '<div class="kpi"><div class="kpi-label">Всего звонков</div>'
                '<div class="kpi-value">' + str(stats['total']) + '</div>'
                '<div class="kpi-hint">за последние сутки</div></div>'
                '<div class="kpi green"><div class="kpi-label">Входящих</div>'
                '<div class="kpi-value">' + str(stats['incoming']) + '</div></div>'
                '<div class="kpi accent"><div class="kpi-label">Исходящих</div>'
                '<div class="kpi-value">' + str(stats['outgoing']) + '</div></div>'
                '<div class="kpi amber"><div class="kpi-label">Проанализировано</div>'
                '<div class="kpi-value">' + str(analyzed_count) + '</div>'
                '<div class="kpi-hint">из ' + str(stats['total']) + ' за сутки</div></div>'
                '<div class="kpi red"><div class="kpi-label">Срочно к РОПу</div>'
                '<div class="kpi-value">' + str(critical_count) + '</div>'
                '<div class="kpi-hint">критичных сегодня</div></div>'
                '</div>')

    # Рейтинг менеджеров
    managers_html = ""
    for i, m in enumerate(stats["managers"][:10], 1):
        rank_class = ["", "gold", "silver", "bronze"][min(i, 3)] if i <= 3 else ""
        avatar_class = manager_color_class(m["id"])
        if m.get("avg_score") is not None:
            avg_cls = score_color(m["avg_score"])
            avg_html = '<div class="m-avg ' + avg_cls + '">' + str(m["avg_score"]) + '</div>'
            stats_line = ('оценок: <b>' + str(m["analyzed"]) + '</b> из ' + str(m["count"]))
            if m["critical"]:
                stats_line += ' · 🔴 <b>' + str(m["critical"]) + '</b>'
        else:
            avg_html = '<div class="m-avg empty">—</div>'
            stats_line = ('входящих: <b>' + str(m["in"]) + '</b> · исходящих: <b>' + str(m["out"]) + '</b>')

        managers_html += ('<a href="managers/' + str(m['id']) + '.html" class="row-link">'
                          '<div class="manager-row">'
                          '<div class="rank ' + rank_class + '">' + str(i) + '</div>'
                          '<div class="manager-info">'
                          '<div class="m-avatar ' + avatar_class + '">' + manager_avatar_html(m) + '</div>'
                          '<div><div class="m-name">' + esc(m['name']) + '</div>'
                          '<div class="m-stats">' + stats_line + '</div></div>'
                          '</div>'
                          + avg_html +
                          '<div class="m-count">' + str(m['count']) + '</div>'
                          '</div></a>')
    if not managers_html:
        managers_html = '<div class="empty">Звонков нет</div>'

    recent = sorted(calls, key=lambda x: x.get("created", ""), reverse=True)[:10]
    calls_html = "".join(render_call_row(c, analysis=analyses.get(c["activity_id"])) for c in recent)
    if not calls_html:
        calls_html = '<div class="empty">Звонков нет</div>'

    notice = ""
    if critical_count > 0:
        notice = ('<div class="notice danger">'
                  '<b>🔴 Внимание!</b> Найдено <b>' + str(critical_count) + '</b> критичных звонков за сутки. '
                  '<a href="critical.html" style="color: var(--red); font-weight: 700; text-decoration: underline;">Перейти к списку →</a>'
                  '</div>')
    elif analyzed_count > 0:
        notice = ('<div class="notice success">'
                  '<b>✓ Анализ работает.</b> ' + str(analyzed_count) + ' звонков проанализированы за сутки.'
                  '</div>')

    import json as _json_idx
    _calls_data = _json_idx.dumps([{
        'id': c['activity_id'], 'created': c.get('created',''),
        'direction': c.get('direction',''), 'client': c.get('client',{}).get('name',''),
        'manager': c.get('manager',{}).get('name',''), 'duration': c.get('duration_sec',0),
        'score': analyses.get(c['activity_id'],{}).get('analysis',{}).get('overall_score'),
        'is_critical': bool(analyses.get(c['activity_id'],{}).get('analysis',{}).get('is_critical',False)),
    } for c in sorted(calls, key=lambda x: x.get('created',''), reverse=True)], ensure_ascii=False)

    period_tabs_html = (
        '<div class="period-tabs">'
        '<button class="period-tab active" data-period="day" type="button">День</button>'
        '<button class="period-tab" data-period="week" type="button">Неделя</button>'
        '<button class="period-tab" data-period="month" type="button">Месяц</button>'
        '<button class="period-tab" data-period="all" type="button">Всё время</button>'
        '</div>')

    kpi_html2 = (
        '<div class="kpi-row">'
        '<div class="kpi"><div class="kpi-label">Всего звонков</div>'
        '<div class="kpi-value" id="kpi-total">' + str(stats['total']) + '</div><div class="kpi-hint">за период</div></div>'
        '<div class="kpi green"><div class="kpi-label">Входящих</div>'
        '<div class="kpi-value" id="kpi-incoming">' + str(stats['incoming']) + '</div></div>'
        '<div class="kpi accent"><div class="kpi-label">Исходящих</div>'
        '<div class="kpi-value" id="kpi-outgoing">' + str(stats['outgoing']) + '</div></div>'
        '<div class="kpi amber"><div class="kpi-label">Проанализировано</div>'
        '<div class="kpi-value" id="kpi-analyzed">' + str(analyzed_count) + '</div><div class="kpi-hint">за период</div></div>'
        '<div class="kpi red"><div class="kpi-label">Срочно к РОПу</div>'
        '<div class="kpi-value" id="kpi-critical">' + str(critical_count) + '</div></div>'
        '</div>')

    period_js = (
        '<script>'
        '(function(){'
        'var _aC=' + _calls_data + ';'
        'function filterByPeriod(p,btn){'
        'document.querySelectorAll(".period-tab").forEach(function(b){b.classList.remove("active");});'
        'if(btn)btn.classList.add("active");'
        'var now=new Date(),ms={day:86400000,week:604800000,month:2592000000};'
        'var f=p==="all"?_aC:_aC.filter(function(c){return !c.created||(now-new Date(c.created))<(ms[p]||0);});'
        'var elTotal=document.getElementById("kpi-total");if(elTotal)elTotal.textContent=f.length;'
        'var elIn=document.getElementById("kpi-incoming");if(elIn)elIn.textContent=f.filter(function(c){return c.direction==="incoming";}).length;'
        'var elOut=document.getElementById("kpi-outgoing");if(elOut)elOut.textContent=f.filter(function(c){return c.direction==="outgoing";}).length;'
        'var elAn=document.getElementById("kpi-analyzed");if(elAn)elAn.textContent=f.filter(function(c){return c.score!=null;}).length;'
        'var elCrit=document.getElementById("kpi-critical");if(elCrit)elCrit.textContent=f.filter(function(c){return c.is_critical;}).length;'
        'var h="";f.slice(0,15).forEach(function(c){'
        'var s=c.score,sc=s!=null?s:"",cl=c.is_critical?"crit":s>=7.5?"high":s>=5?"mid":"low";'
        'var sH=sc!==""?"<div class=\\"mini-score "+cl+"\\">"+sc+"</div>":"";'
        'var dH=c.duration?"<div class=\\"call-duration\\">"+Math.floor(c.duration/60)+" мин</div>":"";'
        'var di=c.direction==="incoming"?"↓":"↑",dc=c.direction==="incoming"?"in":"out";'
        'var rc=c.is_critical?"row-link critical":"row-link";'
        'h+="<a href=\\"/calls/"+c.id+"\\" class=\\""+rc+"\\">"'
        '+"<div class=\\"call-row\\">"'
        '+"<div class=\\"call-direction "+dc+"\\">"+di+"</div>"'
        '+"<div class=\\"call-info\\"><div class=\\"call-client\\">"+( c.client||"—")+"</div>"'
        '+"<div class=\\"call-meta\\">"+( c.manager||"")+"</div></div>"'
        '+dH+sH+"</div></a>";});'
        'var listEl=document.getElementById("recent-calls-list");'
        'if(listEl)listEl.innerHTML=h||"<div class=\\"empty\\">Нет звонков</div>";'
        '}'
        'function wirePeriodTabs(){'
        'document.querySelectorAll(".period-tab").forEach(function(btn){'
        'btn.addEventListener("click",function(){filterByPeriod(btn.getAttribute("data-period"),btn);});'
        '});'
        'var first=document.querySelector(".period-tab");'
        'filterByPeriod("day",first);'
        '}'
        'if(document.readyState==="loading"){'
        'document.addEventListener("DOMContentLoaded",wirePeriodTabs);'
        '}else{wirePeriodTabs();}'
        '})();'
        '</script>')

    body = (
        '<div class="page-head">'
        '<div><h1>Сводка</h1><div class="sub">' + COMPANY_NAME + ' · ' + str(stats['managers_count']) + ' менеджеров</div></div>'
        '<div class="pill">📅 ' + today + '</div>'
        '</div>'
        + period_tabs_html + notice + kpi_html2
        + '<div class="grid">'
        '<div class="panel">'
        '<div class="panel-head"><h3>Рейтинг по качеству</h3><a href="managers.html" class="hint" style="color:var(--brand-medium);">все →</a></div>'
        + managers_html
        + '</div>'
        '<div class="panel">'
        '<div class="panel-head"><h3>Последние звонки</h3><a href="all-calls.html" class="hint" style="color:var(--brand-medium);">все →</a></div>'
        '<div id="recent-calls-list">' + calls_html + '</div>'
        '</div></div>' + period_js)
    return page_template("Сводка", body, "dashboard", generated_at, critical_count)


# ============================================================
# ОТЧЁТ РОПа
# ============================================================

def render_rop_report(calls: List[Dict], stats: Dict, analyses: Dict, generated_at: str) -> str:
    today = format_date(generated_at)
    critical_count = stats["critical_count"]

    # Топ-5 слабых критериев по компании
    criterion_scores = defaultdict(list)
    for a in analyses.values():
        scores = (a.get("analysis") or {}).get("scores", {}) or {}
        for crit, val in scores.items():
            try:
                criterion_scores[crit].append(float(val))
            except (TypeError, ValueError):
                pass
    weak_criteria = []
    for crit, vals in criterion_scores.items():
        if vals:
            avg = sum(vals) / len(vals)
            weak_criteria.append((crit, round(avg, 1), len(vals)))
    weak_criteria.sort(key=lambda x: x[1])
    weak_html = ""
    for crit, avg, count in weak_criteria[:5]:
        cls = score_color(avg)
        weak_html += ('<div class="rop-list-item">'
                      '<div>' + esc(crit) + '</div>'
                      '<div><span class="mini-score ' + cls + '">' + str(avg) + '</span> · ' + str(count) + ' звонков</div>'
                      '</div>')
    if not weak_html:
        weak_html = '<div class="empty">Нет данных</div>'

    # Топ-5 триггеров
    trigger_counter = Counter()
    for a in analyses.values():
        for t in (a.get("analysis") or {}).get("triggers", []) or []:
            trigger_counter[t.get("name", "")] += 1
    top_triggers = trigger_counter.most_common(5)
    triggers_html = ""
    for name, cnt in top_triggers:
        triggers_html += ('<div class="rop-list-item">'
                          '<div>' + esc(name) + '</div>'
                          '<div class="count">' + str(cnt) + '×</div>'
                          '</div>')
    if not triggers_html:
        triggers_html = '<div class="empty">Триггеров не зафиксировано</div>'

    # Менеджеры с просадкой (низкие средние оценки)
    weak_managers = [m for m in stats["managers"] if m.get("avg_score") is not None and m["avg_score"] < 6.5]
    weak_managers.sort(key=lambda x: x["avg_score"])
    weak_m_html = ""
    for m in weak_managers[:5]:
        avg_cls = score_color(m["avg_score"])
        weak_m_html += ('<div class="rop-list-item">'
                        '<div><a href="managers/' + str(m['id']) + '.html">' + esc(m['name']) + '</a> '
                        '<span style="color:var(--text-muted); font-size:11px;">(' + str(m['analyzed']) + ' разборов)</span></div>'
                        '<div><span class="mini-score ' + avg_cls + '">' + str(m['avg_score']) + '</span></div>'
                        '</div>')
    if not weak_m_html:
        weak_m_html = '<div class="empty">Все менеджеры в норме</div>'

    analyzed_today_rop = sum(1 for c in calls if c["activity_id"] in analyses)
    critical_today_rop = sum(
        1 for c in calls
        if c["activity_id"] in analyses
        and (analyses[c["activity_id"]].get("analysis") or {}).get("is_critical")
    )
    body = ('<div class="breadcrumb"><a href="index.html">Главная</a> › Отчёт РОПа</div>'
            '<div class="page-head">'
            '<div><h1>📊 Сводный отчёт РОПа</h1><div class="sub">' + today + ' · аналитика по отделу</div></div>'
            '</div>'
            '<div class="kpi-row">'
            '<div class="kpi"><div class="kpi-label">Всего звонков</div><div class="kpi-value">' + str(stats['total']) + '</div><div class="kpi-hint">за сутки</div></div>'
            '<div class="kpi amber"><div class="kpi-label">Проанализировано</div><div class="kpi-value">' + str(analyzed_today_rop) + '</div><div class="kpi-hint">из сегодняшних</div></div>'
            '<div class="kpi red"><div class="kpi-label">Критичных</div><div class="kpi-value">' + str(critical_today_rop) + '</div><div class="kpi-hint">сегодня</div></div>'
            '<div class="kpi blue"><div class="kpi-label">Менеджеров</div><div class="kpi-value">' + str(stats['managers_count']) + '</div></div>'
            '</div>'
            '<div class="rop-section">'
            '<h3>📉 Топ-5 слабых критериев по отделу</h3>'
            '<div class="label">Где менеджеры в среднем недорабатывают</div>'
            + weak_html +
            '</div>'
            '<div class="rop-section">'
            '<h3>⚠️ Топ-5 частых триггеров</h3>'
            '<div class="label">Какие ошибки повторяются чаще всего</div>'
            + triggers_html +
            '</div>'
            '<div class="rop-section">'
            '<h3>👤 Менеджеры с просадкой (средняя &lt; 6.5)</h3>'
            '<div class="label">С кем нужно поработать индивидуально</div>'
            + weak_m_html +
            '</div>')
    return page_template("Отчёт РОПа", body, "rop", generated_at, critical_count)


# ============================================================
# СПИСОК МЕНЕДЖЕРОВ
# ============================================================

def render_managers_list(stats: Dict, generated_at: str) -> str:
    rows = ""
    for i, m in enumerate(stats["managers"], 1):
        rank_class = ["", "gold", "silver", "bronze"][min(i, 3)] if i <= 3 else ""
        avatar_class = manager_color_class(m["id"])
        if m.get("avg_score") is not None:
            avg_cls = score_color(m["avg_score"])
            avg_html = '<div class="m-avg ' + avg_cls + '">' + str(m["avg_score"]) + '</div>'
            stats_line = ('оценок: <b>' + str(m["analyzed"]) + '</b> из ' + str(m["count"]))
            if m["critical"]:
                stats_line += ' · 🔴 <b>' + str(m["critical"]) + '</b>'
        else:
            avg_html = '<div class="m-avg empty">—</div>'
            stats_line = ('входящих: <b>' + str(m["in"]) + '</b> · исходящих: <b>' + str(m["out"]) + '</b>')
        rows += ('<a href="managers/' + str(m['id']) + '.html" class="row-link">'
                 '<div class="manager-row">'
                 '<div class="rank ' + rank_class + '">' + str(i) + '</div>'
                 '<div class="manager-info">'
                 '<div class="m-avatar ' + avatar_class + '">' + manager_avatar_html(m) + '</div>'
                 '<div><div class="m-name">' + esc(m['name']) + '</div>'
                 '<div class="m-stats">' + stats_line + '</div></div>'
                 '</div>'
                 + avg_html +
                 '<div class="m-count">' + str(m['count']) + '</div>'
                 '</div></a>')
    if not rows:
        rows = '<div class="empty">Менеджеров нет</div>'
    body = ('<div class="breadcrumb"><a href="index.html">Главная</a> › Менеджеры</div>'
            '<div class="page-head"><div><h1>Менеджеры</h1>'
            '<div class="sub">' + COMPANY_NAME + ' · ' + str(stats['managers_count']) + ' · ' + str(stats['total']) + ' звонков</div></div></div>'
            '<div class="panel"><div class="panel-head"><h3>Рейтинг по качеству</h3></div>' + rows + '</div>')
    return page_template("Менеджеры", body, "managers", generated_at, stats["critical_count"])


# ============================================================
# СТРАНИЦА МЕНЕДЖЕРА (С ДНЕВНЫМ ОТЧЁТОМ)
# ============================================================

def render_manager_page(manager: Dict, analyses: Dict, generated_at: str, critical_count: int) -> str:
    calls = sorted(manager["calls"], key=lambda x: x.get("created", ""), reverse=True)
    rows = "".join(render_call_row(c, show_manager=False,
                                   analysis=analyses.get(c["activity_id"]),
                                   base_path="../") for c in calls)
    if not rows:
        rows = '<div class="empty">Звонков нет</div>'
    avatar_class = manager_color_class(manager["id"])

    kpis = ('<div class="kpi-row">'
            '<div class="kpi"><div class="kpi-label">Звонков</div><div class="kpi-value">' + str(manager['count']) + '</div></div>'
            '<div class="kpi green"><div class="kpi-label">Входящих</div><div class="kpi-value">' + str(manager['in']) + '</div></div>'
            '<div class="kpi accent"><div class="kpi-label">Исходящих</div><div class="kpi-value">' + str(manager['out']) + '</div></div>')
    if manager.get("avg_score") is not None:
        kpis += ('<div class="kpi amber"><div class="kpi-label">Средняя оценка</div>'
                 '<div class="kpi-value">' + str(manager['avg_score']) + '</div>'
                 '<div class="kpi-hint">по ' + str(manager['analyzed']) + ' разборам</div></div>')
    if manager.get("critical", 0) > 0:
        kpis += ('<div class="kpi red"><div class="kpi-label">Критичных</div>'
                 '<div class="kpi-value">' + str(manager['critical']) + '</div></div>')
    kpis += "</div>"

    # Дневной отчёт по менеджеру
    daily = compute_manager_daily_report(manager, analyses)
    daily_html = ""
    if daily.get("has_data"):
        # Лучший и худший
        best_html = ""
        if daily["best"]:
            c, a = daily["best"]
            sc = (a.get("analysis") or {}).get("overall_score", 0)
            cls = score_color(sc)
            best_html = ('<div class="dr-callref">'
                         '<span class="sc mini-score ' + cls + '">' + str(sc) + '</span>'
                         '<a href="../calls/' + esc(c['activity_id']) + '.html">'
                         + esc(c.get('client', {}).get('name', '')) + ' · ' + format_time(c.get('created', '')) + '</a>'
                         '</div>')

        worst_html = ""
        if daily["worst"] and daily["worst"] != daily["best"]:
            c, a = daily["worst"]
            sc = (a.get("analysis") or {}).get("overall_score", 0)
            cls = "crit" if (a.get("analysis") or {}).get("is_critical") else score_color(sc)
            worst_html = ('<div class="dr-callref">'
                          '<span class="sc mini-score ' + cls + '">' + str(sc) + '</span>'
                          '<a href="../calls/' + esc(c['activity_id']) + '.html">'
                          + esc(c.get('client', {}).get('name', '')) + ' · ' + format_time(c.get('created', '')) + '</a>'
                          '</div>')

        weak_html = ""
        for crit, avg in daily["weak_criteria"]:
            weak_html += '<div class="dr-list-item">' + esc(crit) + ' — <b>' + str(avg) + '</b>/10</div>'
        if not weak_html:
            weak_html = '<div class="dr-list-item" style="color:var(--brand-olive);">Все критерии выше 6</div>'

        trig_html = ""
        for name, cnt in daily["top_triggers"]:
            trig_html += '<div class="dr-list-item">' + esc(name) + ' — <b>' + str(cnt) + '×</b></div>'
        if not trig_html:
            trig_html = '<div class="dr-list-item" style="color:var(--brand-olive);">Триггеров не зафиксировано</div>'

        daily_html = ('<div class="daily-report">'
                      '<h3>📋 Дневной отчёт по менеджеру</h3>'
                      '<div class="dr-block">'
                      '<div class="label">Проанализировано звонков: ' + str(daily["analyzed"]) + '</div>'
                      '</div>')
        if best_html:
            daily_html += '<div class="dr-block"><div class="label">🏆 Лучший разбор</div>' + best_html + '</div>'
        if worst_html:
            daily_html += '<div class="dr-block"><div class="label">📉 Худший разбор</div>' + worst_html + '</div>'
        daily_html += ('<div class="dr-block"><div class="label">Слабые критерии</div><div class="dr-list">' + weak_html + '</div></div>'
                       '<div class="dr-block"><div class="label">Частые триггеры</div><div class="dr-list">' + trig_html + '</div></div>'
                       '</div>')

    growth_chart = render_growth_chart(manager["id"], manager["name"], manager["calls"], analyses)
    body = ('<div class="breadcrumb"><a href="../index.html">Главная</a> › <a href="../managers.html">Менеджеры</a> › ' + esc(manager['name']) + '</div>'
            '<div class="page-head">'
            '<div style="display:flex; align-items:center; gap:18px;">'
            '<div class="m-avatar ' + avatar_class + '" style="width:64px; height:64px; font-size:20px;">' + manager_avatar_html(manager, base_path="../") + '</div>'
            '<div><h1>' + esc(manager['name']) + '</h1><div class="sub">' + COMPANY_NAME + ' · ' + str(manager['count']) + ' звонков</div></div>'
            '</div></div>'
            + kpis + daily_html + growth_chart +
            '<div class="panel"><div class="panel-head"><h3>Все звонки</h3>'
            '<span class="hint">' + str(len(calls)) + ' штук</span></div>' + rows + '</div>')
    return page_template(manager["name"], body, "managers", generated_at, critical_count, base_path="../")


# ============================================================
# СТРАНИЦА ЗВОНКА (С РУЧНОЙ ПРАВКОЙ)
# ============================================================

def render_call_page(call: Dict, analysis_data: Optional[Dict], generated_at: str, critical_count: int) -> str:
    direction = call.get("direction", "")
    dir_label = direction_label(direction)
    client = call.get("client", {})
    manager = call.get("manager", {})
    bitrix_link = crm_link(call)
    activity_id = call["activity_id"]

    crm_link_html = ""
    if bitrix_link:
        owner_type_label = {"deal": "сделка", "lead": "лид", "contact": "контакт", "company": "компания"}.get(
            call.get("crm", {}).get("owner_type", ""), "запись")
        crm_link_html = ('<a href="' + esc(bitrix_link) + '" target="_blank">' + owner_type_label +
                         ' №' + esc(call.get("crm", {}).get("owner_id", "")) + '</a>')

    has_record = analysis_data is not None
    has_ai = has_record and (analysis_data or {}).get("analysis") is not None
    transcribe_only = has_record and not has_ai  # запись есть, но это короткий звонок без анализа
    analysis = (analysis_data or {}).get("analysis") or {} if has_record else {}
    transcription = (analysis_data or {}).get("transcription", {}) if has_record else {}
    is_crit = analysis.get("is_critical", False)
    crit_reason = analysis.get("critical_reason", "")
    is_corrected = analysis.get("score_corrected", False)
    correction_comment = analysis.get("correction_comment", "")

    # Теги: двухосевая классификация (тип/контекст + цели)
    tags_html = '<span class="call-tag accent">' + esc(dir_label) + '</span>'
    if has_ai:
        cls = analysis.get("classification", {})
        # CRM-контекст (тип звонка/этап воронки)
        if cls.get("crm_context"):
            tags_html += '<span class="call-tag">' + esc(cls["crm_context"]) + '</span>'
        elif cls.get("type"):
            tags_html += '<span class="call-tag">' + esc(cls["type"]) + '</span>'
        if cls.get("funnel_stage"):
            tags_html += '<span class="call-tag">Этап: ' + esc(cls["funnel_stage"]) + '</span>'
        # Цели — отдельная ось
        goals = cls.get("goals") or ([cls["goal"]] if cls.get("goal") else [])
        for g in goals:
            if g:
                tags_html += '<span class="call-tag goal">' + esc(g) + '</span>'
        triggers_count = len(analysis.get("triggers", []) or [])
        if triggers_count > 0:
            tags_html += '<span class="call-tag warning">Триггеров: ' + str(triggers_count) + '</span>'

    critical_badge = ""
    if is_crit:
        critical_badge = '<div class="critical-badge">🔴 СРОЧНО · ' + esc(crit_reason) + '</div>'

    # Оценка с кнопкой правки
    edit_btn_html = ('<button class="edit-btn" id="editScoreBtn" type="button">✏️ Изменить оценку</button>'
                     '<button class="edit-btn" id="reanalyzeBtn" type="button" '
                     'style="margin-top:6px;background:rgba(255,255,255,0.15);" '
                     'title="Запустить анализ заново">🔄 Переанализировать</button>')
    corrected_mark_html = '<span class="score-corrected-mark">ред.</span>' if is_corrected else ''
    if has_ai:
        score = analysis.get("overall_score")
        if score is not None:
            sc_cls = "crit" if is_crit else score_color(score)
            score_html = ('<div class="score-big ' + sc_cls + '">'
                          + corrected_mark_html +
                          '<div class="num">' + esc(score) + '<span class="max">/10</span></div>'
                          '<div class="label">Оценка</div>'
                          + edit_btn_html + '</div>')
        else:
            score_html = '<div class="score-big placeholder"><div class="num">—</div><div class="label">нет</div></div>'
    elif transcribe_only:
        score_html = ('<div class="score-big placeholder" style="background:rgba(124,139,111,0.12);border:1px dashed var(--olive, #7C8B6F);">'
                      '<div class="num" style="font-size:1.1rem;">⏱️</div>'
                      '<div class="label">Короткий звонок<br>без ИИ-анализа</div></div>')
    else:
        score_html = '<div class="score-big placeholder"><div class="num">—</div><div class="label">не анализирован</div></div>'

    # Длительность
    duration_html = ""
    dur = call.get("duration_sec") or transcription.get("duration_sec")
    if dur:
        duration_html = ('<div class="detail"><div class="detail-label">Длительность</div>'
                         '<div class="detail-value">' + format_duration(dur) + '</div></div>')

    card_classes = "call-card critical" if is_crit else "call-card"
    client_name_full = esc(client.get('name', 'Неизвестный клиент'))
    if client.get('company'):
        client_name_full += ' · ' + esc(client.get('company'))

    # Статус сделки
    deal_stage = call.get('crm', {}).get('stage_name') or call.get('crm', {}).get('status') or ''
    deal_stage_id = str(call.get('crm', {}).get('stage_id') or '').lower()
    if deal_stage:
        if any(x in deal_stage_id for x in ['won','win','success','оплач']):
            sc = 'won'; sl = '✅ ' + esc(deal_stage)
        elif any(x in deal_stage_id for x in ['lost','fail','отказ','проигр']):
            sc = 'lost'; sl = '❌ ' + esc(deal_stage)
        elif any(x in deal_stage_id for x in ['new','новый','лид']):
            sc = 'new'; sl = '🔵 ' + esc(deal_stage)
        else:
            sc = 'work'; sl = '🔄 ' + esc(deal_stage)
        deal_status_html = ('<div class="detail"><div class="detail-label">Статус сделки</div>'
                            '<div class="detail-value"><span class="deal-status ' + sc + '">' + sl + '</span></div></div>')
    else:
        deal_status_html = ''


    # Статус сделки
    deal_stage = call.get('crm', {}).get('stage_name') or call.get('crm', {}).get('status') or ''
    deal_stage_id = str(call.get('crm', {}).get('stage_id') or '').lower()
    if deal_stage:
        if any(x in deal_stage_id for x in ['won','win','success','оплач']):
            _dsc = 'won'; _dsl = '✅ ' + esc(deal_stage)
        elif any(x in deal_stage_id for x in ['lost','fail','отказ','проигр']):
            _dsc = 'lost'; _dsl = '❌ ' + esc(deal_stage)
        elif any(x in deal_stage_id for x in ['new','новый','лид']):
            _dsc = 'new'; _dsl = '🔵 ' + esc(deal_stage)
        else:
            _dsc = 'work'; _dsl = '🔄 ' + esc(deal_stage)
        deal_status_html = ('<div class="detail"><div class="detail-label">Статус сделки</div>'
                            '<div class="detail-value"><span class="deal-status ' + _dsc + '">' + _dsl + '</span></div></div>')
    else:
        deal_status_html = ''

    head_html = ('<div class="' + card_classes + '">'
                 '<div class="call-card-head">'
                 '<div>' + critical_badge +
                 '<h2>' + client_name_full + '</h2>'
                 '<div class="call-tags">' + tags_html + '</div>'
                 '</div>'
                 + score_html +
                 '</div>'
                 '<div class="detail-grid">'
                 '<div class="detail"><div class="detail-label">Менеджер</div>'
                 '<div class="detail-value"><a href="../managers/' + str(manager.get('id', 0)) + '.html">' + esc(manager.get('name', '')) + '</a></div></div>'
                 '<div class="detail"><div class="detail-label">Телефон</div>'
                 '<div class="detail-value">' + esc(client.get('phone_masked') or '—') + '</div></div>'
                 '<div class="detail"><div class="detail-label">Время</div>'
                 '<div class="detail-value">' + format_datetime(call.get('created', '')) + '</div></div>'
                 + duration_html +
                 '<div class="detail"><div class="detail-label">Направление</div>'
                 '<div class="detail-value">' + esc(dir_label) + '</div></div>'
                 '<div class="detail"><div class="detail-label">В CRM</div>'
                 '<div class="detail-value">' + (crm_link_html or '—') + '</div></div>'
                 + deal_status_html +
                 '</div></div>')

    # Комментарий к ручной правке
    correction_note_html = ""
    if is_corrected and correction_comment:
        correction_note_html = ('<div class="correction-note">'
                                '<b>✏️ Оценка отредактирована вручную.</b> Комментарий: ' + esc(correction_comment) +
                                '</div>')

    # Аудио-плеер
    audio = call.get("audio") or {}
    public_path = audio.get("public_path")
    bitrix_call_url = call.get("bitrix_url", "") or (bitrix_link if bitrix_link else "")
    audio_html = ""
    # Аудио через прокси /audio/<id> (работает на Render)
    # или через прямой URL Bitrix (для GitHub Pages)
    audio_direct_url = (call.get('audio') or {}).get('url', '')
    use_proxy = __import__('os').environ.get('RENDER', '') or __import__('os').environ.get('USE_AUDIO_PROXY', '')
    if audio_direct_url or public_path:
        bitrix_btn = ""
        if bitrix_call_url:
            bitrix_btn = '<a href="' + esc(bitrix_call_url) + '" target="_blank" class="bitrix-link">Открыть в Bitrix24</a>'
        if use_proxy and audio_direct_url:
            audio_src = '/audio/' + esc(activity_id)
        elif audio_direct_url:
            audio_src = esc(audio_direct_url)
        else:
            audio_src = '../' + esc(public_path)
        audio_html = ('<div class="audio-player">'
                      '<audio controls preload="metadata" src="' + audio_src + '" id="callAudio"></audio>'
                      + bitrix_btn + '</div>')
    elif bitrix_call_url:
        audio_html = ('<div class="audio-missing">'
                      '🎵 Запись в Bitrix24: <a href="' + esc(bitrix_call_url) + '" target="_blank">открыть карточку звонка →</a>'
                      '</div>')

    # Анализ
    if transcribe_only:
        transcript_text_tc = transcription.get("text_with_timecodes") or transcription.get("text", "")
        if transcript_text_tc:
            rest_html = ('<div class="placeholder-panel" style="text-align:left;border-style:dashed;">'
                         '<h3>⏱️ Короткий звонок — без ИИ-анализа</h3>'
                         '<p style="margin-bottom:14px;">Звонок короче 30 секунд: оценка и разбор ИИ для таких '
                         'звонков не делаются, но запись и расшифровка доступны ниже.</p>'
                         '<div class="label">📝 Транскрипт</div>'
                         '<div class="text" style="white-space:pre-wrap;">' + esc(transcript_text_tc) + '</div>'
                         '</div>')
        else:
            rest_html = ('<div class="placeholder-panel">'
                         '<h3>⏱️ Короткий звонок — без ИИ-анализа</h3>'
                         '<p>Звонок короче 30 секунд, транскрипт пуст или не получен.</p>'
                         '</div>')
    elif not has_ai:
        rest_html = ('<div class="placeholder-panel">'
                     '<h3>📝 Транскрипт</h3><p>Звонок ещё не прошёл ИИ-анализ.</p>'
                     '</div>')
    else:
        rest_html = ""

        # Цель звонка + резюме + итог
        summary_parts = []
        if analysis.get("call_goal"):
            summary_parts.append('<div class="label">🎯 Цель звонка</div><div class="text" style="font-weight:600;">' + esc(analysis["call_goal"]) + '</div>')
        if analysis.get("summary"):
            summary_parts.append('<div class="label" style="margin-top:10px;">📋 Резюме</div><div class="text">' + esc(analysis["summary"]) + '</div>')
        if analysis.get("outcome"):
            summary_parts.append('<div class="label" style="margin-top:10px;">✅ Итог</div><div class="text">' + esc(analysis["outcome"]) + '</div>')
        if summary_parts:
            rest_html += ('<div class="ai-summary">' + "".join(summary_parts) + '</div>')

        # Ключевые моменты звонка
        key_moments = analysis.get("key_moments") or []
        if key_moments:
            m_html = ''
            for km in key_moments:
                if not isinstance(km, dict): continue
                ktype = km.get('type','neutral')
                kcolor = {'positive':'var(--brand-olive)','negative':'var(--red)','neutral':'var(--brand-gold)'}.get(ktype,'var(--brand-gold)')
                kicon = {'positive':'✅','negative':'⚠️','neutral':'💡'}.get(ktype,'💡')
                ktc = km.get('time','')
                ktc_html = ('<span class="tc-link" onclick="seekAudio(\'' + esc(ktc) + '\')" style="margin-left:6px;">' + esc(ktc) + '</span>') if ktc else ''
                kdetail = esc(km.get('detail',''))
                m_html += ('<div style="padding:10px 0;border-bottom:1px solid var(--border-soft);font-family:-apple-system,sans-serif;">'
                           '<div style="font-size:13px;color:' + kcolor + ';font-weight:600;">' + kicon + ' ' + esc(km.get('text','')) + ktc_html + '</div>'
                           + ('<div style="font-size:12px;color:var(--text-muted);margin-top:3px;">' + kdetail + '</div>' if kdetail else '') +
                           '</div>')
            rest_html += ('<div class="panel" style="margin-bottom:16px;">'
                          '<div class="panel-head"><h3>⚡ Ключевые моменты</h3>'
                          '<span class="hint">' + str(len(key_moments)) + ' эпизодов</span></div>'
                          '<div style="padding:0 20px;">' + m_html + '</div></div>')


        # Объяснение оценки
        if analysis.get("score_explanation"):
            rest_html += ('<div class="recommendation" style="border-color:var(--brand-medium);">'
                          '<div class="label">💬 Почему такая оценка</div>'
                          '<div class="text">' + esc(analysis["score_explanation"]) + '</div>'
                          '</div>')

        # Основная проблема (старый формат)
        if analysis.get("main_problem"):
            rest_html += ('<div class="main-problem">'
                          '<div class="label">⚠️ Основная проблема</div>'
                          '<div class="text">' + esc(analysis["main_problem"]) + '</div>'
                          '</div>')

        # Сильные / Слабые стороны
        # Поддержка и нового (strengths/improvements) и старого (strengths/weaknesses) формата
        strengths_raw = analysis.get("strengths") or []
        improvements_raw = analysis.get("improvements") or []
        weaknesses_raw = analysis.get("weaknesses") or []

        # Нормализуем: новый формат strengths — список dict {text, time}, старый — список строк
        def norm_list(items):
            result = []
            for item in items:
                if isinstance(item, dict):
                    t = item.get("text", "")
                    tc = item.get("time", "") or item.get("quote", "")
                    result.append((t, tc))
                elif isinstance(item, str):
                    result.append((item, ""))
            return result

        strengths = norm_list(strengths_raw)
        improvements = norm_list(improvements_raw) or norm_list(weaknesses_raw)

        if strengths or improvements:
            sw_html = '<div class="strengths-weaknesses">'
            if strengths:
                st_items = "".join(
                    '<li>' + esc(t) + (('<span class="timecode" style="margin-left:6px;font-size:10px;color:var(--brand-medium);">' + esc(tc) + '</span>') if tc else '') + '</li>'
                    for t, tc in strengths
                )
                sw_html += ('<div class="sw-box strong">'
                            '<div class="label">✅ Сильные стороны</div>'
                            '<ul>' + st_items + '</ul></div>')
            if improvements:
                wk_items = "".join(
                    '<li>' + esc(t) + (('<span class="timecode" style="margin-left:6px;font-size:10px;color:var(--brand-medium);">' + esc(tc) + '</span>') if tc else '') + '</li>'
                    for t, tc in improvements
                )
                sw_html += ('<div class="sw-box weak">'
                            '<div class="label">⚠️ Что улучшить</div>'
                            '<ul>' + wk_items + '</ul></div>')
            sw_html += '</div>'
            rest_html += sw_html

        # Цитаты
        key_quotes = analysis.get("key_quotes") or ([analysis.get("key_quote")] if analysis.get("key_quote") else [])
        for kq in key_quotes:
            if not kq:
                continue
            time_str = kq.get("time", "")
            who = "клиент" if kq.get("speaker") == "client" else "менеджер"
            time_meta = "— " + esc(who)
            if time_str:
                time_meta += " · " + esc(time_str)
            rest_html += ('<div class="ai-key-quote">' + esc(kq.get("text", "")) +
                          '<span class="meta">' + time_meta + '</span></div>')

        # Дата следующего контакта
        next_contact = analysis.get("next_contact") or {}
        nc_text = ""
        if isinstance(next_contact, dict):
            # Новый формат: date_or_period, time, initiator, context
            if next_contact.get("date_or_period"):
                nc_text = "📅 " + esc(next_contact["date_or_period"])
                if next_contact.get("time"):
                    nc_text += " в " + esc(next_contact["time"])
                if next_contact.get("context"):
                    nc_text += " — " + esc(next_contact["context"])
                if next_contact.get("initiator"):
                    nc_text += ' <span style="font-size:11px;opacity:0.7;">(' + esc(next_contact["initiator"]) + ' звонит)</span>'
            # Старый формат
            elif next_contact.get("date"):
                nc_text = "📅 " + esc(next_contact["date"])
                if next_contact.get("action"):
                    nc_text += " — " + esc(next_contact["action"])
            elif next_contact.get("text"):
                nc_text = esc(next_contact["text"])
        elif isinstance(next_contact, str) and next_contact:
            nc_text = esc(next_contact)
        if nc_text:
            # Проверяем есть ли дело в CRM
            has_crm_task = call.get('crm', {}).get('has_next_activity', False)
            crm_task_date = call.get('crm', {}).get('next_activity_date', '')
            if has_crm_task and crm_task_date:
                crm_check = (' <span style="color:var(--brand-olive);font-size:11px;">✅ Дело в CRM: ' + esc(crm_task_date[:10]) + '</span>')
            elif has_crm_task:
                crm_check = ' <span style="color:var(--brand-olive);font-size:11px;">✅ Дело создано в CRM</span>'
            else:
                crm_check = ' <span style="color:var(--brand-terracotta);font-size:11px;">⚠️ Дела в CRM нет</span>'
            rest_html += ('<div class="next-contact">'
                          '<div class="label">📞 Следующий контакт</div>'
                          '<div class="text">' + nc_text + crm_check + '</div></div>')

        # Использованные скрипты
        scripts_used = analysis.get("scripts_used") or []
        if scripts_used:
            tags = "".join('<span class="tag">' + esc(s) + '</span>' for s in scripts_used)
            rest_html += ('<div class="scripts-used"><b>Сравнение со скриптами:</b> ' + tags + '</div>')

        # Соответствие этапам скриптов
        scripts_alignment = analysis.get("scripts_alignment") or []
        if scripts_alignment:
            sa_rows = ""
            for stage in scripts_alignment:
                stage_name = esc(stage.get("stage", ""))
                status = stage.get("status", "miss")
                status_cls = {"full": "full", "partial": "partial", "miss": "miss"}.get(status, "miss")
                status_label = {"full": "✓ Соблюдено", "partial": "± Частично", "miss": "✗ Пропущено"}.get(status, "—")
                sa_rows += ('<div class="sa-row">'
                            '<div class="sa-stage">' + stage_name + '</div>'
                            '<div class="sa-status ' + status_cls + '">' + status_label + '</div>'
                            '</div>')
            rest_html += ('<div class="scripts-alignment">'
                          '<div class="label">📑 Соответствие этапам скрипта</div>'
                          + sa_rows + '</div>')

        # Транскрипт
        ts = analysis.get("transcript_split") or []
        if ts:
            msgs = ""
            for m in ts:
                who = m.get("speaker", "")
                who_label = "менеджер" if who == "manager" else "клиент"
                time_str = esc(m.get("time", ""))
                msgs += ('<div class="msg">'
                         '<div class="msg-time">' + time_str + '</div>'
                         '<div class="msg-body">'
                         '<div class="msg-who ' + esc(who) + '">' + who_label + '</div>'
                         '<div class="msg-text">' + esc(m.get('text', '')) + '</div>'
                         '</div></div>')
            transcript_js = (
                '<script>'
                'function seekAudio(tc){'
                'var a=document.getElementById("callAudio")||document.querySelector("audio");'
                'if(!a)return;'
                'var p=tc.split(":"),s=parseInt(p[0])*60+(p[1]?parseInt(p[1]):0);'
                'a.currentTime=s;a.play();}'
                'function searchTranscript(){'
                'var q=document.getElementById("tsSearch").value.toLowerCase();'
                'document.querySelectorAll(".msg").forEach(function(m){'
                'var t=m.querySelector(".msg-text");'
                'if(!t)return;'
                'var orig=t.dataset.orig||t.textContent;'
                't.dataset.orig=orig;'
                'if(!q){t.innerHTML=orig;m.classList.remove("highlight");return;}'
                'var idx=orig.toLowerCase().indexOf(q);'
                'if(idx>=0){'
                't.innerHTML=orig.slice(0,idx)+"<mark class=\\"highlight-text\\">"+orig.slice(idx,idx+q.length)+"</mark>"+orig.slice(idx+q.length);'
                'm.classList.add("highlight");m.scrollIntoView({block:"nearest"});'
                '}else{t.innerHTML=orig;m.classList.remove("highlight");}});}'
                '</script>')
            rest_html += ('<div class="transcript-panel">'
                          '<div class="transcript-head"><h3>📝 Транскрипт</h3>'
                          '<span class="hint">' + str(len(ts)) + ' реплик · ' + format_duration(transcription.get("duration_sec")) + '</span>'
                          '</div>'
                          '<div style="padding:8px 20px 0;">'
                          '<input type="text" id="tsSearch" class="transcript-search" placeholder="🔍 Поиск по тексту разговора..." oninput="searchTranscript()">'
                          '</div>'
                          '<div class="transcript-body">' + msgs + '</div>'
                          '</div>' + transcript_js)

        # Оценки по 17 критериям + веса
        scores = analysis.get("scores") or {}
        weights = analysis.get("weights") or {}
        if scores:
            score_rows = ""
            # Сортируем по весу (важные сверху)
            sorted_criteria = sorted(
                scores.items(),
                key=lambda x: -float(weights.get(x[0], 0)) if weights else 0
            )
            for crit, val in sorted_criteria:
                try:
                    val_f = float(val)
                except (TypeError, ValueError):
                    val_f = 0
                cls = score_color(val_f)
                width = max(0, min(100, int(val_f * 10)))
                weight = weights.get(crit, 0)
                weight_str = ""
                if weight:
                    try:
                        weight_str = str(int(float(weight) * 100)) + "%"
                    except (TypeError, ValueError):
                        weight_str = ""
                score_rows += ('<div class="score-row">'
                               '<div class="score-name">' + esc(crit) + '</div>'
                               '<div class="score-bar"><div class="score-fill ' + cls + '" style="width:' + str(width) + '%;"></div></div>'
                               '<div class="score-val ' + cls + '">' + str(val_f) + '</div>'
                               '<div class="score-weight">' + weight_str + '</div>'
                               '</div>')
            rest_html += ('<div class="scores-panel">'
                          '<div class="panel-head"><h3>📊 Оценка по 17 критериям</h3>'
                          '<span class="hint">по весам ТЗ</span></div>'
                          + score_rows + '</div>')

        # Низкие баллы с цитатами и рекомендациями
        low_details = analysis.get("low_score_details") or []
        for ld in low_details:
            crit = esc(ld.get("criterion", ""))
            sc_val = ld.get("score", "")
            problem = esc(ld.get("problem", ""))
            quote = ld.get("quote", "")
            t = ld.get("time", "")
            reco = esc(ld.get("recommendation", ""))
            quote_html = ""
            if quote:
                qt = "« " + esc(quote) + " »"
                if t:
                    qt += ' <span class="timecode" style="font-size:10px;color:var(--brand-medium);">' + esc(t) + '</span>'
                quote_html = '<div class="lsq">' + qt + '</div>'
            reco_html = ""
            if reco:
                reco_html = '<div class="lsr"><b>Рекомендация:</b> ' + reco + '</div>'
            rest_html += ('<div class="low-score-block">'
                          '<div class="lsh">' + crit + ' — ' + str(sc_val) + '/10</div>'
                          '<div>' + problem + '</div>'
                          + quote_html + reco_html +
                          '</div>')

        # Триггеры
        triggers = analysis.get("triggers") or []
        if triggers:
            items = ""
            for t in triggers:
                time_str = t.get("time", "")
                tc = ('<span class="timecode">' + esc(time_str) + '</span>') if time_str else ""
                desc = t.get("description", "")
                desc_html = ('<div class="sub">' + esc(desc) + '</div>') if desc else ""
                items += ('<div class="trigger-row">'
                          '<div class="trigger-marker"></div>'
                          '<div class="trigger-text">' + esc(t.get('name', '')) + tc + desc_html + '</div>'
                          '</div>')
            rest_html += ('<div class="trigger-list">'
                          '<div class="panel-head"><h3>⚠️ Триггеры</h3>'
                          '<span class="hint">' + str(len(triggers)) + ' шт</span></div>'
                          + items + '</div>')

        # Флаги (новый формат)
        flags = analysis.get("flags") or {}
        flag_items = []
        if flags.get("missed_deal"):
            flag_items.append('🚨 <b>Упущенная сделка</b> — клиент был готов купить, но менеджер не закрыл')
        if flags.get("no_next_step"):
            flag_items.append('⏰ <b>Нет следующего шага</b> — важный звонок завершился без договорённости')
        if flag_items:
            rest_html += ('<div class="main-problem">'
                          '<div class="label">🚩 Флаги</div>'
                          + "".join('<div class="text" style="margin-top:6px;">' + f + '</div>' for f in flag_items) +
                          '</div>')

        # Рекомендация
        if analysis.get("recommendation"):
            rest_html += ('<div class="recommendation">'
                          '<div class="label">💡 Рекомендация менеджеру</div>'
                          '<div class="text">' + esc(analysis["recommendation"]) + '</div>'
                          '</div>')

    # Модалка для ручной правки
    modal_html = build_edit_modal(activity_id, analysis.get("overall_score", ""))

    # AI чат-виджет
    ai_chat_html = ""
    if has_ai:
        ai_chat_html = build_ai_chat_widget(call, analysis)

    # Мета-тег с ключом API для чата (читается из env через шаблон page_template)
    proxy_url = __import__("os").environ.get("PROXY_URL", "")
    meta_key = '<meta name="proxy-url" content="' + esc(proxy_url) + '">'
    body = ('<div class="breadcrumb"><a href="../index.html">Главная</a> › <a href="../all-calls.html">Звонки</a> › Звонок №' + esc(activity_id) + '</div>'
            + head_html + correction_note_html + audio_html + rest_html + modal_html + ai_chat_html)
    return page_template("Звонок №" + str(activity_id), body, "calls", generated_at, critical_count, base_path="../", extra_head=meta_key)


def build_edit_modal(activity_id: str, current_score) -> str:
    """Модальное окно для правки оценки."""
    safe_id = esc(activity_id)
    safe_score = esc(current_score) if current_score else ""
    return ('<div class="modal-backdrop" id="editModal">'
            '<div class="modal">'
            '<h3>Изменить оценку звонка №' + safe_id + '</h3>'
            '<label>Новая оценка (0–10)</label>'
            '<input type="number" step="0.1" min="0" max="10" id="newScore" value="' + safe_score + '">'
            '<label>Комментарий (опционально)</label>'
            '<textarea id="newComment" placeholder="Почему изменена оценка..."></textarea>'
            '<div class="instruction" style="background:var(--blue-soft);border-color:var(--blue);">'
            '💾 Оценка сохранится мгновенно и обновится на странице.'
            '</div>'
            '<div class="actions">'
            '<button class="btn-cancel" id="cancelEditBtn" type="button">Отмена</button>'
            '<button class="btn-save" id="saveScoreBtn" type="button">💾 Сохранить</button>'
            '</div>'
            '</div></div>'
            '<script>'
            '(function() {'
            '  var ACTIVITY_ID = "' + safe_id + '";'
            '  function openEditModal() { var m = document.getElementById("editModal"); if (m) m.classList.add("active"); }'
            '  function closeEditModal() { var m = document.getElementById("editModal"); if (m) m.classList.remove("active"); }'
            '  function copyCorrection() {'
            '    var score = parseFloat(document.getElementById("newScore").value);'
            '    var comment = document.getElementById("newComment").value;'
            '    var btn = document.getElementById("saveScoreBtn");'
            '    btn.textContent = "Сохраняю..."; btn.disabled = true;'
            '    fetch("/api/correct", {'
            '      method: "POST",'
            '      headers: {"Content-Type": "application/json"},'
            '      body: JSON.stringify({activity_id: ACTIVITY_ID, score: score, comment: comment})'
            '    }).then(function(r) { return r.json(); }).then(function(d) {'
            '      if (d.ok) {'
            '        alert("✅ Оценка сохранена!");'
            '        closeEditModal();'
            '        location.reload();'
            '      } else { alert("Ошибка: " + JSON.stringify(d)); }'
            '    }).catch(function(e) {'
            '      var obj = {}; obj[ACTIVITY_ID] = {overall_score: score, comment: comment};'
            '      navigator.clipboard.writeText(JSON.stringify(obj, null, 2));'
            '      alert("API недоступен. JSON скопирован в буфер.");'
            '    }).finally(function() { btn.textContent = "💾 Сохранить"; btn.disabled = false; });'
            '  }'
            '  function reanalyzeCall() {'
            '    if (!confirm("Запустить повторный анализ этого звонка? Это займёт пару минут.")) return;'
            '    var btn = document.getElementById("reanalyzeBtn");'
            '    btn.textContent = "Запускаю..."; btn.disabled = true;'
            '    fetch("/calls/" + ACTIVITY_ID + "/reanalyze", { method: "POST" })'
            '      .then(function(r) { return r.json(); })'
            '      .then(function(d) {'
            '        if (d.ok) { alert("✅ " + (d.message || "Анализ запущен")); }'
            '        else { alert("Ошибка: " + (d.error || JSON.stringify(d))); }'
            '      })'
            '      .catch(function(e) { alert("Ошибка запроса: " + e.message); })'
            '      .finally(function() { btn.textContent = "🔄 Переанализировать"; btn.disabled = false; });'
            '  }'
            '  function wire() {'
            '    var editBtn = document.getElementById("editScoreBtn");'
            '    var cancelBtn = document.getElementById("cancelEditBtn");'
            '    var saveBtn = document.getElementById("saveScoreBtn");'
            '    var reanalyzeBtn = document.getElementById("reanalyzeBtn");'
            '    if (editBtn) editBtn.addEventListener("click", openEditModal);'
            '    if (cancelBtn) cancelBtn.addEventListener("click", closeEditModal);'
            '    if (saveBtn) saveBtn.addEventListener("click", copyCorrection);'
            '    if (reanalyzeBtn) reanalyzeBtn.addEventListener("click", reanalyzeCall);'
            '  }'
            '  if (document.readyState === "loading") {'
            '    document.addEventListener("DOMContentLoaded", wire);'
            '  } else {'
            '    wire();'
            '  }'
            '  window.openEditModal = openEditModal;'
            '  window.closeEditModal = closeEditModal;'
            '})();'
            '</script>')


# ============================================================
# ВСЕ ЗВОНКИ
# ============================================================

def render_all_calls(calls: List[Dict], analyses: Dict, generated_at: str, critical_count: int) -> str:
    sorted_calls = sorted(calls, key=lambda x: x.get("created", ""), reverse=True)
    rows = "".join(render_call_row(c, analysis=analyses.get(c["activity_id"])) for c in sorted_calls)
    if not rows:
        rows = '<div class="empty">Звонков нет</div>'

    script_block = """
<script>
var currentFilter = 'all';
function setFilter(f, btn) {
  currentFilter = f;
  document.querySelectorAll('.filter-btn').forEach(function(b){ b.classList.remove('active'); });
  btn.classList.add('active');
  filterCalls();
}
function filterCalls() {
  var q = document.getElementById('searchInput').value.toLowerCase();
  var rows = document.querySelectorAll('#callsList .row-link');
  var v = 0;
  rows.forEach(function(r) {
    var t = r.textContent.toLowerCase();
    var d = r.querySelector('.call-direction').classList;
    var matchText = !q || t.indexOf(q) !== -1;
    var matchFilter = currentFilter === 'all' || 
      (currentFilter === 'incoming' && d.contains('in')) ||
      (currentFilter === 'outgoing' && d.contains('out')) ||
      (currentFilter === 'critical' && r.classList.contains('critical')) ||
      (currentFilter === 'analyzed' && r.querySelector('.mini-score')) ||
      (currentFilter === 'no_next_step' && r.dataset.nonextstep === '1');
    var show = matchText && matchFilter;
    r.style.display = show ? '' : 'none';
    if (show) v++;
  });
  document.getElementById('visibleCount').textContent = v + ' видно';
}
</script>"""

    body = ('<div class="breadcrumb"><a href="index.html">Главная</a> › Все звонки</div>'
            '<div class="page-head"><div><h1>Все звонки</h1>'
            '<div class="sub">' + COMPANY_NAME + ' · ' + str(len(calls)) + ' штук</div></div></div>'
            '<input type="text" class="search-box" id="searchInput" placeholder="🔍 Поиск..." oninput="filterCalls()">'
            '<div class="filter-row">'
            '<button class="filter-btn active" onclick="setFilter(\'all\', this)">Все</button>'
            '<button class="filter-btn" onclick="setFilter(\'incoming\', this)">Входящие</button>'
            '<button class="filter-btn" onclick="setFilter(\'outgoing\', this)">Исходящие</button>'
            '<button class="filter-btn" onclick="setFilter(\'critical\', this)">🔴 Критичные</button>'
            '<button class="filter-btn" onclick="setFilter(\'analyzed\', this)">✅ С анализом</button>'
            '<button class="filter-btn" onclick="setFilter(\'no_next_step\', this)">⏰ Без след. шага</button>'
            '</div>'
            '<div class="panel"><div class="panel-head"><h3>Список</h3>'
            '<span class="hint" id="visibleCount">' + str(len(calls)) + ' видно</span></div>'
            '<div id="callsList">' + rows + '</div></div>'
            + script_block)
    return page_template("Все звонки", body, "calls", generated_at, critical_count)


# ============================================================
# КРИТИЧНЫЕ
# ============================================================

def render_critical_page(calls: List[Dict], analyses: Dict, generated_at: str, critical_count: int) -> str:
    critical_calls = []
    for c in calls:
        a = analyses.get(c["activity_id"])
        if a and (a.get("analysis") or {}).get("is_critical"):
            critical_calls.append((c, a))
    critical_calls.sort(key=lambda x: x[0].get("created", ""), reverse=True)

    body_top = ('<div class="breadcrumb"><a href="index.html">Главная</a> › Срочно</div>'
                '<div class="page-head"><div><h1>🔴 Срочно к РОПу</h1>'
                '<div class="sub">Критичные звонки, требующие немедленного внимания</div></div></div>')

    if not critical_calls:
        body = body_top + '<div class="notice success"><b>✓ Критичных звонков нет.</b></div>'
    else:
        rows = ""
        for c, a in critical_calls:
            an = a["analysis"]
            score = an.get("overall_score", 0)
            reason = an.get("critical_reason", "")
            triggers = an.get("triggers", [])
            trig_list = ", ".join(t.get("name", "") for t in triggers[:3])
            if len(triggers) > 3:
                trig_list += " + ещё " + str(len(triggers) - 3)
            duration_html = ""
            if c.get("duration_sec"):
                duration_html = '<div class="call-duration">' + format_duration(c.get("duration_sec")) + '</div>'
            dir_icon = "↓" if c.get('direction') == 'incoming' else "↑"
            dir_cls = "in" if c.get('direction') == 'incoming' else "out"
            rows += ('<a href="calls/' + esc(c['activity_id']) + '.html" class="row-link critical">'
                     '<div class="call-row">'
                     '<div class="call-direction ' + dir_cls + '">' + dir_icon + '</div>'
                     '<div class="call-info">'
                     '<div class="call-client">' + esc(c.get('client', {}).get('name', '')) + ' · ' + esc(c.get('manager', {}).get('name', '')) + '</div>'
                     '<div class="call-meta"><b>' + esc(reason) + '.</b> ' + esc(trig_list) + '</div>'
                     '</div>'
                     + duration_html +
                     '<div class="call-time">' + format_time(c.get('created', '')) + '</div>'
                     '<div class="mini-score crit">' + esc(score) + '</div>'
                     '</div></a>')
        body = body_top + ('<div class="panel"><div class="panel-head"><h3>Найдено критичных</h3>'
                           '<span class="hint">' + str(len(critical_calls)) + ' звонков</span></div>'
                           + rows + '</div>')
    return page_template("Срочно", body, "critical", generated_at, critical_count)


# ============================================================
# ТРИГГЕРЫ
# ============================================================

def render_triggers_page(calls: List[Dict], analyses: Dict, generated_at: str, critical_count: int) -> str:
    triggers_data = defaultdict(list)
    for c in calls:
        a = analyses.get(c["activity_id"])
        if a:
            for t in (a.get("analysis") or {}).get("triggers", []) or []:
                triggers_data[t.get("name", "")].append((c, a, t))

    body_top = ('<div class="breadcrumb"><a href="index.html">Главная</a> › Триггеры</div>'
                '<div class="page-head"><div><h1>⚠️ Триггеры</h1>'
                '<div class="sub">Группировка по типу ошибок</div></div></div>')

    if not triggers_data:
        body = body_top + ('<div class="placeholder-panel">'
                           '<h3>⏳ Триггеры пока не зафиксированы</h3>'
                           '<p>После анализа звонков сюда попадут все срабатывания.</p></div>')
    else:
        sections = ""
        for trig_name, items in sorted(triggers_data.items(), key=lambda x: -len(x[1])):
            rows = ""
            for c, a, t in items[:10]:
                an = a["analysis"]
                score = an.get("overall_score", 0)
                cls = "crit" if an.get("is_critical") else score_color(score)
                duration_html = ""
                if c.get("duration_sec"):
                    duration_html = '<div class="call-duration">' + format_duration(c.get("duration_sec")) + '</div>'
                dir_icon = "↓" if c.get('direction') == 'incoming' else "↑"
                dir_cls = "in" if c.get('direction') == 'incoming' else "out"
                bitrix_url = crm_link(c)
                bitrix_btn_html = (
                    '<a href="' + esc(bitrix_url) + '" target="_blank" rel="noopener" '
                    'onclick="event.stopPropagation()" '
                    'style="margin-left:10px;font-size:11px;color:var(--brand-medium);white-space:nowrap;text-decoration:underline;">'
                    'Bitrix24 →</a>'
                ) if bitrix_url else ''
                rows += ('<a href="calls/' + esc(c['activity_id']) + '.html" class="row-link">'
                         '<div class="call-row">'
                         '<div class="call-direction ' + dir_cls + '">' + dir_icon + '</div>'
                         '<div class="call-info">'
                         '<div class="call-client">' + esc(c.get('client', {}).get('name', '')) + ' · ' + esc(c.get('manager', {}).get('name', '')) + bitrix_btn_html + '</div>'
                         '<div class="call-meta">' + esc(t.get('description', '')) + '</div>'
                         '</div>'
                         + duration_html +
                         '<div class="call-time">' + format_time(c.get('created', '')) + '</div>'
                         '<div class="mini-score ' + cls + '">' + esc(score) + '</div>'
                         '</div></a>')
            sections += ('<div class="panel">'
                         '<div class="panel-head"><h3>' + esc(trig_name) + '</h3>'
                         '<span class="hint">' + str(len(items)) + ' срабатываний</span></div>'
                         + rows + '</div>')
        body = body_top + sections
    return page_template("Триггеры", body, "triggers", generated_at, critical_count)


# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ ДАННЫЕ ДЛЯ НОВЫХ СТРАНИЦ
# ============================================================

def get_weekly_scores(manager_id: int, calls: List[Dict], analyses: Dict) -> List[Dict]:
    """Возвращает среднюю оценку по неделям для динамики роста."""
    from collections import defaultdict
    week_scores = defaultdict(list)
    for c in calls:
        if c.get("manager", {}).get("id") != manager_id:
            continue
        a = analyses.get(c["activity_id"])
        if not a:
            continue
        score = (a.get("analysis") or {}).get("overall_score")
        if score is None:
            continue
        try:
            dt = datetime.fromisoformat(c.get("created", ""))
            week_key = dt.strftime("%Y-W%W")
            week_scores[week_key].append(float(score))
        except Exception:
            pass
    result = []
    for week, scores in sorted(week_scores.items()):
        result.append({"week": week, "avg": round(sum(scores)/len(scores), 1), "count": len(scores)})
    return result


def get_objections_stats(analyses: Dict) -> List[Dict]:
    """Частота возражений из текста триггеров и анализов."""
    objection_keywords = [
        "дорого", "подумаю", "не сейчас", "посоветуюсь", "не уверен",
        "нет бюджета", "уже есть", "не актуально", "перезвоните", "занят",
        "не интересно", "конкурент", "сравниваем", "руководство решает",
    ]
    counter = Counter()
    for a_data in analyses.values():
        an = a_data.get("analysis") or {}
        text_sources = []
        # из transcript_split
        for msg in an.get("transcript_split", []) or []:
            if msg.get("speaker") == "client":
                text_sources.append(msg.get("text", "").lower())
        # из key_quotes
        for kq in an.get("key_quotes", []) or []:
            text_sources.append(kq.get("text", "").lower())
        # из improvements
        for imp in an.get("improvements", []) or []:
            if isinstance(imp, dict):
                text_sources.append(imp.get("text", "").lower())
                text_sources.append(imp.get("quote", "").lower())
        combined = " ".join(text_sources)
        for kw in objection_keywords:
            if kw in combined:
                counter[kw] += 1
    return [{"name": k, "count": v} for k, v in counter.most_common(10)]


def get_best_calls(calls: List[Dict], analyses: Dict, n: int = 10) -> List[tuple]:
    """Лучшие звонки за всё время по оценке."""
    scored = []
    for c in calls:
        a = analyses.get(c["activity_id"])
        if not a:
            continue
        score = (a.get("analysis") or {}).get("overall_score")
        if score is None:
            continue
        try:
            scored.append((float(score), c, a))
        except (TypeError, ValueError):
            pass
    scored.sort(key=lambda x: -x[0])
    return [(c, a, sc) for sc, c, a in scored[:n]]


# ============================================================
# СРАВНЕНИЕ МЕНЕДЖЕРОВ
# ============================================================

def render_compare_page(stats: Dict, calls: List[Dict], analyses: Dict, generated_at: str) -> str:
    managers = [m for m in stats["managers"] if m.get("analyzed", 0) > 0]
    crit = stats["critical_count"]

    if len(managers) < 2:
        body = ('<div class="breadcrumb"><a href="index.html">Главная</a> › Сравнение</div>'
                '<div class="page-head"><div><h1>📊 Сравнение менеджеров</h1></div></div>'
                '<div class="notice">Нужно минимум 2 менеджера с анализами для сравнения.</div>')
        return page_template("Сравнение", body, "compare", generated_at, crit)

    # Цвета для менеджеров
    colors = ["var(--brand-olive)", "var(--brand-terracotta)", "var(--brand-gold)", "var(--blue)"]

    # KPI карточки по каждому менеджеру
    kpi_html = '<div class="kpi-row">'
    for i, m in enumerate(managers):
        color = colors[i % len(colors)]
        avg = m.get("avg_score", 0) or 0
        kpi_html += (f'<div class="kpi" style="border-top-color:{color};">'
                     f'<div class="kpi-label">{esc(m["name"])}</div>'
                     f'<div class="kpi-value" style="color:{color};">{avg}</div>'
                     f'<div class="kpi-hint">{m["analyzed"]} разборов · {m["critical"]} критичных</div>'
                     f'</div>')
    kpi_html += '</div>'

    # Бар-чарт сравнения оценок через SVG
    bar_width = 80
    bar_gap = 40
    chart_w = len(managers) * (bar_width + bar_gap) + bar_gap
    chart_h = 200
    bars_svg = f'<svg viewBox="0 0 {chart_w} {chart_h + 40}" xmlns="http://www.w3.org/2000/svg" style="width:100%;max-width:500px;display:block;margin:0 auto;">'
    for i, m in enumerate(managers):
        x = bar_gap + i * (bar_width + bar_gap)
        avg = float(m.get("avg_score") or 0)
        bar_h = int(avg / 10 * chart_h)
        y = chart_h - bar_h
        color_idx = ["#7C8B6F", "#B86B4F", "#C9A961", "#4A6B8A"][i % 4]
        bars_svg += (f'<rect x="{x}" y="{y}" width="{bar_width}" height="{bar_h}" fill="{color_idx}" rx="4"/>'
                     f'<text x="{x + bar_width//2}" y="{y - 6}" text-anchor="middle" font-size="14" font-weight="bold" fill="{color_idx}">{avg}</text>'
                     f'<text x="{x + bar_width//2}" y="{chart_h + 20}" text-anchor="middle" font-size="11" fill="var(--text-secondary)">{esc(m["name"].split()[0])}</text>')
    bars_svg += '</svg>'

    def smart_truncate(text, max_len=90):
        """Обрезает текст по границе слова, не разрывая слово на середине."""
        if not text or len(text) <= max_len:
            return text
        cut = text[:max_len].rsplit(" ", 1)[0]
        return cut.rstrip(",.;:") + "…"

    # Сравнительная таблица частых проблем
    manager_issues = {}
    for m in managers:
        issues = Counter()
        for c in m["calls"]:
            a = analyses.get(c["activity_id"])
            if a:
                for imp in (a.get("analysis") or {}).get("improvements", []) or []:
                    if isinstance(imp, dict) and imp.get("text"):
                        issues[smart_truncate(imp["text"])] += 1
                for t in (a.get("analysis") or {}).get("triggers", []) or []:
                    issues[smart_truncate(t.get("name", ""))] += 1
        manager_issues[m["id"]] = issues.most_common(5)

    issues_html = '<div class="panel"><div class="panel-head"><h3>Топ проблем по каждому менеджеру</h3></div>'
    for i, m in enumerate(managers):
        color = colors[i % len(colors)]
        issues_html += (f'<div style="padding:14px 20px;border-bottom:1px solid var(--border-soft);">'
                        f'<div style="font-weight:600;color:{color};margin-bottom:8px;font-family:-apple-system,sans-serif;">{esc(m["name"])}</div>')
        for issue, cnt in manager_issues.get(m["id"], []):
            issues_html += (f'<div class="rop-list-item">'
                            f'<div style="font-size:13px;">{esc(issue)}</div>'
                            f'<div class="count">{cnt}×</div></div>')
        if not manager_issues.get(m["id"]):
            issues_html += '<div class="empty">Проблем не зафиксировано</div>'
        issues_html += '</div>'
    issues_html += '</div>'

    # Динамика по дням
    from collections import defaultdict
    day_data = defaultdict(lambda: defaultdict(list))
    for c in calls:
        a = analyses.get(c["activity_id"])
        if not a:
            continue
        score = (a.get("analysis") or {}).get("overall_score")
        mid = c.get("manager", {}).get("id")
        if score is None or mid is None:
            continue
        try:
            dt = datetime.fromisoformat(c.get("created", ""))
            day = dt.strftime("%d.%m")
            day_data[day][mid].append(float(score))
        except Exception:
            pass

    days = sorted(day_data.keys())
    dynamics_html = ""
    if len(days) >= 2:
        dynamics_html = '<div class="panel"><div class="panel-head"><h3>📈 Динамика по дням</h3></div><div style="padding:16px 20px;overflow-x:auto;">'
        table = '<table style="width:100%;border-collapse:collapse;font-family:-apple-system,sans-serif;font-size:13px;">'
        table += '<tr><th style="text-align:left;padding:6px 12px;color:var(--text-muted);font-weight:600;">Менеджер</th>'
        for d in days:
            table += f'<th style="padding:6px 12px;color:var(--text-muted);font-weight:600;text-align:center;">{esc(d)}</th>'
        table += '</tr>'
        for i, m in enumerate(managers):
            color = colors[i % len(colors)]
            table += f'<tr><td style="padding:8px 12px;font-weight:600;color:{color};">{esc(m["name"].split()[0])}</td>'
            for d in days:
                scores_for_day = day_data[d].get(m["id"], [])
                if scores_for_day:
                    avg = round(sum(scores_for_day)/len(scores_for_day), 1)
                    cls = score_color(avg)
                    bg = {"high": "rgba(124,139,111,0.15)", "mid": "rgba(201,169,97,0.18)", "low": "rgba(184,107,79,0.15)"}[cls]
                    col = {"high": "var(--brand-olive)", "mid": "var(--brand-gold)", "low": "var(--brand-terracotta)"}[cls]
                    table += f'<td style="padding:6px 12px;text-align:center;"><span style="background:{bg};color:{col};font-weight:700;padding:3px 8px;border-radius:4px;">{avg}</span></td>'
                else:
                    table += '<td style="padding:6px 12px;text-align:center;color:var(--text-muted);">—</td>'
            table += '</tr>'
        table += '</table>'
        dynamics_html += table + '</div></div>'

    body = ('<div class="breadcrumb"><a href="index.html">Главная</a> › Сравнение</div>'
            '<div class="page-head"><div><h1>📊 Сравнение менеджеров</h1>'
            f'<div class="sub">{len(managers)} менеджера · средние оценки</div></div></div>'
            + kpi_html
            + '<div class="panel"><div class="panel-head"><h3>Средняя оценка</h3></div>'
            + '<div style="padding:24px;">' + bars_svg + '</div></div>'
            + dynamics_html
            + issues_html)
    return page_template("Сравнение менеджеров", body, "compare", generated_at, crit)


# ============================================================
# АНАЛИЗ ВОЗРАЖЕНИЙ
# ============================================================

def render_objections_page(calls: List[Dict], analyses: Dict, generated_at: str, critical_count: int) -> str:
    objections = get_objections_stats(analyses)

    # Группируем звонки по типу возражения
    objection_calls = defaultdict(list)
    objection_keywords = [o["name"] for o in objections]
    for c in calls:
        a = analyses.get(c["activity_id"])
        if not a:
            continue
        an = a.get("analysis") or {}
        text_blob = " ".join([
            m.get("text", "") for m in (an.get("transcript_split") or []) if m.get("speaker") == "client"
        ] + [
            kq.get("text", "") for kq in (an.get("key_quotes") or [])
        ]).lower()
        for kw in objection_keywords:
            if kw in text_blob:
                objection_calls[kw].append((c, a))

    body = ('<div class="breadcrumb"><a href="index.html">Главная</a> › Возражения</div>'
            '<div class="page-head"><div><h1>💬 Анализ возражений</h1>'
            '<div class="sub">Какие возражения встречаются чаще всего</div></div></div>')

    if not objections:
        body += '<div class="notice">Недостаточно данных для анализа возражений.</div>'
    else:
        # Визуальный бар-чарт частоты
        max_count = objections[0]["count"] if objections else 1
        body += '<div class="panel"><div class="panel-head"><h3>Частота возражений</h3><span class="hint">за весь период</span></div>'
        for obj in objections:
            pct = int(obj["count"] / max_count * 100)
            body += (f'<div style="padding:10px 20px;border-bottom:1px solid var(--border-soft);font-family:-apple-system,sans-serif;">'
                     f'<div style="display:flex;justify-content:space-between;margin-bottom:4px;">'
                     f'<span style="font-size:13px;font-weight:600;color:var(--brand-dark);">«{esc(obj["name"])}»</span>'
                     f'<span style="font-size:13px;font-weight:700;color:var(--brand-terracotta);">{obj["count"]}×</span>'
                     f'</div>'
                     f'<div style="height:6px;background:var(--brand-paper);border-radius:3px;">'
                     f'<div style="width:{pct}%;height:100%;background:var(--brand-terracotta);border-radius:3px;"></div>'
                     f'</div></div>')
        body += '</div>'

        # Примеры звонков по каждому возражению
        for obj in objections[:5]:
            kw = obj["name"]
            examples = objection_calls.get(kw, [])[:3]
            if not examples:
                continue
            rows = ""
            for c, a in examples:
                an = a.get("analysis") or {}
                score = an.get("overall_score", 0)
                cls = score_color(score)
                rows += (f'<a href="calls/{esc(c["activity_id"])}.html" class="row-link">'
                         f'<div class="call-row">'
                         f'<div class="call-direction {"in" if c.get("direction")=="incoming" else "out"}">{"↓" if c.get("direction")=="incoming" else "↑"}</div>'
                         f'<div class="call-info">'
                         f'<div class="call-client">{esc(c.get("client",{}).get("name",""))}</div>'
                         f'<div class="call-meta">{esc(c.get("manager",{}).get("name",""))} · {format_time(c.get("created",""))}</div>'
                         f'</div>'
                         f'<div class="call-time">{format_date(c.get("created",""))}</div>'
                         f'<div class="mini-score {cls}">{score}</div>'
                         f'</div></a>')
            body += (f'<div class="panel">'
                     f'<div class="panel-head"><h3>«{esc(kw)}» — {obj["count"]} раз</h3>'
                     f'<span class="hint">примеры звонков</span></div>'
                     + rows + '</div>')

    return page_template("Возражения", body, "objections", generated_at, critical_count)


# ============================================================
# ЛУЧШИЕ ЗВОНКИ
# ============================================================

def render_best_calls_page(calls: List[Dict], analyses: Dict, generated_at: str, critical_count: int) -> str:
    best = get_best_calls(calls, analyses, n=20)

    body = ('<div class="breadcrumb"><a href="index.html">Главная</a> › Лучшие звонки</div>'
            '<div class="page-head"><div><h1>🏆 Лучшие звонки</h1>'
            '<div class="sub">Эталонные звонки для обучения</div></div></div>')

    if not best:
        body += '<div class="notice">Анализов пока нет.</div>'
    else:
        body += '<div class="panel"><div class="panel-head"><h3>Топ звонков по оценке</h3></div>'
        for i, (c, a, score) in enumerate(best, 1):
            an = a.get("analysis") or {}
            cls = score_color(score)
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"{i}.")
            recommendation = an.get("recommendation", "")
            strengths = an.get("strengths") or []
            first_strength = ""
            if strengths:
                s = strengths[0]
                first_strength = s.get("text", s) if isinstance(s, dict) else s
            body += (f'<a href="calls/{esc(c["activity_id"])}.html" class="row-link">'
                     f'<div style="padding:14px 20px;border-bottom:1px solid var(--border-soft);display:grid;grid-template-columns:30px 1fr auto;gap:14px;align-items:start;font-family:-apple-system,sans-serif;">'
                     f'<div style="font-size:18px;line-height:1.4;">{medal}</div>'
                     f'<div>'
                     f'<div style="font-weight:600;font-size:14px;color:var(--brand-dark);">{esc(c.get("client",{}).get("name",""))} · {esc(c.get("manager",{}).get("name",""))}</div>'
                     f'<div style="font-size:12px;color:var(--text-muted);margin-top:2px;">{format_datetime(c.get("created",""))} · {format_duration(c.get("duration_sec"))}</div>'
                     + (f'<div style="font-size:12px;color:var(--brand-olive);margin-top:4px;">✅ {esc(first_strength)}</div>' if first_strength else '') +
                     f'</div>'
                     f'<div class="mini-score {cls}" style="font-size:18px;padding:6px 12px;">{score}</div>'
                     f'</div></a>')
        body += '</div>'

    return page_template("Лучшие звонки", body, "best", generated_at, critical_count)


# ============================================================
# ДИНАМИКА РОСТА МЕНЕДЖЕРА (обновлённая страница менеджера)
# ============================================================

def render_growth_chart(manager_id: int, manager_name: str, calls: List[Dict], analyses: Dict) -> str:
    """SVG-график динамики оценок по дням."""
    from collections import defaultdict
    day_scores = defaultdict(list)
    for c in calls:
        if c.get("manager", {}).get("id") != manager_id:
            continue
        a = analyses.get(c["activity_id"])
        if not a:
            continue
        score = (a.get("analysis") or {}).get("overall_score")
        if score is None:
            continue
        try:
            dt = datetime.fromisoformat(c.get("created", ""))
            day_scores[dt.strftime("%d.%m")].append(float(score))
        except Exception:
            pass

    if len(day_scores) < 2:
        return ""

    days = sorted(day_scores.keys())
    avgs = [round(sum(day_scores[d])/len(day_scores[d]), 1) for d in days]

    W, H = 500, 120
    pad_l, pad_r, pad_t, pad_b = 40, 20, 15, 30
    plot_w = W - pad_l - pad_r
    plot_h = H - pad_t - pad_b
    n = len(days)

    def px(i): return pad_l + int(i / max(n - 1, 1) * plot_w)
    def py(v): return pad_t + int((10 - v) / 10 * plot_h)

    # Линия
    points = " ".join(f"{px(i)},{py(v)}" for i, v in enumerate(avgs))
    area_points = f"{px(0)},{pad_t+plot_h} {points} {px(n-1)},{pad_t+plot_h}"

    svg = (f'<svg viewBox="0 0 {W} {H+10}" xmlns="http://www.w3.org/2000/svg" '
           f'style="width:100%;display:block;margin-top:8px;">')

    # Сетка
    for v in [3, 5, 7, 10]:
        y = py(v)
        svg += (f'<line x1="{pad_l}" y1="{y}" x2="{W-pad_r}" y2="{y}" '
                f'stroke="var(--border)" stroke-width="1"/>'
                f'<text x="{pad_l-4}" y="{y+4}" text-anchor="end" font-size="9" fill="var(--text-muted)">{v}</text>')

    # Область
    svg += f'<polygon points="{area_points}" fill="rgba(124,139,111,0.1)"/>'
    # Линия
    svg += f'<polyline points="{points}" fill="none" stroke="var(--brand-olive)" stroke-width="2.5" stroke-linejoin="round"/>'

    # Точки и подписи дат
    for i, (d, v) in enumerate(zip(days, avgs)):
        x, y = px(i), py(v)
        color = {"high": "var(--brand-olive)", "mid": "var(--brand-gold)", "low": "var(--brand-terracotta)"}[score_color(v)]
        svg += (f'<circle cx="{x}" cy="{y}" r="4" fill="{color}" stroke="#fff" stroke-width="1.5"/>'
                f'<text x="{x}" y="{H-2}" text-anchor="middle" font-size="9" fill="var(--text-muted)">{esc(d)}</text>')
        # Значение над точкой
        if i == 0 or i == n-1 or v == max(avgs) or v == min(avgs):
            svg += f'<text x="{x}" y="{y-7}" text-anchor="middle" font-size="10" font-weight="bold" fill="{color}">{v}</text>'

    svg += '</svg>'
    return (f'<div class="panel" style="margin-bottom:16px;">'
            f'<div class="panel-head"><h3>📈 Динамика оценок</h3>'
            f'<span class="hint">{len(days)} дней · тренд</span></div>'
            f'<div style="padding:12px 16px;">{svg}</div>'
            f'</div>')


# ============================================================
# ЧЁРНЫЙ ЯЩИК — AI ЧАТ ПО ЗВОНКУ
# ============================================================

def build_ai_chat_widget(call: Dict, analysis: Dict, vibe_api_note: str = "") -> str:
    """
    Плавающая кнопка и чат-окно. ИИ знает контекст конкретного звонка.
    Запросы идут к Vibe Code AI Router через fetch (VIBE_API_KEY передаётся через data-атрибут из env).
    """
    import json as _json
    call_context = _json.dumps({
        "manager": call.get("manager", {}).get("name", ""),
        "client": call.get("client", {}).get("name", ""),
        "company": call.get("client", {}).get("company", ""),
        "score": analysis.get("overall_score"),
        "call_type": analysis.get("call_type", {}).get("label", "") if isinstance(analysis.get("call_type"), dict) else "",
        "summary": (analysis.get("summary") or "")[:300],
        "score_explanation": (analysis.get("score_explanation") or "")[:300],
        "main_problem": analysis.get("main_problem", ""),
        "recommendation": analysis.get("recommendation", ""),
        "strengths": [
            (s.get("text", s) if isinstance(s, dict) else s)
            for s in (analysis.get("strengths") or [])[:3]
        ],
        "improvements": [
            (s.get("text", s) if isinstance(s, dict) else s)
            for s in (analysis.get("improvements") or analysis.get("weaknesses") or [])[:3]
        ],
        "flags": analysis.get("flags", {}),
    }, ensure_ascii=False)

    safe_context = call_context.replace('\\', '\\\\').replace('`', '\\`').replace('$', '\\$')

    return f'''
<div id="aiChatBtn" onclick="toggleAiChat()" style="
  position:fixed; bottom:28px; right:28px; z-index:1000;
  width:52px; height:52px; border-radius:50%;
  background:var(--brand-dark); color:#fff;
  display:flex; align-items:center; justify-content:center;
  font-size:22px; cursor:pointer; box-shadow:0 4px 16px rgba(61,46,31,0.35);
  border:2px solid var(--brand-gold); transition:transform 0.2s;
" title="Спросить ИИ про этот звонок">💬</div>

<div id="aiChatPanel" style="
  display:none; position:fixed; bottom:90px; right:28px; z-index:1001;
  width:360px; max-width:calc(100vw - 40px);
  background:#fff; border-radius:12px;
  box-shadow:0 8px 32px rgba(61,46,31,0.25);
  border:1px solid var(--brand-gold);
  overflow:hidden; font-family:-apple-system,sans-serif;
">
  <div style="background:var(--brand-dark);color:#fff;padding:12px 16px;display:flex;justify-content:space-between;align-items:center;">
    <div>
      <div style="font-weight:600;font-size:14px;">💬 Спросить про звонок</div>
      <div style="font-size:11px;opacity:0.7;margin-top:2px;">ИИ знает контекст этого звонка</div>
    </div>
    <button onclick="toggleAiChat()" style="background:none;border:none;color:#fff;font-size:18px;cursor:pointer;padding:0 4px;">×</button>
  </div>
  <div id="aiChatMessages" style="height:280px;overflow-y:auto;padding:12px;display:flex;flex-direction:column;gap:8px;">
    <div style="background:var(--brand-paper);border-radius:8px;padding:10px 12px;font-size:13px;color:var(--brand-dark);">
      Привет! Я знаю этот звонок. Можешь спросить:<br>
      <span style="color:var(--text-muted);">— Почему такая оценка?<br>— Что конкретно не так с возражением?<br>— Как менеджеру улучшиться?</span>
    </div>
  </div>
  <div style="border-top:1px solid var(--border);padding:10px 12px;display:flex;gap:8px;">
    <input id="aiChatInput" type="text" placeholder="Задай вопрос..." 
      style="flex:1;padding:8px 12px;border:1px solid var(--border);border-radius:6px;font-size:13px;font-family:inherit;"
      onkeydown="if(event.key==='Enter')sendAiMessage()">
    <button onclick="sendAiMessage()" style="
      padding:8px 14px;background:var(--brand-dark);color:#fff;
      border:none;border-radius:6px;font-size:13px;cursor:pointer;font-weight:600;
    ">→</button>
  </div>
</div>

<script>
var _callCtx = `{safe_context}`;
var _chatHistory = [];

function toggleAiChat() {{
  var p = document.getElementById('aiChatPanel');
  p.style.display = p.style.display === 'none' ? 'block' : 'none';
  if (p.style.display === 'block') document.getElementById('aiChatInput').focus();
}}

function appendMsg(role, text) {{
  var el = document.createElement('div');
  el.style.cssText = role === 'user'
    ? 'background:var(--brand-dark);color:#fff;border-radius:8px 8px 2px 8px;padding:8px 12px;font-size:13px;align-self:flex-end;max-width:85%;'
    : 'background:var(--brand-paper);border-radius:8px 8px 8px 2px;padding:8px 12px;font-size:13px;color:var(--brand-dark);max-width:90%;';
  el.textContent = text;
  var msgs = document.getElementById('aiChatMessages');
  msgs.appendChild(el);
  msgs.scrollTop = msgs.scrollHeight;
  return el;
}}

async function sendAiMessage() {{
  var input = document.getElementById('aiChatInput');
  var q = input.value.trim();
  if (!q) return;
  input.value = '';
  appendMsg('user', q);

  _chatHistory.push({{"role":"user","content":q}});

  var thinking = appendMsg('assistant', '...');

  var systemPrompt = `Ты — РОП компании Mavis Group. Тебе известен полный анализ конкретного звонка:
${{_callCtx}}

Отвечай кратко и по делу. Используй контекст этого звонка. Пиши на русском.`;

  var messages = [{{"role":"user","content":systemPrompt + "\\n\\nПользователь: " + q}}];
  if (_chatHistory.length > 2) {{
    messages = [{{"role":"user","content":systemPrompt}}];
    _chatHistory.forEach(function(m) {{ messages.push(m); }});
  }}

  try {{
    // Запрос идёт через Cloudflare Worker (прокси) — ключ хранится там, не в браузере
    var proxyUrl = document.querySelector('meta[name=proxy-url]');
    var url = proxyUrl ? proxyUrl.content : '';
    if (!url) {{
      thinking.textContent = 'Прокси не настроен. Укажи PROXY_URL в переменных окружения GitHub.';
      return;
    }}
    var res = await fetch(url, {{
      method: 'POST',
      headers: {{'Content-Type':'application/json'}},
      body: JSON.stringify({{
        model: 'bitrix/bitrixgpt-5.5',
        max_tokens: 500,
        messages: messages
      }})
    }});
    var data = await res.json();
    var answer = (data.choices||[{{message:{{content:'Ошибка ответа'}}}}])[0].message.content;
    thinking.textContent = answer;
    _chatHistory.push({{"role":"assistant","content":answer}});
  }} catch(e) {{
    thinking.textContent = 'Ошибка: ' + e.message;
  }}
}}
</script>
'''




# ============================================================
# ОСНОВНАЯ ГЕНЕРАЦИЯ
# ============================================================

def generate(calls_json_path: str = "calls_data.json",
             analyses_json_path: str = "analyses.json",
             corrections_json_path: str = "manual_corrections.json",
             output_dir: str = "docs"):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    out_dir = Path(output_dir)
    out_dir.mkdir(exist_ok=True)
    (out_dir / "managers").mkdir(exist_ok=True)
    (out_dir / "calls").mkdir(exist_ok=True)

    # Загрузка данных
    calls = []
    if Path(calls_json_path).exists():
        calls = json.loads(Path(calls_json_path).read_text(encoding="utf-8"))

    analyses = {}
    if Path(analyses_json_path).exists():
        analyses = json.loads(Path(analyses_json_path).read_text(encoding="utf-8"))

    corrections = {}
    if Path(corrections_json_path).exists():
        try:
            corrections = json.loads(Path(corrections_json_path).read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning(f"Не удалось прочитать {corrections_json_path}, игнорируем")

    # Применяем ручные правки
    if corrections:
        analyses = apply_manual_corrections(analyses, corrections)
        logger.info(f"Применено ручных правок: {len(corrections)}")

    logger.info(f"Звонков: {len(calls)}, анализов: {len(analyses)}")
    stats = compute_stats(calls, analyses)
    generated_at = datetime.now().isoformat()
    crit = stats["critical_count"]

    # Главные страницы (в корне docs/)
    (out_dir / "index.html").write_text(
        render_index(calls, stats, analyses, generated_at), encoding="utf-8")
    (out_dir / "rop-report.html").write_text(
        render_rop_report(calls, stats, analyses, generated_at), encoding="utf-8")
    (out_dir / "managers.html").write_text(
        render_managers_list(stats, generated_at), encoding="utf-8")
    (out_dir / "all-calls.html").write_text(
        render_all_calls(calls, analyses, generated_at, crit), encoding="utf-8")
    (out_dir / "critical.html").write_text(
        render_critical_page(calls, analyses, generated_at, crit), encoding="utf-8")
    (out_dir / "triggers.html").write_text(
        render_triggers_page(calls, analyses, generated_at, crit), encoding="utf-8")
    # Новые страницы
    (out_dir / "compare.html").write_text(
        render_compare_page(stats, calls, analyses, generated_at), encoding="utf-8")
    (out_dir / "objections.html").write_text(
        render_objections_page(calls, analyses, generated_at, crit), encoding="utf-8")
    (out_dir / "best-calls.html").write_text(
        render_best_calls_page(calls, analyses, generated_at, crit), encoding="utf-8")

    # Страницы менеджеров
    for m in stats["managers"]:
        (out_dir / "managers" / f"{m['id']}.html").write_text(
            render_manager_page(m, analyses, generated_at, crit), encoding="utf-8")

    # Страницы звонков
    for call in calls:
        analysis_data = analyses.get(call["activity_id"])
        (out_dir / "calls" / f"{call['activity_id']}.html").write_text(
            render_call_page(call, analysis_data, generated_at, crit), encoding="utf-8")

    (out_dir / ".nojekyll").write_text("", encoding="utf-8")

    print("")
    print("✅ Сайт сгенерирован")
    print(f"   Звонков: {len(calls)}")
    print(f"   С ИИ-анализом: {len(analyses)}")
    print(f"   Критичных: {crit}")
    print(f"   Ручных правок: {len(corrections)}")


if __name__ == "__main__":
    generate()
