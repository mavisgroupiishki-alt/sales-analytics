"""
Генератор HTML-отчёта по звонкам Mavis Group — версия по ТЗ.
"""

import json
import html
import logging
from pathlib import Path
from datetime import datetime
from collections import defaultdict, Counter
from typing import List, Dict, Any

logger = logging.getLogger(__name__)
COMPANY_NAME = "Mavis Group"


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


def compute_stats(calls: List[Dict], analyses: Dict) -> Dict[str, Any]:
    total = len(calls)
    incoming = sum(1 for c in calls if c.get("direction") == "incoming")
    outgoing = sum(1 for c in calls if c.get("direction") == "outgoing")
    critical = sum(1 for a in analyses.values() if a.get("analysis", {}).get("is_critical"))

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
            sc = a.get("analysis", {}).get("overall_score")
            if sc is not None:
                try:
                    by_manager[mid]["scores"].append(float(sc))
                except (TypeError, ValueError):
                    pass
            if a.get("analysis", {}).get("is_critical"):
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

    # Топ ошибок по критериям и триггерам по отделу
    all_low_criteria = []
    all_triggers = []
    for a in analyses.values():
        an = a.get("analysis", {})
        for crit, val in (an.get("scores") or {}).items():
            try:
                if float(val) < 6:
                    all_low_criteria.append(crit)
            except (TypeError, ValueError):
                pass
        for t in (an.get("triggers") or []):
            all_triggers.append(t.get("name", ""))

    return {
        "total": total, "incoming": incoming, "outgoing": outgoing,
        "managers_count": len(by_manager),
        "managers": managers_by_quality,
        "critical_count": critical,
        "top_low_criteria": Counter(all_low_criteria).most_common(5),
        "top_triggers": Counter(all_triggers).most_common(5),
        "avg_dept_score": (
            round(sum(s for m in by_manager.values() for s in m["scores"]) /
                  sum(1 for m in by_manager.values() for _ in m["scores"]), 1)
            if any(m["scores"] for m in by_manager.values()) else None
        ),
    }


def compute_manager_daily_report(manager: Dict, analyses: Dict) -> Dict[str, Any]:
    """Дневной отчёт по менеджеру согласно разделу 12 ТЗ."""
    calls = manager["calls"]
    analyses_for = [analyses[c["activity_id"]] for c in calls if c["activity_id"] in analyses]
    
    if not analyses_for:
        return {
            "analyzed_count": 0, "avg_score": None,
            "crm_contexts": {}, "goals": {},
            "low_score_calls": [], "critical_calls": [],
            "strengths_top": [], "weaknesses_top": [],
            "main_problem_day": None, "advice": None,
            "criteria_avgs": {},
        }
    
    # Подсчёты
    scores = [a["analysis"].get("overall_score", 0) for a in analyses_for]
    crm_contexts = Counter()
    goals_counter = Counter()
    low_criteria = []
    all_strengths = []
    all_weaknesses = []
    criteria_scores = defaultdict(list)
    
    low_score_calls = []
    critical_calls = []
    
    for a in analyses_for:
        an = a["analysis"]
        ctx = an.get("classification", {}).get("crm_context", "")
        if ctx:
            crm_contexts[ctx] += 1
        for g in an.get("classification", {}).get("goals", []) or []:
            goals_counter[g] += 1
        for crit, val in (an.get("scores") or {}).items():
            try:
                vf = float(val)
                criteria_scores[crit].append(vf)
                if vf < 6:
                    low_criteria.append(crit)
            except (TypeError, ValueError):
                pass
        for s in (an.get("strengths") or []):
            all_strengths.append(s)
        for w in (an.get("weaknesses") or []):
            all_weaknesses.append(w)
        # Низкобалльные
        if an.get("overall_score", 10) < 6:
            low_score_calls.append((a["call_meta"], an))
        if an.get("is_critical"):
            critical_calls.append((a["call_meta"], an))
    
    # Средние по критериям
    criteria_avgs = {c: round(sum(v) / len(v), 1) for c, v in criteria_scores.items()}
    
    # Главная проблема дня — наиболее частый низкий критерий
    main_problem = None
    if low_criteria:
        most_common = Counter(low_criteria).most_common(1)[0]
        main_problem = f"В {most_common[1]} из {len(analyses_for)} звонков проблемы с критерием «{most_common[0]}»"
    
    return {
        "analyzed_count": len(analyses_for),
        "avg_score": round(sum(scores) / len(scores), 1) if scores else None,
        "crm_contexts": dict(crm_contexts),
        "goals": dict(goals_counter),
        "low_score_calls": sorted(low_score_calls, key=lambda x: x[1].get("overall_score", 0))[:5],
        "critical_calls": critical_calls,
        "strengths_top": Counter(all_strengths).most_common(3),
        "weaknesses_top": Counter(all_weaknesses).most_common(3),
        "main_problem_day": main_problem,
        "criteria_avgs": criteria_avgs,
    }


