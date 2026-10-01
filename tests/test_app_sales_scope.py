import unittest
from datetime import date, timedelta
from unittest.mock import patch

import app as jarvis_app


class SalesScopeTests(unittest.TestCase):
    def setUp(self):
        jarvis_app.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        self.client = jarvis_app.app.test_client()
        with self.client.session_transaction() as session:
            session.update({"username": "rop", "role": "rop", "name": "РОП"})

    @patch.object(jarvis_app, "load_snapshot")
    def test_all_application_data_excludes_experts_and_other_staff(self, load_snapshot):
        load_snapshot.return_value = (
            [
                {"activity_id": "roman", "manager": {"id": 1286, "name": "Роман Авсеенко"}},
                {"activity_id": "irina", "manager": {"id": 2100, "name": "Ирина Богомольцева"}},
                {"activity_id": "alena", "manager": {"id": 2272, "name": "Алена Хурсик"}},
                {"activity_id": "irina_new", "manager": {"id": 2274, "name": "Ирина Базылева"}},
                {"activity_id": "olga", "manager": {"id": 2198, "name": "Ольга Панькова"}},
                {"activity_id": "expert", "manager": {"id": 2192, "name": "Екатерина Николаева"}},
            ],
            {},
        )

        calls, _ = jarvis_app.get_data()

        self.assertEqual([call["activity_id"] for call in calls], ["roman", "irina", "alena", "irina_new"])

    def test_team_settings_are_available_to_rop(self):
        response = self.client.get("/team")

        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Настройка прослушки", html)
        self.assertIn("Алена Хурсик", html)
        self.assertIn("Ирина Базылева", html)

    @patch.object(jarvis_app, "get_data")
    def test_overview_period_changes_the_complete_dashboard_sample(self, get_data):
        today = date.today()
        yesterday = today - timedelta(days=1)
        get_data.return_value = (
            [
                {"activity_id": "today", "created": f"{today.isoformat()}T10:00:00+03:00", "client": {"name": "Клиент Сегодня"}, "manager": {"id": 1286, "name": "Роман Авсеенко"}},
                {"activity_id": "yesterday", "created": f"{yesterday.isoformat()}T10:00:00+03:00", "client": {"name": "Клиент Вчера"}, "manager": {"id": 2100, "name": "Ирина Богомольцева"}},
            ],
            {},
        )

        response = self.client.get("/?period=yesterday")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Клиент Вчера", html)
        self.assertNotIn("Клиент Сегодня", html)
        self.assertIn('aria-current="page">Вчера</a>', html)

    @patch.object(jarvis_app, "get_data")
    def test_daily_reports_show_only_the_selected_period(self, get_data):
        yesterday = date.today() - timedelta(days=1)
        get_data.return_value = (
            [
                {
                    "activity_id": "yesterday",
                    "created": f"{yesterday.isoformat()}T10:00:00+03:00",
                    "client": {"name": "Клиент Вчера"},
                    "manager": {"id": 1286, "name": "Роман Авсеенко"},
                },
            ],
            {"yesterday": {"analysis": {"overall_score": 5, "recommended_action": "Перезвонить клиенту", "flags": {}}}},
        )

        response = self.client.get("/daily-reports?period=yesterday")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Отчёты менеджеров", html)
        self.assertIn("Клиент Вчера", html)
        self.assertIn("Перезвонить клиенту", html)

    @patch.object(jarvis_app, "get_data")
    def test_calls_accept_a_custom_date_range(self, get_data):
        get_data.return_value = (
            [
                {"activity_id": "first", "created": "2026-09-19T10:00:00+03:00", "client": {"name": "Первый"}, "manager": {"id": 1286, "name": "Роман Авсеенко"}},
                {"activity_id": "second", "created": "2026-09-20T10:00:00+03:00", "client": {"name": "Второй"}, "manager": {"id": 1286, "name": "Роман Авсеенко"}},
                {"activity_id": "third", "created": "2026-09-21T10:00:00+03:00", "client": {"name": "Третий"}, "manager": {"id": 1286, "name": "Роман Авсеенко"}},
            ],
            {},
        )

        response = self.client.get("/calls?date_from=2026-09-20&date_to=2026-09-21")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Первый", html)
        self.assertIn("Второй", html)
        self.assertIn("Третий", html)
        self.assertIn('name="date_from" value="2026-09-20"', html)


if __name__ == "__main__":
    unittest.main()
