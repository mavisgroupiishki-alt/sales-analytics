"""Operational dashboard for the Jarvis Flask application."""

from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Any, Dict, Iterable, List

from claude_analyzer import evaluate_triage


_AQUA_CSS = r'''
.jd-heading p,.jd-metrics span,.jd-panel-head p,.jd-panel-head>span,.jd-action small,.jd-review small,.jd-feed small,.jd-manager small,.jd-review-reason,.jd-call-type,.jd-empty{color:var(--muted)}.jd-source i{background:var(--amber)}.jd-source.ok i{background:var(--green)}.jd-source b{color:var(--green)}.jd-source.warning b{color:var(--amber)}.jd-metric-critical b{color:var(--red)}.jd-metric-review b{color:var(--amber)}.jd-action em{color:#9c525b}.jd-arrow,.jd-panel-head a{color:#109d9a}.jd-empty b{color:var(--ink)}.jd-avatar{background:#dff7f5;color:#187e82}.jd-status.critical,.jd-feed-status.critical{background:var(--red-soft);color:var(--red)}.jd-status.review,.jd-feed-status.needs_review,.jd-review-score{background:var(--amber-soft);color:var(--amber)}.jd-status.normal,.jd-feed-status.normal{background:var(--green-soft);color:var(--green)}.jd-dot{background:#a7b6bd}.jd-dot.critical{background:var(--red)}.jd-dot.needs_review{background:#e5a735}.jd-feed-status.pending{background:#eef4f4;color:#617982}
'''


def _text(value: Any) -> str:
    return escape(str(value or ""))


def _analysis_for(analyses: Dict[str, Any], call: Dict[str, Any]) -> Dict[str, Any]:
    return (analyses.get(str(call.get("activity_id")), {}) or {}).get("analysis") or {}


def triage_for(analysis: Dict[str, Any]) -> tuple[str, str, str]:
    """Classify both new and legacy analyses with the current strict rules."""
    return evaluate_triage(analysis or {})


def _format_timestamp(value: str) -> str:
    if not value:
        return "нет данных"
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%d.%m.%Y, %H:%M")
    except ValueError:
        return value


def _avatar(manager: Dict[str, Any]) -> str:
    name = str(manager.get("name") or "Менеджер")
    initials = "".join(part[:1] for part in name.split()[:2]).upper() or "М"
    avatar_file = str(manager.get("avatar_file") or "")
    image = ""
    if avatar_file:
        image = (
            f'<img src="/avatars/{escape(avatar_file)}" alt="" '
            "onload=\"this.parentElement.classList.add('has-image')\" "
            "onerror=\"this.remove()\">"
        )
    return f'<span class="jd-avatar">{image}<span>{escape(initials)}</span></span>'


def _freshness(calls: Iterable[Dict[str, Any]]) -> tuple[str, str]:
    values = [str(call.get("created") or "") for call in calls if call.get("created")]
    if not values:
        return "нет данных", "unknown"
    latest = max(values)
    try:
        age = datetime.now(datetime.fromisoformat(latest.replace("Z", "+00:00")).tzinfo) - datetime.fromisoformat(latest.replace("Z", "+00:00"))
        return _format_timestamp(latest), "fresh" if age.total_seconds() <= 26 * 3600 else "stale"
    except ValueError:
        return _format_timestamp(latest), "unknown"


def dashboard_model(calls: List[Dict[str, Any]], analyses: Dict[str, Any]) -> Dict[str, Any]:
    """Return only deterministic, source-backed management indicators."""
    analyzed = []
    critical = []
    review = []
    by_manager: Dict[int, Dict[str, Any]] = {}

    for call in calls:
        analysis = _analysis_for(analyses, call)
        manager = call.get("manager") or {}
        manager_id = int(manager.get("id") or 0)
        entry = by_manager.setdefault(
            manager_id,
            {"manager": manager, "calls": 0, "analyzed": 0, "critical": 0, "review": 0, "scores": []},
        )
        entry["calls"] += 1
        if not analysis:
            continue
        analyzed.append(call)
        entry["analyzed"] += 1
        score = analysis.get("overall_score")
        if isinstance(score, (int, float)):
            entry["scores"].append(float(score))
        status, reason, rule_id = triage_for(analysis)
        record = {"call": call, "analysis": analysis, "reason": reason, "rule_id": rule_id}
        if status == "critical":
            critical.append(record)
            entry["critical"] += 1
        elif status == "needs_review":
            review.append(record)
            entry["review"] += 1

    managers = []
    for entry in by_manager.values():
        scores = entry.pop("scores")
        entry["average"] = round(sum(scores) / len(scores), 1) if scores else None
        managers.append(entry)
    managers.sort(key=lambda item: (item["critical"], item["review"], -(item["average"] or 0)), reverse=True)

    newest = sorted(calls, key=lambda call: str(call.get("created") or ""), reverse=True)
    critical.sort(key=lambda item: str(item["call"].get("created") or ""), reverse=True)
    review.sort(key=lambda item: str(item["call"].get("created") or ""), reverse=True)
    return {
        "calls": len(calls),
        "analyzed": len(analyzed),
        "incoming": sum(call.get("direction") == "incoming" for call in calls),
        "outgoing": sum(call.get("direction") == "outgoing" for call in calls),
        "critical": critical,
        "review": review,
        "managers": managers,
        "freshness": _freshness(calls),
        "newest": newest[:6],
    }