# ============================================================
# CSS (полный)
# ============================================================
CSS = """
:root {
  --brand-dark: #3D2E1F; --brand-medium: #6B5544; --brand-light: #A0826D;
  --brand-cream: #FAF7F2; --brand-paper: #F2EBE0;
  --brand-gold: #C9A961; --brand-olive: #7C8B6F; --brand-terracotta: #B86B4F;
  --text-primary: #2A1F15; --text-secondary: #6B5544; --text-muted: #A39686;
  --border: #E8DFD0; --border-soft: #F0E8DA;
  --red: #A0432B; --red-soft: #FFE5DC;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: "Georgia", -apple-system, "Segoe UI", system-ui, serif; background: var(--brand-cream); color: var(--text-primary); -webkit-font-smoothing: antialiased; line-height: 1.55; }
a { color: inherit; text-decoration: none; }
.topbar { background: var(--brand-dark); color: #fff; padding: 10px 24px; display: flex; align-items: center; gap: 20px; position: sticky; top: 0; z-index: 10; box-shadow: 0 2px 12px rgba(61,46,31,0.25); border-bottom: 3px solid var(--brand-gold); overflow: hidden; }
.logo { display: flex; align-items: center; gap: 14px; flex-shrink: 0; }
.logo-img { height: 48px; width: auto; display: block; flex-shrink: 0; }
.logo-text { display: flex; flex-direction: column; line-height: 1.1; }
.logo-text .brand { font-size: 17px; font-weight: 700; letter-spacing: 1px; color: #fff; }
.logo-text .sub { font-size: 10px; letter-spacing: 3px; color: var(--brand-gold); text-transform: uppercase; margin-top: 2px; }
.nav { display: flex; gap: 22px; font-size: 13px; flex: 1; overflow-x: auto; -webkit-overflow-scrolling: touch; scrollbar-width: none; font-family: -apple-system, sans-serif; }
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
.kpi-label { font-size: 10px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 1.5px; font-weight: 600; margin-bottom: 8px; font-family: -apple-system, sans-serif; }
.kpi-value { font-size: 30px; font-weight: 400; line-height: 1; color: var(--brand-dark); }
.kpi-hint { margin-top: 6px; font-size: 11px; color: var(--text-muted); font-family: -apple-system, sans-serif; }
.grid { display: grid; grid-template-columns: 1.3fr 1fr; gap: 18px; margin-bottom: 22px; }
@media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
.panel { background: #fff; border-radius: 6px; border: 1px solid var(--border); overflow: hidden; margin-bottom: 16px; }
.panel-head { padding: 14px 18px; border-bottom: 1px solid var(--border-soft); display: flex; justify-content: space-between; align-items: center; background: linear-gradient(180deg, #FFFCF7, #fff); }
.panel-head h3 { font-size: 16px; font-weight: 600; color: var(--brand-dark); }
.panel-head .hint { font-size: 12px; color: var(--text-muted); font-family: -apple-system, sans-serif; }
.panel-body { padding: 16px 20px; font-family: -apple-system, sans-serif; font-size: 14px; color: var(--brand-dark); }
.row-link { display: block; transition: background 0.15s; font-family: -apple-system, sans-serif; }
.row-link:hover { background: var(--brand-paper); }
.row-link.critical { background: linear-gradient(90deg, var(--red-soft), transparent 60%); }
.manager-row { display: grid; grid-template-columns: 26px 1fr auto auto; align-items: center; gap: 12px; padding: 12px 18px; border-bottom: 1px solid var(--border-soft); }
.rank { width: 22px; height: 22px; border-radius: 50%; background: var(--brand-paper); color: var(--text-secondary); font-weight: 700; font-size: 11px; display: flex; align-items: center; justify-content: center; }
.rank.gold { background: var(--brand-gold); color: #fff; }
.rank.silver { background: #BFB5A8; color: #fff; }
.rank.bronze { background: var(--brand-terracotta); color: #fff; }
.manager-info { display: flex; align-items: center; gap: 12px; min-width: 0; }
.m-avatar { width: 38px; height: 38px; border-radius: 50%; background: linear-gradient(135deg, var(--brand-medium), var(--brand-dark)); color: #fff; display: flex; align-items: center; justify-content: center; font-weight: 600; font-size: 12px; flex-shrink: 0; overflow: hidden; border: 2px solid #fff; box-shadow: 0 0 0 1px var(--border); }
.m-avatar img { width: 100%; height: 100%; border-radius: 50%; object-fit: cover; }
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
.m-avg.empty { background: var(--brand-paper); color: var(--text-muted); font-size: 11px; }
.m-count { font-weight: 600; font-size: 18px; min-width: 36px; text-align: right; color: var(--text-secondary); font-family: "Georgia", serif; }
.call-row { display: grid; grid-template-columns: 28px 1fr auto auto auto; align-items: center; gap: 10px; padding: 11px 18px; border-bottom: 1px solid var(--border-soft); font-size: 13px; }
.call-direction { width: 26px; height: 26px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 14px; font-weight: 700; }
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
.call-card.critical { border-color: var(--red); box-shadow: 0 0 0 1px var(--red); }
.call-card-head { padding: 22px 24px; display: grid; grid-template-columns: 1fr auto; gap: 18px; align-items: center; background: linear-gradient(180deg, #FFFCF7, #fff); border-bottom: 1px solid var(--border-soft); }
.call-card.critical .call-card-head { background: linear-gradient(180deg, var(--red-soft), #fff); }
.call-card-head h2 { font-size: 22px; font-weight: 400; margin-bottom: 10px; color: var(--brand-dark); }
.critical-badge { display: inline-block; background: var(--red); color: #fff; padding: 3px 10px; border-radius: 4px; font-size: 11px; font-weight: 700; margin-bottom: 10px; font-family: -apple-system, sans-serif; }
.corrected-badge { display: inline-block; background: var(--brand-olive); color: #fff; padding: 3px 10px; border-radius: 4px; font-size: 11px; font-weight: 700; margin-bottom: 10px; font-family: -apple-system, sans-serif; margin-left: 6px; }
.call-tags { display: flex; gap: 6px; flex-wrap: wrap; font-size: 12px; font-family: -apple-system, sans-serif; }
.call-tag { background: var(--brand-paper); padding: 4px 10px; border-radius: 12px; color: var(--brand-medium); border: 1px solid var(--border); }
.call-tag.accent { background: var(--brand-dark); color: #fff; font-weight: 600; }
.call-tag.warning { background: var(--brand-terracotta); color: #fff; font-weight: 600; }
.call-tag.green { background: var(--brand-olive); color: #fff; font-weight: 600; }
.score-big { border-radius: 6px; padding: 14px 22px; text-align: center; min-width: 130px; color: #fff; position: relative; }
.score-big.high { background: linear-gradient(135deg, var(--brand-olive), #6B7B5F); }
.score-big.mid { background: linear-gradient(135deg, var(--brand-gold), #B89A55); }
.score-big.low { background: linear-gradient(135deg, var(--brand-terracotta), #A65E45); }
.score-big.crit { background: linear-gradient(135deg, var(--red), #7D2818); }
.score-big.placeholder { background: linear-gradient(135deg, #BFB5A8, var(--text-muted)); }
.score-big .num { font-family: "Georgia", serif; font-size: 36px; font-weight: 400; line-height: 1; }
.score-big .num .max { font-size: 16px; opacity: 0.7; }
.score-big .label { font-size: 10px; text-transform: uppercase; letter-spacing: 1.5px; margin-top: 4px; opacity: 0.85; font-family: -apple-system, sans-serif; }
.edit-score-btn { display: inline-block; margin-top: 8px; padding: 4px 10px; background: rgba(255,255,255,0.2); border: 1px solid rgba(255,255,255,0.3); color: #fff; border-radius: 4px; font-size: 11px; font-family: -apple-system, sans-serif; cursor: pointer; }
.edit-score-btn:hover { background: rgba(255,255,255,0.3); }
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
.ai-key-quote { background: var(--brand-paper); border-left: 3px solid var(--brand-medium); padding: 12px 16px; margin-top: 12px; font-style: italic; font-size: 14px; color: var(--brand-dark); }
.ai-key-quote::before { content: "« "; color: var(--brand-medium); }
.ai-key-quote::after { content: " »"; color: var(--brand-medium); }
.ai-key-quote .meta { display: block; font-style: normal; font-size: 11px; color: var(--text-muted); margin-top: 6px; font-family: -apple-system, sans-serif; }
.sw-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 16px; }
@media (max-width: 700px) { .sw-grid { grid-template-columns: 1fr; } }
.sw-card { background: #fff; border: 1px solid var(--border); border-radius: 6px; padding: 16px 20px; font-family: -apple-system, sans-serif; }
.sw-card.strengths { border-left: 4px solid var(--brand-olive); }
.sw-card.weaknesses { border-left: 4px solid var(--brand-terracotta); }
.sw-card h4 { font-size: 13px; color: var(--brand-dark); margin-bottom: 10px; }
.sw-card ul { list-style: none; padding: 0; }
.sw-card li { padding: 4px 0; font-size: 13px; color: var(--brand-dark); display: flex; gap: 8px; }
.sw-card li::before { content: "▸"; color: var(--brand-medium); flex-shrink: 0; }
.main-problem { background: linear-gradient(135deg, var(--red-soft), #FFEEE5); border: 1px solid var(--red); border-radius: 6px; padding: 16px 22px; margin-bottom: 16px; font-family: -apple-system, sans-serif; }
.main-problem .label { font-size: 10px; text-transform: uppercase; letter-spacing: 1.5px; color: var(--red); font-weight: 700; margin-bottom: 6px; }
.main-problem .text { font-size: 14px; color: var(--brand-dark); font-weight: 600; }
.transcript-panel { background: #fff; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; margin-bottom: 16px; }
.transcript-head { padding: 14px 20px; border-bottom: 1px solid var(--border-soft); background: linear-gradient(180deg, #FFFCF7, #fff); display: flex; justify-content: space-between; align-items: center; }
.transcript-head h3 { font-size: 16px; font-weight: 600; color: var(--brand-dark); }
.transcript-body { padding: 6px 0; max-height: 500px; overflow-y: auto; font-family: -apple-system, sans-serif; }
.msg { padding: 10px 20px; border-bottom: 1px solid var(--border-soft); display: grid; grid-template-columns: 60px 1fr; gap: 12px; }
.msg-time { font-size: 11px; color: var(--text-muted); font-weight: 600; font-variant-numeric: tabular-nums; padding-top: 2px; }
.msg-body { min-width: 0; }
.msg-who { font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 3px; }
.msg-who.client { color: var(--brand-olive); }
.msg-who.manager { color: var(--brand-terracotta); }
.msg-text { font-size: 13px; color: var(--brand-dark); line-height: 1.55; }
.scores-panel { background: #fff; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; margin-bottom: 16px; }
.score-row { display: grid; grid-template-columns: 1fr 110px 50px; align-items: center; gap: 14px; padding: 9px 20px; border-bottom: 1px solid var(--border-soft); font-family: -apple-system, sans-serif; font-size: 13px; }
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
.low-score-detail { background: #FFF5F0; border: 1px solid var(--brand-terracotta); border-radius: 6px; padding: 14px 18px; margin-bottom: 10px; font-family: -apple-system, sans-serif; font-size: 13px; }
.low-score-detail .crit-name { font-weight: 700; color: var(--brand-terracotta); margin-bottom: 4px; font-size: 13px; }
.low-score-detail .crit-quote { background: #fff; border-left: 2px solid var(--brand-terracotta); padding: 8px 12px; margin: 6px 0; font-style: italic; color: var(--brand-dark); }
.low-score-detail .crit-time { font-size: 10px; color: var(--text-muted); font-variant-numeric: tabular-nums; }
.low-score-detail .crit-reco { color: var(--brand-dark); margin-top: 6px; }
.trigger-list { background: #fff; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; margin-bottom: 16px; }
.trigger-row { padding: 12px 20px; display: flex; gap: 12px; align-items: flex-start; border-bottom: 1px solid var(--border-soft); }
.trigger-marker { width: 6px; height: 6px; border-radius: 50%; background: var(--brand-terracotta); margin-top: 7px; flex-shrink: 0; }
.trigger-text { font-family: -apple-system, sans-serif; font-size: 13px; color: var(--brand-dark); flex: 1; }
.trigger-text .sub { font-size: 11px; color: var(--text-muted); margin-top: 3px; }
.trigger-text .timecode { display: inline-block; font-size: 10px; color: var(--brand-medium); background: var(--brand-paper); padding: 1px 6px; border-radius: 3px; margin-left: 6px; font-variant-numeric: tabular-nums; }
.recommendation { background: linear-gradient(135deg, #FFF8E8, #FFF4DC); border: 1px solid var(--brand-gold); border-radius: 6px; padding: 18px 22px; margin-bottom: 16px; font-family: -apple-system, sans-serif; }
.recommendation .label { font-size: 10px; text-transform: uppercase; letter-spacing: 1.5px; color: var(--brand-dark); font-weight: 700; margin-bottom: 8px; }
.recommendation .text { font-size: 14px; color: var(--brand-dark); line-height: 1.6; }
.scripts-used { background: var(--brand-paper); border: 1px solid var(--border); border-radius: 6px; padding: 12px 18px; margin-bottom: 16px; font-family: -apple-system, sans-serif; font-size: 12px; color: var(--text-secondary); }
.scripts-used b { color: var(--brand-dark); }
.scripts-used .tag { display: inline-block; background: #fff; border: 1px solid var(--border); padding: 2px 8px; margin: 3px 4px 0 0; border-radius: 10px; font-size: 11px; }
.scripts-alignment { background: #fff; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; margin-bottom: 16px; }
.alignment-row { display: grid; grid-template-columns: 1fr auto; gap: 12px; padding: 11px 20px; border-bottom: 1px solid var(--border-soft); font-family: -apple-system, sans-serif; font-size: 13px; }
.alignment-stage { color: var(--brand-dark); font-weight: 600; }
.alignment-evidence { font-size: 11px; color: var(--text-muted); margin-top: 3px; }
.alignment-status { font-size: 11px; padding: 3px 8px; border-radius: 10px; font-weight: 600; white-space: nowrap; }
.alignment-status.full { background: rgba(124,139,111,0.18); color: var(--brand-olive); }
.alignment-status.partial { background: rgba(201,169,97,0.18); color: var(--brand-gold); }
.alignment-status.missing { background: rgba(184,107,79,0.18); color: var(--brand-terracotta); }
.next-contact { background: linear-gradient(135deg, #E8F0FE, #F0F4FF); border: 1px solid #5B7AC4; border-radius: 6px; padding: 16px 22px; margin-bottom: 16px; font-family: -apple-system, sans-serif; }
.next-contact .label { font-size: 10px; text-transform: uppercase; letter-spacing: 1.5px; color: #5B7AC4; font-weight: 700; margin-bottom: 6px; }
.next-contact .text { font-size: 14px; color: var(--brand-dark); font-weight: 600; }
.next-contact .sub { font-size: 12px; color: var(--text-secondary); margin-top: 4px; }
.placeholder-panel { background: linear-gradient(135deg, var(--brand-paper), #F8F1E3); border: 1px dashed var(--brand-gold); border-radius: 6px; padding: 20px 22px; margin-bottom: 16px; }
.dist-bar { display: flex; gap: 4px; margin: 8px 0; }
.dist-slice { flex: 1; height: 28px; display: flex; align-items: center; justify-content: center; font-size: 11px; color: #fff; font-weight: 700; padding: 0 8px; border-radius: 3px; font-family: -apple-system, sans-serif; }
.modal-overlay { display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.5); z-index: 1000; align-items: center; justify-content: center; padding: 20px; }
.modal-overlay.show { display: flex; }
.modal { background: #fff; border-radius: 8px; padding: 24px; max-width: 500px; width: 100%; font-family: -apple-system, sans-serif; }
.modal h3 { color: var(--brand-dark); margin-bottom: 12px; }
.modal p { font-size: 13px; color: var(--text-secondary); margin-bottom: 14px; line-height: 1.5; }
.modal input, .modal textarea { width: 100%; padding: 10px 12px; border: 1px solid var(--border); border-radius: 4px; font-size: 14px; font-family: inherit; margin-bottom: 12px; }
.modal textarea { resize: vertical; min-height: 70px; }
.modal-buttons { display: flex; gap: 10px; justify-content: flex-end; }
.btn { padding: 9px 16px; border-radius: 4px; font-size: 13px; font-weight: 600; cursor: pointer; border: none; font-family: inherit; }
.btn.primary { background: var(--brand-dark); color: #fff; }
.btn.secondary { background: var(--brand-paper); color: var(--brand-dark); border: 1px solid var(--border); }
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
  .score-row { grid-template-columns: 1fr 80px 40px; padding: 9px 14px; }
  .call-row { grid-template-columns: 28px 1fr auto auto; }
  .call-row .call-time { display: none; }
  .manager-row { grid-template-columns: 26px 1fr auto; }
  .manager-row .m-count { display: none; }
  .msg { grid-template-columns: 45px 1fr; gap: 8px; }
}
@media (max-width: 380px) { .kpi-row { grid-template-columns: 1fr; } }
"""


