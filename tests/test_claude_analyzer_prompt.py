import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claude_analyzer import (  # noqa: E402
    analyze_transcript,
    complete_missing_criteria_neutrally,
    decode_json_response,
    detect_call_type,
    detect_service_administrative_followup,
    detect_service_contact_routing,
    detect_service_document_delivery,
)


class ClaudeAnalyzerPromptTests(unittest.TestCase):
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
