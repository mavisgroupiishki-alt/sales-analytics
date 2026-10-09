import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:
    from jarvis_live_worker import LivePipeline, run_reactivation_schedule, run_sales_schedule  # noqa: E402
except ModuleNotFoundError as exc:
    if exc.name == "flask":
        raise unittest.SkipTest("Flask is installed by requirements.txt in the worker image")
    raise


class LiveWorkerDateWindowTests(unittest.TestCase):
    def test_sales_sync_waits_an_interval_so_an_operator_can_start_reanalysis_first(self):
        class Pipeline:
            def __init__(self):
                self.starts = 0

            def start(self):
                self.starts += 1
                return True

        class StopImmediately:
            def __init__(self):
                self.waits = []

            def wait(self, seconds):
                self.waits.append(seconds)
                return True

        pipeline = Pipeline()
        stopper = StopImmediately()

        run_sales_schedule(pipeline, 300, stopper)

        self.assertEqual(stopper.waits, [300])
        self.assertEqual(pipeline.starts, 0)

    def test_reactivation_waits_an_interval_before_claiming_the_sales_worker(self):
        class Pipeline:
            def __init__(self):
                self.starts = []

            def start(self, **kwargs):
                self.starts.append(kwargs)
                return True

        class StopImmediately:
            def __init__(self):
                self.waits = []

            def wait(self, seconds):
                self.waits.append(seconds)
                return True

        pipeline = Pipeline()
        stopper = StopImmediately()

        run_reactivation_schedule(pipeline, 3600, stopper)

        self.assertEqual(stopper.waits, [3600])
        self.assertEqual(pipeline.starts, [])

    def test_prepare_runtime_removes_stale_audio_only(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime_dir = Path(directory)
            audio_dir = runtime_dir / "audio_temp"
            audio_dir.mkdir()
            (audio_dir / "stale.mp3").write_bytes(b"stale")
            (runtime_dir / "analyses.json").write_text("{}", encoding="utf-8")

            pipeline = LivePipeline(runtime_dir)
            pipeline._prepare_runtime()

            self.assertEqual(list(audio_dir.iterdir()), [])
            self.assertTrue((runtime_dir / "analyses.json").exists())

    def test_historical_reanalysis_is_bounded_to_one_calendar_day(self):
        keys = ("DATE_FROM", "DATE_TO", "REANALYZE_DATE", "DOWNLOAD_AUDIO_COUNT", "JARVIS_TRANSCRIPT_ONLY_REANALYSIS")
        previous = {key: os.environ.get(key) for key in keys}
        try:
            with tempfile.TemporaryDirectory() as directory:
                pipeline = LivePipeline(Path(directory))
                with pipeline._runtime_environment(True, "2026-09-08"):
                    self.assertEqual(os.environ["DATE_FROM"], "2026-09-08")
                    self.assertEqual(os.environ["DATE_TO"], "2026-09-08")
                    self.assertEqual(os.environ["REANALYZE_DATE"], "2026-09-08")
                    self.assertEqual(os.environ["DOWNLOAD_AUDIO_COUNT"], "0")
                    self.assertEqual(os.environ["JARVIS_TRANSCRIPT_ONLY_REANALYSIS"], "1")
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_historical_reanalysis_is_queued_when_another_run_holds_the_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            pipeline = LivePipeline(Path(directory))
            pipeline.lock.acquire()
            try:
                status = pipeline.request_reanalysis("2026-10-07")
            finally:
                pipeline.lock.release()

        self.assertEqual(status, "queued")
        self.assertEqual(
            pipeline._pending_reanalysis,
            {"reanalyze_today": True, "reanalysis_date": "2026-10-07"},
        )


if __name__ == "__main__":
    unittest.main()