def page_template(title: str, body: str, active_nav: str, generated_at: str, critical_count: int = 0) -> str:
    nav_items = [
        ("dashboard", "index.html", "Сводка"),
        ("rop_report", "rop-report.html", "Отчёт РОПа"),
        ("managers", "managers.html", "Менеджеры"),
        ("calls", "all-calls.html", "Все звонки"),
        ("critical", "critical.html", f"Срочно{f' ({critical_count})' if critical_count else ''}"),
        ("triggers", "triggers.html", "Триггеры"),
    ]
    nav_html = ""
    for key, href, label in nav_items:
        classes = []
        if key == active_nav: classes.append("active")
        if key == "critical" and critical_count > 0: classes.append("critical-link")
        cls = f' class="{" ".join(classes)}"' if classes else ""
        nav_html += f'<a{cls} href="{href}">{label}</a>'
    year = datetime.now().year
    return f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{esc(title)} — {esc(COMPANY_NAME)}</title><style>{CSS}</style></head>
<body>
<div class="topbar">
  <a href="index.html" class="logo">
    <img class="logo-img" src="logo.png" alt="{esc(COMPANY_NAME)}">
    <div class="logo-text"><span class="brand">{esc(COMPANY_NAME)}</span><span class="sub">Sales Analytics</span></div>
  </a>
  <div class="nav">{nav_html}</div>
  <div class="timestamp">обновлено {esc(generated_at[:16].replace('T', ' '))}</div>
</div>
<div class="container">{body}</div>
<div class="footer-note">
  <div class="footer-brand">{esc(COMPANY_NAME).upper()}</div>
  <div class="footer-divider"></div>
  <div>Sales Analytics · Анализ звонков отдела продаж</div>
  <div style="margin-top:6px; font-size:11px;">Bitrix24 · GitHub Actions · Claude Haiku · © {year}</div>
</div>
</body></html>"""


def render_call_row(c: Dict, show_manager: bool = True, analysis: Dict = None, base_calls_path: str = "calls/") -> str:
    direction = c.get("direction", "")
    dir_icon = "↓" if direction == "incoming" else "↑"
    dir_class = "in" if direction == "incoming" else "out"
    client_name = c.get("client", {}).get("name", "Неизвестно")
    company = c.get("client", {}).get("company", "")
    manager = c.get("manager", {}).get("name", "") if show_manager else ""
    meta_parts = []
    if company: meta_parts.append(esc(company))
    if manager: meta_parts.append(f"менеджер: {esc(manager)}")
    meta = " · ".join(meta_parts)
    duration_html = ""
    dur = c.get("duration_sec")
    if dur:
        duration_html = f'<div class="call-duration">{format_duration(dur)}</div>'
    score_html = ""
    row_classes = ["row-link"]
    if analysis:
        an = analysis.get("analysis", {})
        score = an.get("overall_score")
        if score is not None:
            cls = "crit" if an.get("is_critical") else score_color(score)
            score_html = f'<div class="mini-score {cls}">{score}</div>'
        if an.get("is_critical"):
            row_classes.append("critical")
    return f"""<a href="{base_calls_path}{esc(c['activity_id'])}.html" class="{' '.join(row_classes)}">
