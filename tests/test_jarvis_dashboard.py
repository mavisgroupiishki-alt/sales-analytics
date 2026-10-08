import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis_dashboard import (  # noqa: E402
    dashboard_model,
    is_operationally_excluded,
    render_call_detail,
    render_calls,
    render_daily_reports,
    render_dashboard,
    render_funnel,
)


class DashboardModelTests(unittest.TestCase):
    def test_operational_exclusion_covers_empty_short_and_non_sales_calls(self):
        self.assertTrue(is_operationally_excluded({"audio": {"status": "empty"}}, None))
        self.assertTrue(is_operationally_excluded({"duration_sec": 20}, None))
        self.assertTrue(is_operationally_excluded({}, {"not_sales": True}))
        self.assertFalse(is_operationally_excluded({"duration_sec": 90}, {"overall_score": 7, "flags": {}}))

    def test_low_score_is_visible_to_rop_without_faking_a_critical_incident(self):
        calls = [{"activity_id": "1", "created": "2026-09-08T12:00:00+03:00", "manager": {"id": 1, "name": "Анна"}}]
        analyses = {"1": {"analysis": {
            "overall_score": 2.5,
            "overall_score_method": "applicable_rubric_v1",
            "flags": {},
            "call_type": {"confirmed": True},
        }}}

        model = dashboard_model(calls, analyses)
        overview = render_dashboard(calls, analyses, {"role": "rop", "name": "РОП"})
        journal = render_calls(calls, analyses, {"role": "rop", "name": "РОП"})

        self.assertEqual(len(model["critical"]), 0)
        self.assertEqual(len(model["attention"]), 1)
        self.assertIn("Внимание РОПа", overview)
        self.assertIn("0 критичных · 1 с баллом ≤3", overview)
        self.assertIn("Низкая оценка", journal)

    def test_manager_average_ignores_records_excluded_from_operational_scoring(self):
        calls = [
            {"activity_id": "sales", "manager": {"id": 1, "name": "Анна"}, "duration_sec": 180},
            {"activity_id": "short", "manager": {"id": 1, "name": "Анна"}, "duration_sec": 20},
            {"activity_id": "poor-audio", "manager": {"id": 1, "name": "Анна"}, "duration_sec": 180},
        ]
        analyses = {
            "sales": {"analysis": {"overall_score": 8.0, "flags": {}}},
            "short": {"analysis": {"overall_score": 1.0, "flags": {}}},
            "poor-audio": {"analysis": {"overall_score": 1.0, "exclude_from_stats": True, "poor_audio": True, "flags": {}}},
        }

        model = dashboard_model(calls, analyses)
        manager = model["managers"][0]

        self.assertEqual(manager["average"], 8.0)
        self.assertEqual(manager["scored"], 1)
        self.assertEqual(manager["excluded"], 2)

    def test_missing_analysis_is_not_claimed_to_be_waiting_for_ai(self):
        calls = [{"activity_id": "1", "created": "2026-09-08T12:00:00+03:00", "manager": {"name": "Анна"}}]

        overview = render_dashboard(calls, {}, {"role": "rop", "name": "РОП"})
        journal = render_calls(calls, {}, {"role": "rop", "name": "РОП"})

        self.assertIn("Без разбора", overview)
        self.assertIn("нет пригодной записи или анализ ещё идёт", overview)
        self.assertIn("Нет разбора", journal)

    def test_missing_or_unconfirmed_analysis_appears_in_rop_decision_queue(self):
        calls = [
            {"activity_id": "missing", "created": "2026-09-08T12:00:00+03:00", "manager": {"name": "Анна"}},
            {"activity_id": "type", "created": "2026-09-08T13:00:00+03:00", "manager": {"name": "Анна"}},
        ]
        analyses = {"type": {"analysis": {
            "overall_score": 7,
            "overall_score_method": "applicable_rubric_v1",
            "flags": {},
            "call_type": {"key": "unknown", "confirmed": False},
        }}}

        model = dashboard_model(calls, analyses)
        overview = render_dashboard(calls, analyses, {"role": "rop", "name": "РОП"})

        self.assertEqual(len(model["review"]), 2)
        self.assertIn("Нужно решение РОПа", overview)
        self.assertIn("Неподтверждённый тип", overview)

    def test_unconfirmed_type_does_not_lower_manager_average_or_show_a_draft_score(self):
        calls = [
            {"activity_id": "confirmed", "created": "2026-09-08T12:00:00+03:00", "manager": {"id": 1, "name": "Анна"}},
            {"activity_id": "unknown", "created": "2026-09-08T13:00:00+03:00", "manager": {"id": 1, "name": "Анна"}},
        ]
        analyses = {
            "confirmed": {"analysis": {
                "overall_score": 8.0, "overall_score_method": "applicable_rubric_v2", "flags": {},
                "call_type": {"key": "payment_push", "confirmed": True},
            }},
            "unknown": {"analysis": {
                "overall_score": 1.0, "overall_score_method": "applicable_rubric_v2", "flags": {},
                "call_type": {"key": "unknown", "confirmed": True},
            }},
        }

        model = dashboard_model(calls, analyses)
        journal = render_calls(calls, analyses, {"role": "rop", "name": "РОП"})
        detail = render_call_detail(calls[1], analyses["unknown"], {"role": "rop", "name": "РОП"})

        self.assertEqual(model["managers"][0]["average"], 8.0)
        self.assertEqual(model["managers"][0]["scored"], 1)
        self.assertEqual(len(model["review"]), 1)
        self.assertIn("Требует подтверждения типа", detail)
        self.assertNotIn("Низкая оценка", journal)

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

    def test_excluded_short_call_is_only_visible_on_the_excluded_tab(self):
        calls = [{"activity_id": "1", "created": "2026-09-08T12:00:00+03:00", "manager": {"name": "Анна"}}]
        analyses = {"1": {"analysis": {
            "review_status": "excluded",
            "exclude_from_stats": True,
            "exclusion_reason": "Звонок короче 30 секунд",
        }}}

        journal = render_calls([], analyses, {"role": "rop", "name": "РОП"})
        excluded = render_calls(calls, analyses, {"role": "rop", "name": "РОП"}, filters={"tab": "excluded"})

        self.assertNotIn("Короткий звонок", journal)
        self.assertIn("Рабочие звонки", journal)
        self.assertIn("Короткий звонок", excluded)
        self.assertIn("Исключённые", excluded)

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

    def test_call_card_shows_manual_type_review_and_history_indicator(self):
        call = {"activity_id": "42", "manager": {"name": "Роман"}}
        stored = {"analysis": {
            "call_type": {"key": "unknown", "label": "Неизвестно", "confirmed": False},
            "context_snapshot": {
                "previous_calls": [{"date": "2026-09-01", "call_type": "Дожим", "summary": "Обсудили срок"}],
                "memory": {"scope": "company", "facts_count": 18},
            },
        }}

        html = render_call_detail(call, stored, {"role": "rop", "name": "РОП"})

        self.assertIn("Проверка типа звонка", html)
        self.assertIn("Почему ИИ ошибся", html)
        self.assertIn("использовано 1 из 8 предыдущих звонков", html)
        self.assertIn("Постоянная память компании:</b> 18 фактов", html)
        self.assertIn("Обсудили срок", html)

    def test_call_list_keeps_all_filters_in_the_back_link_and_marks_manual_review(self):
        call = {
            "activity_id": "42",
            "created": "2026-10-05T10:00:00+03:00",
            "manager": {"id": "7", "name": "Роман"},
            "crm": {"owner_type": "deal", "owner_id": "11", "stage_name": "КП"},
            "_manual_review": {"reviewed": True, "reviewer_name": "РОП"},
        }
        filters = {
            "period": "month", "manager": "7", "call_type": "payment_push",
            "stage": "КП", "score_min": "6", "score_max": "9", "analysis": "with", "status": "normal",
        }

        html = render_calls([call], {"42": {"analysis": {"overall_score": 8, "review_status": "normal"}}}, {"role": "rop", "name": "РОП"}, period="month", filters=filters)

        self.assertIn("jc-row is-manual-reviewed", html)
        self.assertIn("Проверено вручную", html)
        self.assertIn("return_to=%2Fcalls%3Fperiod%3Dmonth%26manager%3D7", html)
        self.assertIn("score_max%3D9%26analysis%3Dwith%26status%3Dnormal", html)

    def test_call_card_shows_manual_review_toggle_and_explicit_manager_errors(self):
        call = {
            "activity_id": "42",
            "manager": {"name": "Роман"},
            "_manual_review": {"reviewed": True, "reviewer_name": "РОП", "reviewed_at": "2026-10-05T12:00:00+03:00"},
        }
        stored = {"analysis": {
            "overall_score": 7.8,
            "manager_errors": [{"criterion": "next_step", "text": "Не зафиксировал дату следующего контакта", "time": "01:20", "quote": "Созвонимся потом"}],
        }}

        html = render_call_detail(call, stored, {"role": "rop", "name": "РОП"}, return_to="/calls?manager=7&status=normal")

        self.assertIn('id="manualReviewed"', html)
        self.assertIn("checked", html)
        self.assertIn("Ошибки менеджера и снижение оценки", html)
        self.assertIn("Не зафиксировал дату следующего контакта", html)
        self.assertIn('href="/calls?manager=7&amp;status=normal"', html)

    def test_context_card_names_sources_instead_of_calling_everything_current_deal(self):
        call = {"activity_id": "42", "manager": {"name": "Роман"}}
        stored = {"analysis": {"context_snapshot": {
            "previous_calls": [
                {"date": "2026-10-01", "call_type": "Дожим", "summary": "Договорились", "source": "previous_sales_deal", "source_deal_id": "38"},
                {"date": "2026-10-02", "call_type": "КП", "summary": "Есть вопрос", "source": "contact"},
            ],
            "sources": {"previous_sales_deal": {"deal_id": "38"}, "contact": {"count": 1}},
        }}}

        html = render_call_detail(call, stored, {"role": "rop", "name": "РОП"})

        self.assertIn("Контекст из предыдущей сделки продаж №38", html)
        self.assertIn("Контекст через контакт", html)

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

        self.assertIn("/calls?period=today&stage=5.+%D0%9A%D0%9F+%D0%BE%D1%82%D0%BF%D1%80%D0%B0%D0%B2%D0%BB%D0%B5%D0%BD%D0%BE", html)

    def test_period_switch_is_visible_and_preserved_in_links(self):
        call = {
            "activity_id": "42",
            "created": "2026-09-11T10:00:00+03:00",
            "manager": {"id": 1286, "name": "Роман Авсеенко"},
            "crm": {"owner_type": "deal", "owner_id": "7", "stage_name": "5. КП отправлено"},
        }

        overview = render_dashboard([call], {}, {"role": "rop", "name": "РОП"}, period="week")
        journal = render_calls([call], {}, {"role": "rop", "name": "РОП"}, period="week")

        for label in ("Сегодня", "Вчера", "Неделя", "Месяц"):
            self.assertIn(label, overview)
            self.assertIn(label, journal)
        self.assertIn('href="/?period=week"', overview)
        self.assertIn('aria-current="page">Неделя</a>', overview)
        self.assertIn('/calls?period=week', overview)

    def test_daily_reports_group_actions_and_exclude_service_calls(self):
        calls = [
            {
                "activity_id": "42",
                "created": "2026-09-17T10:00:00+03:00",
                "manager": {"id": 1286, "name": "Роман Авсеенко"},
                "client": {"name": "Анастасия"},
            },
            {
                "activity_id": "43",
                "created": "2026-09-17T11:00:00+03:00",
                "manager": {"id": 1286, "name": "Роман Авсеенко"},
                "client": {"name": "Служебный клиент"},
            },
        ]
        analyses = {
            "42": {"analysis": {"overall_score": 4.0, "recommended_action": "Перезвонить и предложить СПК", "flags": {}}},
            "43": {"analysis": {"service_call": True, "exclude_from_stats": True, "recommended_action": "Служебный текст", "flags": {}}},
        }

        html = render_daily_reports(calls, analyses, {"role": "rop", "name": "РОП"}, period="yesterday")

        self.assertIn("Роман Авсеенко", html)
        self.assertIn("Перезвонить и предложить СПК", html)
        self.assertIn('href="/calls/42?period=yesterday"', html)
        self.assertNotIn("Служебный текст", html)


if __name__ == "__main__":
    unittest.main()
