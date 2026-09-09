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
        dashboard_app._AUDIO_CACHE.clear()
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

    @patch("requests.get")
    @patch("requests.post")
    def test_audio_proxy_validates_signs_and_returns_media(self, post, get):
        activity = Mock()
        activity.json.return_value = {"result": {"FILES": [{"id": "42"}]}}
        activity.raise_for_status.return_value = None
        post.return_value = activity
        upstream = Mock()
        upstream.headers = {"Content-Type": "audio/mpeg", "Content-Length": "5"}
        upstream.iter_content.return_value = [b"audio"]
        upstream.raise_for_status.return_value = None
        get.return_value = upstream

        response = self.client.post(
            "/internal/bitrix-audio",
            json={"file_url": "https://example.bitrix24.by/bitrix/tools/crm_show_file.php?fileId=42&ownerTypeId=6&ownerId=7&auth=", "activity_id": "101"},
            headers={"x-jarvis-sync-secret": "test-bridge-secret"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"audio")
        self.assertIn("auth=token", get.call_args.args[0])
        self.assertFalse(get.call_args.kwargs["allow_redirects"])

    @patch("requests.get")
    def test_audio_proxy_rejects_external_url_without_fetching(self, get):
        response = self.client.post(
            "/internal/bitrix-audio",
            json={"file_url": "https://evil.example/audio.mp3?fileId=42", "activity_id": "101"},
            headers={"x-jarvis-sync-secret": "test-bridge-secret"},
        )

        self.assertEqual(response.status_code, 400)
        get.assert_not_called()

    @patch("requests.get")
    @patch("requests.post")
    def test_audio_proxy_rejects_file_not_attached_to_activity(self, post, get):
        activity = Mock()
        activity.json.return_value = {"result": {"FILES": [{"id": "99"}]}}
        activity.raise_for_status.return_value = None
        post.return_value = activity

        response = self.client.post(
            "/internal/bitrix-audio",
            json={"file_url": "https://example.bitrix24.by/bitrix/tools/crm_show_file.php?fileId=42&ownerTypeId=6&ownerId=7&auth=", "activity_id": "101"},
            headers={"x-jarvis-sync-secret": "test-bridge-secret"},
        )

        self.assertEqual(response.status_code, 403)
        get.assert_not_called()

    @patch("requests.get")
    @patch.object(dashboard_app, "load_calls")
    def test_user_audio_route_rejects_external_stored_url(self, load_calls, get):
        load_calls.return_value = [{
            "activity_id": "101",
            "manager": {"id": 1286},
            "audio": {"url": "https://evil.example/audio.mp3?fileId=42"},
        }]
        with self.client.session_transaction() as session:
            session.update({"username": "rop", "role": "rop", "name": "РОП"})

        response = self.client.get("/audio/101")

        self.assertEqual(response.status_code, 404)
        get.assert_not_called()

    @patch("requests.get")
    @patch.object(dashboard_app, "load_calls")
    def test_user_audio_route_disables_redirects_and_streams_media(self, load_calls, get):
        load_calls.return_value = [{
            "activity_id": "101",
            "manager": {"id": 1286},
            "audio": {
                "url": "https://example.bitrix24.by/bitrix/tools/crm_show_file.php?fileId=42&ownerTypeId=6&ownerId=7&auth="
            },
        }]
        upstream = Mock()
        upstream.headers = {"Content-Type": "audio/mpeg"}
        upstream.iter_content.return_value = [b"audio"]
        upstream.raise_for_status.return_value = None
        get.return_value = upstream
        with self.client.session_transaction() as session:
            session.update({"username": "rop", "role": "rop", "name": "РОП"})

        response = self.client.get("/audio/101")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"audio")
        self.assertFalse(get.call_args.kwargs["allow_redirects"])
        self.assertTrue(get.call_args.kwargs["stream"])
