import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from report_generator import compute_stats  # noqa: E402


class ReportGeneratorStatsTests(unittest.TestCase):
    def test_critical_count_does_not_include_historical_analysis_outside_calls(self):
        calls = [
            {
                "activity_id": "current-critical",
                "direction": "incoming",
                "manager": {"id": 1, "name": "Менеджер"},
            },
            {
                "activity_id": "current-normal",
                "direction": "outgoing",
                "manager": {"id": 1, "name": "Менеджер"},
            },
        ]
        analyses = {
            "current-critical": {"analysis": {"is_critical": True}},
            "current-normal": {"analysis": {"is_critical": False}},
            "historical-critical": {"analysis": {"is_critical": True}},
        }

        stats = compute_stats(calls, analyses)

        self.assertEqual(stats["critical_count"], 1)


if __name__ == "__main__":
    unittest.main()