<div class="call-row">
  <div class="call-direction {dir_class}">{dir_icon}</div>
  <div class="call-info"><div class="call-client">{esc(client_name)}</div><div class="call-meta">{meta}</div></div>
  {duration_html}
  <div class="call-time">{format_time(c.get('created', ''))}</div>
  {score_html}
</div></a>"""


def render_index(calls: List[Dict], stats: Dict, analyses: Dict, generated_at: str) -> str:
    today = format_date(generated_at)
    analyzed_count = len(analyses)
    critical_count = stats["critical_count"]
    avg = stats.get("avg_dept_score")
    avg_html = f'<div class="kpi green"><div class="kpi-label">Средняя оценка</div><div class="kpi-value">{avg}</div></div>' if avg else ""

    kpi_html = f"""<div class="kpi-row">
      <div class="kpi"><div class="kpi-label">Всего звонков</div><div class="kpi-value">{stats['total']}</div></div>
      <div class="kpi green"><div class="kpi-label">Входящих</div><div class="kpi-value">{stats['incoming']}</div></div>
      <div class="kpi accent"><div class="kpi-label">Исходящих</div><div class="kpi-value">{stats['outgoing']}</div></div>
      <div class="kpi amber"><div class="kpi-label">Проанализировано</div><div class="kpi-value">{analyzed_count}</div></div>
      <div class="kpi red"><div class="kpi-label">Срочно</div><div class="kpi-value">{critical_count}</div></div>
      {avg_html}
    </div>"""

    managers_html = ""
    for i, m in enumerate(stats["managers"][:10], 1):
        rank_class = ["", "gold", "silver", "bronze"][min(i, 3)] if i <= 3 else ""
        avatar_class = manager_color_class(m["id"])
        if m.get("avg_score") is not None:
            avg_cls = score_color(m["avg_score"])
            avg_html2 = f'<div class="m-avg {avg_cls}">{m["avg_score"]}</div>'
            stats_line = f'оценок: <b>{m["analyzed"]}</b>'
            if m["critical"]: stats_line += f' · 🔴 <b>{m["critical"]}</b>'
        else:
            avg_html2 = '<div class="m-avg empty">—</div>'
            stats_line = f'входящих: <b>{m["in"]}</b> · исходящих: <b>{m["out"]}</b>'
        managers_html += f"""<a href="managers/{m['id']}.html" class="row-link">
<div class="manager-row">
  <div class="rank {rank_class}">{i}</div>
  <div class="manager-info">
    <div class="m-avatar {avatar_class}">{manager_avatar_html(m)}</div>
    <div><div class="m-name">{esc(m['name'])}</div><div class="m-stats">{stats_line}</div></div>
  </div>
  {avg_html2}<div class="m-count">{m['count']}</div>
</div></a>"""
    if not managers_html: managers_html = '<div class="empty">Звонков нет</div>'

    recent = sorted(calls, key=lambda x: x.get("created", ""), reverse=True)[:10]
    calls_html = "".join(render_call_row(c, analysis=analyses.get(c["activity_id"])) for c in recent) or '<div class="empty">Нет</div>'

    notice = ""
    if critical_count > 0:
        notice = f'<div class="notice danger"><b>🔴 Внимание!</b> Найдено <b>{critical_count}</b> критичных звонков. <a href="critical.html" style="color: var(--red); font-weight: 700; text-decoration: underline;">К списку →</a></div>'

    body = f"""<div class="page-head"><div><h1>Сводка</h1><div class="sub">{COMPANY_NAME} · {stats['total']} звонков</div></div><div class="pill">📅 {today}</div></div>
{notice}{kpi_html}
<div class="grid">
  <div class="panel"><div class="panel-head"><h3>Рейтинг по качеству</h3><a href="managers.html" class="hint" style="color:var(--brand-medium);">все →</a></div>{managers_html}</div>
  <div class="panel"><div class="panel-head"><h3>Последние звонки</h3><a href="all-calls.html" class="hint" style="color:var(--brand-medium);">все →</a></div>{calls_html}</div>
</div>"""
    return page_template("Сводка", body, "dashboard", generated_at, critical_count)


def render_rop_report(calls: List[Dict], stats: Dict, analyses: Dict, generated_at: str) -> str:
    """Сводный отчёт для руководителя (раздел 13 ТЗ)."""
    today = format_date(generated_at)
    critical_count = stats["critical_count"]

    # Топ-5 ошибок
    top_low_html = ""
    if stats["top_low_criteria"]:
        items = ""
        for crit, cnt in stats["top_low_criteria"]:
            items += f'<div class="alignment-row"><div><span class="alignment-stage">{esc(crit)}</span></div><div class="alignment-status missing">{cnt} раз</div></div>'
        top_low_html = f'<div class="panel"><div class="panel-head"><h3>📉 Топ слабых критериев</h3></div>{items}</div>'

    top_trig_html = ""
    if stats["top_triggers"]:
        items = ""
        for t, cnt in stats["top_triggers"]:
            items += f'<div class="alignment-row"><div><span class="alignment-stage">{esc(t)}</span></div><div class="alignment-status partial">{cnt} раз</div></div>'
        top_trig_html = f'<div class="panel"><div class="panel-head"><h3>⚠️ Топ-5 триггеров дня</h3></div>{items}</div>'

    # Рейтинг с просадкой (avg<6)
    drop_managers = [m for m in stats["managers"] if m.get("avg_score") is not None and m["avg_score"] < 6]
    drop_html = ""
    if drop_managers:
        items = ""
        for m in drop_managers:
            avatar_class = manager_color_class(m["id"])
            items += f"""<a href="managers/{m['id']}.html" class="row-link"><div class="manager-row">
  <div class="rank">!</div>
  <div class="manager-info"><div class="m-avatar {avatar_class}">{manager_avatar_html(m)}</div>
  <div><div class="m-name">{esc(m['name'])}</div><div class="m-stats">{m['analyzed']} разборов</div></div></div>
  <div class="m-avg low">{m['avg_score']}</div><div class="m-count">{m['count']}</div>
</div></a>"""
        drop_html = f'<div class="panel"><div class="panel-head"><h3>📉 Менеджеры с просадкой (avg &lt; 6)</h3></div>{items}</div>'

    # Критичные звонки
    critical_calls = []
    for c in calls:
        a = analyses.get(c["activity_id"])
        if a and a.get("analysis", {}).get("is_critical"):
            critical_calls.append((c, a))
    critical_calls.sort(key=lambda x: x[1]["analysis"].get("overall_score", 0))
    crit_rows = ""
    for c, a in critical_calls[:10]:
        an = a["analysis"]
        score = an.get("overall_score", 0)
        reason = an.get("critical_reason", "")
        crit_rows += f"""<a href="calls/{esc(c['activity_id'])}.html" class="row-link critical"><div class="call-row">
  <div class="call-direction {'in' if c.get('direction') == 'incoming' else 'out'}">{'↓' if c.get('direction') == 'incoming' else '↑'}</div>
  <div class="call-info"><div class="call-client">{esc(c.get('client', {}).get('name', ''))} · {esc(c.get('manager', {}).get('name', ''))}</div>
  <div class="call-meta"><b>{esc(reason)}</b></div></div>
  <div class="call-time">{format_time(c.get('created', ''))}</div>
  <div class="mini-score crit">{score}</div>
