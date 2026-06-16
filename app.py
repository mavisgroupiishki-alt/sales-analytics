"""
Flask веб-приложение для Sales Analytics с авторизацией.
Использует существующий report_generator.py для генерации HTML.
"""

import json
import os
import hashlib
import sys
import math
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict, Counter
from functools import wraps
from flask import Flask, request, redirect, url_for, session, abort, Response, jsonify, Blueprint

# Admin module (встроен напрямую)
admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

DATA_DIR = Path(os.environ.get("DATA_DIR", "."))

# ============================================================
# КОНФИГИ (хранятся в JSON файлах)
# ============================================================

def load_config(name: str, default: dict) -> dict:
    p = DATA_DIR / f"config_{name}.json"
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_config(name: str, data: dict):
    p = DATA_DIR / f"config_{name}.json"
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


DEFAULT_CRITERIA = {
    "criteria": [
        {"id": "goal", "name": "Представление и цель звонка", "weight": 0.05, "enabled": True},
        {"id": "need", "name": "Выявление потребности", "weight": 0.12, "enabled": True},
        {"id": "questions", "name": "Глубина уточняющих вопросов", "weight": 0.08, "enabled": True},
        {"id": "summary", "name": "Резюмирование потребности", "weight": 0.07, "enabled": True},
        {"id": "presentation", "name": "Презентация через пользу", "weight": 0.12, "enabled": True},
        {"id": "expertise", "name": "Экспертность и уверенность", "weight": 0.08, "enabled": True},
        {"id": "upsell", "name": "Допродажа", "weight": 0.08, "enabled": True},
        {"id": "objection_detect", "name": "Распознавание возражений", "weight": 0.08, "enabled": True},
        {"id": "objection_handle", "name": "Качество отработки возражений", "weight": 0.10, "enabled": True},
        {"id": "closing", "name": "Попытка закрытия", "weight": 0.10, "enabled": True},
        {"id": "next_step", "name": "Фиксация следующего шага", "weight": 0.07, "enabled": True},
        {"id": "speech", "name": "Речь и эмоциональный фон", "weight": 0.03, "enabled": True},
        {"id": "ending", "name": "Корректное завершение", "weight": 0.02, "enabled": True},
    ]
}

DEFAULT_TRIGGERS = {
    "triggers": [
        {"id": "no_next_step", "name": "Не зафиксирован следующий шаг", "enabled": True, "weight": "high"},
        {"id": "missed_objection", "name": "Не отработано возражение", "enabled": True, "weight": "high"},
        {"id": "missed_deal", "name": "Упущенная сделка", "enabled": True, "weight": "critical"},
        {"id": "no_needs", "name": "Не выявлены потребности", "enabled": True, "weight": "high"},
        {"id": "discount_no_nego", "name": "Скидка без переговоров", "enabled": True, "weight": "medium"},
        {"id": "interrupting", "name": "Перебивал клиента", "enabled": True, "weight": "medium"},
        {"id": "no_name", "name": "Не использовал имя клиента", "enabled": False, "weight": "low"},
        {"id": "no_deadline", "name": "Не уточнил сроки", "enabled": True, "weight": "medium"},
        {"id": "bad_emotions", "name": "Негативный эмоциональный фон", "enabled": True, "weight": "critical"},
        {"id": "no_summary", "name": "Нет резюме договорённостей", "enabled": True, "weight": "medium"},
        {"id": "no_alternative", "name": "Нет альтернативы при отказе", "enabled": True, "weight": "medium"},
        {"id": "no_closing", "name": "Звонок без понятного результата", "enabled": True, "weight": "high"},
    ]
}

DEFAULT_SCRIPTS = {
    "scripts": [
        {
            "id": "primary_new",
            "name": "Первичный входящий (новый клиент)",
            "stages": [
                "Приветствие и установление контакта",
                "Программирование разговора",
                "Выявление потребности",
                "Выявление боли и мотива",
                "Резюмирование потребности",
                "Презентация решения через пользу",
                "Допродажа",
                "Работа с возражениями",
                "Попытка закрытия",
                "Фиксация договорённости",
            ],
            "critical_stages": ["Выявление потребности", "Попытка закрытия", "Фиксация договорённости"],
            "success_criteria": "КП отправлено, назначен следующий звонок",
        }
    ]
}

# ============================================================
# AUTH
# ============================================================

def rop_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "username" not in session:
            return redirect("/login")
        if session.get("role") != "rop":
            abort(403)
        return f(*args, **kwargs)
    return decorated


def html_r(html):
    return Response(html, mimetype="text/html; charset=utf-8")


def admin_page(title, content_html, active=""):
    return f"""<!DOCTYPE html><html lang="ru"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — Админ</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,"Segoe UI",sans-serif;background:#FAF7F2;color:#2A1F15}}
.topbar{{background:#3D2E1F;color:#fff;padding:10px 24px;display:flex;align-items:center;gap:20px;border-bottom:3px solid #C9A961}}
.topbar a{{color:rgba(255,255,255,.75);text-decoration:none;font-size:13px;padding:4px 6px;border-radius:4px}}
.topbar a:hover,.topbar a.active{{color:#fff;background:rgba(255,255,255,.1)}}
.topbar .brand{{font-weight:700;font-size:15px;color:#C9A961;margin-right:8px}}
.container{{max-width:1100px;margin:0 auto;padding:24px}}
h1{{font-size:24px;font-weight:400;color:#3D2E1F;margin-bottom:20px;font-family:Georgia,serif}}
h2{{font-size:18px;font-weight:600;color:#3D2E1F;margin:20px 0 12px}}
.card{{background:#fff;border:1px solid #E8DFD0;border-radius:8px;padding:20px;margin-bottom:16px}}
.btn{{padding:8px 16px;border-radius:6px;border:none;cursor:pointer;font-family:inherit;font-size:13px;font-weight:600}}
.btn-primary{{background:#3D2E1F;color:#fff}}.btn-primary:hover{{background:#6B5544}}
.btn-danger{{background:#A0432B;color:#fff}}.btn-secondary{{background:#F2EBE0;color:#3D2E1F}}
input,textarea,select{{width:100%;padding:8px 12px;border:1px solid #E8DFD0;border-radius:4px;font-size:13px;font-family:inherit;background:#FAF7F2}}
input:focus,textarea:focus,select:focus{{outline:none;border-color:#C9A961}}
label{{font-size:12px;font-weight:600;color:#A39686;text-transform:uppercase;letter-spacing:1px;display:block;margin-bottom:4px;margin-top:12px}}
.row{{display:flex;gap:12px;align-items:center;padding:10px 0;border-bottom:1px solid #F0E8DA}}
.row:last-child{{border-bottom:none}}
.tag{{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600}}
.tag-high{{background:rgba(184,107,79,.15);color:#B86B4F}}
.tag-critical{{background:rgba(160,67,43,.15);color:#A0432B}}
.tag-medium{{background:rgba(201,169,97,.18);color:#C9A961}}
.tag-low{{background:rgba(124,139,111,.15);color:#7C8B6F}}
.notice{{background:#FFF8E8;border:1px solid #C9A961;border-radius:6px;padding:12px 16px;font-size:13px;margin-bottom:16px}}
.stat-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:20px}}
.stat{{background:#fff;border:1px solid #E8DFD0;border-radius:6px;padding:14px;border-top:3px solid #C9A961}}
.stat-val{{font-size:28px;font-family:Georgia,serif;color:#3D2E1F}}
.stat-label{{font-size:11px;color:#A39686;text-transform:uppercase;letter-spacing:1px;margin-top:4px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{text-align:left;padding:8px 12px;color:#A39686;font-size:11px;text-transform:uppercase;letter-spacing:1px;border-bottom:2px solid #E8DFD0}}
td{{padding:10px 12px;border-bottom:1px solid #F0E8DA}}
tr:hover td{{background:#FAF7F2}}
.progress{{height:6px;background:#F0E8DA;border-radius:3px;overflow:hidden}}
.progress-fill{{height:100%;border-radius:3px}}
.risk-high{{color:#A0432B;font-weight:700}}
.risk-medium{{color:#C9A961;font-weight:700}}
.risk-low{{color:#7C8B6F;font-weight:700}}
</style></head><body>
<div class="topbar">
  <span class="brand">⚙️ Админ</span>
  <a href="/admin" {"class='active'" if active=='home' else ""}>Главная</a>
  <a href="/admin/criteria" {"class='active'" if active=='criteria' else ""}>Критерии</a>
  <a href="/admin/triggers" {"class='active'" if active=='triggers' else ""}>Триггеры</a>
  <a href="/admin/scripts" {"class='active'" if active=='scripts' else ""}>Скрипты</a>
  <a href="/admin/learning" {"class='active'" if active=='learning' else ""}>Самообучение</a>
  <a href="/admin/conversion" {"class='active'" if active=='conversion' else ""}>Конверсия</a>
  <a href="/admin/forecast" {"class='active'" if active=='forecast' else ""}>Прогноз риска</a>
  <a href="/" style="margin-left:auto">← На сайт</a>
</div>
<div class="container">
<h1>{title}</h1>
{content_html}
</div></body></html>"""


