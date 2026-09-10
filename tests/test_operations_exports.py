import os
import unittest
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

    @patch.object(jarvis_app, "get_data")
    def test_sales_calls_export_returns_source_backed_summary(self, get_data):
        get_data.return_value = (
            [{"activity_id": "42", "created": "2026-09-09T10:00:00+03:00", "duration_sec": 90, "direction": "outgoing", "manager": {"name": "Роман"}, "client": {"company": "Мавис"}, "crm": {"stage_name": "КП"}}],
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
