import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claude_analyzer import compute_applicable_score, evaluate_triage, is_reanalysis_target, reanalysis_scope  # noqa: E402
from jarvis_store import JarvisRepository, JarvisStore, normalize_bitrix_call, payload_sha256  # noqa: E402


class JarvisStoreTests(unittest.TestCase):
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

    def test_payload_hash_is_stable_for_equivalent_dicts(self):
        self.assertEqual(payload_sha256({"a": 1, "b": 2}), payload_sha256({"b": 2, "a": 1}))

    def test_low_score_is_review_not_critical(self):
        status, _, rule = evaluate_triage({"overall_score": 2.5, "flags": {}})

        self.assertEqual(status, "needs_review")
        self.assertEqual(rule, "")

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

    def test_unproven_critical_flag_is_review_not_alert(self):
        status, _, _ = evaluate_triage(
            {
                "flags": {
                    "critical": True,
                    "critical_rule_id": "confirmed_rudeness",
                    "critical_evidence": {"time": "", "quote": ""},
                }
            }
        )

        self.assertEqual(status, "needs_review")

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

    def test_invalid_critical_timecode_is_review_not_alert(self):
        status, _, _ = evaluate_triage({"flags": {"critical": True, "critical_rule_id": "confirmed_rudeness", "critical_evidence": {"time": "later", "quote": "Больше мне не звоните"}}})
        self.assertEqual(status, "needs_review")

    def test_critical_timecode_cannot_exceed_source_recording(self):
        status, _, _ = evaluate_triage({"source_duration_seconds": 30, "flags": {"critical": True, "critical_rule_id": "confirmed_rudeness", "critical_evidence": {"time": "01:00", "quote": "Больше мне не звоните"}}})
        self.assertEqual(status, "needs_review")

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