# ============================================================
# ГЛАВНАЯ АДМИНКИ
# ============================================================

@admin_bp.route("/")
@rop_required
def admin_home():
    criteria_cfg = load_config("criteria", DEFAULT_CRITERIA)
    triggers_cfg = load_config("triggers", DEFAULT_TRIGGERS)
    scripts_cfg = load_config("scripts", DEFAULT_SCRIPTS)

    content = f"""
<div class="notice">⚙️ Здесь настраивается логика оценки звонков. Изменения применяются к новым анализам.</div>
<div class="stat-grid">
  <div class="stat"><div class="stat-val">{len(criteria_cfg['criteria'])}</div><div class="stat-label">Критериев оценки</div></div>
  <div class="stat"><div class="stat-val">{sum(1 for t in triggers_cfg['triggers'] if t['enabled'])}</div><div class="stat-label">Активных триггеров</div></div>
  <div class="stat"><div class="stat-val">{len(scripts_cfg['scripts'])}</div><div class="stat-label">Скриптов</div></div>
</div>
<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px">
  <a href="/admin/criteria" style="text-decoration:none">
    <div class="card" style="text-align:center;cursor:pointer">
      <div style="font-size:32px">📊</div>
      <div style="font-weight:600;margin-top:8px">Критерии оценки</div>
      <div style="font-size:12px;color:#A39686;margin-top:4px">Веса и описания критериев</div>
    </div>
  </a>
  <a href="/admin/triggers" style="text-decoration:none">
    <div class="card" style="text-align:center;cursor:pointer">
      <div style="font-size:32px">⚠️</div>
      <div style="font-weight:600;margin-top:8px">Триггеры</div>
      <div style="font-size:12px;color:#A39686;margin-top:4px">Включить/выключить ошибки</div>
    </div>
  </a>
  <a href="/admin/scripts" style="text-decoration:none">
    <div class="card" style="text-align:center;cursor:pointer">
      <div style="font-size:32px">📝</div>
      <div style="font-weight:600;margin-top:8px">Скрипты и эталоны</div>
      <div style="font-size:12px;color:#A39686;margin-top:4px">Этапы идеального звонка</div>
    </div>
  </a>
  <a href="/admin/learning" style="text-decoration:none">
    <div class="card" style="text-align:center;cursor:pointer">
      <div style="font-size:32px">🧠</div>
      <div style="font-weight:600;margin-top:8px">Самообучение</div>
      <div style="font-size:12px;color:#A39686;margin-top:4px">Калибровка на ваших оценках</div>
    </div>
  </a>
  <a href="/admin/conversion" style="text-decoration:none">
    <div class="card" style="text-align:center;cursor:pointer">
      <div style="font-size:32px">💰</div>
      <div style="font-weight:600;margin-top:8px">Конверсия</div>
      <div style="font-size:12px;color:#A39686;margin-top:4px">Связь качества с оплатами</div>
    </div>
  </a>
  <a href="/admin/forecast" style="text-decoration:none">
    <div class="card" style="text-align:center;cursor:pointer">
      <div style="font-size:32px">🔮</div>
      <div style="font-weight:600;margin-top:8px">Прогноз риска</div>
      <div style="font-size:12px;color:#A39686;margin-top:4px">Сделки под угрозой потери</div>
    </div>
  </a>
</div>"""
    return html_r(admin_page("Административные настройки", content, "home"))


# ============================================================
# КРИТЕРИИ
# ============================================================

@admin_bp.route("/criteria", methods=["GET", "POST"])
@rop_required
def admin_criteria():
    if request.method == "POST":
        data = request.json or {}
        save_config("criteria", data)
        return jsonify({"ok": True})

    cfg = load_config("criteria", DEFAULT_CRITERIA)
    total_weight = sum(c["weight"] for c in cfg["criteria"] if c["enabled"])

    rows = ""
    for c in cfg["criteria"]:
        enabled_check = "checked" if c["enabled"] else ""
        pct = int(c["weight"] * 100)
        rows += f"""
<div class="row" data-id="{c['id']}">
  <div style="flex:0 0 30px"><input type="checkbox" {enabled_check} onchange="updateCriteria()" data-field="enabled" style="width:auto"></div>
  <div style="flex:1;font-weight:600;font-size:14px">{c['name']}</div>
  <div style="flex:0 0 80px">
    <input type="number" value="{pct}" min="0" max="100" step="1"
      onchange="updateCriteria()" data-field="weight"
      style="width:70px;text-align:center"> %
  </div>
  <div style="flex:0 0 120px">
    <div class="progress"><div class="progress-fill" style="width:{min(pct,100)}%;background:{'#7C8B6F' if c['enabled'] else '#E8DFD0'}"></div></div>
  </div>
</div>"""

    content = f"""
<div class="notice">Сумма весов активных критериев должна быть = 100%. Сейчас: <b id="totalWeight">{int(total_weight*100)}%</b></div>
<div class="card">
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
  <div style="font-size:13px;color:#A39686">Изменения применяются к следующим анализам</div>
  <button class="btn btn-primary" onclick="saveCriteria()">💾 Сохранить</button>
</div>
{rows}
</div>
<script>
var _cfg = {json.dumps(cfg, ensure_ascii=False)};
function updateCriteria() {{
  var rows = document.querySelectorAll('[data-id]');
  var total = 0;
  rows.forEach(function(row) {{
    var id = row.dataset.id;
    var enabled = row.querySelector('[data-field=enabled]').checked;
    var weight = parseFloat(row.querySelector('[data-field=weight]').value) / 100;
    var c = _cfg.criteria.find(function(x){{return x.id===id}});
    if (c) {{ c.enabled = enabled; c.weight = weight; }}
    if (enabled) total += weight;
    var bar = row.querySelector('.progress-fill');
    if (bar) {{ bar.style.width = Math.min(weight*100, 100)+'%'; bar.style.background = enabled ? '#7C8B6F' : '#E8DFD0'; }}
  }});
  document.getElementById('totalWeight').textContent = Math.round(total*100)+'%';
  document.getElementById('totalWeight').style.color = Math.abs(total-1) < 0.01 ? '#7C8B6F' : '#A0432B';
}}
function saveCriteria() {{
  fetch('/admin/criteria', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify(_cfg)}})
  .then(r=>r.json()).then(d=>alert(d.ok ? '✅ Сохранено!' : 'Ошибка'));
}}
</script>"""
    return html_r(admin_page("Критерии оценки", content, "criteria"))


