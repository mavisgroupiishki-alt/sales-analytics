import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bitrix import download_audio  # noqa: E402


class BitrixAudioDownloadTests(unittest.TestCase):
    @patch("bitrix.requests.post")
    def test_uses_private_audio_proxy_when_disk_permission_is_missing(self, post):
        client = Mock()
        client.call.side_effect = RuntimeError("ACCESS_DENIED")
        client.file_proxy_url = "https://dashboard.example/internal/bitrix-audio"
        client.webhook = ""
        client.headers = {"x-jarvis-sync-secret": "secret"}
        response = Mock()
        response.status_code = 200
        response.headers = {"Content-Length": "10"}
        response.iter_content.return_value = [b"0123456789"]
        post.return_value = response
        file_url = "https://example.bitrix24.by/bitrix/tools/crm_show_file.php?fileId=42&auth="

        with tempfile.TemporaryDirectory() as directory:
            path = download_audio(client, 42, Path(directory), file_url, "101")
            self.assertEqual(path.read_bytes(), b"0123456789")

        post.assert_called_once_with(
            client.file_proxy_url,
            json={"file_url": file_url, "activity_id": "101"},
            headers=client.headers,
            timeout=60,
            stream=True,
        )

    @patch("bitrix.requests.get")
    def test_direct_mode_rejects_external_url_without_leaking_webhook(self, get):
        client = Mock()
        client.call.side_effect = RuntimeError("ACCESS_DENIED")
        client.file_proxy_url = ""
        client.webhook = "https://example.bitrix24.by/rest/1/private-token/"
        client.headers = {}

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError) as raised:
                download_audio(client, 42, Path(directory), "https://evil.example/audio.mp3", "101")

        self.assertNotIn("private-token", str(raised.exception))
        get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
