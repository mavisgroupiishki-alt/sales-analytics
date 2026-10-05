"""Private HTTP worker for the Jarvis live call-quality pipeline."""

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


def worker_port() -> int:
    """Use a distinct port when the worker shares a Render web service."""
    return int(os.environ.get("JARVIS_WORKER_PORT") or os.environ.get("PORT", "8080"))


def autosync_interval_seconds() -> int:
    """Return zero when periodic processing is deliberately disabled."""
    try:
        return max(0, int(os.environ.get("JARVIS_AUTOSYNC_SECONDS", "0")))
    except ValueError:
        return 0


def reactivation_sync_interval_seconds() -> int:
    """Run the expensive historical reactivation pass at a calmer cadence."""
    try:
        return max(0, int(os.environ.get("JARVIS_REACTIVATION_SYNC_SECONDS", "3600")))
    except ValueError:
        return 0


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

    def start(
        self,
        *,
        reanalyze_today: bool = False,
        reanalysis_date: str | None = None,
        reactivation: bool = False,
    ) -> bool:
        if not self.lock.acquire(blocking=False):
            return False
        thread = threading.Thread(
            target=self._run,
            kwargs={"reanalyze_today": reanalyze_today, "reanalysis_date": reanalysis_date, "reactivation": reactivation},
            daemon=True,
            name="jarvis-live-sync",
        )
        thread.start()
        return True

    def _run(self, *, reanalyze_today: bool, reanalysis_date: str | None = None, reactivation: bool = False) -> None:
        self.state.update(
            {
                "status": "running",
                "started_at": datetime.now().astimezone().isoformat(),
                "finished_at": None,
                "last_error": None,
                "mode": "reactivation" if reactivation else (f"reanalyze_day:{reanalysis_date}" if reanalysis_date else ("reanalyze_today" if reanalyze_today else "sync_today")),
            }
        )
        try:
            self._validate_environment()
            self._prepare_runtime()
            with self._runtime_environment(reanalyze_today, reanalysis_date):
                # Import here so these modules receive the controlled runtime
                # directory instead of the container source directory.
                if reactivation:
                    from bitrix import Bitrix24Client
                    from reactivation import analyse_reactivation_calls

                    analyse_reactivation_calls(Bitrix24Client(), self.runtime_dir)
                else:
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
        required = ("VIBE_API_KEY", "JARVIS_DATABASE_URL")
        missing = [name for name in required if not os.environ.get(name)]
        if not (os.environ.get("BITRIX_WEBHOOK_URL") or os.environ.get("BITRIX_PROXY_URL")):
            missing.append("BITRIX_WEBHOOK_URL or BITRIX_PROXY_URL")
        if missing:
            raise RuntimeError("Missing required live worker configuration")
        rubric_id = os.environ.get("JARVIS_RUBRIC_ID")
        if not rubric_id:
            import psycopg

            with psycopg.connect(os.environ["JARVIS_DATABASE_URL"]) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        select id
                        from jarvis.rubrics
                        where code = 'jarvis_rop' and version = 1 and status = 'active'
                        """
                    )
                    row = cursor.fetchone()
            if not row:
                raise RuntimeError("Active Jarvis rubric is not configured")
            rubric_id = str(row[0])
            os.environ["JARVIS_RUBRIC_ID"] = rubric_id
        try:
            if int(rubric_id) <= 0:
                raise ValueError
        except ValueError as exc:
            raise RuntimeError("Invalid Jarvis rubric configuration") from exc

    def _prepare_runtime(self) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        audio_dir = self.runtime_dir / "audio_temp"
        shutil.rmtree(audio_dir, ignore_errors=True)
        audio_dir.mkdir()
        scripts = self.runtime_dir / "scripts.json"
        if not scripts.exists():
            shutil.copy2(PROJECT_DIR / "scripts.json", scripts)

    @contextmanager
    def _runtime_environment(self, reanalyze_today: bool, reanalysis_date: str | None = None) -> Iterator[None]:
        previous_cwd = Path.cwd()
        changed = {
            "DATE_FROM": reanalysis_date or datetime.now().date().isoformat(),
            "DATE_TO": reanalysis_date,
            "DAYS_BACK": None,
            "DOWNLOAD_AUDIO_COUNT": "-1",
            "NOTIFY_MANAGERS": "0",
            "REANALYZE_TODAY": "1" if reanalyze_today else None,
            "REANALYZE_DATE": reanalysis_date if reanalyze_today else None,
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
    interval = autosync_interval_seconds()
    if interval:
        def run_periodically() -> None:
            live_pipeline.start()
            while True:
                threading.Event().wait(interval)
                live_pipeline.start()

        threading.Thread(target=run_periodically, daemon=True, name="jarvis-live-scheduler").start()

    reactivation_interval = reactivation_sync_interval_seconds()
    if reactivation_interval:
        def run_reactivation_periodically() -> None:
            # Do not drop a full-history scan merely because the five-minute
            # sales sync owns the worker lock at that exact moment.
            while not live_pipeline.start(reactivation=True):
                threading.Event().wait(30)
            while True:
                threading.Event().wait(reactivation_interval)
                while not live_pipeline.start(reactivation=True):
                    threading.Event().wait(30)

        threading.Thread(target=run_reactivation_periodically, daemon=True, name="jarvis-reactivation-scheduler").start()

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
        mode = str(body.get("mode") or "")
        reactivation = mode == "reactivation"
        reanalyze_today = mode in {"reanalyze_today", "reanalyze_day"}
        reanalysis_date = None
        if mode == "reanalyze_day":
            try:
                reanalysis_date = datetime.fromisoformat(str(body.get("date") or "")).date().isoformat()
            except ValueError:
                return jsonify({"error": "invalid reanalysis date"}), 400
        if not live_pipeline.start(reanalyze_today=reanalyze_today, reanalysis_date=reanalysis_date, reactivation=reactivation):
            return jsonify({"status": "already_running"}), 409
        accepted_mode = "reactivation" if reactivation else (f"reanalyze_day:{reanalysis_date}" if reanalysis_date else ("reanalyze_today" if reanalyze_today else "sync_today"))
        return jsonify({"status": "accepted", "mode": accepted_mode}), 202

    return app


if __name__ == "__main__":
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    create_app().run(host="0.0.0.0", port=worker_port())