# ============================================================
# ТРИГГЕРЫ
# ============================================================

@admin_bp.route("/triggers", methods=["GET", "POST"])
@rop_required
def admin_triggers():
    if request.method == "POST":
        save_config("triggers", request.json or {})
        return jsonify({"ok": True})

    cfg = load_config("triggers", DEFAULT_TRIGGERS)
    rows = ""
    for t in cfg["triggers"]:
        enabled_check = "checked" if t["enabled"] else ""
        tag_cls = f"tag-{t.get('weight','medium')}"
        rows += f"""
<div class="row" data-id="{t['id']}">
  <div style="flex:0 0 30px"><input type="checkbox" {enabled_check} onchange="updateTriggers()" data-field="enabled" style="width:auto"></div>
  <div style="flex:1;font-size:14px">{t['name']}</div>
  <div style="flex:0 0 120px">
    <select onchange="updateTriggers()" data-field="weight">
      <option value="low" {'selected' if t.get('weight')=='low' else ''}>Низкий</option>
      <option value="medium" {'selected' if t.get('weight')=='medium' else ''}>Средний</option>
      <option value="high" {'selected' if t.get('weight')=='high' else ''}>Высокий</option>
      <option value="critical" {'selected' if t.get('weight')=='critical' else ''}>Критичный</option>
    </select>
  </div>
</div>"""

    content = f"""
<div class="card">
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
  <div style="font-size:13px;color:#A39686">Отключённые триггеры не будут срабатывать при анализе</div>
  <button class="btn btn-primary" onclick="saveTriggers()">💾 Сохранить</button>
</div>
{rows}
</div>
<script>
var _tcfg = {json.dumps(cfg, ensure_ascii=False)};
function updateTriggers() {{
  document.querySelectorAll('[data-id]').forEach(function(row) {{
    var id = row.dataset.id;
    var t = _tcfg.triggers.find(function(x){{return x.id===id}});
    if (t) {{
      t.enabled = row.querySelector('[data-field=enabled]').checked;
      t.weight = row.querySelector('[data-field=weight]').value;
    }}
  }});
}}
function saveTriggers() {{
  fetch('/admin/triggers', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify(_tcfg)}})
  .then(r=>r.json()).then(d=>alert(d.ok ? '✅ Сохранено!' : 'Ошибка'));
}}
</script>"""
    return html_r(admin_page("Настройка триггеров", content, "triggers"))


# ============================================================
# СКРИПТЫ
# ============================================================

@admin_bp.route("/scripts", methods=["GET", "POST"])
@rop_required
def admin_scripts():
    if request.method == "POST":
        save_config("scripts", request.json or {})
        return jsonify({"ok": True})

    cfg = load_config("scripts", DEFAULT_SCRIPTS)
    scripts_html = ""
    for s in cfg["scripts"]:
        stages_text = "\n".join(s.get("stages", []))
        critical_text = "\n".join(s.get("critical_stages", []))
        scripts_html += f"""
<div class="card" data-script-id="{s['id']}">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
    <div style="font-weight:700;font-size:16px">{s['name']}</div>
    <button class="btn btn-primary" onclick="saveScript('{s['id']}')">💾 Сохранить</button>
  </div>
  <label>Название</label>
  <input type="text" value="{s['name']}" data-field="name">
  <label>Этапы эталонного разговора (каждый с новой строки)</label>
  <textarea rows="10" data-field="stages" style="font-size:12px">{stages_text}</textarea>
  <label>Критические этапы (обязательные, каждый с новой строки)</label>
  <textarea rows="4" data-field="critical_stages" style="font-size:12px">{critical_text}</textarea>
  <label>Критерий успеха</label>
  <input type="text" value="{s.get('success_criteria','')}" data-field="success_criteria">
</div>"""

    content = f"""
<div class="notice">Скрипты используются как эталон при анализе. ИИ сопоставляет разговор с этапами.</div>
<button class="btn btn-secondary" onclick="addScript()" style="margin-bottom:16px">+ Добавить скрипт</button>
{scripts_html}
<script>
var _scfg = {json.dumps(cfg, ensure_ascii=False)};
function saveScript(id) {{
  var card = document.querySelector('[data-script-id="'+id+'"]');
  var s = _scfg.scripts.find(function(x){{return x.id===id}});
  if (!s) return;
  s.name = card.querySelector('[data-field=name]').value;
  s.stages = card.querySelector('[data-field=stages]').value.split('\\n').filter(Boolean);
  s.critical_stages = card.querySelector('[data-field=critical_stages]').value.split('\\n').filter(Boolean);
  s.success_criteria = card.querySelector('[data-field=success_criteria]').value;
  fetch('/admin/scripts', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify(_scfg)}})
  .then(r=>r.json()).then(d=>alert(d.ok ? '✅ Сохранено!' : 'Ошибка'));
}}
function addScript() {{
  var name = prompt('Название нового скрипта:');
  if (!name) return;
  var id = name.toLowerCase().replace(/[^a-z0-9]/g,'_');
  _scfg.scripts.push({{id:id, name:name, stages:[], critical_stages:[], success_criteria:''}});
  location.reload();
}}
</script>"""
    return html_r(admin_page("Скрипты и эталоны", content, "scripts"))


# ============================================================
# САМООБУЧЕНИЕ
# ============================================================

