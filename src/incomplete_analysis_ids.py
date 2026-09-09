"""Select only downloaded calls that still lack a valid 1–10 analysis."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List


def find_incomplete_activity_ids(
    calls: Iterable[Dict[str, Any]], analyses: Dict[str, Any], audio_file_ids: set[str]
) -> List[str]:
    result: List[str] = []
    for call in calls:
        activity_id = str(call.get("activity_id") or "")
        audio_id = str((call.get("audio") or {}).get("file_id") or "")
        raw_duration = call.get("duration_sec")
        try:
            duration = int(raw_duration or 0)
        except (TypeError, ValueError):
            duration = 0
        duration_is_known_short = raw_duration not in (None, "") and 0 < duration < 30
        if not activity_id or not audio_id or audio_id not in audio_file_ids or duration_is_known_short:
            continue
        stored = analyses.get(activity_id)
        if not stored:
            result.append(activity_id)
            continue
        analysis = (stored.get("analysis") or {}) if isinstance(stored, dict) else {}
        try:
            score = float(analysis.get("overall_score"))
        except (TypeError, ValueError):
            if not analysis.get("exclude_from_stats"):
                result.append(activity_id)
            continue
        if not 1 <= score <= 10:
            result.append(activity_id)
    return result


def main() -> None:
    runtime_dir = Path(os.environ.get("JARVIS_RUNTIME_DIR", "/var/lib/jarvis"))
    calls = json.loads((runtime_dir / "calls_data.json").read_text(encoding="utf-8"))
    analyses = json.loads((runtime_dir / "analyses.json").read_text(encoding="utf-8"))
    audio_ids = {path.name.split("_", 1)[0] for path in (runtime_dir / "audio_temp").glob("*.mp3")}
    print(",".join(find_incomplete_activity_ids(calls, analyses, audio_ids)))


if __name__ == "__main__":
    main()
