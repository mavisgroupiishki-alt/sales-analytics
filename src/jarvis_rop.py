"""Source-backed ROP report for the Jarvis operational console."""

from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Any, Dict, Iterable, List, Mapping
from urllib.parse import urlencode

from jarvis_dashboard import _analysis_for, _avatar, _format_timestamp, _freshness, triage_for


_AQUA_CSS = r'''
:root{--ink:#115172;--nav:#15c8c3;--blue:#15c8c3;--paper:#fff;--canvas:#f5faf9;--line:#d8e8ea;--muted:#6d8791;--red:#d94d5c;--redbg:#fff1f3;--amber:#a97813;--amberbg:#fff8e8;--green:#12a986;--greenbg:#e6faf4}body{background:var(--canvas);color:var(--ink)}.jr-top{position:fixed;inset:0 auto 0 0;width:218px;height:100vh;padding:26px 20px;background:var(--nav);display:flex;flex-direction:column;align-items:stretch;gap:39px;z-index:30}.jr-brand{align-items:flex-start}.jr-brand>span{flex:0 0 auto;width:34px;height:34px;border-radius:50%;background:#fff;color:#139c9b;font-size:16px}.jr-brand b{font-size:20px;letter-spacing:.01em}.jr-brand small{color:#ddffff;line-height:1.35;margin-top:5px}.jr-top nav{height:auto;display:grid;gap:3px}.jr-top nav a{height:auto;padding:10px 11px;border:0;border-radius:7px;color:#e0ffff;font-size:12px}.jr-top nav a:before{content:"";width:6px;height:6px;border-radius:50%;background:currentColor;margin-right:9px;opacity:.75}.jr-top nav a.active,.jr-top nav a:hover{border:0;background:rgba(255,255,255,.2);color:#fff}.jr-user{margin-top:auto;color:#e3ffff;line-height:1.8}.jr-user a{display:block;margin:0;color:#fff;border-bottom:1px solid rgba(255,255,255,.6);width:max-content}.jr-shell{max-width:1490px;margin-left:218px;padding:34px 36px 42px}.jr-hero{margin-bottom:22px}.jr-hero h1{font-size:35px;letter-spacing:-.035em}.jr-hero h1 span{color:#78a4ad}.jr-freshness b{color:#148b89}.jr-hero aside{border:0;border-radius:10px;background:#e6fbfa;box-shadow:0 7px 18px rgba(30,101,105,.1)}.jr-hero aside a,.jr-section-title>a{color:#0d9f9c}.jr-filters{border:0;border-radius:10px;box-shadow:0 7px 18px rgba(30,86,97,.1)}.jr-filters select{border-color:#cfe2e5;background:#fbfefe}.jr-filters button{background:var(--blue)}.jr-metrics{gap:15px;margin-bottom:36px}.jr-metrics a{border:0;border-radius:10px;box-shadow:0 7px 19px rgba(30,86,97,.11)}.jr-metrics a:hover{border:0;box-shadow:0 10px 25px rgba(22,130,132,.18)}.jr-section{margin-bottom:33px}.jr-section-title{margin-bottom:12px}.jr-section-title h2{font-size:19px;letter-spacing:-.025em}.jr-panel{border:0;border-radius:10px;box-shadow:0 7px 20px rgba(30,86,97,.1)}.jr-action:hover,.jr-review:hover,.jr-manager-row:hover{background:#f0fffe}.jr-alert{border-radius:50%}.jr-source-grid{gap:15px}.jr-source-card{border:0;border-top:3px solid #a9c9ce;border-radius:10px;box-shadow:0 7px 18px rgba(30,86,97,.1)}.jr-source-card.good{border-top-color:var(--blue)}.jr-source-card.warn{border-top-color:#e0ab33}.jr-note{border:0;border-radius:10px;background:#e6fbfa;color:#397783}.jr-note b{color:#13817f}@media(max-width:900px){.jr-top{position:static;width:100%;height:auto;padding:14px 18px;display:flex;flex-direction:row;align-items:center}.jr-top nav{display:none}.jr-user{margin:0 0 0 auto;font-size:0}.jr-user a{font-size:12px}.jr-shell{margin-left:0;padding:28px 16px}.jr-brand b{font-size:17px}.jr-brand small{display:none}}
'''


