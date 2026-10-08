import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claude_analyzer import compute_applicable_score, evaluate_triage, is_reanalysis_target, reanalysis_scope  # noqa: E402
from jarvis_store import (  # noqa: E402
    JarvisRepository,
    JarvisStore,
    NormalizedCall,
    context_fact_from_analysis,
    context_scopes_for_call,
    normalize_bitrix_call,
    payload_sha256,
)


class JarvisStoreTests(unittest.TestCase):
    def test_context_scopes_are_explicit_and_partitioned_by_funnel(self):
        scopes = context_scopes_for_call(
            {
                "crm": {
                    "owner_type": "deal", "owner_id": "42", "company_id": "5",
                    "contact_ids": ["9", "9"], "category_id": "0",
                },
                "client": {"contact_id": "11"},
            }
        )

        self.assertEqual(scopes, [("deal", "42", "0"), ("company", "5", "0"), ("contact", "9", "0"), ("contact", "11", "0")])

    def test_context_scopes_do_not_share_an_unclassified_company_between_funnels(self):
        scopes = context_scopes_for_call(
            {
                "crm": {"owner_type": "contact", "owner_id": "9", "company_id": "5"},
                "client": {"contact_id": "9"},
            }
        )

        self.assertEqual(scopes, [])

    def test_context_fact_keeps_compact_call_outcome_not_transcript(self):
        fact = context_fact_from_analysis(
            {"activity_id": "100", "created": "2026-10-05T12:00:00+03:00"},
            {
                "call_type": {"key": "payment_push", "label": "Дожим клиента"},
                "summary": "Обсудили оплату",
                "outcome": "Клиент вернётся с ответом",
                "recommended_action": "Перезвонить в пятницу",
                "key_moments": [{"type": "negative", "text": "Нет точной даты"}],
                "transcript": "Полный текст никогда не должен попадать в факт",
            },
        )

        self.assertEqual(fact["activity_id"], "100")
        self.assertEqual(fact["call_type_key"], "payment_push")
        self.assertEqual(fact["objections"], ["Нет точной даты"])
        self.assertNotIn("transcript", fact)

    def test_context_fact_has_hard_bounds_for_prompt_safe_memory(self):
        fact = context_fact_from_analysis(
            {"activity_id": "100", "created": "2026-10-05T12:00:00+03:00"},
            {
                "call_type": {"key": "k" * 120, "label": "l" * 200},
                "summary": "s" * 700,
                "outcome": "o" * 500,
                "recommended_action": "n" * 500,
                "key_moments": [{"type": "negative", "text": "x" * 300}] * 4,
            },
        )

        self.assertEqual(len(fact["call_type_key"]), 80)
        self.assertEqual(len(fact["call_type"]), 160)
        self.assertEqual(len(fact["summary"]), 600)
        self.assertEqual(len(fact["outcome"]), 400)
        self.assertEqual(len(fact["next_step"]), 400)
        self.assertEqual(len(fact["objections"]), 3)
        self.assertTrue(all(len(item) == 240 for item in fact["objections"]))

    def test_context_backfill_uses_the_same_compact_fact_limits(self):
        migration = (Path(__file__).resolve().parents[1] / "migrations" / "006_company_context_memory.sql").read_text(encoding="utf-8")

        self.assertIn("left(coalesce(result ->> 'summary', ''), 600)", migration)
        self.assertIn("left(coalesce(result ->> 'outcome', ''), 400)", migration)
        self.assertIn("left(coalesce(moment.value ->> 'text', moment.value ->> 'detail'), 240)", migration)
        self.assertIn("order by moment.ordinal", migration)
        self.assertIn("limit 3", migration)

    def test_excluded_call_does_not_write_company_context_fact(self):
        class Cursor:
            def __init__(self):
                self.statements = []

            def execute(self, statement, _params):
                self.statements.append(statement)

            def fetchall(self):
                return []

        cursor = Cursor()
        JarvisStore._write_context_facts(
            object.__new__(JarvisStore),
            cursor,
            call_id=7,
            analysis_id=8,
            call={"activity_id": "100", "created": "2026-10-05T12:00:00+03:00", "crm": {"company_id": "5"}},
            analysis={"summary": "Короткий звонок"},
            status="excluded",
        )
        self.assertEqual(len(cursor.statements), 1)
        self.assertIn("delete from jarvis.context_facts", cursor.statements[0].lower())

    def test_reanalysis_to_excluded_removes_its_previous_context_fact(self):
        class Cursor:
            def __init__(self):
                self.statements = []

            def execute(self, statement, params):
                self.statements.append((statement, params))

            def fetchall(self):
                return [{"scope_type": "company", "scope_external_id": "5", "funnel_id": "0"}]

        cursor = Cursor()
        JarvisStore._write_context_facts(
            object.__new__(JarvisStore),
            cursor,
            call_id=7,
            analysis_id=8,
            call={"activity_id": "100", "created": "2026-10-05T12:00:00+03:00"},
            analysis={"summary": "Короткий технический звонок"},
            status="excluded",
        )

        self.assertEqual(len(cursor.statements), 3)
        self.assertIn("delete from jarvis.context_facts", cursor.statements[0][0].lower())
        self.assertIn("jarvis.context_profiles", cursor.statements[1][0].lower())

    def test_marking_manual_view_does_not_update_context_call_type(self):
        class Cursor:
            def __init__(self):
                self.results = [{"id": 7}, {"reviewed": True, "reviewer_name": "РОП", "reviewed_at": None}]
                self.statements = []

            def execute(self, statement, params):
                self.statements.append(statement)

            def fetchone(self):
                return self.results.pop(0)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        class Connection:
            def __init__(self):
                self.cursor_instance = Cursor()

            def cursor(self):
                return self.cursor_instance

            def commit(self):
                pass

        connection = Connection()
        result = JarvisRepository(connection).save_call_manual_review("100", True, "РОП")

        self.assertTrue(result["reviewed"])
        self.assertEqual(len(connection.cursor_instance.statements), 2)

    def test_worker_checks_only_current_activity_ids_for_existing_analysis(self):
        class Cursor:
            def __init__(self):
                self.params = ()

            def execute(self, statement, params):
                self.params = params
                self.statement = statement

            def fetchall(self):
                return [{"source_call_id": "100"}]

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        class Connection:
            def __init__(self):
                self.cursor_instance = Cursor()

            def cursor(self):
                return self.cursor_instance

            def commit(self):
                pass

        connection = Connection()
        existing = JarvisRepository(connection).load_persisted_activity_ids(["101", "100", "100"])

        self.assertEqual(existing, {"100"})
        self.assertEqual(connection.cursor_instance.params, (["100", "101"],))
        self.assertIn("ca.status <> 'failed'", connection.cursor_instance.statement)

    def test_context_memory_reads_prior_company_facts_without_transcript(self):
        class Cursor:
            def __init__(self):
                self.results = [
                    [{
                        "call_id": 7,
                        "occurred_at": "2026-10-04T10:00:00+03:00",
                        "scope_type": "company", "scope_external_id": "5",
                        "fact": {"activity_id": "99", "summary": "Обсудили бюджет", "outcome": "Вернутся с ответом", "next_step": "Перезвонить", "objections": ["Нет бюджета"]},
                    }],
                    [{
                        "scope_type": "company", "scope_external_id": "5", "facts_count": 24,
                        "latest_call_at": "2026-10-04T10:00:00+03:00",
                        "current_state": {"last_outcome": "Вернутся с ответом", "next_step": "Перезвонить"},
                    }],
                ]
                self.statements = []

            def execute(self, statement, params):
                self.statements.append((statement, params))

            def fetchall(self):
                return self.results.pop(0)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        class Connection:
            def __init__(self):
                self.cursor_instance = Cursor()

            def cursor(self):
                return self.cursor_instance

            def commit(self):
                pass

        connection = Connection()
        context = JarvisRepository(connection).load_context_for_call(
            {"activity_id": "100", "created": "2026-10-05T12:00:00+03:00", "crm": {"company_id": "5", "category_id": "0"}}
        )

        self.assertEqual(context["memory"]["facts_count"], 24)
        self.assertEqual(context["previous_calls"][0]["activity_id"], "99")
        self.assertEqual(context["previous_calls"][0]["objections"], "Нет бюджета")
        self.assertIn("cf.occurred_at <", connection.cursor_instance.statements[0][0])
        self.assertNotIn("transcript", connection.cursor_instance.statements[0][0].lower())
        self.assertEqual(connection.cursor_instance.statements[0][1][-1], 8)

    def test_manual_type_review_refreshes_context_fact(self):
        class Cursor:
            def __init__(self):
                self.results = [{"id": 7}, {"call_type_key": "payment_push", "reason": "Верный тип", "reviewer_name": "РОП", "reviewed_at": None, "reanalysis_requested": False}]
                self.statements = []

            def execute(self, statement, params):
                self.statements.append(statement)

            def fetchone(self):
                return self.results.pop(0)

            def fetchall(self):
                return []

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        class Connection:
            def __init__(self):
                self.cursor_instance = Cursor()

            def cursor(self):
                return self.cursor_instance

            def commit(self):
                pass

        connection = Connection()
        JarvisRepository(connection).save_call_review("100", "payment_push", "Верный тип", "РОП")

        self.assertIn("update jarvis.context_facts", connection.cursor_instance.statements[2].lower())

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

    def test_crm_link_keeps_only_one_primary_link_per_call_and_entity_type(self):
        class Cursor:
            def __init__(self):
                self.statement = ""
                self.params = ()

            def execute(self, statement, params):
                self.statement = statement
                self.params = params

        cursor = Cursor()
        call = NormalizedCall(
            source_call_id="1",
            occurred_at="2026-09-08T10:00:00+03:00",
            direction="outgoing",
            duration_seconds=30,
            manager_external_id="1286",
            recording_available=True,
            audio_status="available",
            crm_owner_type="deal",
            crm_owner_id="42",
        )

        JarvisStore._upsert_crm_link(None, cursor, call_id=7, call=call)

        self.assertIn("not exists", cursor.statement.lower())
        self.assertIn("jarvis.call_links.is_primary or not exists", cursor.statement.lower())
        self.assertEqual(cursor.params, (7, "deal", "42", 7, "deal"))

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

    def test_rejected_non_audio_file_is_not_marked_available(self):
        normalized = normalize_bitrix_call(
            {
                "activity_id": "1",
                "created": "2026-09-18T10:00:00+03:00",
                "audio": {"file_id": 42, "status": "invalid", "error": "non_audio_file"},
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

    def test_narrow_call_does_not_turn_irrelevant_sales_stages_into_zeroes(self):
        score = compute_applicable_score(
            "payment_push",
            [
                {"code": "opening", "applicable": True, "score": 8},
                {"code": "need", "applicable": False},
                {"code": "expertise", "applicable": True, "score": 8},
                {"code": "objection", "applicable": False},
                {"code": "closing", "applicable": False},
                {"code": "next_step", "applicable": True, "score": 8},
                {"code": "communication", "applicable": True, "score": 8},
            ],
        )

        self.assertEqual(score, 8.0)

    def test_tone_alone_cannot_create_a_sales_score(self):
        score = compute_applicable_score(
            "unknown",
            [
                {"code": "expertise", "applicable": False},
                {"code": "next_step", "applicable": False},
                {"code": "communication", "applicable": True, "score": 10},
            ],
        )

        self.assertIsNone(score)

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
