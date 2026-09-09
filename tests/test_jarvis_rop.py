import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis_rop import filter_calls, rop_model  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
