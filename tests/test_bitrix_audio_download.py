import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bitrix import NonAudioFileError, download_audio, should_skip_audio_download  # noqa: E402


class BitrixAudioDownloadTests(unittest.TestCase):
    @patch("bitrix.requests.get")
    def test_rejects_html_disguised_as_mp3_before_transcription(self, get):
        client = Mock()
        client.call.return_value = {"result": {"DOWNLOAD_URL": "https://example.bitrix24.by/download/42", "NAME": "call.mp3"}}
        client.file_proxy_url = ""
        client.webhook = ""
        client.headers = {}
        response = Mock()
        response.status_code = 200
        response.headers = {"Content-Type": "audio/mpeg", "Content-Length": "10000"}
        response.iter_content.return_value = [b"<html><body>access denied</body></html>" + b" " * 9_960]
        get.return_value = response

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(NonAudioFileError):
                download_audio(client, 42, Path(directory))
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_skips_previously_rejected_non_audio_recording(self):
        self.assertTrue(should_skip_audio_download({"audio": {"status": "invalid", "error": "non_audio_file"}}))
        self.assertTrue(should_skip_audio_download({"audio": {"status": "empty", "error": "empty_recording"}}))
        self.assertFalse(should_skip_audio_download({"audio": {"status": "error", "error": "timeout"}}))

    @patch("bitrix.requests.post")
    def test_uses_private_audio_proxy_when_disk_permission_is_missing(self, post):
        client = Mock()
        client.call.side_effect = RuntimeError("ACCESS_DENIED")
        client.file_proxy_url = "https://dashboard.example/internal/bitrix-audio"
        client.webhook = ""
        client.headers = {"x-jarvis-sync-secret": "secret"}
        response = Mock()
        response.status_code = 200
        response.headers = {"Content-Length": "10", "Content-Type": "audio/mpeg"}
        response.iter_content.return_value = [b"ID3audio..."]
        post.return_value = response
        file_url = "https://example.bitrix24.by/bitrix/tools/crm_show_file.php?fileId=42&auth="

        with tempfile.TemporaryDirectory() as directory:
            path = download_audio(client, 42, Path(directory), file_url, "101")
            self.assertEqual(path.read_bytes(), b"ID3audio...")

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
