"""
Flask веб-приложение для Sales Analytics с авторизацией.
- РОП видит всё
- Менеджер видит только свои звонки
- Данные читаются из analyses.json и calls_data.json (обновляются GitHub Actions)
"""

import json
import os
import hashlib
from pathlib import Path
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, abort, jsonify

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me-in-production")

# ============================================================
# ПОЛЬЗОВАТЕЛИ
# Хранятся в переменных окружения Render
# Формат: USERS_JSON = [{"username":"rop","password_hash":"...","role":"rop","manager_id":null},...]
# ============================================================

def get_users():
    raw = os.environ.get("USERS_JSON", "")
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            pass
    # Дефолтные пользователи если USERS_JSON не задан
    return [
        {
            "username": "rop",
            "password_hash": hash_password(os.environ.get("ROP_PASSWORD", "rop123")),
            "role": "rop",
            "name": "Руководитель",
            "manager_id": None,
        },
        {
            "username": "roman",
            "password_hash": hash_password(os.environ.get("ROMAN_PASSWORD", "roman123")),
            "role": "manager",
            "name": "Роман Авсеенко",
            "manager_id": 1286,
        },
        {
            "username": "irina",
            "password_hash": hash_password(os.environ.get("IRINA_PASSWORD", "irina123")),
            "role": "manager",
            "name": "Ирина Богомольцева",
            "manager_id": 2100,
        },
    ]


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def find_user(username: str, password: str):
    ph = hash_password(password)
    for u in get_users():
        if u["username"] == username and u["password_hash"] == ph:
            return u
    return None


# ============================================================
# ДЕКОРАТОРЫ АВТОРИЗАЦИИ
# ============================================================

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
        if "username" not in session:
            return redirect(url_for("login"))
        if session.get("role") != "rop":
            abort(403)
        return f(*args, **kwargs)
    return decorated


def current_user():
    return {
        "username": session.get("username", ""),
        "role": session.get("role", ""),
        "name": session.get("name", ""),
        "manager_id": session.get("manager_id"),
    }


# ============================================================
# ЗАГРУЗКА ДАННЫХ
# ============================================================

DATA_DIR = Path(os.environ.get("DATA_DIR", "."))


def load_calls():
    p = DATA_DIR / "calls_data.json"
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


def load_analyses():
    p = DATA_DIR / "analyses.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def load_corrections():
    p = DATA_DIR / "manual_corrections.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def filter_calls_for_user(calls, user):
    """Менеджер видит только свои звонки."""
    if user["role"] == "rop":
        return calls
    mid = user["manager_id"]
    if mid is None:
        return []
    return [c for c in calls if c.get("manager", {}).get("id") == mid]


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
    """Загружает и фильтрует данные для текущего пользователя."""
    calls = load_calls()
    analyses = load_analyses()
    corrections = load_corrections()
    analyses = apply_corrections(analyses, corrections)

    if user:
        calls = filter_calls_for_user(calls, user)

    return calls, analyses


def score_color(score):
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
# МАРШРУТЫ АВТОРИЗАЦИИ
# ============================================================

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = find_user(username, password)
        if user:
            session["username"] = user["username"]
            session["role"] = user["role"]
            session["name"] = user["name"]
            session["manager_id"] = user["manager_id"]
            next_url = request.args.get("next") or url_for("index")
            return redirect(next_url)
        else:
            error = "Неверный логин или пароль"
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ============================================================
# ОСНОВНЫЕ МАРШРУТЫ
# ============================================================

@app.route("/")
@login_required
def index():
    user = current_user()
    calls, analyses = get_data(user)
    from src.report_generator import compute_stats
    stats = compute_stats(calls, analyses)
    generated_at = datetime.now().isoformat()

    # Данные для JS фильтрации по периоду
    calls_json = json.dumps([{
        "id": c["activity_id"],
        "created": c.get("created", ""),
        "direction": c.get("direction", ""),
        "client": c.get("client", {}).get("name", ""),
        "manager": c.get("manager", {}).get("name", ""),
        "duration": c.get("duration_sec", 0),
        "score": analyses.get(c["activity_id"], {}).get("analysis", {}).get("overall_score"),
        "is_critical": bool(analyses.get(c["activity_id"], {}).get("analysis", {}).get("is_critical", False)),
    } for c in sorted(calls, key=lambda x: x.get("created", ""), reverse=True)], ensure_ascii=False)

    return render_template("index.html",
        user=user, stats=stats, calls=calls, analyses=analyses,
        generated_at=generated_at, calls_json=calls_json)


