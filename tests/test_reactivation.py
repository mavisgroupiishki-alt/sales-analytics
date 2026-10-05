import sys
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from reactivation import (
    _deal_rows,
    ReactivationError,
    analyse_reactivation_calls,
    build_reactivation_queue,
    fetch_reactivation_calls,
    reactivate_to_new,
)


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
        if method == "user.get":
            user_id = int(params["ID"])
            return {"result": [{"ID": user_id, "NAME": "Менеджер", "LAST_NAME": "Тест"}]}
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

    def test_queue_counts_all_direct_calls_but_uses_only_ai_results_as_context(self):
        calls = [
            {
                "activity_id": "a1", "created": "2026-01-01T10:00:00+03:00",
                "crm": {"owner_type": "deal", "owner_id": "42"},
                "audio": {"file_id": "file-1"},
            },
            {
                "activity_id": "a2", "created": "2026-01-02T10:00:00+03:00",
                "crm": {"owner_type": "deal", "owner_id": "42"}, "audio": None,
            },
        ]
        analyses = {"a1": {"analysis": {"summary": "Подтвердил интерес", "overall_score": 8}}}

        row = build_reactivation_queue(FakeBitrix(), calls, analyses, now=datetime(2026, 10, 2, tzinfo=timezone.utc))["recommendations"][0]

        self.assertEqual(row["context"]["callStats"], {
            "total": 2,
            "analyzed": 1,
            "unavailable": 1,
            "pending": 0,
            "notScored": 0,
            "issues": [{
                "activityId": "a2",
                "occurredAt": "2026-01-02T10:00:00+03:00",
                "reason": "В Bitrix нет доступной записи.",
            }],
        })
        self.assertEqual([item["activityId"] for item in row["context"]["calls"]], ["a1"])

    def test_fetches_only_calls_directly_attached_to_reactivation_deal(self):
        client = FakeBitrix()
        client.call_all = lambda method, params: [
            {
                "ID": "a1", "OWNER_TYPE_ID": 2, "OWNER_ID": "42", "COMPLETED": "Y", "TYPE_ID": 2,
                "CREATED": "2026-01-01T10:00:00+03:00", "AUTHOR_ID": "10", "DIRECTION": 2,
                "FILES": [{"id": "file-1"}], "COMMUNICATIONS": [],
            },
            {
                "ID": "a2", "OWNER_TYPE_ID": 3, "OWNER_ID": "42", "COMPLETED": "Y", "TYPE_ID": 2,
                "CREATED": "2026-01-01T11:00:00+03:00", "AUTHOR_ID": "10", "FILES": [], "COMMUNICATIONS": [],
            },
        ] if method == "crm.activity.list" else []

        calls = fetch_reactivation_calls(client, [{"ID": "42"}])

        self.assertEqual([call["activity_id"] for call in calls], ["a1"])
        self.assertEqual(calls[0]["crm"], {
            "owner_type": "deal", "owner_id": "42", "stage_name": "", "stage_id": "",
            "has_next_activity": False, "next_activity_date": "",
        })

    def test_does_not_silently_cap_open_reactivation_deals(self):
        client = FakeBitrix()
        client.call_all = lambda method, params: [
            {"ID": str(index), "TITLE": f"Сделка {index}"} for index in range(301)
        ] if method == "crm.deal.list" else []

        self.assertEqual(len(_deal_rows(client, 20)), 301)

    def test_history_fetch_fails_instead_of_claiming_a_partial_history(self):
        client = FakeBitrix()
        def fail_activities(method, params):
            if method == "crm.activity.list":
                raise RuntimeError("bitrix unavailable")
            return []
        client.call_all = fail_activities

        with self.assertRaises(ReactivationError):
            fetch_reactivation_calls(client, [{"ID": "42"}])

    def test_worker_does_not_reanalyse_a_saved_direct_call(self):
        direct_call = {
            "activity_id": "a1",
            "created": "2026-01-01T10:00:00+03:00",
            "duration_sec": 60,
            "crm": {"owner_type": "deal", "owner_id": "42"},
            "audio": {"file_id": "file-1"},
        }
        analyses = {}

        class Repository:
            def load_snapshot(self):
                return [], analyses

            def close(self):
                return None

        calls_to_model = []
        with tempfile.TemporaryDirectory() as directory:
            audio_path = Path(directory) / "call.ogg"

            def download(*_args):
                audio_path.write_bytes(b"audio")
                return audio_path

            def analyse(*_args, **_kwargs):
                calls_to_model.append(True)
                return {"summary": "Клиент подтвердил интерес", "overall_score": 8}

            with patch.dict(os.environ, {"JARVIS_DATABASE_URL": "test"}, clear=False), \
                 patch("reactivation.find_reactivation_category", return_value=(20, "Реанимация")), \
                 patch("reactivation._deal_rows", return_value=[{"ID": "42"}]), \
                 patch("reactivation.fetch_reactivation_calls", return_value=[direct_call]), \
                 patch("jarvis_store.JarvisRepository.connect", return_value=Repository()), \
                 patch("bitrix.mirror_snapshot_to_jarvis"), \
                 patch("bitrix.download_audio", side_effect=download), \
                 patch("claude_analyzer.transcribe_audio", return_value={"text": "Клиент подтвердил интерес и готов обсудить предложение."}), \
                 patch("claude_analyzer.analyze_transcript", side_effect=analyse), \
                 patch("claude_analyzer.build_deal_context", return_value=[]), \
                 patch("claude_analyzer.load_manual_corrections", return_value={}), \
                 patch("claude_analyzer.load_scripts", return_value={}), \
                 patch("claude_analyzer.mirror_analyses_to_jarvis"):
                first = analyse_reactivation_calls(FakeBitrix(), Path(directory))
                second = analyse_reactivation_calls(FakeBitrix(), Path(directory))

        self.assertEqual(first["analyzed"], 1)
        self.assertEqual(second["alreadyAnalyzed"], 1)
        self.assertEqual(calls_to_model, [True])


if __name__ == "__main__":
    unittest.main()
