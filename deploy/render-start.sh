#!/usr/bin/env sh
set -eu

worker_pid=""
if [ "${JARVIS_LIVE_WORKER_ENABLED:-0}" = "1" ]; then
  JARVIS_WORKER_PORT="${JARVIS_WORKER_PORT:-8080}" python src/jarvis_live_worker.py &
  worker_pid="$!"
fi

gunicorn app:app --bind "0.0.0.0:${PORT}" --workers 1 --threads 4 --timeout 120 &
web_pid="$!"

cleanup() {
  [ -z "$worker_pid" ] || kill "$worker_pid" 2>/dev/null || true
  kill "$web_pid" 2>/dev/null || true
}
trap cleanup INT TERM EXIT
wait "$web_pid"
