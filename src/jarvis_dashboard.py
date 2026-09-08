"""Operational dashboard for the Jarvis Flask application."""

from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Any, Dict, Iterable, List

from claude_analyzer import evaluate_triage


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
<title>Джарвис — центр управления продажами</title><style>{_CSS}</style></head>
<body><!-- JARVIS-DIRECTION: THESIS: operational queue over decorative reporting. OWN-WORLD: dark ink navigation, cool white workspace, cobalt action signal. STORY: a ROP sees freshness, verified incidents and ownership before analytics. FIRST VIEWPORT: status rail, action queue, then live worklist. FORM: operating console; seed jarvis-ops-2026. FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, and DESIGN.md. -->
<header class="jd-top"><a class="jd-brand" href="/"><span class="jd-mark">J</span><span>Джарвис<small>ЦЕНТР УПРАВЛЕНИЯ ПРОДАЖАМИ</small></span></a>
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
:root{--ink:#132237;--muted:#657487;--line:#dfe6ee;--canvas:#f4f7fa;--surface:#fff;--blue:#1d5cff;--blue-soft:#edf3ff;--red:#d94242;--red-soft:#fff0f0;--amber:#a76607;--amber-soft:#fff7e8;--green:#16835d;--green-soft:#eaf8f2}*{box-sizing:border-box}body{margin:0;background:var(--canvas);color:var(--ink);font-family:"Avenir Next",Avenir,"Helvetica Neue",Arial,sans-serif;font-size:14px;line-height:1.4}.jd-top{height:68px;padding:0 max(24px,calc((100vw - 1440px)/2));background:var(--ink);color:#fff;display:flex;align-items:center;gap:42px}.jd-brand{color:#fff;text-decoration:none;display:flex;gap:10px;align-items:center;font-size:17px;font-weight:750;letter-spacing:-.02em}.jd-brand small{display:block;color:#9eb0c5;font-size:8px;letter-spacing:.13em;margin-top:2px}.jd-mark{width:30px;height:30px;border-radius:9px;background:var(--blue);display:grid;place-items:center;font-weight:800}nav{display:flex;gap:22px;height:100%;align-items:center}nav a{height:100%;display:flex;align-items:center;color:#9eb0c5;text-decoration:none;font-size:13px;font-weight:650;border-bottom:2px solid transparent}nav a:hover,nav a.active{color:#fff;border-color:#5b8cff}.jd-user{margin-left:auto;display:flex;align-items:center;gap:16px;color:#c6d1dd;font-size:12px}.jd-user a{color:#fff;text-decoration:none;font-weight:650}.jd-shell{max-width:1360px;margin:auto;padding:38px 28px 32px}.jd-heading{display:flex;align-items:flex-end;justify-content:space-between;gap:24px;margin-bottom:26px}.jd-heading p{margin:0 0 7px;color:var(--muted);font-weight:650;font-size:12px}.jd-heading h1{margin:0;font-size:32px;line-height:1.05;letter-spacing:-.045em}.jd-heading h1 span{color:#8492a2;font-weight:500}.jd-source{min-width:280px;display:grid;grid-template-columns:9px auto 1fr;gap:7px 9px;align-items:center;background:var(--surface);border:1px solid var(--line);border-radius:13px;padding:11px 13px;font-size:12px}.jd-source i{width:8px;height:8px;border-radius:50%;background:var(--amber);grid-row:span 2}.jd-source.ok i{background:var(--green)}.jd-source span{font-weight:700}.jd-source b{font-size:11px;text-align:right;color:var(--green)}.jd-source.warning b{color:var(--amber)}.jd-source small{grid-column:2 / -1;color:var(--muted);font-size:10px}.jd-metrics{display:grid;grid-template-columns:1.2fr 1.2fr 1fr 1fr;background:var(--surface);border:1px solid var(--line);border-radius:14px;overflow:hidden;margin-bottom:20px}.jd-metrics>div{padding:18px 20px;border-right:1px solid var(--line);min-height:115px}.jd-metrics>div:last-child{border:0}.jd-metrics small{display:block;color:var(--muted);font-weight:650;font-size:11px}.jd-metrics b{display:block;font-size:34px;line-height:1;margin:10px 0 7px;letter-spacing:-.05em}.jd-metrics span{color:var(--muted);font-size:11px}.jd-metric-critical{background:var(--red-soft)}.jd-metric-critical b{color:var(--red)}.jd-metric-review{background:var(--amber-soft)}.jd-metric-review b{color:var(--amber)}.jd-grid{display:grid;grid-template-columns:minmax(0,1.45fr) minmax(360px,.95fr);gap:20px}.jd-panel{background:var(--surface);border:1px solid var(--line);border-radius:14px;overflow:hidden}.jd-panel-head{padding:19px 20px 15px;display:flex;justify-content:space-between;gap:10px;align-items:flex-start;border-bottom:1px solid var(--line)}.jd-panel-head h2{font-size:16px;line-height:1.1;margin:0 0 5px;letter-spacing:-.02em}.jd-panel-head p{margin:0;color:var(--muted);font-size:11px}.jd-panel-head a{color:var(--blue);font-size:12px;text-decoration:none;font-weight:700;white-space:nowrap}.jd-panel-head>span{font-size:11px;color:var(--muted);font-weight:650}.jd-actions{grid-row:span 1}.jd-action{display:grid;grid-template-columns:28px minmax(0,1fr) 16px;gap:10px;padding:14px 20px;color:inherit;text-decoration:none;border-bottom:1px solid var(--line)}.jd-action:last-child{border-bottom:0}.jd-action:hover,.jd-review:hover,.jd-feed:hover,.jd-manager:hover{background:#f8fbff}.jd-action-marker{width:22px;height:22px;border-radius:7px;background:var(--red);color:#fff;display:grid;place-items:center;font-size:14px;font-weight:800}.jd-action b,.jd-review b,.jd-feed b,.jd-manager b{display:block;font-size:13px}.jd-action small,.jd-review small,.jd-feed small,.jd-manager small{display:block;color:var(--muted);font-size:11px;margin-top:3px}.jd-action em{display:block;color:#8d3c3c;font-style:normal;font-size:11px;white-space:nowrap;text-overflow:ellipsis;overflow:hidden;margin-top:7px}.jd-arrow{align-self:center;color:var(--blue);font-size:16px}.jd-empty{padding:25px 20px;display:grid;gap:5px;color:var(--muted);font-size:12px}.jd-empty b{color:var(--ink);font-size:13px}.jd-manager{display:grid;grid-template-columns:40px minmax(0,1fr) 44px 74px;gap:10px;align-items:center;padding:12px 20px;color:inherit;text-decoration:none;border-bottom:1px solid var(--line)}.jd-manager:last-child{border:0}.jd-avatar{width:38px;height:38px;border-radius:50%;display:grid;place-items:center;overflow:hidden;background:#dbe5f4;color:#315172;font-size:12px;font-weight:800}.jd-avatar img{display:block;width:100%;height:100%;object-fit:cover}.jd-avatar.has-image>span{display:none}.jd-manager-name{min-width:0}.jd-manager-name b{white-space:nowrap;text-overflow:ellipsis;overflow:hidden}.jd-score{font-size:17px;font-weight:760;letter-spacing:-.04em;text-align:right}.jd-status{font-size:10px;font-weight:750;text-align:center;padding:5px 4px;border-radius:6px}.jd-status.critical{background:var(--red-soft);color:var(--red)}.jd-status.review{background:var(--amber-soft);color:var(--amber)}.jd-status.normal{background:var(--green-soft);color:var(--green)}.jd-review-panel,.jd-feed-panel{margin-top:0}.jd-review{display:grid;grid-template-columns:35px minmax(0,1fr) minmax(160px,1.1fr);gap:12px;align-items:center;padding:12px 20px;color:inherit;text-decoration:none;border-bottom:1px solid var(--line)}.jd-review:last-child{border:0}.jd-review-score{display:grid;place-items:center;width:30px;height:30px;background:var(--amber-soft);color:var(--amber);border-radius:8px;font-weight:800}.jd-review-reason{color:var(--muted);font-size:11px;line-height:1.3}.jd-feed-panel{grid-column:span 2}.jd-feed{display:grid;grid-template-columns:10px minmax(150px,1fr) minmax(170px,.8fr) 80px 30px;gap:10px;align-items:center;padding:12px 20px;color:inherit;text-decoration:none;border-bottom:1px solid var(--line)}.jd-feed:last-child{border:0}.jd-dot{width:7px;height:7px;border-radius:50%;background:#a7b2c0}.jd-dot.critical{background:var(--red)}.jd-dot.needs_review{background:#e7a526}.jd-dot.normal{background:var(--green)}.jd-call-type{color:var(--muted);font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.jd-feed-status{font-size:10px;font-weight:750;text-align:center;padding:4px 6px;border-radius:5px}.jd-feed-status.critical{background:var(--red-soft);color:var(--red)}.jd-feed-status.needs_review{background:var(--amber-soft);color:var(--amber)}.jd-feed-status.normal{background:var(--green-soft);color:var(--green)}.jd-feed-status.pending{background:#eef1f5;color:#6d7887}.jd-feed-score{text-align:right;font-weight:800;font-size:15px}.jd-limits{margin-top:20px;border:1px solid #dfe8fa;background:#f6f9ff;border-radius:12px;padding:13px 16px;display:flex;gap:14px;color:#52647b;font-size:12px}.jd-limits b{color:#254269;white-space:nowrap}@media(max-width:900px){.jd-top{padding:0 18px;gap:16px}.jd-top nav{display:none}.jd-user span{display:none}.jd-shell{padding:25px 16px}.jd-heading{align-items:flex-start;flex-direction:column}.jd-source{width:100%}.jd-metrics{grid-template-columns:1fr 1fr}.jd-metrics>div:nth-child(2){border-right:0}.jd-metrics>div:nth-child(-n+2){border-bottom:1px solid var(--line)}.jd-grid{grid-template-columns:1fr}.jd-feed-panel{grid-column:auto}.jd-review{grid-template-columns:35px minmax(0,1fr)}.jd-review-reason{display:none}.jd-feed{grid-template-columns:9px minmax(0,1fr) 34px}.jd-call-type,.jd-feed-status{display:none}.jd-limits{display:grid;gap:5px}}@media(max-width:480px){.jd-heading h1{font-size:28px}.jd-metrics>div{padding:15px}.jd-metrics b{font-size:28px}.jd-manager{grid-template-columns:36px minmax(0,1fr) 38px}.jd-status{display:none}.jd-panel-head{padding-left:15px;padding-right:15px}.jd-action,.jd-review,.jd-feed,.jd-manager{padding-left:15px;padding-right:15px}}
'''