def render_dashboard(calls: List[Dict[str, Any]], analyses: Dict[str, Any], user: Dict[str, Any]) -> str:
    model = dashboard_model(calls, analyses)
    fresh_at, fresh_state = model["freshness"]
    source_text = "данные актуальны" if fresh_state == "fresh" else "требуется обновление"
    source_class = "ok" if fresh_state == "fresh" else "warning"
    role = "РОП" if user.get("role") in {"rop", "director"} else "Менеджер"
    today = datetime.now().strftime("%d.%m.%Y")

    alerts = ""
    for item in model["critical"][:4]:
        call, analysis = item["call"], item["analysis"]
        client = (call.get("client") or {}).get("name") or "Клиент не определён"
        evidence = ((analysis.get("flags") or {}).get("critical_evidence") or {})
        quote = evidence.get("quote") or item["reason"]
        alerts += f'''<a class="jd-action jd-action-critical" href="/calls/{_text(call.get("activity_id"))}">
          <span class="jd-action-marker">!</span><span><b>{_text(client)}</b><small>{_text(item["reason"])} · {_text(evidence.get("time") or "без таймкода")}</small>
          <em>«{_text(str(quote)[:120])}»</em></span><span class="jd-arrow">→</span></a>'''
    if not alerts:
        alerts = '<div class="jd-empty"><b>Критичных звонков нет.</b><span>Ни один текущий флаг не прошёл проверку правила, цитаты и таймкода.</span></div>'

    reviews = ""
    for item in model["review"][:5]:
        call, analysis = item["call"], item["analysis"]
        client = (call.get("client") or {}).get("name") or "Клиент не определён"
        manager = (call.get("manager") or {}).get("name") or "Менеджер не определён"
        score = analysis.get("overall_score")
        reviews += f'''<a class="jd-review" href="/calls/{_text(call.get("activity_id"))}">
          <span class="jd-review-score">{_text(score if score is not None else "—")}</span>
          <span><b>{_text(client)}</b><small>{_text(manager)} · {_format_timestamp(str(call.get("created") or ""))}</small></span>
          <span class="jd-review-reason">{_text(item["reason"])}</span></a>'''
    if not reviews:
        reviews = '<div class="jd-empty"><b>Очередь проверки пуста.</b><span>Звонки с неясными основаниями появятся здесь.</span></div>'

    managers = ""
    for item in model["managers"][:8]:
        manager = item["manager"]
        average = f'{item["average"]:.1f}' if item["average"] is not None else "—"
        signal = "Критично" if item["critical"] else ("Проверить" if item["review"] else "В норме")
        signal_class = "critical" if item["critical"] else ("review" if item["review"] else "normal")
        managers += f'''<a class="jd-manager" href="/managers/{_text(manager.get("id"))}">
          {_avatar(manager)}<span class="jd-manager-name"><b>{_text(manager.get("name") or "Менеджер")}</b><small>{item["analyzed"]} разборов · {item["calls"]} звонков</small></span>
          <span class="jd-score">{average}</span><span class="jd-status {signal_class}">{signal}</span></a>'''
    if not managers:
        managers = '<div class="jd-empty"><b>Нет менеджеров в выбранной выборке.</b></div>'

    feed = ""
    for call in model["newest"]:
        analysis = _analysis_for(analyses, call)
        status, _, _ = triage_for(analysis) if analysis else ("pending", "", "")
        client = (call.get("client") or {}).get("name") or "Клиент не определён"
        manager = (call.get("manager") or {}).get("name") or ""
        score = analysis.get("overall_score") if analysis else None
        status_label = {"critical": "Критично", "needs_review": "Проверить", "normal": "Без риска", "pending": "Ожидает AI"}[status]
        feed += f'''<a class="jd-feed" href="/calls/{_text(call.get("activity_id"))}">
          <span class="jd-dot {status}"></span><span><b>{_text(client)}</b><small>{_text(manager)} · {_format_timestamp(str(call.get("created") or ""))}</small></span>
          <span class="jd-call-type">{_text((analysis.get("call_type") or {}).get("label") or "Тип не подтверждён")}</span>
          <span class="jd-feed-status {status}">{status_label}</span><span class="jd-feed-score">{_text(score if score is not None else "—")}</span></a>'''
    if not feed:
        feed = '<div class="jd-empty"><b>Звонков пока нет.</b></div>'

    return f'''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Джарвис — центр управления продажами</title><style>{_CSS}{_AQUA_CSS}</style></head>
<body><!-- JARVIS-DIRECTION: THESIS: a ROP navigates one calm control surface, not a dark executive report. OWN-WORLD: turquoise rail, white data tiles, chart-like micro-structure and marine-blue type. STORY: the first thing seen is ДЖАРВИС and the work queue; every tile descends from a real source. FIRST VIEWPORT: the brand leads the left rail; a compact status row starts the workspace. FORM: bright analytics console from the supplied visual reference; seed jarvis-aqua-2026. FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, and DESIGN.md. -->
<header class="jd-top"><a class="jd-brand" href="/"><span class="jd-mark">J</span><span><strong>ДЖАРВИС</strong><small>ЦЕНТР УПРАВЛЕНИЯ ПРОДАЖАМИ</small></span></a>
<nav><a class="active" href="/">Обзор</a><a href="/calls">Звонки</a>{'<a href="/managers">Команда</a><a href="/rop">Отчёт РОПа</a>' if user.get('role') in {'rop', 'director'} else ''}</nav>
<div class="jd-user"><span>{_text(role)} · {_text(user.get('name'))}</span><a href="/logout">Выйти</a></div></header>
<main class="jd-shell"><section class="jd-heading"><div><p>{today}</p><h1>Картина продаж <span>на сейчас</span></h1></div><div class="jd-source {source_class}"><i></i><span>Bitrix24</span><b>{source_text}</b><small>последняя запись: {fresh_at}</small></div></section>
<section class="jd-metrics"><div><small>Звонки в выборке</small><b>{model['calls']}</b><span>{model['incoming']} входящих · {model['outgoing']} исходящих</span></div><div><small>Разобрано AI</small><b>{model['analyzed']}</b><span>{round(model['analyzed'] / model['calls'] * 100) if model['calls'] else 0}% от выборки</span></div><div class="jd-metric-critical"><small>Срочно к РОПу</small><b>{len(model['critical'])}</b><span>только с правилом и доказательством</span></div><div class="jd-metric-review"><small>Нужна проверка</small><b>{len(model['review'])}</b><span>не является тревогой автоматически</span></div></section>
<section class="jd-grid"><section class="jd-panel jd-actions"><div class="jd-panel-head"><div><h2>Действия РОПа</h2><p>Подтверждённые риски, требующие вмешательства</p></div><a href="/critical">Вся очередь →</a></div>{alerts}</section>
<section class="jd-panel jd-team"><div class="jd-panel-head"><div><h2>Команда</h2><p>Кого открыть первым</p></div><a href="/managers">Все менеджеры →</a></div><div class="jd-manager-list">{managers}</div></section>
<section class="jd-panel jd-review-panel"><div class="jd-panel-head"><div><h2>На проверку</h2><p>AI увидел сигнал, но не доказал критичность</p></div><span>{len(model['review'])} звонков</span></div>{reviews}</section>
<section class="jd-panel jd-feed-panel"><div class="jd-panel-head"><div><h2>Последние звонки</h2><p>Первичные записи в хронологическом порядке</p></div><a href="/calls">Открыть журнал →</a></div>{feed}</section></section>
<section class="jd-limits"><b>Граница данных</b><span>План/факт, оплаты, маркетинг и CRM Health ещё не подключены — Джарвис не подменяет их нулевыми или вымышленными показателями.</span></section></main></body></html>'''


