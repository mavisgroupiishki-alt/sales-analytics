import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import app as dashboard_app  # noqa: E402
from jarvis_dashboard import health_dashboard_model, render_crm_health  # noqa: E402


class CrmHealthDashboardTests(unittest.TestCase):
    def setUp(self):
        dashboard_app.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        self.client = dashboard_app.app.test_client()

    def _login_as(self, role):
        with self.client.session_transaction() as session:
            session.update({"username": role, "role": role, "name": "Тестовый пользователь"})

    def test_model_counts_zones_and_explainable_risks(self):
        records = [
            {"deal_id": "1", "zone": "red", "score": 20, "responsible_id": "2100", "issue_codes": ["missing_next_activity"], "data_quality": "incomplete"},
            {"deal_id": "2", "zone": "green", "score": 100, "responsible_id": "2100", "issue_codes": [], "data_quality": "complete"},
        ]

        model = health_dashboard_model(records, {"status": "succeeded"})

        self.assertEqual((model["red"], model["green"], model["incomplete"]), (1, 1, 1))
        self.assertEqual(model["issues"][0], ("missing_next_activity", 1))

    def test_view_escapes_untrusted_stage_and_exposes_filter_url(self):
        html = render_crm_health(
            [{"deal_id": "1", "zone": "red", "score": 20, "stage_id": "<script>", "responsible_id": "2100", "issue_codes": ["missing_next_activity"], "data_quality": "incomplete", "rule_version": "crm_health_v0"}],
            {"status": "succeeded", "happened_at": "2026-09-08T12:00:00+00:00"},
            {"role": "rop", "name": "РОП"},
            {"zone": "red", "responsible_id": "2100"},
            crm_portal_url="https://portal.example",
        )

        self.assertIn("Здоровье CRM", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn('name="zone"', html)
        self.assertIn('href="/crm-health"', html)
        self.assertIn('href="https://portal.example/crm/deal/details/1/"', html)

    def test_health_route_is_limited_to_rop_or_director(self):
        self._login_as("manager")
        self.assertEqual(self.client.get("/crm-health").status_code, 403)

        self._login_as("rop")
        self.assertEqual(self.client.get("/crm-health?zone=red").status_code, 200)
        self.assertEqual(self.client.get("/crm-health?zone=unexpected").status_code, 400)


if __name__ == "__main__":
    unittest.main()