@admin_bp.route("/learning")
@rop_required
def admin_learning():
    """Показывает паттерны из ручных правок для калибровки ИИ."""
    corrections_path = DATA_DIR / "manual_corrections.json"
    analyses_path = DATA_DIR / "analyses.json"

    corrections = {}
    if corrections_path.exists():
        try: corrections = json.loads(corrections_path.read_text(encoding="utf-8"))
        except: pass

    analyses = {}
    if analyses_path.exists():
        try: analyses = json.loads(analyses_path.read_text(encoding="utf-8"))
        except: pass

    if not corrections:
        content = '<div class="notice">Пока нет ручных правок оценок. Начните исправлять оценки ИИ — здесь появится анализ паттернов.</div>'
        return html_r(admin_page("Самообучение", content, "learning"))

    # Анализируем расхождения ИИ и человека
    diffs = []
    overcritical = 0  # ИИ занижает
    underlevel = 0    # ИИ завышает
    total_diff = 0

    for aid, corr in corrections.items():
        a = analyses.get(aid, {}).get("analysis", {})
        ai_score = a.get("overall_score")
        human_score = corr.get("overall_score")
        if ai_score is None or human_score is None:
            continue
        diff = float(human_score) - float(ai_score)
        total_diff += diff
        if diff > 1: overcritical += 1
        elif diff < -1: underlevel += 1
        diffs.append({
            "id": aid,
            "ai": float(ai_score),
            "human": float(human_score),
            "diff": round(diff, 1),
            "comment": corr.get("comment", ""),
            "call_type": a.get("call_type", {}).get("label", "") if isinstance(a.get("call_type"), dict) else "",
        })

    diffs.sort(key=lambda x: abs(x["diff"]), reverse=True)
    avg_diff = round(total_diff / len(diffs), 2) if diffs else 0
    bias = "занижает" if avg_diff > 0 else "завышает"
    bias_color = "#7C8B6F" if abs(avg_diff) < 0.5 else "#A0432B"

    # Паттерны по типам звонков
    type_diffs = defaultdict(list)
    for d in diffs:
        if d["call_type"]:
            type_diffs[d["call_type"]].append(d["diff"])

    type_rows = ""
    for ctype, ds in sorted(type_diffs.items(), key=lambda x: abs(sum(x[1])/len(x[1])), reverse=True):
        avg = round(sum(ds)/len(ds), 1)
        color = "#A0432B" if abs(avg) > 1 else "#7C8B6F"
        type_rows += f'<tr><td>{ctype}</td><td>{len(ds)}</td><td style="color:{color};font-weight:700">{avg:+.1f}</td></tr>'

    # Калибровочный промпт
    calibration_hint = ""
    if len(diffs) >= 5:
        if avg_diff > 0.5:
            calibration_hint = f"ИИ в среднем занижает оценки на {avg_diff} балла. Рекомендуется добавить в системный промпт: «Будь немного мягче в оценках — твои оценки в среднем на {avg_diff} ниже оценок руководителя»."
        elif avg_diff < -0.5:
            calibration_hint = f"ИИ в среднем завышает оценки на {abs(avg_diff)} балла. Рекомендуется добавить в системный промпт: «Будь строже в оценках»."
        else:
            calibration_hint = "ИИ хорошо откалиброван — расхождение с оценками руководителя минимально."

    # Генерируем калибровочный промпт
    calibration_prompt = _generate_calibration_prompt(diffs, corrections)

    diff_rows = "".join(
        f'<tr><td><a href="/calls/{d["id"]}">{d["id"]}</a></td>'
        f'<td>{d["call_type"]}</td>'
        f'<td>{d["ai"]}</td><td>{d["human"]}</td>'
        f'<td style="color:{"#7C8B6F" if abs(d["diff"])<1 else "#A0432B"};font-weight:700">{d["diff"]:+.1f}</td>'
        f'<td style="font-size:11px;color:#A39686">{d["comment"][:50]}</td></tr>'
        for d in diffs[:20]
    )

    content = f"""
<div class="stat-grid">
  <div class="stat"><div class="stat-val">{len(diffs)}</div><div class="stat-label">Правок оценок</div></div>
  <div class="stat"><div class="stat-val" style="color:{bias_color}">{avg_diff:+.1f}</div><div class="stat-label">Средний сдвиг (ИИ {bias})</div></div>
  <div class="stat"><div class="stat-val" style="color:#7C8B6F">{overcritical}</div><div class="stat-label">ИИ занижал</div></div>
  <div class="stat"><div class="stat-val" style="color:#C9A961">{underlevel}</div><div class="stat-label">ИИ завышал</div></div>
</div>

<div class="card">
  <h2>💡 Вывод</h2>
  <p style="font-size:14px;line-height:1.6">{calibration_hint}</p>
</div>

<div class="card">
  <h2>🔧 Калибровочная инструкция для ИИ</h2>
  <p style="font-size:12px;color:#A39686;margin-bottom:8px">Скопируйте и добавьте в системный промпт claude_analyzer.py</p>
  <textarea rows="6" style="font-size:12px;background:#F8F5F0">{calibration_prompt}</textarea>
  <button class="btn btn-primary" style="margin-top:8px" onclick="navigator.clipboard.writeText(document.querySelector('textarea').value);alert('Скопировано!')">📋 Скопировать</button>
</div>

<div class="card">
  <h2>📊 Расхождения по типам звонков</h2>
  <table><tr><th>Тип звонка</th><th>Правок</th><th>Средний сдвиг</th></tr>{type_rows}</table>
</div>

<div class="card">
  <h2>📋 Все правки (топ расхождений)</h2>
  <table>
    <tr><th>ID</th><th>Тип</th><th>ИИ</th><th>Человек</th><th>Разница</th><th>Комментарий</th></tr>
    {diff_rows}
  </table>
</div>"""
    return html_r(admin_page("Самообучение", content, "learning"))


def _generate_calibration_prompt(diffs, corrections):
    if not diffs:
        return "Недостаточно данных для калибровки."
    avg_diff = sum(d["diff"] for d in diffs) / len(diffs)
    examples = [d for d in diffs if abs(d["diff"]) >= 2][:3]

    prompt = f"КАЛИБРОВКА ОЦЕНОК (на основе {len(diffs)} правок руководителя):\n"
    if avg_diff > 0.3:
        prompt += f"- Твои оценки в среднем на {avg_diff:.1f} балла НИЖЕ оценок руководителя. Будь немного мягче.\n"
    elif avg_diff < -0.3:
        prompt += f"- Твои оценки в среднем на {abs(avg_diff):.1f} балла ВЫШЕ оценок руководителя. Будь строже.\n"
    else:
        prompt += "- Твои оценки хорошо совпадают с оценками руководителя. Продолжай в том же духе.\n"

    if examples:
        prompt += "\nПримеры расхождений (для понимания шкалы):\n"
        for ex in examples:
            comment = corrections.get(ex["id"], {}).get("comment", "")
            prompt += f"- Звонок оценён тобой как {ex['ai']}, руководитель поставил {ex['human']}"
            if comment:
                prompt += f" (причина: {comment})"
            prompt += "\n"
    return prompt


# ============================================================
# КОНВЕРСИЯ
# ============================================================

