import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from reactivation import ReactivationError, build_reactivation_queue, reactivate_to_new


class FakeBitrix:
    def __init__(self, category=20):
        self.endpoint = "https://mavisgroup.bitrix24.by/rest/1/token/"
        self.category = category
        self.updated = []

    def call_all(self, method, params):
        if method == "crm.deal.list":
            return [{"ID": "42", "TITLE": "Тест", "CATEGORY_ID": self.category, "STAGE_ID": "C20:NEW", "DATE_CREATE": "2026-01-01T10:00:00+03:00", "COMMENTS": "Перезвонить после согласования", "OPPORTUNITY": "1000", "CURRENCY_ID": "BYN"}]
        if method == "crm.activity.list":
            return []
        raise AssertionError(method)

    def call(self, method, params):
        if method == "crm.dealcategory.list" and params == {"order": {"SORT": "ASC"}}:
            return {"result": [{"ID": self.category, "NAME": "Реанимация"}]}
        if method == "crm.dealcategory.stage.list":
            return {"result": [{"STATUS_ID": "NEW", "NAME": "Новая"}]}
        if method == "crm.deal.get":
            return {"result": {"ID": str(params["id"]), "CATEGORY_ID": self.category, "STAGE_ID": "C20:NEW"}}
        if method == "crm.deal.update":
            self.updated.append(params)
            return {"result": True}
        raise AssertionError((method, params))


class ReactivationTests(unittest.TestCase):
    def test_queue_recommends_old_uncontacted_deal_and_keeps_evidence(self):
        payload = build_reactivation_queue(FakeBitrix(), [], {}, now=datetime(2026, 10, 2, tzinfo=timezone.utc))

        self.assertEqual(payload["summary"]["recommended"], 1)
        row = payload["recommendations"][0]
        self.assertEqual(row["dealId"], "42")
        self.assertTrue(row["recommended"])
        self.assertTrue(row["context"]["comments"])
        self.assertIn("нет", row["reasons"][0].casefold())

    def test_recent_contact_is_excluded_from_active_queue(self):
        client = FakeBitrix()
        today = datetime(2026, 10, 2, tzinfo=timezone.utc)
        def activities(method, params):
            if method == "crm.deal.list":
                return [{"ID": "42", "TITLE": "Тест", "CATEGORY_ID": 20, "STAGE_ID": "C20:NEW"}]
            if method == "crm.activity.list":
                return [{"OWNER_ID": "42", "COMPLETED": "Y", "CREATED": (today - timedelta(days=10)).isoformat()}] if params["filter"]["COMPLETED"] == "Y" else []
        client.call_all = activities

        payload = build_reactivation_queue(client, [], {}, now=today)

        self.assertEqual(payload["summary"]["recommended"], 0)
        self.assertEqual(payload["summary"]["excluded"], 1)

    def test_explicit_action_moves_only_a_current_reactivation_deal_to_real_new_stage(self):
        client = FakeBitrix()

        result = reactivate_to_new(client, "42", actor="dashboard-full-access")

        self.assertTrue(result["ok"])
        self.assertEqual(client.updated, [{"id": 42, "fields": {"CATEGORY_ID": 0, "STAGE_ID": "NEW"}}])

    def test_explicit_action_refuses_when_deal_left_reactivation(self):
        client = FakeBitrix(category=20)
        original = client.call
        def wrong_category(method, params):
            if method == "crm.deal.get":
                return {"result": {"ID": "42", "CATEGORY_ID": 0, "STAGE_ID": "NEW"}}
            return original(method, params)
        client.call = wrong_category

        with self.assertRaises(ReactivationError):
            reactivate_to_new(client, "42", actor="dashboard-full-access")
        self.assertEqual(client.updated, [])


if __name__ == "__main__":
    unittest.main()