</div></a>"""
    crit_html = f'<div class="panel"><div class="panel-head"><h3>🔴 Критичные звонки</h3><span class="hint">{len(critical_calls)} шт</span></div>{crit_rows or "<div class=\"empty\">Нет</div>"}</div>'

    # Общий вывод
    conclusion = ""
    avg = stats.get("avg_dept_score")
    if avg:
        if avg >= 7.5:
            conclusion = f"<b>Отдел отработал хорошо.</b> Средняя оценка {avg}/10."
        elif avg >= 6:
            conclusion = f"<b>Средний день.</b> Средняя оценка {avg}/10. Есть моменты для улучшения."
        else:
            conclusion = f"<b>Нужно реагировать.</b> Средняя оценка {avg}/10 ниже нормы."
    if stats["top_low_criteria"]:
        first = stats["top_low_criteria"][0]
        conclusion += f"<br>Главная зона роста — <b>{esc(first[0])}</b> ({first[1]} срабатываний)."

    body = f"""<div class="breadcrumb"><a href="index.html">Главная</a> › Отчёт РОПа</div>
<div class="page-head"><div><h1>Сводный отчёт</h1><div class="sub">{COMPANY_NAME} · {today}</div></div></div>
<div class="kpi-row">
  <div class="kpi"><div class="kpi-label">Всего звонков</div><div class="kpi-value">{stats['total']}</div></div>
  <div class="kpi amber"><div class="kpi-label">Проанализировано</div><div class="kpi-value">{len(analyses)}</div></div>
  <div class="kpi green"><div class="kpi-label">Средняя оценка</div><div class="kpi-value">{avg or '—'}</div></div>
  <div class="kpi red"><div class="kpi-label">Критичных</div><div class="kpi-value">{critical_count}</div></div>
</div>
<div class="recommendation"><div class="label">📋 Вывод по отделу за день</div><div class="text">{conclusion}</div></div>
{top_low_html}{top_trig_html}{drop_html}{crit_html}"""
    return page_template("Отчёт РОПа", body, "rop_report", generated_at, critical_count)


def render_managers_list(stats: Dict, generated_at: str) -> str:
    rows = ""
    for i, m in enumerate(stats["managers"], 1):
        rank_class = ["", "gold", "silver", "bronze"][min(i, 3)] if i <= 3 else ""
        avatar_class = manager_color_class(m["id"])
        if m.get("avg_score") is not None:
            avg_cls = score_color(m["avg_score"])
            avg_html = f'<div class="m-avg {avg_cls}">{m["avg_score"]}</div>'
            stats_line = f'оценок: <b>{m["analyzed"]}</b>'
            if m["critical"]: stats_line += f' · 🔴 <b>{m["critical"]}</b>'
        else:
            avg_html = '<div class="m-avg empty">—</div>'
            stats_line = f'входящих: <b>{m["in"]}</b> · исходящих: <b>{m["out"]}</b>'
        rows += f"""<a href="managers/{m['id']}.html" class="row-link"><div class="manager-row">
  <div class="rank {rank_class}">{i}</div>
  <div class="manager-info"><div class="m-avatar {avatar_class}">{manager_avatar_html(m)}</div>
  <div><div class="m-name">{esc(m['name'])}</div><div class="m-stats">{stats_line}</div></div></div>
  {avg_html}<div class="m-count">{m['count']}</div>
</div></a>"""
    if not rows: rows = '<div class="empty">Нет</div>'
    body = f"""<div class="breadcrumb"><a href="index.html">Главная</a> › Менеджеры</div>
<div class="page-head"><div><h1>Менеджеры</h1><div class="sub">{COMPANY_NAME}</div></div></div>
<div class="panel"><div class="panel-head"><h3>Рейтинг по качеству</h3></div>{rows}</div>"""
    return page_template("Менеджеры", body, "managers", generated_at, stats["critical_count"])


def render_manager_page(manager: Dict, analyses: Dict, generated_at: str, critical_count: int) -> str:
    """Дневной отчёт по менеджеру (раздел 12 ТЗ)."""
    report = compute_manager_daily_report(manager, analyses)
    calls = sorted(manager["calls"], key=lambda x: x.get("created", ""), reverse=True)
    
    kpis = f"""<div class="kpi-row">
      <div class="kpi"><div class="kpi-label">Звонков</div><div class="kpi-value">{manager['count']}</div></div>
      <div class="kpi green"><div class="kpi-label">Входящих</div><div class="kpi-value">{manager['in']}</div></div>
      <div class="kpi accent"><div class="kpi-label">Исходящих</div><div class="kpi-value">{manager['out']}</div></div>
      <div class="kpi amber"><div class="kpi-label">Проанализировано</div><div class="kpi-value">{report['analyzed_count']}</div></div>"""
    if report["avg_score"] is not None:
        kpis += f'<div class="kpi"><div class="kpi-label">Средняя оценка</div><div class="kpi-value">{report["avg_score"]}</div></div>'
    if manager.get("critical", 0) > 0:
        kpis += f'<div class="kpi red"><div class="kpi-label">Критичных</div><div class="kpi-value">{manager["critical"]}</div></div>'
    kpis += "</div>"

    main_problem_html = ""
    if report["main_problem_day"]:
        main_problem_html = f'<div class="main-problem"><div class="label">⚠️ Главная проблема дня</div><div class="text">{esc(report["main_problem_day"])}</div></div>'

    strengths_html = ""
    if report["strengths_top"]:
        items = "".join(f"<li>{esc(s)} <span style='color:var(--text-muted)'>({cnt}×)</span></li>" for s, cnt in report["strengths_top"])
        strengths_html = f'<div class="sw-card strengths"><h4>✓ Сильные стороны</h4><ul>{items}</ul></div>'
    weak_html = ""
    if report["weaknesses_top"]:
        items = "".join(f"<li>{esc(w)} <span style='color:var(--text-muted)'>({cnt}×)</span></li>" for w, cnt in report["weaknesses_top"])
        weak_html = f'<div class="sw-card weaknesses"><h4>✗ Зоны роста</h4><ul>{items}</ul></div>'
    sw_html = f'<div class="sw-grid">{strengths_html}{weak_html}</div>' if (strengths_html or weak_html) else ""

    # Категории CRM-контекста
    ctx_html = ""
    if report["crm_contexts"]:
        items = "".join(f'<div class="alignment-row"><div class="alignment-stage">{esc(k)}</div><div class="alignment-status full">{v}</div></div>' for k, v in report["crm_contexts"].items())
        ctx_html = f'<div class="panel"><div class="panel-head"><h3>По категориям CRM</h3></div>{items}</div>'
    
    goals_html = ""
    if report["goals"]:
        items = "".join(f'<div class="alignment-row"><div class="alignment-stage">{esc(k)}</div><div class="alignment-status full">{v}</div></div>' for k, v in report["goals"].items())
        goals_html = f'<div class="panel"><div class="panel-head"><h3>По целям разговора</h3></div>{items}</div>'

    # Средние оценки по критериям
    crit_avg_html = ""
    if report["criteria_avgs"]:
        rows_a = ""
        for crit, avg in sorted(report["criteria_avgs"].items(), key=lambda x: x[1]):
            cls = score_color(avg)
            width = max(0, min(100, int(avg * 10)))
            rows_a += f"""<div class="score-row"><div class="score-name">{esc(crit)}</div>
<div class="score-bar"><div class="score-fill {cls}" style="width:{width}%"></div></div>
<div class="score-val {cls}">{avg}</div></div>"""
        crit_avg_html = f'<div class="scores-panel"><div class="panel-head"><h3>Средние оценки по критериям</h3></div>{rows_a}</div>'

    # Низкобалльные звонки
    low_html = ""
    if report["low_score_calls"]:
        items = ""
        for c, an in report["low_score_calls"]:
            score = an.get("overall_score", 0)
            cls = "crit" if an.get("is_critical") else score_color(score)
            items += f"""<a href="../calls/{esc(c['activity_id'])}.html" class="row-link"><div class="call-row">
  <div class="call-direction {'in' if c.get('direction') == 'incoming' else 'out'}">{'↓' if c.get('direction') == 'incoming' else '↑'}</div>
  <div class="call-info"><div class="call-client">{esc(c.get('client', {}).get('name', ''))}</div>
  <div class="call-meta">{esc(an.get('main_problem', ''))}</div></div>
  <div class="call-time">{format_time(c.get('created', ''))}</div><div class="mini-score {cls}">{score}</div>
</div></a>"""
        low_html = f'<div class="panel"><div class="panel-head"><h3>📉 Звонки с низкой оценкой</h3></div>{items}</div>'

    rows = "".join(render_call_row(c, show_manager=False, analysis=analyses.get(c["activity_id"]), base_calls_path="../calls/") for c in calls)
    if not rows: rows = '<div class="empty">Нет</div>'
    avatar_class = manager_color_class(manager["id"])

    body = f"""<div class="breadcrumb"><a href="../index.html">Главная</a> › <a href="../managers.html">Менеджеры</a> › {esc(manager['name'])}</div>