@admin_bp.route("/conversion")
@rop_required
def admin_conversion():
    """Связь качества звонков с конверсией в оплату."""
    calls_path = DATA_DIR / "calls_data.json"
    analyses_path = DATA_DIR / "analyses.json"

    calls = []
    if calls_path.exists():
        try: calls = json.loads(calls_path.read_text(encoding="utf-8"))
        except: pass

    analyses = {}
    if analyses_path.exists():
        try: analyses = json.loads(analyses_path.read_text(encoding="utf-8"))
        except: pass

    if not calls or not analyses:
        content = '<div class="notice">Нет данных для анализа конверсии.</div>'
        return html_r(admin_page("Конверсия", content, "conversion"))

    # Группируем по диапазонам оценок
    score_buckets = {"9-10": [], "7-8": [], "5-6": [], "0-4": []}
    won_by_bucket = {"9-10": 0, "7-8": 0, "5-6": 0, "0-4": 0}

    for c in calls:
        a = analyses.get(c["activity_id"], {}).get("analysis", {})
        score = a.get("overall_score")
        if score is None: continue
        score = float(score)
        stage = c.get("crm", {}).get("stage_id", "").upper()
        won = "WON" in stage or "WIN" in stage

        if score >= 9: bucket = "9-10"
        elif score >= 7: bucket = "7-8"
        elif score >= 5: bucket = "5-6"
        else: bucket = "0-4"

        score_buckets[bucket].append(score)
        if won: won_by_bucket[bucket] += 1

    # Средние оценки по менеджерам
    manager_stats = defaultdict(lambda: {"scores": [], "won": 0, "total": 0, "name": ""})
    for c in calls:
        mid = c.get("manager", {}).get("id", 0)
        mname = c.get("manager", {}).get("name", "")
        a = analyses.get(c["activity_id"], {}).get("analysis", {})
        score = a.get("overall_score")
        stage = c.get("crm", {}).get("stage_id", "").upper()
        won = "WON" in stage or "WIN" in stage
        manager_stats[mid]["name"] = mname
        manager_stats[mid]["total"] += 1
        if won: manager_stats[mid]["won"] += 1
        if score is not None:
            manager_stats[mid]["scores"].append(float(score))

    bucket_rows = ""
    for bucket, scores in score_buckets.items():
        if not scores: continue
        count = len(scores)
        won = won_by_bucket[bucket]
        conv = round(won / count * 100, 1) if count > 0 else 0
        avg = round(sum(scores)/count, 1)
        bar_w = min(int(conv), 100)
        bucket_rows += f"""
<tr>
  <td><b>{bucket} баллов</b></td>
  <td>{count} звонков</td>
  <td>{won}</td>
  <td>
    <div style="display:flex;align-items:center;gap:8px">
      <div class="progress" style="flex:1"><div class="progress-fill" style="width:{bar_w}%;background:#7C8B6F"></div></div>
      <b>{conv}%</b>
    </div>
  </td>
</tr>"""

    manager_rows = ""
    for mid, ms in sorted(manager_stats.items(), key=lambda x: -(sum(x[1]["scores"])/len(x[1]["scores"]) if x[1]["scores"] else 0)):
        avg_score = round(sum(ms["scores"])/len(ms["scores"]), 1) if ms["scores"] else 0
        conv = round(ms["won"]/ms["total"]*100, 1) if ms["total"] > 0 else 0
        manager_rows += f"""
<tr>
  <td><b>{ms['name']}</b></td>
  <td>{ms['total']}</td>
  <td>{avg_score}</td>
  <td>{ms['won']}</td>
  <td><b>{conv}%</b></td>
</tr>"""

    # Корреляция оценки и выигрыша
    scored_calls = [(float(analyses[c["activity_id"]]["analysis"]["overall_score"]),
                     1 if "WON" in c.get("crm",{}).get("stage_id","").upper() else 0)
                    for c in calls
                    if c["activity_id"] in analyses
                    and analyses[c["activity_id"]].get("analysis",{}).get("overall_score") is not None]

    correlation_text = ""
    if len(scored_calls) >= 10:
        scores_list = [x[0] for x in scored_calls]
        won_list = [x[1] for x in scored_calls]
        n = len(scores_list)
        mean_s = sum(scores_list)/n
        mean_w = sum(won_list)/n
        cov = sum((s-mean_s)*(w-mean_w) for s,w in zip(scores_list,won_list))/n
        std_s = math.sqrt(sum((s-mean_s)**2 for s in scores_list)/n) or 1
        std_w = math.sqrt(sum((w-mean_w)**2 for w in won_list)/n) or 1
        corr = round(cov/(std_s*std_w), 2)
        if abs(corr) > 0.5:
            correlation_text = f"<div class='notice'>📈 Корреляция оценки ИИ и выигрыша сделки: <b>{corr}</b> — {'сильная связь' if abs(corr)>0.7 else 'умеренная связь'}. {'Высокие оценки действительно предсказывают оплату.' if corr>0 else 'Аномалия — нужна доп. проверка.'}</div>"
        else:
            correlation_text = f"<div class='notice'>📊 Корреляция оценки и выигрыша: <b>{corr}</b> — слабая связь. Возможно нужна калибровка критериев.</div>"

    content = f"""
{correlation_text}
<div class="card">
  <h2>Конверсия по диапазонам оценок</h2>
  <table><tr><th>Оценка ИИ</th><th>Звонков</th><th>Выиграно</th><th>Конверсия</th></tr>
  {bucket_rows or '<tr><td colspan="4" style="color:#A39686;text-align:center">Нет данных о статусах сделок</td></tr>'}
  </table>
</div>
<div class="card">
  <h2>Конверсия по менеджерам</h2>
  <table><tr><th>Менеджер</th><th>Звонков</th><th>Ср. оценка</th><th>Выиграно</th><th>Конверсия</th></tr>
  {manager_rows}
  </table>
</div>"""
    return html_r(admin_page("Конверсия и качество", content, "conversion"))


# ============================================================
# ПРОГНОЗ РИСКА ПОТЕРИ СДЕЛКИ
# ============================================================

@admin_bp.route("/forecast")
@rop_required
def admin_forecast():
    """Прогноз риска потери сделки на основе качества звонков."""
    calls_path = DATA_DIR / "calls_data.json"
    analyses_path = DATA_DIR / "analyses.json"

    calls = []
    if calls_path.exists():
        try: calls = json.loads(calls_path.read_text(encoding="utf-8"))
        except: pass

    analyses = {}
    if analyses_path.exists():
        try: analyses = json.loads(analyses_path.read_text(encoding="utf-8"))
        except: pass

    # Группируем по сделкам
    deals = defaultdict(lambda: {"calls": [], "client": "", "manager": "", "stage": "", "owner_id": ""})
    for c in calls:
        oid = c.get("crm", {}).get("owner_id", "")
        otype = c.get("crm", {}).get("owner_type", "")
        if otype != "deal" or not oid:
            continue
        deals[oid]["calls"].append(c)
        deals[oid]["client"] = c.get("client", {}).get("name", "")
        deals[oid]["manager"] = c.get("manager", {}).get("name", "")
        deals[oid]["stage"] = c.get("crm", {}).get("stage_name", "") or c.get("crm", {}).get("stage_id", "")
        deals[oid]["owner_id"] = oid

    # Считаем риск для каждой сделки
    risk_deals = []
    for deal_id, deal in deals.items():
        stage = deal["stage"].upper()
        # Пропускаем завершённые
        if any(x in stage for x in ["WON", "LOSE", "WIN", "ОТКАЗ", "УСПЕХ"]):
            continue

        scores = []
        flags_count = 0
        no_next_step_count = 0
        last_call_date = None
        triggers_all = []

        for c in deal["calls"]:
            a = analyses.get(c["activity_id"], {}).get("analysis", {})
            score = a.get("overall_score")
            if score is not None:
                scores.append(float(score))
            flags = a.get("flags", {}) or {}
            if flags.get("no_next_step"): no_next_step_count += 1
            if flags.get("missed_deal"): flags_count += 2
            if a.get("is_critical"): flags_count += 1
            for t in (a.get("triggers") or []):
                triggers_all.append(t.get("name", ""))
            created = c.get("created", "")
            if created and (last_call_date is None or created > last_call_date):
                last_call_date = created

        if not scores:
            continue

        avg_score = sum(scores) / len(scores)
        trend = scores[-1] - scores[0] if len(scores) >= 2 else 0

        # Дней без контакта
        days_silent = 0
        if last_call_date:
            try:
                last_dt = datetime.fromisoformat(last_call_date)
                days_silent = (datetime.now() - last_dt).days
            except: pass

        # Риск-скор (0-100, выше = опаснее)
        risk = 0
        if avg_score < 5: risk += 40
        elif avg_score < 7: risk += 20
        if trend < -1: risk += 20
        risk += min(flags_count * 10, 30)
        risk += min(no_next_step_count * 8, 24)
        if days_silent > 7: risk += 15
        if days_silent > 14: risk += 10
        risk = min(risk, 100)

        if risk < 30:
            risk_level = "low"
            risk_label = "Низкий"
        elif risk < 60:
            risk_level = "medium"
            risk_label = "Средний"
        else:
            risk_level = "high"
            risk_label = "Высокий"

        # Причина риска
        reasons = []
        if avg_score < 5: reasons.append(f"низкая оценка звонков ({avg_score:.1f}/10)")
        if trend < -1: reasons.append("оценки падают")
        if no_next_step_count > 0: reasons.append(f"{no_next_step_count} звонков без следующего шага")
        if days_silent > 7: reasons.append(f"{days_silent} дней без контакта")
        if flags_count > 0: reasons.append("критические ошибки")

        risk_deals.append({
            "deal_id": deal_id,
            "client": deal["client"],
            "manager": deal["manager"],
            "stage": deal["stage"],
            "calls_count": len(deal["calls"]),
            "avg_score": round(avg_score, 1),
            "risk": risk,
            "risk_level": risk_level,
            "risk_label": risk_label,
            "reasons": reasons,
            "days_silent": days_silent,
            "bitrix_url": f"https://mavisgroup.bitrix24.by/crm/deal/details/{deal_id}/",
        })

    risk_deals.sort(key=lambda x: -x["risk"])

    if not risk_deals:
        content = '<div class="notice">Нет активных сделок для анализа риска. Данные появятся после анализа звонков с привязкой к сделкам.</div>'
        return html_r(admin_page("Прогноз риска", content, "forecast"))

    high_count = sum(1 for d in risk_deals if d["risk_level"] == "high")
    medium_count = sum(1 for d in risk_deals if d["risk_level"] == "medium")

    rows = ""
    for d in risk_deals:
        risk_cls = f"risk-{d['risk_level']}"
        reasons_str = "; ".join(d["reasons"]) if d["reasons"] else "—"
        bar_color = {"high": "#A0432B", "medium": "#C9A961", "low": "#7C8B6F"}[d["risk_level"]]
        rows += f"""
<tr>
  <td><a href="{d['bitrix_url']}" target="_blank"><b>{d['client']}</b></a></td>
  <td style="font-size:12px;color:#A39686">{d['manager']}</td>
  <td style="font-size:12px">{d['stage']}</td>
  <td>{d['calls_count']}</td>
  <td>{d['avg_score']}</td>
  <td>{d['days_silent']}д</td>
  <td>
    <div style="display:flex;align-items:center;gap:8px">
      <div class="progress" style="width:60px"><div class="progress-fill" style="width:{d['risk']}%;background:{bar_color}"></div></div>
      <span class="{risk_cls}">{d['risk_label']}</span>
    </div>
  </td>
  <td style="font-size:11px;color:#A39686;max-width:200px">{reasons_str}</td>
</tr>"""

    content = f"""
<div class="notice">
  🔴 Высокий риск: <b>{high_count}</b> сделок &nbsp;&nbsp;
  🟡 Средний риск: <b>{medium_count}</b> сделок
</div>
<div class="card">
  <h2>Активные сделки по риску потери</h2>
  <p style="font-size:12px;color:#A39686;margin-bottom:12px">Риск рассчитывается на основе: средней оценки звонков, динамики оценок, наличия следующего шага, дней без контакта, критических ошибок.</p>
  <table>
    <tr><th>Клиент</th><th>Менеджер</th><th>Стадия</th><th>Звонков</th><th>Ср.оценка</th><th>Молчание</th><th>Риск</th><th>Причины</th></tr>
    {rows}
  </table>
</div>"""
    return html_r(admin_page("Прогноз риска потери сделки", content, "forecast"))