@app.route("/calls")
@login_required
def all_calls():
    user = current_user()
    calls, analyses = get_data(user)
    sorted_calls = sorted(calls, key=lambda x: x.get("created", ""), reverse=True)
    return render_template("all_calls.html",
        user=user, calls=sorted_calls, analyses=analyses)


@app.route("/calls/<activity_id>")
@login_required
def call_detail(activity_id):
    user = current_user()
    calls, analyses = get_data(user)

    call = next((c for c in calls if c["activity_id"] == activity_id), None)
    if not call:
        abort(404)

    # Менеджер не может смотреть чужие звонки
    if user["role"] == "manager":
        if call.get("manager", {}).get("id") != user["manager_id"]:
            abort(403)

    analysis_data = analyses.get(activity_id)
    proxy_url = os.environ.get("PROXY_URL", "")

    return render_template("call_detail.html",
        user=user, call=call, analysis_data=analysis_data, proxy_url=proxy_url)


@app.route("/managers")
@rop_required
def managers():
    user = current_user()
    calls, analyses = get_data(user)
    from src.report_generator import compute_stats
    stats = compute_stats(calls, analyses)
    return render_template("managers.html", user=user, stats=stats)


@app.route("/managers/<int:manager_id>")
@login_required
def manager_detail(manager_id):
    user = current_user()
    # Менеджер может смотреть только свою страницу
    if user["role"] == "manager" and user["manager_id"] != manager_id:
        abort(403)

    calls, analyses = get_data({"role": "rop"})  # все данные для расчёта статистики
    from src.report_generator import compute_stats
    stats = compute_stats(calls, analyses)
    manager = next((m for m in stats["managers"] if m["id"] == manager_id), None)
    if not manager:
        abort(404)
    return render_template("manager_detail.html", user=user, manager=manager, analyses=analyses)


@app.route("/rop")
@rop_required
def rop_report():
    user = current_user()
    calls, analyses = get_data(user)
    from src.report_generator import compute_stats, get_objections_stats
    stats = compute_stats(calls, analyses)
    objections = get_objections_stats(analyses)
    return render_template("rop_report.html", user=user, stats=stats,
        analyses=analyses, calls=calls, objections=objections)


@app.route("/compare")
@rop_required
def compare():
    user = current_user()
    calls, analyses = get_data(user)
    from src.report_generator import compute_stats
    stats = compute_stats(calls, analyses)
    return render_template("compare.html", user=user, stats=stats, calls=calls, analyses=analyses)


@app.route("/critical")
@login_required
def critical():
    user = current_user()
    calls, analyses = get_data(user)
    critical_calls = [
        (c, analyses[c["activity_id"]])
        for c in calls
        if c["activity_id"] in analyses
        and analyses[c["activity_id"]].get("analysis", {}).get("is_critical")
    ]
    critical_calls.sort(key=lambda x: x[0].get("created", ""), reverse=True)
    return render_template("critical.html", user=user, critical_calls=critical_calls)


@app.route("/triggers")
@login_required
def triggers():
    user = current_user()
    calls, analyses = get_data(user)
    return render_template("triggers.html", user=user, calls=calls, analyses=analyses)


@app.route("/objections")
@login_required
def objections():
    user = current_user()
    calls, analyses = get_data(user)
    from src.report_generator import get_objections_stats
    obj_stats = get_objections_stats(analyses)
    return render_template("objections.html", user=user, calls=calls,
        analyses=analyses, objections=obj_stats)


@app.route("/best")
@login_required
def best_calls():
    user = current_user()
    calls, analyses = get_data(user)
    from src.report_generator import get_best_calls
    best = get_best_calls(calls, analyses, n=20)
    return render_template("best_calls.html", user=user, best=best)


# ============================================================
# API — ручная правка оценки
# ============================================================

@app.route("/api/correct", methods=["POST"])
@rop_required
def api_correct():
    data = request.json or {}
    activity_id = data.get("activity_id")
    score = data.get("score")
    comment = data.get("comment", "")

    if not activity_id or score is None:
        return jsonify({"error": "missing fields"}), 400

    p = DATA_DIR / "manual_corrections.json"
    corrections = {}
    if p.exists():
        try:
            corrections = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass

    corrections[activity_id] = {"overall_score": float(score), "comment": comment}
    p.write_text(json.dumps(corrections, ensure_ascii=False, indent=2), encoding="utf-8")
    return jsonify({"ok": True})


# ============================================================
# HEALTH CHECK для Render + UptimeRobot
# ============================================================

@app.route("/health")
def health():
    return jsonify({"status": "ok", "time": datetime.now().isoformat()})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