<div class="page-head"><div style="display:flex; align-items:center; gap:18px;">
<div class="m-avatar {avatar_class}" style="width:64px; height:64px; font-size:20px;">{manager_avatar_html(manager, base_path="../")}</div>
<div><h1>{esc(manager['name'])}</h1><div class="sub">Дневной отчёт</div></div></div></div>
{kpis}{main_problem_html}{sw_html}
<div class="sw-grid">{ctx_html}{goals_html}</div>
{crit_avg_html}{low_html}
<div class="panel"><div class="panel-head"><h3>Все звонки</h3><span class="hint">{len(calls)}</span></div>{rows}</div>"""
    return page_template(manager["name"], body, "managers", generated_at, critical_count)


def render_call_page(call: Dict, analysis_data: Dict, generated_at: str, critical_count: int) -> str:
    direction = call.get("direction", "")
    dir_label = direction_label(direction)
    client = call.get("client", {})
    manager = call.get("manager", {})
    bitrix_link = crm_link(call)
    bitrix_call_url = call.get("bitrix_url", "")
    activity_id = call["activity_id"]

    crm_link_html = ""
    if bitrix_link:
        owner_type_label = {"deal": "сделка", "lead": "лид", "contact": "контакт", "company": "компания"}.get(call.get("crm", {}).get("owner_type", ""), "запись")
        crm_link_html = f'<a href="{esc(bitrix_link)}" target="_blank">{owner_type_label} №{esc(call.get("crm", {}).get("owner_id", ""))}</a>'

    has_ai = analysis_data is not None
    analysis = (analysis_data or {}).get("analysis", {}) if has_ai else {}
    transcription = (analysis_data or {}).get("transcription", {}) if has_ai else {}
    is_crit = analysis.get("is_critical", False)
    crit_reason = analysis.get("critical_reason", "")
    is_corrected = analysis.get("manually_corrected", False)

    tags_html = f'<span class="call-tag accent">{esc(dir_label)}</span>'
    if has_ai:
        cls_data = analysis.get("classification", {})
        if cls_data.get("crm_context"):
            tags_html += f'<span class="call-tag">{esc(cls_data["crm_context"])}</span>'
        for g in (cls_data.get("goals") or []):
            tags_html += f'<span class="call-tag green">{esc(g)}</span>'
        triggers_count = len(analysis.get("triggers", []) or [])
        if triggers_count > 0:
            tags_html += f'<span class="call-tag warning">Триггеров: {triggers_count}</span>'

    critical_badge = ""
    if is_crit:
        critical_badge = f'<div class="critical-badge">🔴 СРОЧНО · {esc(crit_reason)}</div>'
    if is_corrected:
        critical_badge += f'<div class="corrected-badge">✏️ Правка РОПа</div>'

    if has_ai:
        score = analysis.get("overall_score")
        if score is not None:
            sc_cls = "crit" if is_crit else score_color(score)
            score_html = f'''<div class="score-big {sc_cls}">
<div class="num">{score}<span class="max">/10</span></div>
<div class="label">Оценка</div>
<button class="edit-score-btn" onclick="openEditModal('{esc(activity_id)}', {score})">✏️ Изменить</button>
</div>'''
        else:
            score_html = '<div class="score-big placeholder"><div class="num">—</div><div class="label">нет</div></div>'
    else:
        score_html = '<div class="score-big placeholder"><div class="num">—</div><div class="label">не анализирован</div></div>'

    duration_html = ""
    dur = call.get("duration_sec") or transcription.get("duration_sec")
    if dur:
        duration_html = f'<div class="detail"><div class="detail-label">Длительность</div><div class="detail-value">{format_duration(dur)}</div></div>'

    card_classes = "call-card critical" if is_crit else "call-card"
    head_html = f"""<div class="{card_classes}"><div class="call-card-head"><div>
{critical_badge}
<h2>{esc(client.get('name', 'Неизвестный клиент'))}{' · ' + esc(client.get('company')) if client.get('company') else ''}</h2>
<div class="call-tags">{tags_html}</div></div>{score_html}</div>
<div class="detail-grid">
  <div class="detail"><div class="detail-label">Менеджер</div><div class="detail-value"><a href="../managers/{manager.get('id', 0)}.html">{esc(manager.get('name', ''))}</a></div></div>
  <div class="detail"><div class="detail-label">Телефон</div><div class="detail-value">{esc(client.get('phone_masked') or '—')}</div></div>
  <div class="detail"><div class="detail-label">Время</div><div class="detail-value">{format_datetime(call.get('created', ''))}</div></div>
  {duration_html}
  <div class="detail"><div class="detail-label">Направление</div><div class="detail-value">{esc(dir_label)}</div></div>
  <div class="detail"><div class="detail-label">В CRM</div><div class="detail-value">{crm_link_html or '—'}</div></div>
</div></div>"""

    audio = call.get("audio") or {}
    public_path = audio.get("public_path")
    audio_html = ""
    if public_path:
        bitrix_btn = f'<a href="{esc(bitrix_call_url)}" target="_blank" class="bitrix-link">Открыть в Bitrix24</a>' if bitrix_call_url else ""
        audio_html = f'<div class="audio-player"><audio controls preload="metadata" src="../{esc(public_path)}"></audio>{bitrix_btn}</div>'
    elif bitrix_call_url:
        audio_html = f'<div class="audio-missing">🎵 Запись в Bitrix24: <a href="{esc(bitrix_call_url)}" target="_blank">открыть карточку звонка →</a></div>'

    # Модальное окно для правки оценки
    modal_html = """
<div class="modal-overlay" id="editModal">
  <div class="modal">
    <h3>Изменить оценку звонка</h3>
    <p>Введите новую оценку. После нажатия «Скопировать» JSON будет в буфере обмена — вставьте его в файл <code>manual_corrections.json</code> на GitHub.</p>
    <p><b>ID звонка:</b> <span id="editCallId"></span></p>
    <input type="number" id="newScore" min="0" max="10" step="0.1" placeholder="Например: 7.5">
    <textarea id="editComment" placeholder="Комментарий (опционально): почему меняете оценку"></textarea>
    <div class="modal-buttons">
      <button class="btn secondary" onclick="closeEditModal()">Отмена</button>
      <button class="btn primary" onclick="copyCorrection()">Скопировать JSON и открыть GitHub</button>
    </div>
  </div>
</div>
<script>
var currentEditId = null;
function openEditModal(id, currentScore) {
  currentEditId = id;
  document.getElementById('editCallId').textContent = id;
  document.getElementById('newScore').value = currentScore;
  document.getElementById('editComment').value = '';
  document.getElementById('editModal').classList.add('show');
}
function closeEditModal() {
  document.getElementById('editModal').classList.remove('show');
}
function copyCorrection() {
  var newScore = parseFloat(document.getElementById('newScore').value);
  var comment = document.getElementById('editComment').value;
  if (isNaN(newScore)) { alert('Введите число от 0 до 10'); return; }
  var correction = {};
  correction[currentEditId] = { overall_score: newScore, comment: comment };
  var jsonStr = JSON.stringify(correction, null, 2);
  navigator.clipboard.writeText(jsonStr).then(function() {
    alert('JSON скопирован в буфер обмена!\\n\\nСейчас откроется файл manual_corrections.json — вставьте туда содержимое.');
    window.open('https://github.com/mavisgroupiishki-alt/sales-analytics/edit/main/manual_corrections.json', '_blank');
    closeEditModal();
  }).catch(function() {
    prompt('Скопируйте этот текст:', jsonStr);
  });
}
</script>"""

    if not has_ai:
        rest_html = '<div class="placeholder-panel"><h3>📝 Транскрипт</h3><p>Звонок ещё не прошёл ИИ-анализ.</p></div>'
    else:
        summary_html = ""
        if analysis.get("summary"):
            summary_html += f'<div class="ai-summary"><div class="label">📋 Резюме</div><div class="text">{esc(analysis["summary"])}</div></div>'
        if analysis.get("outcome"):
            summary_html += f'<div class="ai-summary"><div class="label">🎯 Итог</div><div class="text">{esc(analysis["outcome"])}</div></div>'
        for kq in (analysis.get("key_quotes") or []):
            if not kq: continue
            time_str = kq.get("time", "")
            who = "клиент" if kq.get("speaker") == "client" else "менеджер"
            meta = f"— {esc(who)}"
            if time_str: meta += f" · {esc(time_str)}"
            summary_html += f'<div class="ai-key-quote">{esc(kq.get("text", ""))}<span class="meta">{meta}</span></div>'

        main_problem_html = ""
        if analysis.get("main_problem"):
            main_problem_html = f'<div class="main-problem"><div class="label">⚠️ Основная проблема</div><div class="text">{esc(analysis["main_problem"])}</div></div>'

        # Сильные/слабые
        sw_html = ""
        s_items = analysis.get("strengths") or []
        w_items = analysis.get("weaknesses") or []
        if s_items or w_items:
            s_html = ""
            if s_items:
                items = "".join(f"<li>{esc(s)}</li>" for s in s_items)
                s_html = f'<div class="sw-card strengths"><h4>✓ Сильные стороны</h4><ul>{items}</ul></div>'
            w_html = ""
            if w_items:
                items = "".join(f"<li>{esc(w)}</li>" for w in w_items)
                w_html = f'<div class="sw-card weaknesses"><h4>✗ Зоны роста</h4><ul>{items}</ul></div>'
            sw_html = f'<div class="sw-grid">{s_html}{w_html}</div>'

        # Дата следующего контакта
        nc_html = ""
        nc = analysis.get("next_contact")
        if nc and nc.get("date_or_period"):
            nc_html = f"""<div class="next-contact"><div class="label">📅 Следующий контакт</div>
