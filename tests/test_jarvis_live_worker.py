import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:
    from jarvis_live_worker import autosync_interval_seconds, create_app, worker_port  # noqa: E402
except ModuleNotFoundError as exc:  # local code-only test environments
    if exc.name == "flask":
        raise unittest.SkipTest("Flask is installed by requirements.txt in the worker image")
    raise


class _Pipeline:
    def __init__(self):
        self.state = {"status": "idle", "last_error": None}
        self.modes = []

    def start(self, *, reanalyze_today=False, reanalysis_date=None):
        self.modes.append((reanalyze_today, reanalysis_date))
        return True


class LiveWorkerTests(unittest.TestCase):
    def test_render_worker_uses_its_own_port(self):
        old_port = os.environ.get("PORT")
        old_worker_port = os.environ.get("JARVIS_WORKER_PORT")
        os.environ.update({"PORT": "10000", "JARVIS_WORKER_PORT": "8080"})
        try:
            self.assertEqual(worker_port(), 8080)
        finally:
            if old_port is None:
                os.environ.pop("PORT", None)
            else:
                os.environ["PORT"] = old_port
            if old_worker_port is None:
                os.environ.pop("JARVIS_WORKER_PORT", None)
            else:
                os.environ["JARVIS_WORKER_PORT"] = old_worker_port

    def test_autosync_is_disabled_when_interval_is_invalid(self):
        old_interval = os.environ.get("JARVIS_AUTOSYNC_SECONDS")
        os.environ["JARVIS_AUTOSYNC_SECONDS"] = "not-a-number"
        try:
            self.assertEqual(autosync_interval_seconds(), 0)
        finally:
            if old_interval is None:
                os.environ.pop("JARVIS_AUTOSYNC_SECONDS", None)
            else:
                os.environ["JARVIS_AUTOSYNC_SECONDS"] = old_interval

    def setUp(self):
        self.previous_secret = os.environ.get("JARVIS_SYNC_SECRET")
        os.environ["JARVIS_SYNC_SECRET"] = "test-private-secret"
        self.pipeline = _Pipeline()
        self.client = create_app(self.pipeline).test_client()

    def tearDown(self):
        if self.previous_secret is None:
            os.environ.pop("JARVIS_SYNC_SECRET", None)
        else:
            os.environ["JARVIS_SYNC_SECRET"] = self.previous_secret

    def test_sync_rejects_request_without_private_header(self):
        response = self.client.post("/internal/sync", json={})

        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.pipeline.modes, [])

    def test_today_reanalysis_is_explicit(self):
        response = self.client.post(
            "/internal/sync",
            json={"mode": "reanalyze_today"},
            headers={"x-jarvis-sync-secret": "test-private-secret"},
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(self.pipeline.modes, [(True, None)])

    def test_historical_reanalysis_requires_a_valid_explicit_date(self):
        response = self.client.post(
            "/internal/sync", json={"mode": "reanalyze_day", "date": "2026-09-07"},
            headers={"x-jarvis-sync-secret": "test-private-secret"},
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(self.pipeline.modes, [(True, "2026-09-07")])
