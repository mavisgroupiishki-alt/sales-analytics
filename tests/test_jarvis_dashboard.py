import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis_dashboard import dashboard_model  # noqa: E402


class DashboardModelTests(unittest.TestCase):
    def test_legacy_unproven_critical_is_review_not_urgent(self):
        calls = [{"activity_id": "1", "manager": {"id": 1, "name": "Анна"}}]
        analyses = {"1": {"analysis": {"overall_score": 3, "flags": {"critical": True}}}}

        model = dashboard_model(calls, analyses)

        self.assertEqual(len(model["critical"]), 0)
        self.assertEqual(len(model["review"]), 1)

    def test_evidenced_allowed_rule_is_urgent(self):
        calls = [{"activity_id": "1", "manager": {"id": 1, "name": "Анна"}}]
        analyses = {"1": {"analysis": {"flags": {
            "critical": True,
            "critical_rule_id": "confirmed_rudeness",
            "critical_evidence": {"time": "00:12", "quote": "Больше мне не звоните, пожалуйста"},
        }}}}

        model = dashboard_model(calls, analyses)

        self.assertEqual(len(model["critical"]), 1)
        self.assertEqual(len(model["review"]), 0)


if __name__ == "__main__":
    unittest.main()