<div class="text">{esc(nc.get('date_or_period', ''))}{f" в {esc(nc.get('time'))}" if nc.get('time') else ''}</div>
<div class="sub">{esc(nc.get('context', ''))} · инициатор: {esc(nc.get('initiator', '—'))}</div></div>"""

        # Скрипты
        scripts_used = analysis.get("scripts_used") or []
        scripts_html = ""
        if scripts_used:
            tags = "".join(f'<span class="tag">{esc(s)}</span>' for s in scripts_used)
            scripts_html = f'<div class="scripts-used"><b>Сравнение со скриптами:</b> {tags}</div>'

        # Сопоставление по этапам
        alignment_html = ""
        alignment = analysis.get("scripts_alignment") or []
        if alignment:
            items = ""
            for a in alignment:
                status = a.get("status", "").lower()
                if "соблюд" in status: cls = "full"
                elif "част" in status: cls = "partial"
                else: cls = "missing"
                evidence_html = f'<div class="alignment-evidence">{esc(a.get("evidence", ""))} <span class="crit-time">{esc(a.get("time", ""))}</span></div>' if a.get("evidence") else ""
                items += f'<div class="alignment-row"><div><div class="alignment-stage">{esc(a.get("stage", ""))}</div>{evidence_html}</div><div class="alignment-status {cls}">{esc(a.get("status", ""))}</div></div>'
            alignment_html = f'<div class="scripts-alignment"><div class="panel-head"><h3>Соответствие этапам скрипта</h3></div>{items}</div>'

        # Транскрипт
        transcript_html = ""
        ts = analysis.get("transcript_split") or []
        if ts:
            msgs = ""
            for m in ts:
                who = m.get("speaker", "")
                who_label = "менеджер" if who == "manager" else "клиент"
                time_str = esc(m.get("time", ""))
                msgs += f'<div class="msg"><div class="msg-time">{time_str}</div><div class="msg-body"><div class="msg-who {esc(who)}">{esc(who_label)}</div><div class="msg-text">{esc(m.get("text", ""))}</div></div></div>'
            transcript_html = f'<div class="transcript-panel"><div class="transcript-head"><h3>📝 Транскрипт</h3><span class="hint">{len(ts)} реплик · {format_duration(transcription.get("duration_sec"))}</span></div><div class="transcript-body">{msgs}</div></div>'

        # Оценки
        scores_html = ""
        scores = analysis.get("scores") or {}
        if scores:
            rows_s = ""
            for crit, val in scores.items():
                try: val_f = float(val)
                except: val_f = 0
                cls = score_color(val_f)
                width = max(0, min(100, int(val_f * 10)))
                rows_s += f'<div class="score-row"><div class="score-name">{esc(crit)}</div><div class="score-bar"><div class="score-fill {cls}" style="width:{width}%"></div></div><div class="score-val {cls}">{val_f}</div></div>'
            scores_html = f'<div class="scores-panel"><div class="panel-head"><h3>📊 Оценка по 17 критериям</h3></div>{rows_s}</div>'

        # Низкие баллы с детализацией
        low_details_html = ""
        for lsd in (analysis.get("low_score_details") or []):
            quote = lsd.get("quote", "")
            time_str = lsd.get("time", "")
            low_details_html += f"""<div class="low-score-detail">
<div class="crit-name">{esc(lsd.get('criterion', ''))} — {esc(lsd.get('score', ''))}/10</div>
<div>{esc(lsd.get('problem', ''))}</div>
{f'<div class="crit-quote">«{esc(quote)}» <span class="crit-time">{esc(time_str)}</span></div>' if quote else ''}
<div class="crit-reco">💡 {esc(lsd.get('recommendation', ''))}</div></div>"""
        if low_details_html:
            low_details_html = f'<div class="panel"><div class="panel-head"><h3>⚠️ Низкие баллы (<6)</h3></div><div style="padding:14px 18px;">{low_details_html}</div></div>'

        # Триггеры
        trig_html = ""
        triggers = analysis.get("triggers") or []
        if triggers:
            items = ""
            for t in triggers:
                time_str = t.get("time", "")
                tc = f'<span class="timecode">{esc(time_str)}</span>' if time_str else ""
                desc = t.get("description", "")
                items += f'<div class="trigger-row"><div class="trigger-marker"></div><div class="trigger-text">{esc(t.get("name", ""))}{tc}{f"<div class=\\"sub\\">{esc(desc)}</div>" if desc else ""}</div></div>'
            trig_html = f'<div class="trigger-list"><div class="panel-head"><h3>⚠️ Триггеры</h3><span class="hint">{len(triggers)}</span></div>{items}</div>'

        # Рекомендация
        reco_html = ""
        if analysis.get("recommendation"):
            reco_html = f'<div class="recommendation"><div class="label">💡 Рекомендация</div><div class="text">{esc(analysis["recommendation"])}</div></div>'

        rest_html = summary_html + main_problem_html + nc_html + sw_html + scripts_html + alignment_html + transcript_html + scores_html + low_details_html + trig_html + reco_html

    body = f'<div class="breadcrumb"><a href="../index.html">Главная</a> › <a href="../all-calls.html">Звонки</a> › Звонок №{esc(activity_id)}</div>{head_html}{audio_html}{rest_html}{modal_html}'
    return page_template(f"Звонок №{activity_id}", body, "calls", generated_at, critical_count)


def render_all_calls(calls: List[Dict], analyses: Dict, generated_at: str, critical_count: int) -> str:
    sorted_calls = sorted(calls, key=lambda x: x.get("created", ""), reverse=True)
    rows = "".join(render_call_row(c, analysis=analyses.get(c["activity_id"])) for c in sorted_calls)
    if not rows: rows = '<div class="empty">Нет</div>'
    body = f"""<div class="breadcrumb"><a href="index.html">Главная</a> › Все звонки</div>
<div class="page-head"><div><h1>Все звонки</h1><div class="sub">{len(calls)} штук</div></div></div>
<input type="text" class="search-box" id="searchInput" placeholder="🔍 Поиск..." oninput="filterCalls()">
<div class="filter-row">
  <button class="filter-btn active" onclick="setFilter('all', this)">Все</button>
  <button class="filter-btn" onclick="setFilter('incoming', this)">Входящие</button>
  <button class="filter-btn" onclick="setFilter('outgoing', this)">Исходящие</button>
  <button class="filter-btn" onclick="setFilter('critical', this)">🔴 Критичные</button>
