import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claude_analyzer import compute_applicable_score, evaluate_triage, is_reanalysis_target, reanalysis_scope  # noqa: E402
from jarvis_store import JarvisRepository, JarvisStore, normalize_bitrix_call, payload_sha256  # noqa: E402


class JarvisStoreTests(unittest.TestCase):
    def test_transcript_status_matches_database_contract(self):
        class Cursor:
            def __init__(self):
                self.statements = []
                self.results = [(2,), (11,)]

            def execute(self, statement, params):
                self.statements.append(statement)

            def fetchone(self):
                return self.results.pop(0)

        cursor = Cursor()
        transcript_id = JarvisStore._write_transcript(
            None,
            cursor,
            call_id=7,
            transcription={"text": "Тест", "segments": [], "confidence": 0.9},
        )

        self.assertEqual(transcript_id, 11)
        self.assertIn("'complete'", cursor.statements[1])
        self.assertNotIn("'completed'", cursor.statements[1])

    def test_normalizes_confirmed_crm_owner_without_guessing_client_type(self):
        normalized = normalize_bitrix_call(
            {
                "activity_id": "479476",
                "created": "2026-09-07T17:21:48+03:00",
                "direction": "outgoing",
                "duration_sec": 105,
                "manager": {"id": 1286},
                "audio": {"file_id": 547064},
                "crm": {"owner_type": "deal", "owner_id": "37886"},
            }
        )

        self.assertEqual(normalized.source_call_id, "479476")
        self.assertEqual(normalized.crm_owner_type, "deal")
        self.assertEqual(normalized.crm_owner_id, "37886")
        self.assertEqual(normalized.audio_status, "available")

    def test_unknown_crm_owner_is_left_unlinked(self):
        normalized = normalize_bitrix_call(
            {"activity_id": "1", "created": "2026-09-08T10:00:00+03:00", "crm": {"owner_type": "unknown"}}
        )

        self.assertIsNone(normalized.crm_owner_type)
        self.assertIsNone(normalized.crm_owner_id)

    def test_empty_recording_is_not_marked_available(self):
        normalized = normalize_bitrix_call(
            {
                "activity_id": "1",
                "created": "2026-09-08T10:00:00+03:00",
                "audio": {"file_id": 42, "status": "empty", "error": "empty_recording", "size_bytes": 432},
            }
        )

        self.assertFalse(normalized.recording_available)
        self.assertEqual(normalized.audio_status, "unavailable")

    def test_payload_hash_is_stable_for_equivalent_dicts(self):
        self.assertEqual(payload_sha256({"a": 1, "b": 2}), payload_sha256({"b": 2, "a": 1}))

    def test_legacy_low_score_requires_reanalysis_not_critical(self):
        status, _, rule = evaluate_triage({"overall_score": 2.5, "flags": {}})

        self.assertEqual(status, "requires_reanalysis")
        self.assertEqual(rule, "")

    def test_current_low_score_is_normal_not_critical(self):
        status, _, rule = evaluate_triage({"overall_score": 2.5, "overall_score_method": "applicable_rubric_v1", "flags": {}})

        self.assertEqual(status, "normal")
        self.assertEqual(rule, "")

    def test_low_confidence_current_score_requires_review(self):
        status, _, _ = evaluate_triage(
            {
                "overall_score": 5.0,
                "overall_score_method": "applicable_rubric_v1",
                "rubric_missing_codes": ["next_step"],
                "analysis_confidence": 0.35,
                "flags": {},
            }
        )

        self.assertEqual(status, "needs_review")

    def test_critical_requires_rule_quote_and_time(self):
        status, _, _ = evaluate_triage(
            {
                "overall_score": 2,
                "flags": {
                    "critical": True,
                    "critical_rule_id": "confirmed_rudeness",
                    "critical_evidence": {"time": "00:42", "quote": "Больше мне не звоните"},
                },
            }
        )

        self.assertEqual(status, "critical")

    def test_legacy_unproven_critical_requires_reanalysis_not_alert(self):
        status, _, _ = evaluate_triage(
            {
                "flags": {
                    "critical": True,
                    "critical_rule_id": "confirmed_rudeness",
                    "critical_evidence": {"time": "", "quote": ""},
                }
            }
        )

        self.assertEqual(status, "requires_reanalysis")

    def test_excluded_call_remains_excluded_when_read_back(self):
        status, reason, rule = evaluate_triage(
            {"review_status": "excluded", "exclusion_reason": "Короткий звонок"}
        )

        self.assertEqual(status, "excluded")
        self.assertEqual(reason, "Короткий звонок")
        self.assertEqual(rule, "")

    def test_timecode_and_private_json_projection_are_deterministic(self):
        self.assertEqual(JarvisStore._timecode_seconds("02:05"), 125)
        self.assertIsNone(JarvisStore._timecode_seconds("unknown"))
        self.assertEqual(JarvisRepository._json_object('{"crm":{"owner_id":"42"}}'), {"crm": {"owner_id": "42"}})
        self.assertEqual(JarvisRepository._json_object("[]"), {})

    def test_score_uses_only_criteria_that_fit_call_type(self):
        score = compute_applicable_score(
            "payment_push",
            [
                {"code": "opening", "applicable": True, "score": 8},
                {"code": "need", "applicable": True, "score": 8},
                {"code": "expertise", "applicable": True, "score": 8},
                {"code": "next_step", "applicable": True, "score": 8},
                {"code": "objection", "applicable": True, "score": 6},
                {"code": "closing", "applicable": True, "score": 8},
                {"code": "communication", "applicable": True, "score": 8},
                {"code": "presentation", "applicable": True, "score": 0},
            ],
        )

        # Presentation is not a required criterion for a payment follow-up.
        self.assertEqual(score, 7.6)

    def test_missing_applicable_criterion_is_not_silently_reweighted(self):
        self.assertIsNone(compute_applicable_score("payment_push", [{"code": "next_step", "applicable": True, "score": 10}]))

    def test_unconfirmed_call_type_still_has_a_factual_score(self):
        score = compute_applicable_score(
            "unknown",
            [
                {"code": "expertise", "applicable": True, "score": 8},
                {"code": "next_step", "applicable": True, "score": 6},
                {"code": "communication", "applicable": True, "score": 10},
            ],
        )

        self.assertEqual(score, 7.2)

    def test_overall_score_is_bounded_to_the_requested_one_to_ten_scale(self):
        score = compute_applicable_score(
            "unknown",
            [
                {"code": "expertise", "applicable": True, "score": 0},
                {"code": "next_step", "applicable": True, "score": 0},
                {"code": "communication", "applicable": True, "score": 0},
            ],
        )

        self.assertEqual(score, 1.0)

    def test_legacy_invalid_critical_timecode_requires_reanalysis(self):
        status, _, _ = evaluate_triage({"flags": {"critical": True, "critical_rule_id": "confirmed_rudeness", "critical_evidence": {"time": "later", "quote": "Больше мне не звоните"}}})
        self.assertEqual(status, "requires_reanalysis")

    def test_legacy_timecode_over_recording_requires_reanalysis(self):
        status, _, _ = evaluate_triage({"source_duration_seconds": 30, "flags": {"critical": True, "critical_rule_id": "confirmed_rudeness", "critical_evidence": {"time": "01:00", "quote": "Больше мне не звоните"}}})
        self.assertEqual(status, "requires_reanalysis")

    def test_today_reanalysis_is_limited_to_source_date(self):
        ids, date = reanalysis_scope({"REANALYZE_TODAY": "1"}, today="2026-09-08")

        self.assertEqual(ids, set())
        self.assertTrue(is_reanalysis_target({"activity_id": "1", "created": "2026-09-08T11:00:00+03:00"}, ids, date))
        self.assertFalse(is_reanalysis_target({"activity_id": "2", "created": "2026-09-07T11:00:00+03:00"}, ids, date))

    def test_one_reanalysis_id_does_not_widen_to_other_calls(self):
        ids, date = reanalysis_scope({"REANALYZE_ID": "100"})

        self.assertTrue(is_reanalysis_target({"activity_id": "100", "created": "2026-09-07T11:00:00+03:00"}, ids, date))
        self.assertFalse(is_reanalysis_target({"activity_id": "101", "created": "2026-09-07T11:00:00+03:00"}, ids, date))


if __name__ == "__main__":
    unittest.main()
