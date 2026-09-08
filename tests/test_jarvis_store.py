import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claude_analyzer import evaluate_triage, is_reanalysis_target, reanalysis_scope  # noqa: E402
from jarvis_store import normalize_bitrix_call, payload_sha256  # noqa: E402


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
