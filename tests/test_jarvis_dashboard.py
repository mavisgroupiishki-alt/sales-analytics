import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis_dashboard import dashboard_model, render_call_detail, render_funnel  # noqa: E402


class DashboardModelTests(unittest.TestCase):
    def test_legacy_unproven_critical_requires_reanalysis_not_rop_review(self):
        calls = [{"activity_id": "1", "manager": {"id": 1, "name": "Анна"}}]
        analyses = {"1": {"analysis": {"overall_score": 3, "flags": {"critical": True}}}}

        model = dashboard_model(calls, analyses)

        self.assertEqual(len(model["critical"]), 0)
        self.assertEqual(len(model["review"]), 0)
        self.assertEqual(len(model["reanalysis"]), 1)

    def test_evidenced_allowed_rule_is_urgent(self):
        calls = [{"activity_id": "1", "manager": {"id": 1, "name": "Анна"}}]
        analyses = {"1": {"analysis": {"flags": {
            "critical": True,
            "critical_rule_id": "confirmed_rudeness",
            "critical_evidence": {"time": "00:12", "quote": "Больше мне не звоните, пожалуйста"},
        }}}}

        model = dashboard_model(calls, analyses)

        self.assertEqual(len(model["critical"]), 1)

    def test_rich_call_card_keeps_audio_transcript_and_clickable_timecodes(self):
        call = {
            "activity_id": "42",
            "created": "2026-09-08T12:00:00+03:00",
            "direction": "outgoing",
            "duration_sec": 90,
            "manager": {"name": "Роман Авсеенко"},
            "client": {"name": "Анна", "company": "ООО Бобик"},
            "crm": {"owner_type": "deal", "owner_id": "7", "stage_name": "5. КП отправлено"},
            "audio": {"file_id": "99"},
        }
        stored = {
            "transcription": {"segments": [{"start": 5, "text": "Добрый день"}]},
            "analysis": {
                "overall_score": 8.0,
                "review_status": "normal",
                "criteria": [{"code": "communication", "applicable": True, "score": 8, "finding": "Спокойный тон", "time": "00:05"}],
                "scripts_alignment": [{"stage": "Приветствие", "status": "full", "evidence": "Менеджер представился", "time": "00:05"}],
            },
        }

        html = render_call_detail(call, stored, {"name": "РОП"})

        self.assertIn('src="/audio/42"', html)
        self.assertIn("ООО Бобик", html)
        self.assertIn("Транскрипт разговора", html)
        self.assertIn('data-timecode="00:05"', html)
        self.assertIn("Соблюдение скрипта", html)

    def test_funnel_renders_operational_metrics_and_real_stage_names(self):
        snapshot = {
            "sales": {
                "active_deals_count": 166,
                "overall": {"total": {"metrics": {"leads": 57, "qualified": 14, "deals": 78, "sales": 11, "sales_amount": 34040, "average_check": 3094.55, "net_revenue": 14350}}},
                "stages": [{"name": "5. КП отправлено", "count": 37, "amount": 82490}],
            }
        }

        html = render_funnel(snapshot, [], {"name": "РОП"})

        self.assertIn("Активные сделки", html)
        self.assertIn("166", html)
        self.assertIn("5. КП отправлено", html)
        self.assertNotIn("C28:", html)


if __name__ == "__main__":
    unittest.main()