_CSS = r'''
*{box-sizing:border-box}body{margin:0;font-family:"Avenir Next",Avenir,"Helvetica Neue",Arial,sans-serif;font-size:14px;line-height:1.4}.jd-brand{color:#fff;text-decoration:none;display:flex;gap:10px;font-weight:800}.jd-brand small{display:block}.jd-mark{display:grid;place-items:center;font-weight:900}nav a{display:flex;align-items:center;text-decoration:none;font-weight:700}.jd-user{font-size:12px}.jd-user a{text-decoration:none;font-weight:700}.jd-heading{display:flex;align-items:flex-end;justify-content:space-between;gap:24px}.jd-heading p{margin:0 0 7px;font-size:12px;font-weight:700}.jd-heading h1{margin:0;line-height:1.05}.jd-source{display:grid;grid-template-columns:9px auto 1fr;gap:7px 9px;align-items:center;padding:12px 14px;font-size:12px}.jd-source i{width:8px;height:8px;border-radius:50%;grid-row:span 2}.jd-source span{font-weight:800}.jd-source b{font-size:11px;text-align:right}.jd-source small{grid-column:2/-1;font-size:10px}.jd-metrics{display:grid;grid-template-columns:1.2fr 1.2fr 1fr 1fr;margin-bottom:20px}.jd-metrics>div{padding:18px 20px;min-height:108px}.jd-metrics small{display:block;font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:.07em}.jd-metrics b{display:block;font-size:33px;line-height:1;margin:10px 0 7px;letter-spacing:-.045em}.jd-metrics span{font-size:11px}.jd-grid{display:grid;grid-template-columns:minmax(0,1.45fr) minmax(350px,.95fr);gap:16px}.jd-panel{overflow:hidden}.jd-panel-head{padding:17px 18px 13px;display:flex;justify-content:space-between;gap:10px;align-items:flex-start;border-bottom:1px solid var(--line)}.jd-panel-head h2{font-size:15px;line-height:1.15;margin:0 0 5px}.jd-panel-head p{margin:0;font-size:11px}.jd-panel-head a{font-size:12px;text-decoration:none;font-weight:800;white-space:nowrap}.jd-panel-head>span{font-size:11px;font-weight:700}.jd-action{display:grid;grid-template-columns:28px minmax(0,1fr) 16px;gap:10px;padding:14px 18px;color:inherit;text-decoration:none;border-bottom:1px solid var(--line)}.jd-action:last-child,.jd-review:last-child,.jd-feed:last-child,.jd-manager:last-child{border-bottom:0}.jd-action-marker{width:22px;height:22px;display:grid;place-items:center;color:#fff;font-size:14px;font-weight:900}.jd-action b,.jd-review b,.jd-feed b,.jd-manager b{display:block;font-size:13px}.jd-action small,.jd-review small,.jd-feed small,.jd-manager small{display:block;font-size:11px;margin-top:3px}.jd-action em{display:block;font-style:normal;font-size:11px;white-space:nowrap;text-overflow:ellipsis;overflow:hidden;margin-top:7px}.jd-arrow{align-self:center;font-size:16px}.jd-empty{padding:25px 20px;display:grid;gap:5px;font-size:12px}.jd-empty b{font-size:13px}.jd-manager{display:grid;grid-template-columns:40px minmax(0,1fr) 44px 74px;gap:10px;align-items:center;padding:12px 18px;color:inherit;text-decoration:none;border-bottom:1px solid var(--line)}.jd-avatar{width:38px;height:38px;border-radius:50%;display:grid;place-items:center;overflow:hidden;font-size:12px;font-weight:800}.jd-avatar img{display:block;width:100%;height:100%;object-fit:cover}.jd-avatar.has-image>span{display:none}.jd-manager-name{min-width:0}.jd-manager-name b{white-space:nowrap;text-overflow:ellipsis;overflow:hidden}.jd-score{font-size:17px;font-weight:800;text-align:right}.jd-status{font-size:10px;font-weight:800;text-align:center;padding:5px 4px;border-radius:6px}.jd-review{display:grid;grid-template-columns:35px minmax(0,1fr) minmax(160px,1.1fr);gap:12px;align-items:center;padding:12px 18px;color:inherit;text-decoration:none;border-bottom:1px solid var(--line)}.jd-review-score{display:grid;place-items:center;width:30px;height:30px;border-radius:8px;font-weight:900}.jd-review-reason{font-size:11px;line-height:1.3}.jd-feed-panel{grid-column:span 2}.jd-feed{display:grid;grid-template-columns:10px minmax(150px,1fr) minmax(170px,.8fr) 80px 30px;gap:10px;align-items:center;padding:12px 18px;color:inherit;text-decoration:none;border-bottom:1px solid var(--line)}.jd-dot{width:7px;height:7px;border-radius:50%}.jd-call-type{font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.jd-feed-status{font-size:10px;font-weight:800;text-align:center;padding:4px 6px;border-radius:5px}.jd-feed-score{text-align:right;font-weight:900;font-size:15px}.jd-limits{margin-top:18px;padding:13px 16px;display:flex;gap:14px;font-size:12px}.jd-limits b{white-space:nowrap}@media(max-width:900px){.jd-heading{align-items:flex-start;flex-direction:column}.jd-source{width:100%}.jd-metrics{grid-template-columns:1fr 1fr}.jd-grid{grid-template-columns:1fr}.jd-feed-panel{grid-column:auto}.jd-review{grid-template-columns:35px minmax(0,1fr)}.jd-review-reason{display:none}.jd-feed{grid-template-columns:9px minmax(0,1fr) 34px}.jd-call-type,.jd-feed-status{display:none}.jd-limits{display:grid;gap:5px}}@media(max-width:480px){.jd-heading h1{font-size:28px}.jd-metrics>div{padding:15px}.jd-metrics b{font-size:28px}.jd-manager{grid-template-columns:36px minmax(0,1fr) 38px}.jd-status{display:none}.jd-panel-head{padding-left:15px;padding-right:15px}.jd-action,.jd-review,.jd-feed,.jd-manager{padding-left:15px;padding-right:15px}}
:root{--ink:#115172;--muted:#678092;--line:#d7e8ea;--canvas:#f5faf9;--surface:#fff;--blue:#15c8c3;--blue-soft:#e7fbfa;--red:#d94d5c;--red-soft:#fff1f3;--amber:#ad760d;--amber-soft:#fff8e8;--green:#13a887;--green-soft:#e7faf4}body{background:var(--canvas);color:var(--ink)}.jd-top{position:fixed;inset:0 auto 0 0;width:218px;height:100vh;padding:26px 20px;background:var(--blue);display:flex;flex-direction:column;align-items:stretch;gap:39px;z-index:30}.jd-brand{font-size:20px;gap:10px;align-items:flex-start}.jd-brand strong{display:block;letter-spacing:.01em}.jd-brand small{color:#dffffd;font-size:8px;line-height:1.35;margin-top:5px}.jd-mark{flex:0 0 auto;width:34px;height:34px;border-radius:50%;background:#fff;color:#139e9b;font-size:16px}nav{display:grid;height:auto;gap:3px}nav a{height:auto;padding:10px 11px;border:0;border-radius:7px;color:#e0ffff;font-size:12px}nav a:before{content:"";width:6px;height:6px;border-radius:50%;background:currentColor;margin-right:9px;opacity:.75}nav a:hover,nav a.active{border:0;background:rgba(255,255,255,.2);color:#fff}.jd-user{margin-top:auto;display:grid;gap:9px;color:#e3ffff;line-height:1.35}.jd-user a{display:inline-block;width:max-content;color:#fff;border-bottom:1px solid rgba(255,255,255,.6)}.jd-shell{max-width:1470px;margin-left:218px;padding:33px 36px 42px}.jd-heading{margin-bottom:22px}.jd-heading h1{font-size:29px;letter-spacing:-.03em}.jd-heading h1 span{color:#7d9aa8}.jd-source{border:0;border-radius:9px;box-shadow:0 6px 20px rgba(21,93,106,.1);min-width:265px}.jd-metrics{gap:15px;background:none;border:0;border-radius:0;overflow:visible}.jd-metrics>div{min-height:108px;padding:17px 18px;border:0;border-radius:10px;box-shadow:0 7px 18px rgba(31,86,97,.11)}.jd-metrics>div:last-child{border:0}.jd-metric-critical{background:var(--red-soft)}.jd-metric-review{background:var(--amber-soft)}.jd-grid{gap:16px}.jd-panel{border:0;border-radius:10px;box-shadow:0 7px 21px rgba(30,88,98,.1)}.jd-panel-head{padding:17px 18px 13px;border-bottom:1px solid var(--line)}.jd-panel-head h2{font-size:15px}.jd-action,.jd-review,.jd-feed,.jd-manager{padding-left:18px;padding-right:18px}.jd-action:hover,.jd-review:hover,.jd-feed:hover,.jd-manager:hover{background:#f1fffe}.jd-action-marker{border-radius:50%;background:var(--red)}.jd-score{color:#139f9d}.jd-status.normal{background:var(--blue-soft);color:#128f8c}.jd-dot.normal{background:var(--blue)}.jd-limits{border:0;border-radius:9px;background:#e8fbfa;color:#397482}.jd-limits b{color:#137a81}@media(max-width:900px){.jd-top{position:static;width:100%;height:auto;padding:14px 18px;display:flex;flex-direction:row;align-items:center}.jd-top nav{display:none}.jd-user{margin:0 0 0 auto;display:flex}.jd-shell{margin-left:0;padding:25px 16px}.jd-brand{font-size:17px}.jd-brand small{display:none}}
'''
