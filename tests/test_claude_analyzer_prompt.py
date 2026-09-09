import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claude_analyzer import analyze_transcript, decode_json_response, detect_call_type  # noqa: E402


class ClaudeAnalyzerPromptTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
