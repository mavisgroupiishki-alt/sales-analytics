import os
import unittest
from unittest.mock import Mock, patch

import app as dashboard_app


class BitrixProxyTests(unittest.TestCase):
    def setUp(self):
        self.previous = {
            "JARVIS_SYNC_SECRET": os.environ.get("JARVIS_SYNC_SECRET"),
            "BITRIX_WEBHOOK_URL": os.environ.get("BITRIX_WEBHOOK_URL"),
        }
        os.environ["JARVIS_SYNC_SECRET"] = "test-bridge-secret"
        os.environ["BITRIX_WEBHOOK_URL"] = "https://example.bitrix24.by/rest/1/token"
        dashboard_app.app.config.update(TESTING=True)
        self.client = dashboard_app.app.test_client()

    def tearDown(self):
        for key, value in self.previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_proxy_rejects_missing_secret(self):
        response = self.client.post("/internal/bitrix/user.get", json={"ID": 1})
        self.assertEqual(response.status_code, 401)

    def test_proxy_rejects_method_outside_allowlist(self):
        response = self.client.post(
            "/internal/bitrix/im.message.add",
            json={},
            headers={"x-jarvis-sync-secret": "test-bridge-secret"},
        )
        self.assertEqual(response.status_code, 403)

    @patch("requests.post")
    def test_proxy_forwards_allowed_method(self, post):
        upstream = Mock()
        upstream.status_code = 200
        upstream.content = b'{"result":[]}'
        upstream.headers = {"Content-Type": "application/json"}
        post.return_value = upstream

        response = self.client.post(
            "/internal/bitrix/crm.activity.list",
            json={"filter": {"COMPLETED": "Y"}},
            headers={"x-jarvis-sync-secret": "test-bridge-secret"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"result": []})
        self.assertNotIn("x-jarvis-sync-secret", post.call_args.kwargs.get("headers", {}))
