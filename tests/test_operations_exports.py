import os
import hashlib
import hmac
import time
import unittest
from datetime import date
from unittest.mock import patch

import app as jarvis_app


class OperationsExportsTests(unittest.TestCase):
    def setUp(self):
        jarvis_app.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        self.client = jarvis_app.app.test_client()
        self.environment = patch.dict(os.environ, {"OPERATIONS_DASHBOARD_TOKEN": "shared-secret"}, clear=False)
        self.environment.start()

    def tearDown(self):
        self.environment.stop()

    def test_exports_reject_missing_token(self):
        self.assertEqual(self.client.get("/api/integrations/operations/sales-calls").status_code, 401)
        self.assertEqual(self.client.get("/api/integrations/operations/crm-audit").status_code, 401)
        self.assertEqual(self.client.get("/api/integrations/operations/reactivation-recommendations").status_code, 401)

    @patch.object(jarvis_app, "_operations_reactivation_payload")
    def test_reactivation_export_keeps_explainable_queue_behind_token(self, payload):
        payload.return_value = {"ok": True, "summary": {"recommended": 1}, "recommendations": [{"dealId": "42"}]}

        response = self.client.get(
            "/api/integrations/operations/reactivation-recommendations",
            headers={"Authorization": "Bearer shared-secret"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["recommendations"][0]["dealId"], "42")

    @patch.object(jarvis_app, "_reactivate_recommendation")
    def test_reactivation_action_requires_token_and_passes_explicit_actor(self, action):
        action.return_value = {"ok": True, "dealId": "42", "targetStage": "Новая"}

        response = self.client.post(
            "/api/integrations/operations/reactivation-recommendations/42/reactivate",
            headers={"Authorization": "Bearer shared-secret"},
            json={"actor": "dashboard-full-access"},
        )

        self.assertEqual(response.status_code, 200)
        action.assert_called_once_with("42", actor="dashboard-full-access")

    def test_dashboard_embed_rejects_unsigned_or_expired_requests(self):
        self.assertEqual(self.client.get("/dashboard-embed").status_code, 403)
        old = int(time.time()) - 61
        stale_signature = hmac.new(
            b"shared-secret", f"mavis-dashboard-embed:{old}".encode(), hashlib.sha256
        ).hexdigest()
        self.assertEqual(self.client.get("/dashboard-embed", query_string={"ts": old, "sig": stale_signature}).status_code, 403)

    def test_dashboard_embed_creates_read_only_session_only_for_valid_short_lived_signature(self):
        issued_at = int(time.time())
        signature = hmac.new(
            b"shared-secret", f"mavis-dashboard-embed:{issued_at}".encode(), hashlib.sha256
        ).hexdigest()

        response = self.client.get("/dashboard-embed", query_string={"ts": issued_at, "sig": signature})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/")
        with self.client.session_transaction() as session:
            self.assertEqual(session["username"], "operations-dashboard")
            self.assertEqual(session["role"], "dashboard")
        # The signed iframe is the ROP workspace inside Operations. It may
        # access its work tabs, while unsigned callers still cannot mint it.
        self.assertEqual(self.client.get("/rop").status_code, 200)
        self.assertEqual(self.client.get("/scripts").status_code, 200)

    @patch.object(jarvis_app, "get_data")
    def test_sales_calls_export_returns_source_backed_summary(self, get_data):
        get_data.return_value = (
            [{"activity_id": "42", "created": f"{date.today().isoformat()}T10:00:00+03:00", "duration_sec": 90, "direction": "outgoing", "manager": {"name": "Роман"}, "client": {"company": "Мавис"}, "crm": {"stage_name": "КП"}}],
            {"42": {"analysis": {"overall_score": 8.5, "review_status": "normal", "flags": {}}}},
        )

        response = self.client.get(
            "/api/integrations/operations/sales-calls",
            headers={"Authorization": "Bearer shared-secret"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["summary"]["calls"], 1)
        self.assertEqual(payload["calls"][0]["activityId"], "42")
        self.assertEqual(payload["calls"][0]["score"], 8.5)
        self.assertNotIn("client", payload["calls"][0])

    def test_audit_export_exposes_no_data_until_the_source_is_configured(self):
        response = self.client.get(
            "/api/integrations/operations/crm-audit",
            headers={"Authorization": "Bearer shared-secret"},
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["status"], "not_configured")

    @patch.object(jarvis_app, "_start_call_reanalysis", return_value=True)
    @patch.object(jarvis_app, "persist_call_review")
    @patch.object(jarvis_app, "get_data")
    def test_rop_can_confirm_call_type_with_reason_and_request_reanalysis(self, get_data, persist_review, start_reanalysis):
        get_data.return_value = ([{"activity_id": "42", "manager": {"name": "Роман"}}], {})
        persist_review.return_value = {"call_type_key": "payment_push"}
        with self.client.session_transaction() as session:
            session.update({"username": "rop", "role": "rop", "name": "РОП"})

        response = self.client.post(
            "/calls/42/review",
            json={"call_type_key": "payment_push", "reason": "Это дожим после КП", "reanalyze": True},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["reanalysis"], "started")
        persist_review.assert_called_once()
        start_reanalysis.assert_called_once_with("42")

    @patch.object(jarvis_app, "persist_call_manual_review")
    @patch.object(jarvis_app, "get_data")
    def test_rop_can_mark_a_call_as_manually_reviewed_without_changing_its_type(self, get_data, persist_manual_review):
        get_data.return_value = ([{"activity_id": "42", "manager": {"name": "Роман"}}], {})
        persist_manual_review.return_value = {"reviewed": True, "reviewer_name": "РОП"}
        with self.client.session_transaction() as session:
            session.update({"username": "rop", "role": "rop", "name": "РОП"})

        response = self.client.post("/calls/42/manual-review", json={"reviewed": True})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["review"]["reviewed"])
        persist_manual_review.assert_called_once_with("42", True, "РОП")

    @patch("subprocess.Popen")
    @patch.object(jarvis_app, "load_analyses")
    def test_single_call_reanalysis_forces_a_new_persisted_analysis_version(self, load_analyses, popen):
        load_analyses.return_value = {"42": {"analysis": {"overall_score": 4}}}
        with patch.dict(os.environ, {"JARVIS_DATABASE_URL": "postgres://private"}, clear=False):
            self.assertTrue(jarvis_app._start_call_reanalysis("42"))

        self.assertEqual(popen.call_args.kwargs["env"]["REANALYZE_ID"], "42")
        self.assertEqual(popen.call_args.kwargs["env"]["JARVIS_FORCE_ANALYSIS_VERSION"], "1")

    @patch.object(jarvis_app, "build_crm_audit_snapshot")
    def test_audit_export_returns_existing_table_shape(self, build_snapshot):
        build_snapshot.return_value = {
            "generatedAt": "2026-09-10T09:00:00+00:00",
            "summary": {"activeDeals": 12, "missingSource": 3},
            "funnelBreakdown": [{"name": "1. Продажи", "activeDeals": 4}],
            "details": [{
                "observedOn": "2026-09-10",
                "issue": "Нет источника",
                "priority": "Высокий",
                "funnel": "1. Продажи",
                "entityType": "Сделка",
                "entityId": "42",
                "url": "https://portal.example/crm/deal/details/42/",
            }],
        }

        with patch.dict(os.environ, {"BITRIX_WEBHOOK_URL": "https://portal.example/rest/1/token/"}, clear=False):
            response = self.client.get(
                "/api/integrations/operations/crm-audit",
                headers={"Authorization": "Bearer shared-secret"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["summary"]["activeDeals"], 12)
        self.assertEqual(payload["funnelBreakdown"][0]["name"], "1. Продажи")
        self.assertEqual(payload["details"][0]["entityId"], "42")


if __name__ == "__main__":
    unittest.main()