def _text(value: Any) -> str:
    return escape(str(value or ""))


def _score(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _date(value: Any) -> str:
    return str(value or "")[:10]


def _query(**values: str) -> str:
    return urlencode({key: value for key, value in values.items() if value})


def filter_calls(
    calls: Iterable[Dict[str, Any]], analyses: Mapping[str, Any], filters: Mapping[str, str]
) -> List[Dict[str, Any]]:
    """Filter raw call rows only; no metric can escape the selected period."""
    result = list(calls)
    date = filters.get("date", "")
    manager_id = filters.get("manager", "")
    direction = filters.get("direction", "")
    status = filters.get("status", "")

    if date:
        result = [call for call in result if _date(call.get("created")) == date]
    if manager_id:
        result = [
            call for call in result
            if str((call.get("manager") or {}).get("id") or "") == manager_id
        ]
    if direction in {"incoming", "outgoing"}:
        result = [call for call in result if call.get("direction") == direction]
    if status in {"critical", "needs_review", "requires_reanalysis", "normal", "pending"}:
        result = [
            call for call in result
            if (triage_for(_analysis_for(analyses, call))[0] if _analysis_for(analyses, call) else "pending")
            == status
        ]
    return result


def rop_model(calls: List[Dict[str, Any]], analyses: Dict[str, Any]) -> Dict[str, Any]:
    """Compute management indicators from calls and analyses, never from guesses."""
    analyzed = 0
    available_audio = 0
    linked = 0
    next_activity = 0
    critical: List[Dict[str, Any]] = []
    review: List[Dict[str, Any]] = []
    reanalysis: List[Dict[str, Any]] = []
    by_manager: Dict[str, Dict[str, Any]] = {}

    for call in calls:
        manager = call.get("manager") or {}
        manager_id = str(manager.get("id") or "0")
        entry = by_manager.setdefault(
            manager_id,
            {
                "manager": manager,
                "calls": 0,
                "analyzed": 0,
                "critical": 0,
                "review": 0,
                "reanalysis": 0,
                "linked": 0,
                "next_activity": 0,
                "scores": [],
            },
        )
        entry["calls"] += 1
        audio = call.get("audio") or {}
        if audio.get("file_id") or audio.get("url"):
            available_audio += 1
        crm = call.get("crm") or {}
        if crm.get("owner_id") and crm.get("owner_type") in {"deal", "lead", "contact", "company"}:
            linked += 1
            entry["linked"] += 1
        if crm.get("has_next_activity"):
            next_activity += 1
            entry["next_activity"] += 1

        analysis = _analysis_for(analyses, call)
        if not analysis:
            continue
        analyzed += 1
        entry["analyzed"] += 1
        score = _score(analysis.get("overall_score"))
        if score is not None:
            entry["scores"].append(score)
        status, reason, rule_id = triage_for(analysis)
        item = {"call": call, "analysis": analysis, "reason": reason, "rule_id": rule_id}
        if status == "critical":
            critical.append(item)
            entry["critical"] += 1
        elif status == "needs_review":
            review.append(item)
            entry["review"] += 1
        elif status == "requires_reanalysis":
            reanalysis.append(item)
            entry["reanalysis"] += 1

    managers = []
    for item in by_manager.values():
        scores = item.pop("scores")
        item["average"] = round(sum(scores) / len(scores), 1) if scores else None
        item["coverage"] = round(item["analyzed"] / item["calls"] * 100) if item["calls"] else 0
        item["crm_coverage"] = round(item["linked"] / item["calls"] * 100) if item["calls"] else 0
        managers.append(item)
    managers.sort(key=lambda item: (item["critical"], item["review"], item["reanalysis"], -item["coverage"]), reverse=True)

    critical.sort(key=lambda item: str(item["call"].get("created") or ""), reverse=True)
    review.sort(key=lambda item: str(item["call"].get("created") or ""), reverse=True)
    reanalysis.sort(key=lambda item: str(item["call"].get("created") or ""), reverse=True)
    fresh_at, freshness = _freshness(calls)
    return {
        "calls": len(calls),
        "analyzed": analyzed,
        "available_audio": available_audio,
        "linked": linked,
        "next_activity": next_activity,
        "critical": critical,
        "review": review,
        "reanalysis": reanalysis,
        "managers": managers,
        "fresh_at": fresh_at,
        "freshness": freshness,
        "coverage": round(analyzed / len(calls) * 100) if calls else 0,
        "audio_coverage": round(available_audio / len(calls) * 100) if calls else 0,
        "crm_coverage": round(linked / len(calls) * 100) if calls else 0,
    }


def render_rop_report(
    calls: List[Dict[str, Any]],
    analyses: Dict[str, Any],
    user: Dict[str, Any],
    filters: Mapping[str, str],
    available_calls: Iterable[Dict[str, Any]] | None = None,
) -> str:
    model = rop_model(calls, analyses)
    all_dates = sorted(
        {_date(call.get("created")) for call in (available_calls or calls) if _date(call.get("created"))},
        reverse=True,
    )
    active_date = filters.get("date") or (all_dates[0] if all_dates else "")
    available_managers = sorted(
        {(str((call.get("manager") or {}).get("id") or ""), str((call.get("manager") or {}).get("name") or "Менеджер")) for call in calls},
        key=lambda item: item[1],
    )
    selected_manager = filters.get("manager", "")
    selected_direction = filters.get("direction", "")
    selected_status = filters.get("status", "")
    selected_title = active_date or "вся доступная выборка"

    manager_options = '<option value="">Все менеджеры</option>' + "".join(
        f'<option value="{_text(manager_id)}" {"selected" if manager_id == selected_manager else ""}>{_text(name)}</option>'
        for manager_id, name in available_managers
    )
    filter_base = {"date": active_date, "manager": selected_manager, "direction": selected_direction, "status": selected_status}
    calls_href = "/calls?" + _query(**filter_base)

    actions = ""
    for item in model["critical"][:6]:
        call, analysis = item["call"], item["analysis"]
        evidence = (analysis.get("flags") or {}).get("critical_evidence") or {}
        client = (call.get("client") or {}).get("name") or "Клиент не определён"
        action = analysis.get("recommended_action") or "Открыть звонок и принять решение РОПа"
        actions += f'''<a class="jr-action" href="/calls/{_text(call.get('activity_id'))}">
          <span class="jr-alert">!</span><span><b>{_text(client)}</b><small>{_text(item['reason'])} · {_text(evidence.get('time') or 'нет таймкода')}</small>
          <em>«{_text(str(evidence.get('quote') or '')[:150])}»</em></span><span class="jr-action-task">{_text(action)}</span><span>→</span></a>'''
    if not actions:
        actions = '<div class="jr-empty"><b>Нет подтверждённых критичных звонков.</b><span>Обычные низкие баллы и старые флаги не попадают в срочную очередь.</span></div>'

    manager_rows = ""
    for item in model["managers"]:
        manager = item["manager"]
        score = f"{item['average']:.1f}" if item["average"] is not None else "—"
        next_rate = round(item["next_activity"] / item["calls"] * 100) if item["calls"] else 0
        manager_rows += f'''<a class="jr-manager-row" href="/managers/{_text(manager.get('id'))}">
          {_avatar(manager)}<span class="jr-manager-name"><b>{_text(manager.get('name') or 'Менеджер')}</b><small>{item['calls']} звонков · покрытие AI {item['coverage']}%</small></span>
          <span><b>{score}</b><small>применимый балл</small></span><span><b>{next_rate}%</b><small>следующее дело в CRM</small></span>
          <span class="jr-risk {'red' if item['critical'] else 'amber' if item['review'] else 'green'}">{item['critical'] or item['review'] or '—'}</span></a>'''
    if not manager_rows:
        manager_rows = '<div class="jr-empty"><b>Нет звонков в этой выборке.</b></div>'

    review_rows = ""
    for item in model["review"][:8]:
        call, analysis = item["call"], item["analysis"]
        manager = (call.get("manager") or {}).get("name") or "Менеджер не определён"
        client = (call.get("client") or {}).get("name") or "Клиент не определён"
        review_rows += f'''<a class="jr-review" href="/calls/{_text(call.get('activity_id'))}">
          <span class="jr-review-score">{_text(analysis.get('overall_score') if analysis.get('overall_score') is not None else '—')}</span><span><b>{_text(client)}</b><small>{_text(manager)} · {_format_timestamp(str(call.get('created') or ''))}</small></span><span>{_text(item['reason'])}</span></a>'''
    if not review_rows:
        review_rows = '<div class="jr-empty"><b>Очередь проверки пуста.</b></div>'

    reanalysis_rows = ""
    for item in model["reanalysis"][:8]:
        call, analysis = item["call"], item["analysis"]
        manager = (call.get("manager") or {}).get("name") or "Менеджер не определён"
        client = (call.get("client") or {}).get("name") or "Клиент не определён"
        reanalysis_rows += f'''<a class="jr-review" href="/calls/{_text(call.get('activity_id'))}">
          <span class="jr-review-score">↻</span><span><b>{_text(client)}</b><small>{_text(manager)} · {_format_timestamp(str(call.get('created') or ''))}</small></span><span>{_text(item['reason'])}</span></a>'''
    if not reanalysis_rows:
        reanalysis_rows = '<div class="jr-empty"><b>Нет старых результатов для нового разбора.</b></div>'

    freshness_class = "green" if model["freshness"] == "fresh" else "amber"
    freshness_label = "данные поступают" if model["freshness"] == "fresh" else "нужно обновление"
    date_options = '<option value="">Все даты</option>' + "".join(
        f'<option value="{_text(date)}" {"selected" if date == active_date else ""}>{_text(date)}</option>' for date in all_dates
    )
    return f'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Джарвис — отчёт РОПа</title><style>{_CSS}{_AQUA_CSS}</style></head><body>
<header class="jr-top"><a class="jr-brand" href="/"><span>J</span><b>ДЖАРВИС<small>ЦЕНТР УПРАВЛЕНИЯ ПРОДАЖАМИ</small></b></a><nav><a href="/">Обзор</a><a href="/calls">Звонки</a><a href="/managers">Команда</a><a class="active" href="/rop">Отчёт РОПа</a></nav><div class="jr-user">РОП · {_text(user.get('name'))} <a href="/logout">Выйти</a></div></header>
<main class="jr-shell"><section class="jr-hero"><div><h1>Решения, а не<br><span>шум в отчётах.</span></h1><div class="jr-freshness {freshness_class}"><i></i><b>Bitrix24: {freshness_label}</b><span>последняя запись {model['fresh_at']}</span></div></div>
<aside><b>Что требует внимания</b><p>{'Откройте подтверждённые случаи: у каждого есть правило и доказательство.' if model['critical'] else 'Срочных кейсов без доказательств нет. Очередь «на проверку» — это не тревога.'}</p><a href="#priority">Открыть очередь ↓</a></aside></section>
<form class="jr-filters" method="get"><label>Период<select name="date">{date_options}</select></label><label>Менеджер<select name="manager">{manager_options}</select></label><label>Направление<select name="direction"><option value="">Все звонки</option><option value="incoming" {"selected" if selected_direction == 'incoming' else ''}>Входящие</option><option value="outgoing" {"selected" if selected_direction == 'outgoing' else ''}>Исходящие</option></select></label><label>Статус<select name="status"><option value="">Все статусы</option><option value="critical" {"selected" if selected_status == 'critical' else ''}>Критично</option><option value="needs_review" {"selected" if selected_status == 'needs_review' else ''}>Нужна проверка</option><option value="requires_reanalysis" {"selected" if selected_status == 'requires_reanalysis' else ''}>Нужен новый разбор</option><option value="normal" {"selected" if selected_status == 'normal' else ''}>Без риска</option><option value="pending" {"selected" if selected_status == 'pending' else ''}>Ожидает AI</option></select></label><button>Применить</button></form>
<section class="jr-metrics"><a href="{calls_href}"><small>Звонки</small><b>{model['calls']}</b><span>первичные записи</span></a><a href="{calls_href}"><small>Покрытие AI</small><b>{model['coverage']}%</b><span>{model['analyzed']} разобрано</span></a><a href="/critical?{_query(**filter_base)}"><small>Срочно к РОПу</small><b>{len(model['critical'])}</b><span>только с доказательством</span></a><a href="#reanalysis"><small>Нужен новый разбор</small><b>{len(model['reanalysis'])}</b><span>старые оценки не ушли РОПу</span></a></section>
<section id="priority" class="jr-section"><div class="jr-section-title"><div><h2>Очередь вмешательства</h2></div><span>Критичный звонок = правило + доказательство + контекст</span></div><div class="jr-panel">{actions}</div></section>
<section class="jr-two"><section class="jr-section"><div class="jr-section-title"><div><h2>Где нужен разговор с менеджером</h2></div><a href="/managers">все профили →</a></div><div class="jr-panel jr-manager-head"><span></span><span>Менеджер</span><span>Качество</span><span>CRM-дисциплина</span><span>Сигнал</span></div><div class="jr-panel jr-manager-list">{manager_rows}</div></section>
<section id="review" class="jr-section"><div class="jr-section-title"><div><h2>Очередь проверки</h2></div><a href="/calls?{_query(**{**filter_base, 'status': 'needs_review'})}">все звонки →</a></div><div class="jr-panel">{review_rows}</div></section></section>
<section id="reanalysis" class="jr-section"><div class="jr-section-title"><div><h2>Новый разбор по текущей методике</h2></div><a href="/calls?{_query(**{**filter_base, 'status': 'requires_reanalysis'})}">все звонки →</a></div><div class="jr-panel">{reanalysis_rows}</div></section>
<section class="jr-section"><div class="jr-section-title"><div><h2>Что можно использовать в управлении сегодня</h2></div></div><div class="jr-source-grid"><div class="jr-source-card good"><span>Bitrix24 · звонки</span><b>{model['calls']}</b><p>Записи из текущей выборки. CRM-связь подтверждена у {model['crm_coverage']}%.</p><small>Последняя запись: {model['fresh_at']}</small></div><div class="jr-source-card {'good' if model['coverage'] >= 80 else 'warn'}"><span>AI-разбор</span><b>{model['coverage']}%</b><p>Только разобранные записи участвуют в оценке качества.</p><small>{model['analyzed']} из {model['calls']} звонков</small></div><div class="jr-source-card {'good' if model['audio_coverage'] >= 80 else 'warn'}"><span>Доступность аудио</span><b>{model['audio_coverage']}%</b><p>Без записи звонок не получает выдуманную оценку.</p><small>{model['available_audio']} записей доступны</small></div><div class="jr-source-card muted"><span>План / факт / оплаты</span><b>—</b><p>Не подключены: Джарвис не подставляет цифры из непроверенных источников.</p><small>Нужна карта полей и владельца источника</small></div></div></section>
<section class="jr-note"><b>Как читать отчёт</b><span>Сначала подтверждённые критичные случаи, затем очередь ручной проверки, затем объём и дисциплина данных. Рейтинг менеджеров не строится, пока нет сопоставимой размеченной выборки.</span></section></main></body></html>'''


_CSS = r'''
:root{--ink:#11263c;--nav:#0e2134;--blue:#1d61ff;--paper:#fff;--canvas:#f3f6f9;--line:#dae3ec;--muted:#68788b;--red:#d53f49;--redbg:#fff0f1;--amber:#9a640b;--amberbg:#fff7e8;--green:#0d7d58;--greenbg:#eaf8f2}*{box-sizing:border-box}body{margin:0;background:var(--canvas);color:var(--ink);font-family:"Avenir Next",Avenir,"Helvetica Neue",Arial,sans-serif;font-size:14px;line-height:1.4}.jr-top{height:68px;background:var(--nav);color:#fff;display:flex;gap:40px;align-items:center;padding:0 max(24px,calc((100vw - 1400px)/2))}.jr-brand{display:flex;align-items:center;gap:10px;color:#fff;text-decoration:none}.jr-brand>span{display:grid;place-items:center;width:30px;height:30px;border-radius:8px;background:var(--blue);font-weight:800}.jr-brand b{font-size:17px;line-height:1}.jr-brand small{display:block;font-size:8px;letter-spacing:.13em;color:#aebdca;margin-top:4px}.jr-top nav{height:100%;display:flex;align-items:center;gap:23px}.jr-top nav a{display:flex;height:100%;align-items:center;color:#aab8c7;text-decoration:none;font-size:13px;font-weight:700;border-bottom:2px solid transparent}.jr-top nav a.active,.jr-top nav a:hover{color:#fff;border-color:#6190ff}.jr-user{margin-left:auto;color:#c9d4df;font-size:12px}.jr-user a{margin-left:17px;color:#fff;text-decoration:none;font-weight:700}.jr-shell{max-width:1330px;margin:auto;padding:42px 28px 34px}.jr-hero{display:flex;justify-content:space-between;gap:40px;align-items:flex-end;margin-bottom:28px}.jr-hero p,.jr-section-title p{margin:0 0 9px;color:#617693;font-size:10px;letter-spacing:.12em;font-weight:800}.jr-hero h1{margin:0;font-size:39px;letter-spacing:-.055em;line-height:1.02}.jr-hero h1 span{color:#8697aa;font-weight:500}.jr-freshness{margin-top:19px;display:flex;align-items:center;gap:8px;font-size:12px}.jr-freshness i{width:8px;height:8px;border-radius:50%;background:var(--amber)}.jr-freshness.green i{background:var(--green)}.jr-freshness b{font-size:12px}.jr-freshness span{color:var(--muted)}.jr-hero aside{max-width:315px;background:#e9eff9;border-left:3px solid var(--blue);padding:16px 18px}.jr-hero aside b{font-size:13px}.jr-hero aside p{font-size:12px;letter-spacing:0;color:#536880;line-height:1.5;margin:7px 0 10px}.jr-hero aside a{color:var(--blue);font-size:12px;font-weight:800;text-decoration:none}.jr-filters{display:grid;grid-template-columns:1.1fr 1.3fr 1fr 1fr auto;gap:10px;align-items:end;background:var(--paper);border:1px solid var(--line);padding:13px 14px;border-radius:12px;margin-bottom:18px}.jr-filters label{display:grid;gap:5px;color:var(--muted);font-size:10px;font-weight:800;letter-spacing:.08em;text-transform:uppercase}.jr-filters select{width:100%;border:1px solid #cfdbe7;border-radius:7px;background:#fbfcfe;padding:9px 10px;color:var(--ink);font:600 12px inherit}.jr-filters button{border:0;border-radius:7px;background:var(--blue);padding:10px 16px;color:#fff;font:800 12px inherit;cursor:pointer}.jr-metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:42px}.jr-metrics a{min-height:118px;padding:17px 18px;color:inherit;text-decoration:none;background:var(--paper);border:1px solid var(--line);border-radius:12px}.jr-metrics a:hover{border-color:#93b1ff;box-shadow:0 6px 20px rgba(27,65,108,.08)}.jr-metrics small{display:block;color:var(--muted);font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:.08em}.jr-metrics b{display:block;margin:9px 0 5px;font-size:31px;letter-spacing:-.06em}.jr-metrics span{font-size:11px;color:var(--muted)}.jr-metrics a:nth-child(3){background:var(--redbg)}.jr-metrics a:nth-child(3) b{color:var(--red)}.jr-metrics a:nth-child(4){background:var(--amberbg)}.jr-metrics a:nth-child(4) b{color:var(--amber)}.jr-section{margin-bottom:38px}.jr-section-title{display:flex;align-items:flex-end;justify-content:space-between;gap:18px;margin-bottom:13px}.jr-section-title h2{margin:0;font-size:21px;letter-spacing:-.035em}.jr-section-title>span{color:var(--muted);font-size:11px}.jr-section-title>a{color:var(--blue);font-size:12px;font-weight:800;text-decoration:none}.jr-panel{background:var(--paper);border:1px solid var(--line);border-radius:12px;overflow:hidden}.jr-action{display:grid;grid-template-columns:30px minmax(0,1fr) minmax(180px,.7fr) 14px;gap:12px;align-items:center;padding:15px 18px;color:inherit;text-decoration:none;border-bottom:1px solid var(--line)}.jr-action:last-child,.jr-review:last-child,.jr-manager-row:last-child{border-bottom:0}.jr-action:hover,.jr-review:hover,.jr-manager-row:hover{background:#f8fbff}.jr-alert{display:grid;place-items:center;width:24px;height:24px;border-radius:7px;background:var(--red);color:#fff;font-weight:900}.jr-action b,.jr-review b,.jr-manager-row b{display:block;font-size:13px}.jr-action small,.jr-review small,.jr-manager-row small{display:block;font-size:10px;color:var(--muted);margin-top:3px}.jr-action em{display:block;color:#90404a;font-style:normal;font-size:11px;margin-top:5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.jr-action-task{font-size:11px;color:#53657a;line-height:1.35}.jr-empty{padding:28px 20px;display:grid;gap:5px;color:var(--muted);font-size:12px}.jr-empty b{font-size:13px;color:var(--ink)}.jr-two{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(330px,.8fr);gap:22px}.jr-manager-head{display:grid;grid-template-columns:38px minmax(110px,1fr) 75px 105px 36px;gap:10px;padding:9px 17px;color:var(--muted);font-size:9px;font-weight:800;text-transform:uppercase;letter-spacing:.08em;border-bottom:0;border-radius:12px 12px 0 0}.jr-manager-list{border-top:0;border-radius:0 0 12px 12px}.jr-manager-row{display:grid;grid-template-columns:38px minmax(110px,1fr) 75px 105px 36px;gap:10px;align-items:center;padding:12px 17px;color:inherit;text-decoration:none;border-bottom:1px solid var(--line)}.jr-manager-row>.jd-avatar{width:34px;height:34px}.jr-manager-row>span:not(.jd-avatar)>b{font-size:13px}.jr-risk{width:29px;height:25px;display:grid;place-items:center;border-radius:6px;font-size:11px;font-weight:900}.jr-risk.red{color:var(--red);background:var(--redbg)}.jr-risk.amber{color:var(--amber);background:var(--amberbg)}.jr-risk.green{color:var(--green);background:var(--greenbg)}.jr-review{display:grid;grid-template-columns:35px minmax(0,1fr) minmax(130px,.75fr);gap:10px;align-items:center;padding:13px 16px;color:inherit;text-decoration:none;border-bottom:1px solid var(--line)}.jr-review-score{width:30px;height:30px;display:grid;place-items:center;border-radius:7px;background:var(--amberbg);color:var(--amber);font-weight:900}.jr-review>span:last-child{font-size:11px;color:var(--muted);line-height:1.35}.jr-source-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.jr-source-card{min-height:180px;background:var(--paper);border:1px solid var(--line);border-top:3px solid #aab8c7;border-radius:11px;padding:17px}.jr-source-card.good{border-top-color:var(--green)}.jr-source-card.warn{border-top-color:#dc9c22}.jr-source-card.muted{background:#f7f9fb;border-top-color:#a8b2bc}.jr-source-card>span{font-size:10px;color:var(--muted);font-weight:800;text-transform:uppercase;letter-spacing:.07em}.jr-source-card b{display:block;font-size:28px;letter-spacing:-.06em;margin:12px 0 6px}.jr-source-card p{min-height:49px;margin:0;color:#52657a;font-size:11px;line-height:1.45}.jr-source-card small{display:block;border-top:1px solid var(--line);padding-top:10px;margin-top:10px;color:#758496;font-size:10px}.jr-note{display:grid;grid-template-columns:155px 1fr;gap:16px;background:#e9f0ff;border:1px solid #d2e0ff;border-radius:11px;padding:15px 17px;color:#4b6280;font-size:12px}.jr-note b{color:#1f467e}@media(max-width:900px){.jr-top{padding:0 16px;gap:15px}.jr-top nav{display:none}.jr-user{font-size:0}.jr-user a{font-size:12px}.jr-shell{padding:28px 16px}.jr-hero{align-items:flex-start;flex-direction:column}.jr-hero h1{font-size:34px}.jr-filters{grid-template-columns:1fr 1fr}.jr-metrics{grid-template-columns:1fr 1fr}.jr-two{grid-template-columns:1fr}.jr-source-grid{grid-template-columns:1fr 1fr}.jr-action{grid-template-columns:30px minmax(0,1fr) 14px}.jr-action-task{display:none}}@media(max-width:520px){.jr-filters,.jr-source-grid{grid-template-columns:1fr}.jr-metrics{gap:8px}.jr-metrics a{padding:14px;min-height:105px}.jr-metrics b{font-size:28px}.jr-manager-head{display:none}.jr-manager-row{grid-template-columns:38px minmax(0,1fr) 48px 32px}.jr-manager-row>span:nth-of-type(3){display:none}.jr-review{grid-template-columns:35px minmax(0,1fr)}.jr-review>span:last-child{display:none}.jr-note{grid-template-columns:1fr;gap:5px}}
'''
