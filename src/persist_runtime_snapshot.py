"""Persist a completed runtime snapshot without sending audio to AI again."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict


def normalize_analysis_for_storage(analysis: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(analysis)
    confidence = normalized.get("analysis_confidence")
    if isinstance(confidence, str):
        mapped = {"low": 0.35, "medium": 0.65, "high": 0.9}.get(confidence.lower())
        if mapped is not None:
            normalized["analysis_confidence"] = mapped
    score = normalized.get("overall_score")
    if score is not None:
        try:
            normalized["overall_score"] = max(1.0, min(10.0, float(score)))
        except (TypeError, ValueError):
            pass
    return normalized


def main() -> None:
    runtime_dir = Path(os.environ.get("JARVIS_RUNTIME_DIR", "/var/lib/jarvis"))
    calls = json.loads((runtime_dir / "calls_data.json").read_text(encoding="utf-8"))
    analyses = json.loads((runtime_dir / "analyses.json").read_text(encoding="utf-8"))
    for record in analyses.values():
        if isinstance(record, dict) and isinstance(record.get("analysis"), dict):
            record["analysis"] = normalize_analysis_for_storage(record["analysis"])

    from claude_analyzer import mirror_analyses_to_jarvis

    written = mirror_analyses_to_jarvis(calls, analyses)
    print(f"Persisted runtime analyses: {written}")


if __name__ == "__main__":
    main()
