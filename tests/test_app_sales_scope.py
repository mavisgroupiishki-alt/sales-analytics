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
                {"activity_id": "olga", "manager": {"id": 2198, "name": "Ольга Панькова"}},
                {"activity_id": "expert", "manager": {"id": 2192, "name": "Екатерина Николаева"}},
            ],
            {},
        )

        calls, _ = jarvis_app.get_data()

        self.assertEqual([call["activity_id"] for call in calls], ["roman", "irina"])

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


if __name__ == "__main__":
    unittest.main()