# Добавляем src/ в путь чтобы импортировать report_generator
sys.path.insert(0, str(Path(__file__).parent / "src"))

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "mavis-secret-2026")
app.register_blueprint(admin_bp)

DATA_DIR = Path(os.environ.get("DATA_DIR", "."))

# ============================================================
# ПОЛЬЗОВАТЕЛИ
# ============================================================

def hash_pw(p): return hashlib.sha256(p.encode()).hexdigest()

USERS = [
    {"username": "rop",    "password_hash": hash_pw(os.environ.get("ROP_PASSWORD",   "rop123")),   "role": "rop",     "name": "Руководитель",       "manager_id": None},
    {"username": "roman",  "password_hash": hash_pw(os.environ.get("ROMAN_PASSWORD", "roman123")), "role": "manager", "name": "Роман Авсеенко",     "manager_id": 1286},
    {"username": "irina",  "password_hash": hash_pw(os.environ.get("IRINA_PASSWORD", "irina123")), "role": "manager", "name": "Ирина Богомольцева", "manager_id": 2100},
]

def find_user(username, password):
    ph = hash_pw(password)
    return next((u for u in USERS if u["username"] == username and u["password_hash"] == ph), None)

def current_user():
    return {"username": session.get("username",""), "role": session.get("role",""),
            "name": session.get("name",""), "manager_id": session.get("manager_id")}

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "username" not in session:
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return decorated

def rop_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "username" not in session: return redirect(url_for("login"))
        if session.get("role") != "rop": abort(403)
        return f(*args, **kwargs)
    return decorated

# ============================================================
# ДАННЫЕ
# ============================================================

def load_calls():
    p = DATA_DIR / "calls_data.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []

def load_analyses():
    p = DATA_DIR / "analyses.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

def load_corrections():
    p = DATA_DIR / "manual_corrections.json"
    if not p.exists(): return {}
    try: return json.loads(p.read_text(encoding="utf-8"))
    except: return {}

def apply_corrections(analyses, corrections):
    for aid, corr in corrections.items():
        if aid in analyses:
            ad = analyses[aid].get("analysis", {})
            if "overall_score" in corr:
                ad["overall_score"] = corr["overall_score"]
                ad["score_corrected"] = True
            if "comment" in corr:
                ad["correction_comment"] = corr["comment"]
    return analyses

def get_data(user=None):
    calls = load_calls()
    analyses = load_analyses()
    corrections = load_corrections()
    analyses = apply_corrections(analyses, corrections)
    if user and user.get("role") == "manager" and user.get("manager_id"):
        calls = [c for c in calls if c.get("manager",{}).get("id") == user["manager_id"]]
    return calls, analyses

# ============================================================
# ИНЪЕКЦИЯ НАВИГАЦИИ В HTML
# ============================================================

LOGIN_CSS = """
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,"Segoe UI",sans-serif;background:#FAF7F2;min-height:100vh;display:flex;align-items:center;justify-content:center}
.login-box{background:#fff;border:1px solid #E8DFD0;border-radius:10px;padding:40px 36px;width:100%;max-width:380px;box-shadow:0 4px 24px rgba(61,46,31,.08)}
.brand{font-family:Georgia,serif;font-size:20px;font-weight:700;color:#3D2E1F;letter-spacing:1px;text-align:center}
.sub{font-size:11px;letter-spacing:3px;color:#C9A961;text-transform:uppercase;margin-top:4px;text-align:center}
.divider{width:40px;height:2px;background:#C9A961;margin:16px auto 24px}
label{display:block;font-size:12px;font-weight:600;color:#A39686;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px;margin-top:16px}
input{width:100%;padding:11px 14px;border:1px solid #E8DFD0;border-radius:6px;font-size:14px;font-family:inherit;background:#FAF7F2;color:#2A1F15}
input:focus{outline:none;border-color:#C9A961;background:#fff}
.error{background:#FFE5DC;border:1px solid #A0432B;border-radius:6px;padding:10px 14px;font-size:13px;color:#A0432B;margin-bottom:16px;text-align:center}
button{width:100%;padding:12px;background:#3D2E1F;color:#fff;border:none;border-radius:6px;font-size:15px;font-weight:600;cursor:pointer;margin-top:24px;font-family:inherit}
button:hover{background:#6B5544}
</style>
"""

