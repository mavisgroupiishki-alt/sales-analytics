import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis_rop import filter_calls, render_rop_report, rop_model  # noqa: E402


class RopModelTests(unittest.TestCase):
    def setUp(self):
        self.calls = [
            {
                "activity_id": "one",
                "created": "2026-09-08T09:10:00+03:00",
                "direction": "incoming",
                "audio": {"file_id": 1},
                "manager": {"id": 1286, "name": "Роман"},
                "crm": {"owner_id": "10", "owner_type": "deal", "stage_id": "PREPARATION", "stage_name": "PREPARATION", "has_next_activity": True},
            },
            {
                "activity_id": "two",
                "created": "2026-09-07T10:00:00+03:00",
                "direction": "outgoing",
                "audio": {"file_id": 2},
                "manager": {"id": 2100, "name": "Ирина"},
                "crm": {},
            },
        ]
        self.analyses = {"one": {"analysis": {"overall_score": 8, "flags": {}}}, "two": {"analysis": {"overall_score": 3, "flags": {"critical": True}}}}

    def test_period_filter_prevents_old_analysis_from_leaking_into_report(self):
        selected = filter_calls(self.calls, self.analyses, {"date": "2026-09-08"})
        model = rop_model(selected, self.analyses)
        self.assertEqual(model["calls"], 1)
        self.assertEqual(len(model["review"]), 0)

    def test_quick_periods_cover_today_yesterday_week_and_month(self):
        calls = [
            {"activity_id": "today", "created": "2026-09-11T10:00:00+03:00"},
            {"activity_id": "yesterday", "created": "2026-09-10T10:00:00+03:00"},
            {"activity_id": "week", "created": "2026-09-05T10:00:00+03:00"},
            {"activity_id": "month", "created": "2026-08-13T10:00:00+03:00"},
            {"activity_id": "old", "created": "2026-08-12T10:00:00+03:00"},
        ]
        expected = {
            "today": ["today"],
            "yesterday": ["yesterday"],
            "week": ["today", "yesterday", "week"],
            "month": ["today", "yesterday", "week", "month"],
        }

        for period, activity_ids in expected.items():
            with self.subTest(period=period):
                selected = filter_calls(calls, {}, {"period": period}, today=date(2026, 9, 11))
                self.assertEqual([call["activity_id"] for call in selected], activity_ids)

    def test_exact_date_takes_precedence_over_quick_period(self):
        selected = filter_calls(
            self.calls,
            self.analyses,
            {"date": "2026-09-07", "period": "today"},
            today=date(2026, 9, 11),
        )

        self.assertEqual([call["activity_id"] for call in selected], ["two"])

    def test_crm_and_next_activity_are_only_counted_when_present(self):
        model = rop_model(self.calls, self.analyses)
        self.assertEqual(model["crm_coverage"], 50)
        self.assertEqual(model["next_activity"], 1)
        self.assertEqual(len(model["critical"]), 0)
        self.assertEqual(len(model["review"]), 0)
        self.assertEqual(len(model["reanalysis"]), 2)

    def test_stage_filter_uses_human_readable_name(self):
        selected = filter_calls(self.calls, self.analyses, {"stage": "5. КП отправлено"})
        self.assertEqual([call["activity_id"] for call in selected], ["one"])

    def test_low_score_is_a_separate_rop_attention_signal(self):
        analyses = {"one": {"analysis": {
            "overall_score": 2.5,
            "overall_score_method": "applicable_rubric_v1",
            "flags": {},
        }}}

        model = rop_model([self.calls[0]], analyses)
        selected = filter_calls([self.calls[0]], analyses, {"status": "low_score"})
        html = render_rop_report([self.calls[0]], analyses, {"name": "РОП"}, {})

        self.assertEqual(len(model["critical"]), 0)
        self.assertEqual(len(model["attention"]), 1)
        self.assertEqual([call["activity_id"] for call in selected], ["one"])
        self.assertIn("Внимание РОПа", html)
        self.assertIn("0 критичных · 1 низких", html)

    def test_empty_recording_is_separate_from_pending_analysis(self):
        call = {
            "activity_id": "empty",
            "created": "2026-09-08T12:00:00+03:00",
            "direction": "outgoing",
            "audio": {"file_id": 99, "status": "empty", "error": "empty_recording", "size_bytes": 432},
            "manager": {"id": 1286, "name": "Роман"},
            "crm": {},
        }

        model = rop_model([call], {})
        selected = filter_calls([call], {}, {"status": "audio_unavailable"})
        html = render_rop_report([call], {}, {"name": "РОП"}, {"date": "2026-09-08"})

        self.assertEqual(model["available_audio"], 0)
        self.assertEqual(model["unavailable_audio"], 1)
        self.assertEqual(model["pending_analysis"], 0)
        self.assertEqual([item["activity_id"] for item in selected], ["empty"])
        self.assertIn("1 пустая запись", html)


if __name__ == "__main__":
    unittest.main()
