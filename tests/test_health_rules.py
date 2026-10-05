import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from health_rules import assess_deal_health, business_days_overdue  # noqa: E402


NOW = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)


def healthy_deal(**changes):
    deal = {
        "category_id": 0,
        "stage_id": "PREPARATION",
        "responsible_id": "2100",
        "responsible_active": True,
        "company_id": "100",
        "contact_id": "200",
        "source_id": "WEB",
        "client_type": "new",
        "product_or_service": "ISO 9001",
        "open_activities": [{"due_at": "2026-09-09T12:00:00+00:00"}],
        "last_communication_at": "2026-09-08T09:00:00+00:00",
        "moved_at": "2026-09-08T09:00:00+00:00",
    }
    deal.update(changes)
    return deal


class HealthRulesTests(unittest.TestCase):
    def test_deferred_demand_with_return_plan_is_not_penalized_for_old_contact(self):
        assessment = assess_deal_health(
            healthy_deal(
                stage_id="12",
                deferred_return_at="2026-09-20T12:00:00+00:00",
                last_communication_at="2026-08-01T09:00:00+00:00",
                moved_at="2026-08-01T09:00:00+00:00",
            ),
            now=NOW,
        )

        self.assertEqual(assessment.score, 100)
        self.assertEqual(assessment.zone, "green")

    def test_deferred_demand_accepts_a_dated_open_activity_as_return_plan(self):
        assessment = assess_deal_health(
            healthy_deal(
                stage_id="12",
                last_communication_at="2026-08-01T09:00:00+00:00",
                moved_at="2026-08-01T09:00:00+00:00",
            ),
            now=NOW,
        )

        self.assertEqual(assessment.zone, "green")
        self.assertNotIn("missing_deferred_plan", [issue.code for issue in assessment.issues])

    def test_missing_next_activity_is_explainable_yellow_risk(self):
        assessment = assess_deal_health(healthy_deal(open_activities=[]), now=NOW)

        self.assertEqual(assessment.score, 75)
        self.assertEqual(assessment.zone, "yellow")
        self.assertIn("missing_next_activity", [issue.code for issue in assessment.issues])

    def test_inactive_owner_is_hard_red_risk_without_any_write_action(self):
        deal = healthy_deal(responsible_active=False)
        assessment = assess_deal_health(deal, now=NOW)

        self.assertEqual(assessment.zone, "red")
        self.assertLessEqual(assessment.score, 49)
        self.assertEqual(deal["responsible_active"], False)

    def test_unmapped_live_stage_is_hard_red_risk(self):
        assessment = assess_deal_health(healthy_deal(stage_known=False), now=NOW)

        self.assertEqual(assessment.zone, "red")
        self.assertIn("unknown_stage", [issue.code for issue in assessment.issues])

    def test_severe_overdue_activity_is_red_without_confirmed_new_agreement(self):
        assessment = assess_deal_health(
            healthy_deal(open_activities=[{"due_at": "2026-09-02T12:00:00+00:00"}]), now=NOW
        )

        self.assertEqual(assessment.zone, "red")
        self.assertIn("activity_overdue_severe", [issue.code for issue in assessment.issues])

    def test_missing_product_is_not_penalized_before_qualification(self):
        assessment = assess_deal_health(healthy_deal(stage_id="NEW", product_or_service=""), now=NOW)

        self.assertNotIn("missing_product", [issue.code for issue in assessment.issues])

    def test_missing_product_is_penalized_after_need_collection(self):
        assessment = assess_deal_health(healthy_deal(stage_id="8", product_or_service=""), now=NOW)

        self.assertIn("missing_product", [issue.code for issue in assessment.issues])

    def test_business_day_overdue_skips_weekend(self):
        friday = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
        monday = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)

        self.assertEqual(business_days_overdue(friday, monday), 1)


if __name__ == "__main__":
    unittest.main()