def auth_bar(user):
    """Полоска авторизации которая вставляется в существующий HTML."""
    role_badge = "РОП" if user["role"] == "rop" else "Менеджер"
    extra_links = ""
    admin_link = ""
    if user["role"] == "rop":
        extra_links = """
        <a href="/rop" style="color:rgba(255,255,255,.75);font-size:13px;padding:4px 2px;border-bottom:2px solid transparent;text-decoration:none;white-space:nowrap">Отчёт РОПа</a>
        <a href="/compare" style="color:rgba(255,255,255,.75);font-size:13px;padding:4px 2px;border-bottom:2px solid transparent;text-decoration:none;white-space:nowrap">Сравнение</a>
        <a href="/managers" style="color:rgba(255,255,255,.75);font-size:13px;padding:4px 2px;border-bottom:2px solid transparent;text-decoration:none;white-space:nowrap">Менеджеры</a>
        """
        admin_link = '<a href="/admin" style="color:#C9A961;text-decoration:none;font-weight:600;white-space:nowrap;border:1px solid #C9A961;padding:4px 10px;border-radius:4px">⚙️ Админ</a>'
    return f"""
<div style="background:#1a1208;color:#fff;padding:6px 16px;display:flex;align-items:center;justify-content:space-between;font-family:-apple-system,sans-serif;font-size:12px;gap:16px;flex-wrap:wrap;position:relative;z-index:50">
  <div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap">
    <span style="color:#C9A961;font-weight:700">{role_badge}: {user['name']}</span>
    {extra_links}
    <a href="/" style="color:rgba(255,255,255,.7);text-decoration:none">Сводка</a>
    <a href="/calls" style="color:rgba(255,255,255,.7);text-decoration:none">Все звонки</a>
    <a href="/critical" style="color:rgba(255,255,255,.7);text-decoration:none">🔴 Срочно</a>
    <a href="/best" style="color:rgba(255,255,255,.7);text-decoration:none">🏆 Лучшие</a>
    <a href="/objections" style="color:rgba(255,255,255,.7);text-decoration:none">💬 Возражения</a>
  </div>
  <div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap">
    {admin_link}
    <a href="/logout" style="color:#C9A961;text-decoration:none;font-weight:600;white-space:nowrap">Выйти →</a>
  </div>
</div>
"""

def inject_auth(html, user):
    """Убирает оригинальный топбар report_generator и вставляет наш с авторизацией."""
    marker_start = '<div class="topbar">'
    marker_end = '<div class="container">'
    if marker_start in html and marker_end in html:
        idx_start = html.index(marker_start)
        idx_end = html.index(marker_end)
        html = html[:idx_start] + html[idx_end:]
    html = html.replace('position: sticky; top: 0; z-index: 10;', 'position: relative;')
    bar = auth_bar(user)
    # Добавляем CSS чтобы модалка и чат работали поверх всего
    proxy_url = os.environ.get("PROXY_URL", "")
    fix_css = f"""<style>
.modal-backdrop {{ z-index: 99999 !important; }}
.modal {{ z-index: 100000 !important; }}
#aiChatBtn {{ z-index: 99998 !important; }}
#aiChatPanel {{ z-index: 99999 !important; }}
#editModal {{ z-index: 99999 !important; }}
</style>
<script>
// Переопределяем PROXY_URL из переменной окружения сервера
window._PROXY_URL = "{proxy_url}";
// Патчим функцию sendAiMessage если она уже определена
document.addEventListener("DOMContentLoaded", function() {{
  if (typeof sendAiMessage === "function") {{
    var _orig = sendAiMessage;
  }}
}});
// Переопределяем проверку proxy URL
var _origFetch = window.fetch;
</script>"""
    if "<body>" in html:
        return html.replace("<body>", f"<body>{bar}{fix_css}", 1)
    return bar + fix_css + html

def inject_and_fix(html, user):
    html = fix_links(html)
    return inject_auth(html, user)


def fix_links(html):
    """Заменяет статические HTML-пути на Flask маршруты."""
    replacements = [
        ('href="index.html"',        'href="/"'),
        ('href="../index.html"',     'href="/"'),
        ('href="rop-report.html"',   'href="/rop"'),
        ('href="../rop-report.html"','href="/rop"'),
        ('href="managers.html"',     'href="/managers"'),
        ('href="../managers.html"',  'href="/managers"'),
        ('href="all-calls.html"',    'href="/calls"'),
        ('href="../all-calls.html"', 'href="/calls"'),
        ('href="critical.html"',     'href="/critical"'),
        ('href="../critical.html"',  'href="/critical"'),
        ('href="triggers.html"',     'href="/triggers"'),
        ('href="../triggers.html"',  'href="/triggers"'),
        ('href="compare.html"',      'href="/compare"'),
        ('href="../compare.html"',   'href="/compare"'),
        ('href="objections.html"',   'href="/objections"'),
        ('href="../objections.html"','href="/objections"'),
        ('href="best-calls.html"',   'href="/best"'),
        ('href="../best-calls.html"','href="/best"'),
        ('href="calls/',             'href="/calls/'),
        ('href="../calls/',          'href="/calls/'),
        ('.html"',                   '"'),  # убираем .html из всех ссылок
        ('href="managers/',          'href="/managers/'),
        ('href="../managers/',       'href="/managers/'),
        ('src="../avatars/',         'src="/static/avatars/'),
        ('src="avatars/',            'src="/static/avatars/'),
        ('src="../logo.png"',        'src="/static/logo.png"'),
        ('src="logo.png"',           'src="/static/logo.png"'),
    ]
    for o, n in replacements:
        html = html.replace(o, n)
    # Убираем .html из ссылок на звонки и менеджеров
    import re
    html = re.sub(r'href="(/calls/[^"]+)\.html"', r'href="\1"', html)
    html = re.sub(r'href="(/managers/[^"]+)\.html"', r'href="\1"', html)
    return html

def html_response(html):
    return Response(html, mimetype="text/html; charset=utf-8")

# ============================================================
# МАРШРУТЫ АВТОРИЗАЦИИ
# ============================================================

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        user = find_user(request.form.get("username","").strip(), request.form.get("password",""))
        if user:
            session.update({"username": user["username"], "role": user["role"],
                           "name": user["name"], "manager_id": user["manager_id"]})
            return redirect(request.args.get("next") or "/")
        error = "Неверный логин или пароль"
    err_html = f'<div class="error">{error}</div>' if error else ""
    return html_response(f"""<!DOCTYPE html><html lang="ru"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Вход — Mavis Group</title>{LOGIN_CSS}</head><body>
<div class="login-box">
  <div class="brand">MAVIS GROUP</div>
  <div class="sub">Sales Analytics</div>
  <div class="divider"></div>
  {err_html}
  <form method="POST">
    <label>Логин</label><input name="username" placeholder="Введите логин" autofocus required>
    <label>Пароль</label><input type="password" name="password" placeholder="Введите пароль" required>
    <button type="submit">Войти →</button>
  </form>
</div></body></html>""")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

# ============================================================
# ОСНОВНЫЕ МАРШРУТЫ — используем report_generator
# ============================================================

@app.route("/")
@login_required
def index():
    user = current_user()
    calls, analyses = get_data(user)
    from report_generator import render_index, compute_stats
    stats = compute_stats(calls, analyses)
    html = render_index(calls, stats, analyses, datetime.now().isoformat())
    return html_response(inject_and_fix(html, user))

@app.route("/calls")
@login_required
def all_calls():
    user = current_user()
    calls, analyses = get_data(user)
    from report_generator import render_all_calls, compute_stats
    stats = compute_stats(calls, analyses)
    html = render_all_calls(calls, analyses, datetime.now().isoformat(), stats["critical_count"])
    return html_response(inject_and_fix(html, user))

@app.route("/calls/<activity_id>")
@app.route("/calls/<activity_id>.html")
@login_required
def call_detail(activity_id):
    user = current_user()
    calls, analyses = get_data(user)
    call = next((c for c in calls if c["activity_id"] == activity_id), None)
    if not call: abort(404)
    if user["role"] == "manager" and call.get("manager",{}).get("id") != user["manager_id"]:
        abort(403)
    from report_generator import render_call_page, compute_stats
    stats = compute_stats(calls, analyses)
    # Передаём PROXY_URL через env
    os.environ["PROXY_URL"] = os.environ.get("PROXY_URL", "")
    html = render_call_page(call, analyses.get(activity_id), datetime.now().isoformat(), stats["critical_count"])
    # Фиксируем относительные пути calls/ → /calls/
    return html_response(inject_and_fix(html, user))

