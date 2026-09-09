import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:
    from jarvis_live_worker import LivePipeline  # noqa: E402
except ModuleNotFoundError as exc:
    if exc.name == "flask":
        raise unittest.SkipTest("Flask is installed by requirements.txt in the worker image")
    raise


class LiveWorkerDateWindowTests(unittest.TestCase):
    def test_historical_reanalysis_is_bounded_to_one_calendar_day(self):
        keys = ("DATE_FROM", "DATE_TO", "REANALYZE_DATE")
        previous = {key: os.environ.get(key) for key in keys}
        try:
            with tempfile.TemporaryDirectory() as directory:
                pipeline = LivePipeline(Path(directory))
                with pipeline._runtime_environment(True, "2026-09-08"):
                    self.assertEqual(os.environ["DATE_FROM"], "2026-09-08")
                    self.assertEqual(os.environ["DATE_TO"], "2026-09-08")
                    self.assertEqual(os.environ["REANALYZE_DATE"], "2026-09-08")
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


if __name__ == "__main__":
    unittest.main()
