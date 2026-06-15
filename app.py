"""
Flask веб-приложение для Sales Analytics с авторизацией.
Использует существующий report_generator.py для генерации HTML.
"""

import json
import os
import hashlib
import sys
from pathlib import Path
from datetime import datetime
from functools import wraps
from flask import Flask, request, redirect, url_for, session, abort, Response, jsonify

# Добавляем src/ в путь чтобы импортировать report_generator
sys.path.insert(0, str(Path(__file__).parent / "src"))

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "mavis-secret-2026")

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
    if user["role"] == "rop":
        extra_links = """
        <a href="/rop" style="color:rgba(255,255,255,.75);font-size:13px;padding:4px 2px;border-bottom:2px solid transparent;text-decoration:none;white-space:nowrap">Отчёт РОПа</a>
        <a href="/compare" style="color:rgba(255,255,255,.75);font-size:13px;padding:4px 2px;border-bottom:2px solid transparent;text-decoration:none;white-space:nowrap">Сравнение</a>
        <a href="/managers" style="color:rgba(255,255,255,.75);font-size:13px;padding:4px 2px;border-bottom:2px solid transparent;text-decoration:none;white-space:nowrap">Менеджеры</a>
        """
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
  <a href="/logout" style="color:#C9A961;text-decoration:none;font-weight:600;white-space:nowrap">Выйти →</a>
</div>
"""

def inject_auth(html, user):
    """Убирает оригинальный топбар report_generator и вставляет наш с авторизацией."""
    marker_start = '<div class="topbar">'
    marker_end = '<div class="container">'
    if marker_start in html and marker_end in html:
        idx_start = html.index(marker_start)
        idx_end = html.index(marker_end)
        # Убираем топбар полностью
        html = html[:idx_start] + html[idx_end:]
    # Также убираем sticky topbar CSS чтобы не было пустого места сверху
    html = html.replace(
        'position: sticky; top: 0; z-index: 10;',
        'position: relative;'
    )
    bar = auth_bar(user)
    if "<body>" in html:
        return html.replace("<body>", f"<body>{bar}", 1)
    return bar + html

def fix_links(html):
    """Заменяет статические HTML-пути на Flask маршруты."""
    replacements = [
        ('href="index.html"',       'href="/"'),
        ('href="../index.html"',    'href="/"'),
        ('href="rop-report.html"',  'href="/rop"'),
        ('href="../rop-report.html"','href="/rop"'),
        ('href="managers.html"',    'href="/managers"'),
        ('href="../managers.html"', 'href="/managers"'),
        ('href="all-calls.html"',   'href="/calls"'),
        ('href="../all-calls.html"','href="/calls"'),
        ('href="critical.html"',    'href="/critical"'),
        ('href="../critical.html"', 'href="/critical"'),
        ('href="triggers.html"',    'href="/triggers"'),
        ('href="../triggers.html"', 'href="/triggers"'),
        ('href="compare.html"',     'href="/compare"'),
        ('href="../compare.html"',  'href="/compare"'),
        ('href="objections.html"',  'href="/objections"'),
        ('href="../objections.html"','href="/objections"'),
        ('href="best-calls.html"',  'href="/best"'),
        ('href="../best-calls.html"','href="/best"'),
        ('href="calls/',            'href="/calls/'),
        ('href="../calls/',         'href="/calls/'),
        ('href="managers/',         'href="/managers/'),
        ('href="../managers/',      'href="/managers/'),
        ('src="../',                'src="/'),
        ('src="avatars/',           'src="/static/avatars/'),
        ('src="logo.png"',          'src="/static/logo.png"'),
        ('src="../logo.png"',       'src="/static/logo.png"'),
    ]
    for old_str, new_str in replacements:
        return html

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
