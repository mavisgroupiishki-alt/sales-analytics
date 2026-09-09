import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from incomplete_analysis_ids import find_incomplete_activity_ids  # noqa: E402


class IncompleteAnalysisIdsTests(unittest.TestCase):
    def test_selects_missing_and_out_of_range_scores_only(self):
        calls = [
            {"activity_id": "missing", "duration_sec": 60, "audio": {"file_id": "a"}},
            {"activity_id": "low", "duration_sec": 60, "audio": {"file_id": "b"}},
            {"activity_id": "ready", "duration_sec": 60, "audio": {"file_id": "c"}},
            {"activity_id": "short", "duration_sec": 20, "audio": {"file_id": "d"}},
            {"activity_id": "excluded", "duration_sec": 60, "audio": {"file_id": "e"}},
            {"activity_id": "excluded-low", "duration_sec": 60, "audio": {"file_id": "f"}},
            {"activity_id": "unknown-duration", "duration_sec": None, "audio": {"file_id": "g"}},
        ]
        analyses = {
            "low": {"analysis": {"overall_score": 0.6}},
            "ready": {"analysis": {"overall_score": 7.5}},
            "excluded": {"analysis": {"overall_score": None, "exclude_from_stats": True}},
            "excluded-low": {"analysis": {"overall_score": 0.7, "exclude_from_stats": True}},
        }

        result = find_incomplete_activity_ids(calls, analyses, {"a", "b", "c", "d", "e", "f", "g"})

        self.assertEqual(result, ["missing", "low", "excluded-low", "unknown-duration"])


if __name__ == "__main__":
    unittest.main()
