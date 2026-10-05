import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claude_analyzer import (  # noqa: E402
    analyze_transcript,
    apply_manual_corrections,
    build_analysis_prompt,
    build_deal_context,
    complete_missing_criteria_neutrally,
    decode_json_response,
    detect_call_type,
    detect_service_administrative_followup,
    detect_service_contact_routing,
    detect_service_document_delivery,
)
from bitrix import (  # noqa: E402
    fetch_previous_deal_recordings,
    select_previous_deal_recordings,
    select_related_context_recordings,
)


class ClaudeAnalyzerPromptTests(unittest.TestCase):
    def test_context_backfill_reads_previous_calls_from_bitrix(self):
        class BitrixFixture:
            def __init__(self):
                self.requests = []

            def call_all(self, method, params):
                self.requests.append((method, params))
                return [
                    {
                        "ID": str(number),
                        "CREATED": f"2026-09-{number + 1:02d}T10:00:00+03:00",
                        "FILES": [{"id": number + 100}],
                        "COMMUNICATIONS": [{}],
                        "DIRECTION": 2,
                        "OWNER_TYPE_ID": 2,
                        "OWNER_ID": "42",
                        "AUTHOR_ID": 1286,
                    }
                    for number in range(10)
                ]

            def call(self, method, params):
                self.requests.append((method, params))
                self.assertEqual(method, "user.get")
                return {"result": [{"NAME": "Роман", "LAST_NAME": "Авсеенко"}]}

            def assertEqual(self, actual, expected):
                if actual != expected:
                    raise AssertionError(f"{actual!r} != {expected!r}")

        client = BitrixFixture()
        selected = fetch_previous_deal_recordings(
            client,
            [{
                "activity_id": "current",
                "created": "2026-09-20T10:00:00+03:00",
                "crm": {"owner_type": "deal", "owner_id": "42"},
            }],
        )

        self.assertEqual([call["activity_id"] for call in selected], [str(number) for number in range(2, 10)])
        method, payload = client.requests[0]
        self.assertEqual(method, "crm.activity.list")
        self.assertEqual(payload["filter"]["OWNER_ID"], "42")

    def test_context_backfill_selects_eight_prior_recordings_even_without_analysis(self):
        target = {
            "activity_id": "current",
            "created": "2026-09-20T10:00:00+03:00",
            "crm": {"owner_type": "deal", "owner_id": "42"},
        }
        candidates = [
            {
                "activity_id": str(number),
                "created": f"2026-09-{number + 1:02d}T10:00:00+03:00",
                "crm": {"owner_type": "deal", "owner_id": "42"},
                "audio": {"file_id": number + 100},
                "duration_sec": 60,
            }
            for number in range(10)
        ]

        selected = select_previous_deal_recordings([target], candidates)

        self.assertEqual([call["activity_id"] for call in selected], [str(number) for number in range(2, 10)])

    def test_related_context_backfill_adds_contact_and_company_and_uses_prior_deal_only_as_fallback(self):
        target = {
            "activity_id": "current", "created": "2026-10-05T10:00:00+03:00",
            "crm": {"owner_type": "deal", "owner_id": "42", "contact_ids": ["9"], "company_id": "5", "category_id": "0"},
        }
        candidates = [
            {"activity_id": "contact", "created": "2026-10-01T10:00:00+03:00", "crm": {"owner_type": "contact", "owner_id": "9"}, "audio": {"file_id": 1}},
            {"activity_id": "company", "created": "2026-10-02T10:00:00+03:00", "crm": {"owner_type": "company", "owner_id": "5"}, "audio": {"file_id": 2}},
            {"activity_id": "previous", "created": "2026-10-03T10:00:00+03:00", "crm": {"owner_type": "deal", "owner_id": "38", "contact_ids": ["9"], "company_id": "5", "category_id": "0"}, "audio": {"file_id": 3}},
        ]

        selected = select_related_context_recordings([target], candidates)

        self.assertEqual([call["activity_id"] for call in selected], ["contact", "company", "previous"])
        candidates.append({"activity_id": "current-deal", "created": "2026-10-04T10:00:00+03:00", "crm": {"owner_type": "deal", "owner_id": "42"}, "audio": {"file_id": 4}})
        selected = select_related_context_recordings([target], candidates)
        self.assertEqual([call["activity_id"] for call in selected], ["contact", "company", "current-deal"])

    def test_deal_context_uses_only_eight_earlier_calls_from_same_deal(self):
        calls = []
        analyses = {}
        for number in range(10):
            activity_id = str(number)
            calls.append({
                "activity_id": activity_id,
                "created": f"2026-09-{number + 1:02d}T10:00:00+03:00",
                "crm": {"owner_type": "deal", "owner_id": "42"},
            })
            analyses[activity_id] = {"analysis": {
                "summary": f"Итог {number}",
                "outcome": "Договорённость",
                "recommendation": "Перезвонить",
                "call_type": {"label": "Дожим"},
            }}
        current = {
            "activity_id": "current",
            "created": "2026-09-20T10:00:00+03:00",
            "manager": {"name": "Роман"},
            "crm": {"owner_type": "deal", "owner_id": "42", "stage_name": "КП"},
        }
        calls.append({
            "activity_id": "foreign",
            "created": "2026-09-19T10:00:00+03:00",
            "crm": {"owner_type": "deal", "owner_id": "not-42"},
        })
        analyses["foreign"] = {"analysis": {"summary": "Чужая сделка", "call_type": {"label": "Дожим"}}}

        context = build_deal_context(calls, analyses, current)
        prompt = build_analysis_prompt("[00:00] разговор", current, [], "unknown", context)

        self.assertEqual(len(context["previous_calls"]), 8)
        self.assertEqual(context["previous_calls"][0]["activity_id"], "2")
        self.assertNotIn("Итог 0", prompt)
        self.assertIn("Итог 9", prompt)
        self.assertNotIn("Чужая сделка", prompt)
        self.assertIn("Использовано предыдущих разговоров: 8 из 8", prompt)

    def test_context_uses_contact_and_company_and_falls_back_to_previous_sales_deal_only_without_current_deal_calls(self):
        calls = [
            {
                "activity_id": "previous-deal", "created": "2026-09-01T10:00:00+03:00",
                "crm": {"owner_type": "deal", "owner_id": "38", "category_id": "0", "contact_ids": ["9"], "company_id": "5"},
            },
            {
                "activity_id": "contact", "created": "2026-09-02T10:00:00+03:00",
                "crm": {"owner_type": "contact", "owner_id": "9"},
            },
            {
                "activity_id": "company", "created": "2026-09-03T10:00:00+03:00",
                "crm": {"owner_type": "company", "owner_id": "5"},
            },
        ]
        analyses = {
            item["activity_id"]: {"analysis": {"summary": item["activity_id"], "outcome": "Есть контакт", "recommendation": "Продолжить", "call_type": {"label": "Дожим"}}}
            for item in calls
        }
        current = {
            "activity_id": "current", "created": "2026-10-05T10:00:00+03:00", "manager": {"name": "Роман"},
            "crm": {"owner_type": "deal", "owner_id": "42", "category_id": "0", "contact_ids": ["9"], "company_id": "5"},
        }

        context = build_deal_context(calls, analyses, current)

        self.assertEqual([item["activity_id"] for item in context["previous_calls"]], ["previous-deal", "contact", "company"])
        self.assertEqual(context["previous_calls"][0]["source"], "previous_sales_deal")
        self.assertEqual(context["previous_calls"][1]["source"], "contact")
        self.assertEqual(context["previous_calls"][2]["source"], "company")
        self.assertEqual(context["sources"]["previous_sales_deal"]["deal_id"], "38")

    def test_current_deal_calls_prevent_previous_deal_fallback_but_keep_contact_and_company_context(self):
        calls = [
            {"activity_id": "same", "created": "2026-10-01T10:00:00+03:00", "crm": {"owner_type": "deal", "owner_id": "42", "contact_ids": ["9"], "company_id": "5"}},
            {"activity_id": "previous", "created": "2026-09-01T10:00:00+03:00", "crm": {"owner_type": "deal", "owner_id": "38", "contact_ids": ["9"], "company_id": "5"}},
            {"activity_id": "contact", "created": "2026-10-02T10:00:00+03:00", "crm": {"owner_type": "contact", "owner_id": "9"}},
        ]
        analyses = {item["activity_id"]: {"analysis": {"summary": item["activity_id"], "call_type": {"label": "Дожим"}}} for item in calls}
        current = {"activity_id": "current", "created": "2026-10-05T10:00:00+03:00", "crm": {"owner_type": "deal", "owner_id": "42", "contact_ids": ["9"], "company_id": "5"}}

        context = build_deal_context(calls, analyses, current)

        self.assertEqual({item["activity_id"] for item in context["previous_calls"]}, {"same", "contact"})
        self.assertNotIn("previous_sales_deal", context["sources"])

    def test_analysis_prompt_contains_calibration_rules_from_manual_reviews(self):
        prompt = build_analysis_prompt("[00:00] тест", {"crm": {}}, [], "unknown")

        self.assertIn("переадресовал к ЛПР", prompt)
        self.assertIn("не больше чем на 1–2 балла", prompt)
        self.assertIn("вежливый отказ", prompt)

    def test_manual_type_is_preserved_over_new_ai_result(self):
        analysis = apply_manual_corrections(
            "42",
            {"call_type": {"key": "cold_new", "label": "Первичный холодный"}},
            {"42": {"call_type_key": "payment_push", "reason": "Дожим оплаты", "reviewer_name": "РОП"}},
        )

        self.assertEqual(analysis["ai_call_type"]["key"], "cold_new")
        self.assertEqual(analysis["call_type"]["key"], "payment_push")
        self.assertTrue(analysis["call_type"]["confirmed"])

    def test_missing_criteria_are_neutral_and_visible(self):
        criteria, missing = complete_missing_criteria_neutrally(
            "unknown",
            [{"code": "expertise", "applicable": True, "score": 8}],
        )

        self.assertEqual(missing, ["next_step", "communication"])
        self.assertEqual([item["score"] for item in criteria], [8, 5.0, 5.0])

    def test_json_decoder_accepts_a_fenced_object(self):
        self.assertEqual(decode_json_response('```json\n{"ok": true}\n```'), {"ok": True})

    def test_call_type_prompt_contains_literal_json_contract(self):
        response = '{"call_type_key":"unknown","confirmed":false,"evidence":"нет достаточных оснований"}'

        with patch("claude_analyzer.call_claude_api", return_value=(response, {})) as api:
            result = detect_call_type("Короткий деловой разговор", {"direction": "incoming", "crm": {}})

        self.assertEqual(result, "unknown")
        prompt = api.call_args.args[0]
        self.assertIn('"call_type_key":"ключ из списка или unknown"', prompt)

    def test_analysis_retries_malformed_json_and_requires_complete_rubric(self):
        call_type = '{"call_type_key":"unknown","confirmed":false,"evidence":"нет достаточных оснований"}'
        valid_analysis = json.dumps(
            {
                "overall_score": 6,
                "criteria": [
                    {"code": "expertise", "applicable": True, "score": 6},
                    {"code": "next_step", "applicable": True, "score": 7},
                    {"code": "communication", "applicable": True, "score": 8},
                ],
                "flags": {},
            }
        )
        responses = [(call_type, {}), ('{"broken":', {}), (valid_analysis, {})]

        with patch("claude_analyzer.call_claude_api", side_effect=responses) as api:
            result = analyze_transcript(
                {"text": "Достаточно длинный транскрипт разговора", "text_with_timecodes": "[00:00] Текст"},
                {"direction": "incoming", "duration_sec": 60, "crm": {}},
                {},
            )

        self.assertEqual(api.call_count, 3)
        self.assertEqual(result["_meta"]["attempts"], 2)
        self.assertEqual(result["overall_score"], 6.8)

    def test_analysis_requests_focused_rubric_when_full_response_is_incomplete(self):
        call_type = '{"call_type_key":"unknown","confirmed":false,"evidence":"нет достаточных оснований"}'
        incomplete = json.dumps({"overall_score": 5, "criteria": [], "flags": {}})
        rubric = json.dumps(
            {
                "criteria": [
                    {"code": "expertise", "applicable": True, "score": 7},
                    {"code": "next_step", "applicable": True, "score": 5},
                    {"code": "communication", "applicable": True, "score": 8},
                ]
            }
        )
        responses = [(call_type, {}), (incomplete, {}), (incomplete, {}), (rubric, {})]

        with patch("claude_analyzer.call_claude_api", side_effect=responses) as api:
            result = analyze_transcript(
                {"text": "Достаточно длинный транскрипт разговора", "text_with_timecodes": "[00:00] Текст"},
                {"direction": "incoming", "duration_sec": 60, "crm": {}},
                {},
            )

        self.assertEqual(api.call_count, 4)
        self.assertEqual(result["_meta"]["attempts"], 3)
        self.assertEqual(result["overall_score"], 6.1)

    def test_ai_audio_warning_does_not_exclude_a_complete_transcript(self):
        call_type = '{"call_type_key":"unknown","confirmed":false,"evidence":"нет достаточных оснований"}'
        valid_analysis = json.dumps(
            {
                "overall_score": 3,
                "criteria": [
                    {"code": "expertise", "applicable": True, "score": 3},
                    {"code": "next_step", "applicable": True, "score": 2},
                    {"code": "communication", "applicable": True, "score": 4},
                ],
                "flags": {"poor_audio": True, "poor_audio_reason": "есть ошибки Whisper"},
            }
        )

        with patch("claude_analyzer.call_claude_api", side_effect=[(call_type, {}), (valid_analysis, {})]):
            result = analyze_transcript(
                {
                    "text": "Полный транскрипт разговора, в котором достаточно текста для оценки диалога и его результата.",
                    "text_with_timecodes": "[00:00] Полный транскрипт разговора",
                    "duration_sec": 55,
                },
                {"direction": "incoming", "duration_sec": 55, "crm": {}},
                {},
            )

        self.assertFalse(result["poor_audio"])
        self.assertTrue(result["poor_audio_reported_by_ai"])
        self.assertNotEqual(result["review_status"], "excluded")

    def test_contact_routing_call_is_analyzed_without_lowering_sales_score(self):
        transcript = (
            "Со мной лучше связываться по тому номеру, с которого я сейчас набираю. "
            "К Viber привязан другой телефон. По этому номеру можете набирать. Хорошо, спасибо."
        )

        self.assertTrue(detect_service_contact_routing(transcript))
        with patch("claude_analyzer.call_claude_api") as api:
            result = analyze_transcript(
                {
                    "text": transcript,
                    "text_with_timecodes": "[00:20] Клиент: со мной лучше связываться по тому номеру",
                    "duration_sec": 55,
                },
                {"direction": "outgoing", "duration_sec": 55, "crm": {}},
                {},
            )

        api.assert_not_called()
        self.assertEqual(result["call_type"]["key"], "service_contact_routing")
        self.assertEqual(result["review_status"], "excluded")
        self.assertTrue(result["service_call"])
        self.assertTrue(result["exclude_from_stats"])
        self.assertIsNone(result["overall_score"])

    def test_sales_call_with_phone_detail_is_not_misclassified_as_service(self):
        transcript = (
            "Позвоните по другому номеру, а сейчас обсудим стоимость сертификата, сроки оплаты и договор. "
            "Клиент возражает против цены, менеджер предлагает следующий шаг."
        )

        self.assertFalse(detect_service_contact_routing(transcript))

    def test_document_delivery_followup_is_not_scored_as_a_sales_call(self):
        transcript = (
            "Алло, Александр. Вы говорили, что на Вайбертки инфиденты, что ничего не пришло. "
            "Да, я еще в процессе, я еще делаю немножечко. Отвлекли клиенты. "
            "Хорошо, все, жду. Спасибо, на сегодня."
        )

        self.assertTrue(detect_service_document_delivery(transcript))
        with patch("claude_analyzer.call_claude_api") as api:
            result = analyze_transcript(
                {
                    "text": transcript,
                    "text_with_timecodes": "[00:08] Менеджер: документы на Viber пришли?",
                    "duration_sec": 31,
                },
                {"direction": "incoming", "duration_sec": 31, "crm": {"stage_id": "PREPARATION"}},
                {},
            )

        api.assert_not_called()
        self.assertEqual(result["call_type"]["key"], "service_document_delivery")
        self.assertEqual(result["review_status"], "excluded")
        self.assertTrue(result["service_call"])
        self.assertTrue(result["exclude_from_stats"])
        self.assertIsNone(result["overall_score"])

    def test_document_delivery_mention_does_not_hide_a_real_sales_discussion(self):
        transcript = (
            "Документы в Viber получили? Теперь обсудим стоимость, условия оплаты и ваши возражения."
        )

        self.assertFalse(detect_service_document_delivery(transcript))

    def test_unfinished_document_followup_is_administrative_and_not_scored(self):
        transcript = (
            "Алло, Виктор, добрый день. Жду письмо от вас на почте, нету стоимости, еще не сформировали? "
            "Еще ничего не успели сделать. Давайте проверю сейчас, буквально пару минут доделаю."
        )

        self.assertTrue(detect_service_administrative_followup(transcript))
        with patch("claude_analyzer.call_claude_api") as api:
            result = analyze_transcript(
                {"text": transcript, "text_with_timecodes": "[00:02] Клиент: жду письмо", "duration_sec": 54},
                {"direction": "incoming", "duration_sec": 54, "crm": {"stage_id": "PREPARATION"}},
                {},
            )

        api.assert_not_called()
        self.assertEqual(result["call_type"]["key"], "service_administrative_followup")
        self.assertTrue(result["service_call"])
        self.assertIsNone(result["overall_score"])

    def test_ai_non_sales_result_cannot_keep_a_numeric_sales_score(self):
        call_type = '{"call_type_key":"unknown","confirmed":false,"evidence":"нет достаточных оснований"}'
        non_sales_analysis = json.dumps(
            {
                "overall_score": 1,
                "criteria": [
                    {"code": "expertise", "applicable": True, "score": 0},
                    {"code": "next_step", "applicable": True, "score": 0},
                    {"code": "communication", "applicable": True, "score": 3},
                ],
                "flags": {"not_sales": True, "not_sales_reason": "Служебный административный вопрос"},
            }
        )

        with patch("claude_analyzer.call_claude_api", side_effect=[(call_type, {}), (non_sales_analysis, {})]):
            result = analyze_transcript(
                {"text": "Достаточно длинный служебный разговор без консультации или продажи.", "duration_sec": 55},
                {"direction": "incoming", "duration_sec": 55, "crm": {}},
                {},
            )

        self.assertTrue(result["not_sales"])
        self.assertTrue(result["exclude_from_stats"])
        self.assertIsNone(result["overall_score"])
        self.assertEqual(result["overall_score_method"], "not_applicable_non_sales_v1")


if __name__ == "__main__":
    unittest.main()
