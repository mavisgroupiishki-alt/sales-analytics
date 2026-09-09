import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis_dashboard import dashboard_model, render_call_detail, render_calls, render_dashboard, render_funnel  # noqa: E402


class DashboardModelTests(unittest.TestCase):
    def test_low_score_is_visible_to_rop_without_faking_a_critical_incident(self):
        calls = [{"activity_id": "1", "created": "2026-09-08T12:00:00+03:00", "manager": {"id": 1, "name": "Анна"}}]
        analyses = {"1": {"analysis": {
            "overall_score": 2.5,
            "overall_score_method": "applicable_rubric_v1",
            "flags": {},
        }}}

        model = dashboard_model(calls, analyses)
        overview = render_dashboard(calls, analyses, {"role": "rop", "name": "РОП"})
        journal = render_calls(calls, analyses, {"role": "rop", "name": "РОП"})

        self.assertEqual(len(model["critical"]), 0)
        self.assertEqual(len(model["attention"]), 1)
        self.assertIn("Внимание РОПа", overview)
        self.assertIn("0 критичных · 1 с баллом ≤3", overview)
        self.assertIn("Низкая оценка", journal)

    def test_missing_analysis_is_not_claimed_to_be_waiting_for_ai(self):
        calls = [{"activity_id": "1", "created": "2026-09-08T12:00:00+03:00", "manager": {"name": "Анна"}}]

        overview = render_dashboard(calls, {}, {"role": "rop", "name": "РОП"})
        journal = render_calls(calls, {}, {"role": "rop", "name": "РОП"})

        self.assertIn("Без разбора", overview)
        self.assertIn("нет пригодной записи или анализ ещё идёт", overview)
        self.assertIn("Нет разбора", journal)

    def test_empty_bitrix_recording_is_not_presented_as_pending_or_playable(self):
        call = {
            "activity_id": "1",
            "created": "2026-09-08T12:00:00+03:00",
            "manager": {"name": "Анна"},
            "audio": {"file_id": "99", "status": "empty", "error": "empty_recording", "size_bytes": 432},
        }

        model = dashboard_model([call], {})
        overview = render_dashboard([call], {}, {"role": "rop", "name": "РОП"})
        journal = render_calls([call], {}, {"role": "rop", "name": "РОП"})
        detail = render_call_detail(call, {}, {"role": "rop", "name": "РОП"})

        self.assertEqual(model["unavailable_audio"], 1)
        self.assertEqual(model["pending_analysis"], 0)
        self.assertIn("1 пустая запись", overview)
        self.assertIn("Пустая запись", journal)
        self.assertNotIn("Нет разбора", journal)
        self.assertIn("Запись пустая", detail)
        self.assertIn("Пустая запись не анализируется", detail)
        self.assertIn("Оценка, проверка скрипта и транскрипт для этого звонка не создаются", detail)
        self.assertNotIn("Транскрипт разговора", detail)
        self.assertNotIn("Транскрипт формируется", detail)
        self.assertNotIn('src="/audio/1"', detail)

    def test_excluded_short_call_has_a_specific_status(self):
        calls = [{"activity_id": "1", "created": "2026-09-08T12:00:00+03:00", "manager": {"name": "Анна"}}]
        analyses = {"1": {"analysis": {
            "review_status": "excluded",
            "exclude_from_stats": True,
            "exclusion_reason": "Звонок короче 30 секунд",
        }}}

        journal = render_calls(calls, analyses, {"role": "rop", "name": "РОП"})

        self.assertIn("Короткий звонок", journal)
        self.assertNotIn(">Исключён<", journal)

    def test_service_contact_call_is_shown_as_analyzed_and_not_scored(self):
        call = {
            "activity_id": "1",
            "created": "2026-09-08T17:41:00+03:00",
            "manager": {"name": "Роман Авсеенко"},
            "client": {"name": "Кирилл"},
        }
        analyses = {"1": {"analysis": {
            "review_status": "excluded",
            "exclude_from_stats": True,
            "service_call": True,
            "overall_score": None,
            "call_type": {"key": "service_contact_routing", "label": "Служебное уточнение контакта"},
        }}}

        model = dashboard_model([call], analyses)
        journal = render_calls([call], analyses, {"role": "rop", "name": "РОП"})
        detail = render_call_detail(call, analyses["1"], {"role": "rop", "name": "РОП"})

        self.assertEqual(model["analyzed"], 1)
        self.assertIn("Служебный звонок", journal)
        self.assertIn("Не оценивается", detail)
        self.assertNotIn("—/10", detail)

    def test_service_call_does_not_publish_its_internal_transcript(self):
        call = {"activity_id": "1", "manager": {"name": "Роман"}, "audio": {"file_id": "99"}}
        stored = {
            "transcription": {"text": "Внутренняя служебная расшифровка"},
            "analysis": {
                "service_call": True,
                "not_sales": True,
                "not_sales_reason": "Проверка доставки документов",
                "exclude_from_stats": True,
                "review_status": "excluded",
                "overall_score": None,
            },
        }

        detail = render_call_detail(call, stored, {"role": "rop", "name": "РОП"})

        self.assertIn("Служебный звонок не оценивается", detail)
        self.assertNotIn("Транскрипт разговора", detail)
        self.assertNotIn("Внутренняя служебная расшифровка", detail)

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
        self.assertIn('data-funnel-detail="1"', html)

    def test_linked_call_stage_opens_filtered_call_list(self):
        call = {
            "activity_id": "42",
            "crm": {"owner_type": "deal", "owner_id": "7", "stage_id": "PREPARATION", "stage_name": "PREPARATION"},
        }

        html = render_funnel({"sales": {}}, [call], {"name": "РОП"})

        self.assertIn("/calls?stage=5.+%D0%9A%D0%9F+%D0%BE%D1%82%D0%BF%D1%80%D0%B0%D0%B2%D0%BB%D0%B5%D0%BD%D0%BE", html)


if __name__ == "__main__":
    unittest.main()