@app.route("/rop")
@rop_required
def rop_report():
    user = current_user()
    calls, analyses = get_data(user)
    from report_generator import render_rop_report, compute_stats
    stats = compute_stats(calls, analyses)
    html = render_rop_report(calls, stats, analyses, datetime.now().isoformat())
    return html_response(inject_and_fix(html, user))

@app.route("/managers")
@rop_required
def managers():
    user = current_user()
    calls, analyses = get_data(user)
    from report_generator import render_managers_list, compute_stats
    stats = compute_stats(calls, analyses)
    html = render_managers_list(stats, datetime.now().isoformat())
    return html_response(inject_and_fix(html, user))

@app.route("/managers/<int:manager_id>")
@app.route("/managers/<int:manager_id>.html")
@login_required
def manager_detail(manager_id):
    user = current_user()
    if user["role"] == "manager" and user["manager_id"] != manager_id:
        abort(403)
    all_calls_data = load_calls()
    analyses = load_analyses()
    from report_generator import render_manager_page, compute_stats
    stats = compute_stats(all_calls_data, analyses)
    manager = next((m for m in stats["managers"] if m["id"] == manager_id), None)
    if not manager: abort(404)
    html = render_manager_page(manager, analyses, datetime.now().isoformat(), stats["critical_count"])
    return html_response(inject_and_fix(html, user))

@app.route("/critical")
@login_required
def critical():
    user = current_user()
    calls, analyses = get_data(user)
    from report_generator import render_critical_page, compute_stats
    stats = compute_stats(calls, analyses)
    html = render_critical_page(calls, analyses, datetime.now().isoformat(), stats["critical_count"])
    return html_response(inject_and_fix(html, user))

@app.route("/triggers")
@login_required
def triggers():
    user = current_user()
    calls, analyses = get_data(user)
    from report_generator import render_triggers_page, compute_stats
    stats = compute_stats(calls, analyses)
    html = render_triggers_page(calls, analyses, datetime.now().isoformat(), stats["critical_count"])
    return html_response(inject_and_fix(html, user))

@app.route("/compare")
@rop_required
def compare():
    user = current_user()
    calls, analyses = get_data(user)
    from report_generator import render_compare_page, compute_stats
    stats = compute_stats(calls, analyses)
    html = render_compare_page(stats, calls, analyses, datetime.now().isoformat())
    return html_response(inject_and_fix(html, user))

@app.route("/objections")
@login_required
def objections():
    user = current_user()
    calls, analyses = get_data(user)
    from report_generator import render_objections_page, compute_stats
    stats = compute_stats(calls, analyses)
    html = render_objections_page(calls, analyses, datetime.now().isoformat(), stats["critical_count"])
    return html_response(inject_and_fix(html, user))

@app.route("/best")
@login_required
def best_calls():
    user = current_user()
    calls, analyses = get_data(user)
    from report_generator import render_best_calls_page, compute_stats
    stats = compute_stats(calls, analyses)
    html = render_best_calls_page(calls, analyses, datetime.now().isoformat(), stats["critical_count"])
    return html_response(inject_and_fix(html, user))

# ============================================================
# API
# ============================================================

@app.route("/api/correct", methods=["POST"])
@rop_required
def api_correct():
    data = request.json or {}
    aid = data.get("activity_id")
    score = data.get("score")
    comment = data.get("comment", "")
    if not aid or score is None:
        return jsonify({"error": "missing fields"}), 400
    p = DATA_DIR / "manual_corrections.json"
    corrections = {}
    if p.exists():
        try: corrections = json.loads(p.read_text(encoding="utf-8"))
        except: pass
    corrections[aid] = {"overall_score": float(score), "comment": comment}
    p.write_text(json.dumps(corrections, ensure_ascii=False, indent=2), encoding="utf-8")
    return jsonify({"ok": True})

@app.route("/audio/<activity_id>")
@login_required
def serve_audio(activity_id):
    """Проксирует аудио с Bitrix через наш сервер.
    Получает СВЕЖУЮ ссылку на скачивание через disk.file.get,
    так как сохранённый в calls_data.json URL содержит временный токен и протухает."""
    import requests as _req
    from flask import Response as _Resp, stream_with_context

    calls = load_calls()
    call = next((c for c in calls if c["activity_id"] == activity_id), None)
    if not call:
        abort(404)

    user = current_user()
    if user["role"] == "manager" and call.get("manager",{}).get("id") != user["manager_id"]:
        abort(403)

    audio_meta = call.get("audio") or {}
    file_id = audio_meta.get("file_id")
    webhook = os.environ.get("BITRIX_WEBHOOK_URL", "").rstrip("/")

    audio_url = ""
    if file_id and webhook:
        try:
            meta_resp = _req.get(f"{webhook}/disk.file.get", params={"id": file_id}, timeout=15)
            meta_resp.raise_for_status()
            file_data = (meta_resp.json() or {}).get("result", {})
            audio_url = file_data.get("DOWNLOAD_URL", "")
        except Exception as e:
            app.logger.warning(f"Failed to get fresh download URL for file_id={file_id}: {e}")

    # Фоллбэк на старый сохранённый URL, если свежий получить не удалось
    if not audio_url:
        audio_url = audio_meta.get("url", "")

    if not audio_url:
        abort(404)

    try:
        r = _req.get(audio_url, stream=True, timeout=30)
        r.raise_for_status()
        ctype = r.headers.get("Content-Type", "audio/mpeg")
        if "audio" not in ctype and "octet-stream" not in ctype and "video" not in ctype:
            preview = next(r.iter_content(chunk_size=512), b"")
            app.logger.warning(f"Audio proxy got non-audio content-type={ctype} for {activity_id}: {preview[:200]}")
            abort(502)
        return _Resp(
            stream_with_context(r.iter_content(chunk_size=8192)),
            status=200,
            headers={
                "Content-Type": ctype,
                "Accept-Ranges": "bytes",
                "Cache-Control": "private, max-age=3600",
            }
        )
    except Exception:
        abort(502)

@app.route("/calls/<activity_id>/reanalyze", methods=["POST"])
@rop_required
def reanalyze_call(activity_id):
    """Повторный анализ звонка."""
    analyses = load_analyses()
    if activity_id not in analyses:
        return jsonify({"error": "Звонок не найден"}), 404
    del analyses[activity_id]
    p = DATA_DIR / "analyses.json"
    p.write_text(json.dumps(analyses, ensure_ascii=False, indent=2), encoding="utf-8")
    import subprocess, sys
    subprocess.Popen(
        [sys.executable, "src/claude_analyzer.py"],
        env={**os.environ, "REANALYZE_ID": activity_id},
        cwd=str(Path(__file__).parent)
    )
    return jsonify({"ok": True, "message": "Анализ запущен, обновите страницу через минуту"})

@app.route("/health")
def health():
    return jsonify({"status": "ok", "time": datetime.now().isoformat()})

@app.errorhandler(403)
def forbidden(e):
    return html_response("<h2 style='font-family:sans-serif;padding:40px'>403 — Нет доступа. <a href='/'>На главную</a></h2>"), 403

@app.errorhandler(404)
def not_found(e):
    return html_response("<h2 style='font-family:sans-serif;padding:40px'>404 — Не найдено. <a href='/'>На главную</a></h2>"), 404

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