</div>
<div class="panel"><div class="panel-head"><h3>Список</h3><span class="hint" id="visibleCount">{len(calls)} видно</span></div><div id="callsList">{rows}</div></div>
<script>
var currentFilter = 'all';
function setFilter(f, btn) {{
  currentFilter = f;
  document.querySelectorAll('.filter-btn').forEach(function(b){{ b.classList.remove('active'); }});
  btn.classList.add('active'); filterCalls();
}}
function filterCalls() {{
  var q = document.getElementById('searchInput').value.toLowerCase();
  var rows = document.querySelectorAll('#callsList .row-link');
  var v = 0;
  rows.forEach(function(r) {{
    var t = r.textContent.toLowerCase();
    var d = r.querySelector('.call-direction').classList;
    var matchText = !q || t.indexOf(q) !== -1;
    var matchFilter = currentFilter === 'all' || (currentFilter === 'incoming' && d.contains('in')) || (currentFilter === 'outgoing' && d.contains('out')) || (currentFilter === 'critical' && r.classList.contains('critical'));
    var show = matchText && matchFilter;
    r.style.display = show ? '' : 'none';
    if (show) v++;
  }});
  document.getElementById('visibleCount').textContent = v + ' видно';
}}
</script>"""
    return page_template("Все звонки", body, "calls", generated_at, critical_count)


def render_critical_page(calls: List[Dict], analyses: Dict, generated_at: str, critical_count: int) -> str:
    critical_calls = []
    for c in calls:
        a = analyses.get(c["activity_id"])
        if a and a.get("analysis", {}).get("is_critical"):
            critical_calls.append((c, a))
    critical_calls.sort(key=lambda x: x[0].get("created", ""), reverse=True)

    body_top = f"""<div class="breadcrumb"><a href="index.html">Главная</a> › Срочно</div>
<div class="page-head"><div><h1>🔴 Срочно</h1><div class="sub">Критичные звонки</div></div></div>"""

    if not critical_calls:
        body = body_top + '<div class="notice success"><b>✓ Критичных нет.</b></div>'
    else:
        rows = ""
        for c, a in critical_calls:
            an = a["analysis"]
            score = an.get("overall_score", 0)
            reason = an.get("critical_reason", "")
            duration_html = f'<div class="call-duration">{format_duration(c.get("duration_sec"))}</div>' if c.get("duration_sec") else ""
            rows += f"""<a href="calls/{esc(c['activity_id'])}.html" class="row-link critical"><div class="call-row">
<div class="call-direction {'in' if c.get('direction') == 'incoming' else 'out'}">{'↓' if c.get('direction') == 'incoming' else '↑'}</div>
<div class="call-info"><div class="call-client">{esc(c.get('client', {}).get('name', ''))} · {esc(c.get('manager', {}).get('name', ''))}</div>
<div class="call-meta"><b>{esc(reason)}</b> · {esc(an.get('main_problem', ''))}</div></div>
{duration_html}<div class="call-time">{format_time(c.get('created', ''))}</div><div class="mini-score crit">{score}</div>
</div></a>"""
        body = body_top + f'<div class="panel"><div class="panel-head"><h3>Найдено</h3><span class="hint">{len(critical_calls)}</span></div>{rows}</div>'
    return page_template("Срочно", body, "critical", generated_at, critical_count)


def render_triggers_page(calls: List[Dict], analyses: Dict, generated_at: str, critical_count: int) -> str:
    triggers_data = defaultdict(list)
    for c in calls:
        a = analyses.get(c["activity_id"])
        if a:
            for t in a.get("analysis", {}).get("triggers", []):
                triggers_data[t.get("name", "")].append((c, a, t))

    body_top = f"""<div class="breadcrumb"><a href="index.html">Главная</a> › Триггеры</div>
<div class="page-head"><div><h1>⚠️ Триггеры</h1><div class="sub">По типам</div></div></div>"""

    if not triggers_data:
        body = body_top + '<div class="placeholder-panel"><h3>⏳ Триггеры пока не зафиксированы</h3></div>'
    else:
        sections = ""
        for trig_name, items in sorted(triggers_data.items(), key=lambda x: -len(x[1])):
            rows = ""
            for c, a, t in items[:10]:
                an = a["analysis"]
                score = an.get("overall_score", 0)
                cls = "crit" if an.get("is_critical") else score_color(score)
                duration_html = f'<div class="call-duration">{format_duration(c.get("duration_sec"))}</div>' if c.get("duration_sec") else ""
                rows += f"""<a href="calls/{esc(c['activity_id'])}.html" class="row-link"><div class="call-row">
<div class="call-direction {'in' if c.get('direction') == 'incoming' else 'out'}">{'↓' if c.get('direction') == 'incoming' else '↑'}</div>
<div class="call-info"><div class="call-client">{esc(c.get('client', {}).get('name', ''))} · {esc(c.get('manager', {}).get('name', ''))}</div>
<div class="call-meta">{esc(t.get('description', ''))}</div></div>
{duration_html}<div class="call-time">{format_time(c.get('created', ''))}</div><div class="mini-score {cls}">{score}</div>
</div></a>"""
            sections += f'<div class="panel"><div class="panel-head"><h3>{esc(trig_name)}</h3><span class="hint">{len(items)}</span></div>{rows}</div>'
        body = body_top + sections
    return page_template("Триггеры", body, "triggers", generated_at, critical_count)


def generate(calls_json_path: str = "calls_data.json", analyses_json_path: str = "analyses.json", output_dir: str = "docs"):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    out_dir = Path(output_dir)
    out_dir.mkdir(exist_ok=True)
    (out_dir / "managers").mkdir(exist_ok=True)
    (out_dir / "calls").mkdir(exist_ok=True)

    calls = json.loads(Path(calls_json_path).read_text(encoding="utf-8")) if Path(calls_json_path).exists() else []
    analyses = json.loads(Path(analyses_json_path).read_text(encoding="utf-8")) if Path(analyses_json_path).exists() else {}

    logger.info(f"Звонков: {len(calls)}, анализов: {len(analyses)}")
    stats = compute_stats(calls, analyses)
    generated_at = datetime.now().isoformat()
    crit = stats["critical_count"]

    (out_dir / "index.html").write_text(render_index(calls, stats, analyses, generated_at), encoding="utf-8")
    (out_dir / "rop-report.html").write_text(render_rop_report(calls, stats, analyses, generated_at), encoding="utf-8")
    (out_dir / "managers.html").write_text(render_managers_list(stats, generated_at), encoding="utf-8")
    (out_dir / "all-calls.html").write_text(render_all_calls(calls, analyses, generated_at, crit), encoding="utf-8")
    (out_dir / "critical.html").write_text(render_critical_page(calls, analyses, generated_at, crit), encoding="utf-8")
    (out_dir / "triggers.html").write_text(render_triggers_page(calls, analyses, generated_at, crit), encoding="utf-8")

    for m in stats["managers"]:
        html_content = render_manager_page(m, analyses, generated_at, crit)
        for old, new in [('href="index.html"', 'href="../index.html"'), ('href="rop-report.html"', 'href="../rop-report.html"'), ('href="managers.html"', 'href="../managers.html"'), ('href="all-calls.html"', 'href="../all-calls.html"'), ('href="critical.html"', 'href="../critical.html"'), ('href="triggers.html"', 'href="../triggers.html"'), ('src="logo.png"', 'src="../logo.png"')]:
            html_content = html_content.replace(old, new)
        (out_dir / "managers" / f"{m['id']}.html").write_text(html_content, encoding="utf-8")

    for call in calls:
        analysis_data = analyses.get(call["activity_id"])
        html_content = render_call_page(call, analysis_data, generated_at, crit)
        for old, new in [('href="index.html"', 'href="../index.html"'), ('href="rop-report.html"', 'href="../rop-report.html"'), ('href="managers.html"', 'href="../managers.html"'), ('href="all-calls.html"', 'href="../all-calls.html"'), ('href="critical.html"', 'href="../critical.html"'), ('href="triggers.html"', 'href="../triggers.html"'), ('src="logo.png"', 'src="../logo.png"')]:
            html_content = html_content.replace(old, new)
        (out_dir / "calls" / f"{call['activity_id']}.html").write_text(html_content, encoding="utf-8")

    (out_dir / ".nojekyll").write_text("", encoding="utf-8")
    print(f"\n✅ Сайт сгенерирован")
    print(f"   Звонков: {len(calls)}, анализов: {len(analyses)}, критичных: {crit}")


if __name__ == "__main__":
    generate()
