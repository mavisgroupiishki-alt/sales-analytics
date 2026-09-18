# Manager Daily Reports Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the ROP open a report for today or yesterday that groups analyzed sales calls and the concrete recommended actions by manager.

**Architecture:** Build the report from the same in-memory calls and immutable analysis snapshots already used by the Jarvis dashboard. The report is a read-only Flask route and does not send messages, change Bitrix, or re-run AI analysis.

**Tech Stack:** Python 3, Flask, existing `jarvis_dashboard` HTML renderer, `unittest`.

## Global Constraints

- Show only the selected period: today or yesterday from the existing period selector.
- Use `recommended_action` with `recommendation` as the legacy fallback.
- Do not include excluded/service/non-sales calls in manager action lists.
- Keep every recommendation linked to its existing call card and transcript.
- Do not enable manager notifications or alter CRM data.

---

### Task 1: Build the daily-report view model and renderer

**Files:**
- Modify: `src/jarvis_dashboard.py`
- Test: `tests/test_jarvis_dashboard.py`

**Interfaces:**
- Consumes: `calls: list[dict]`, `analyses: dict[str, dict]`, and existing `triage_for`.
- Produces: `daily_reports_model(calls, analyses) -> list[dict]` and `render_daily_reports(calls, analyses, user, period) -> str`.

- [ ] **Step 1: Write the failing test**

```python
html = render_daily_reports(calls, analyses, {"role": "rop", "name": "РОП"}, period="yesterday")
assert "Роман Авсеенко" in html
assert "Перезвонить и предложить СПК" in html
assert "/calls/42" in html
assert "Служебный" not in html
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest tests.test_jarvis_dashboard.DashboardModelTests.test_daily_reports_group_actions_and_exclude_service_calls`

Expected: FAIL because `render_daily_reports` is not defined.

- [ ] **Step 3: Write the minimal implementation**

```python
def daily_reports_model(calls, analyses):
    # retain one actionable analyzed call per report row, grouped by manager
    ...

def render_daily_reports(calls, analyses, user, period="today"):
    # show the existing period switch and direct /calls/<activity_id> links
    ...
```

- [ ] **Step 4: Run the focused test to verify it passes**

Run: `python3 -m unittest tests.test_jarvis_dashboard.DashboardModelTests.test_daily_reports_group_actions_and_exclude_service_calls`

Expected: PASS.

### Task 2: Expose the report to ROP and director users

**Files:**
- Modify: `app.py`
- Test: `tests/test_app_sales_scope.py`

**Interfaces:**
- Consumes: `get_data`, `filter_calls`, `render_daily_reports`.
- Produces: `GET /daily-reports?period=today|yesterday`, protected by `@rop_required`.

- [ ] **Step 1: Write the failing route assertion**

```python
response = client.get("/daily-reports?period=yesterday")
assert response.status_code == 200
assert "Отчёты менеджеров" in response.get_data(as_text=True)
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `python3 -m unittest tests.test_app_sales_scope`

Expected: FAIL with HTTP 404 for `/daily-reports`.

- [ ] **Step 3: Add the protected route and navigation link**

```python
@app.route("/daily-reports")
@rop_required
def daily_reports():
    period = requested_period()
    calls = filter_calls(calls, analyses, {"period": period})
    return html_response(render_daily_reports(calls, analyses, current_user(), period=period))
```

- [ ] **Step 4: Run the route test to verify it passes**

Run: `python3 -m unittest tests.test_app_sales_scope`

Expected: PASS.

### Task 3: Verify and publish the bounded change

**Files:**
- Modify: `src/jarvis_dashboard.py`, `app.py`, `tests/test_jarvis_dashboard.py`

- [ ] **Step 1: Run project checks**

Run: `python3 -m unittest discover -s tests && python3 -m compileall -q src app.py && git diff --check`

Expected: all tests pass, no compiler output, and no whitespace errors.

- [ ] **Step 2: Commit and publish**

```bash
git add app.py src/jarvis_dashboard.py tests/test_jarvis_dashboard.py docs/superpowers/plans/2026-09-18-manager-daily-reports.md
git commit -m "Add daily manager call reports"
git push origin main
```

**Success coverage:** today/yesterday selector, per-manager grouping, action text, direct call links, and exclusion of non-sales/service calls are covered by the renderer test; all existing tests retain regression coverage.
