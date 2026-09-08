"""Private HTTP worker for the Jarvis live call-quality pipeline.

The worker is intended for the existing DigitalOcean Docker network only:
n8n calls it every five minutes and it never exposes Bitrix, Vibe or database
credentials to the browser.  It does not notify managers.
"""

from __future__ import annotations

import hmac
import logging
import os
import shutil
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator

from flask import Flask, jsonify, request


logger = logging.getLogger(__name__)
PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_RUNTIME_DIR = Path("/var/lib/jarvis")


class LivePipeline:
    """Runs one non-overlapping private sync at a time."""

    def __init__(self, runtime_dir: Path):
        self.runtime_dir = runtime_dir
        self.lock = threading.Lock()
        self.state: Dict[str, Any] = {
            "status": "idle",
            "started_at": None,
            "finished_at": None,
            "last_error": None,
            "mode": None,
        }

    def start(self, *, reanalyze_today: bool = False) -> bool:
        if not self.lock.acquire(blocking=False):
            return False
        thread = threading.Thread(
            target=self._run,
            kwargs={"reanalyze_today": reanalyze_today},
            daemon=True,
            name="jarvis-live-sync",
        )
        thread.start()
        return True

    def _run(self, *, reanalyze_today: bool) -> None:
        self.state.update(
            {
                "status": "running",
                "started_at": datetime.now().astimezone().isoformat(),
                "finished_at": None,
                "last_error": None,
                "mode": "reanalyze_today" if reanalyze_today else "sync_today",
            }
        )
        try:
            self._validate_environment()
            self._prepare_runtime()
            with self._runtime_environment(reanalyze_today):
                # Import here so these modules receive the controlled runtime
                # directory instead of the container source directory.
                from bitrix import main as bitrix_main
                from claude_analyzer import main as analyzer_main

                bitrix_main()
                analyzer_main()
            self.state["status"] = "succeeded"
        except Exception as exc:  # logged without request headers or env values
            self.state["status"] = "failed"
            self.state["last_error"] = type(exc).__name__
            database_url = os.environ.get("JARVIS_DATABASE_URL")
            if database_url:
                try:
                    from jarvis_store import record_sync_failure

                    record_sync_failure(database_url, "bitrix24", type(exc).__name__)
                except Exception:
                    logger.exception("Could not persist Jarvis sync failure")
            logger.exception("Jarvis live pipeline failed (%s)", type(exc).__name__)
        finally:
            self.state["finished_at"] = datetime.now().astimezone().isoformat()
            self.lock.release()

    def _validate_environment(self) -> None:
        required = ("BITRIX_WEBHOOK_URL", "VIBE_API_KEY", "JARVIS_DATABASE_URL", "JARVIS_RUBRIC_ID")
        missing = [name for name in required if not os.environ.get(name)]
        if missing:
            raise RuntimeError("Missing required live worker configuration")
        try:
            if int(os.environ["JARVIS_RUBRIC_ID"]) <= 0:
                raise ValueError
        except ValueError as exc:
            raise RuntimeError("Invalid Jarvis rubric configuration") from exc

    def _prepare_runtime(self) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        scripts = self.runtime_dir / "scripts.json"
        if not scripts.exists():
            shutil.copy2(PROJECT_DIR / "scripts.json", scripts)

    @contextmanager
    def _runtime_environment(self, reanalyze_today: bool) -> Iterator[None]:
        previous_cwd = Path.cwd()
        changed = {
            "DATE_FROM": datetime.now().date().isoformat(),
            "DAYS_BACK": None,
            "DOWNLOAD_AUDIO_COUNT": "-1",
            "NOTIFY_MANAGERS": "0",
            "REANALYZE_TODAY": "1" if reanalyze_today else None,
            "JARVIS_FORCE_ANALYSIS_VERSION": "1" if reanalyze_today else None,
        }
        before = {key: os.environ.get(key) for key in changed}
        try:
            for key, value in changed.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            os.chdir(self.runtime_dir)
            yield
        finally:
            os.chdir(previous_cwd)
            for key, value in before.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def create_app(pipeline: LivePipeline | None = None) -> Flask:
    app = Flask(__name__)
    live_pipeline = pipeline or LivePipeline(Path(os.environ.get("JARVIS_RUNTIME_DIR", DEFAULT_RUNTIME_DIR)))

    def authorized() -> bool:
        configured = os.environ.get("JARVIS_SYNC_SECRET", "")
        supplied = request.headers.get("x-jarvis-sync-secret", "")
        return bool(configured) and hmac.compare_digest(supplied, configured)

    @app.get("/health")
    def health():
        return jsonify({"service": "jarvis-live-worker", **live_pipeline.state})

    @app.post("/internal/sync")
    def sync():
        if not authorized():
            return jsonify({"error": "unauthorized"}), 401
        body = request.get_json(silent=True) or {}
        reanalyze_today = body.get("mode") == "reanalyze_today"
        if not live_pipeline.start(reanalyze_today=reanalyze_today):
            return jsonify({"status": "already_running"}), 409
        return jsonify({"status": "accepted", "mode": "reanalyze_today" if reanalyze_today else "sync_today"}), 202

    return app


if __name__ == "__main__":
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    create_app().run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
