import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from persist_runtime_snapshot import normalize_analysis_for_storage  # noqa: E402


class PersistRuntimeSnapshotTests(unittest.TestCase):
    def test_normalizes_confidence_and_bounds_existing_score(self):
        normalized = normalize_analysis_for_storage(
            {"analysis_confidence": "low", "overall_score": 0.7}
        )

        self.assertEqual(normalized["analysis_confidence"], 0.35)
        self.assertEqual(normalized["overall_score"], 1.0)


if __name__ == "__main__":
    unittest.main()
