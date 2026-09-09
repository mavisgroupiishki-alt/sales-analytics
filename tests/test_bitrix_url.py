import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bitrix_url import (  # noqa: E402
    add_webhook_auth_to_file_url,
    build_bitrix_method_url,
    normalize_bitrix_webhook_url,
    safe_webhook_label,
    validate_bitrix_download_url,
    validate_bitrix_file_url,
)


class BitrixUrlTests(unittest.TestCase):
    def test_strips_accidentally_copied_method(self):
        raw = "https://example.bitrix24.by/rest/2110/secret/profile.json"
        self.assertEqual(
            normalize_bitrix_webhook_url(raw),
            "https://example.bitrix24.by/rest/2110/secret",
        )

    def test_builds_method_without_double_method(self):
        raw = "https://example.bitrix24.by/rest/2110/secret/profile.json/"
        self.assertEqual(
            build_bitrix_method_url(raw, "disk.file.get"),
            "https://example.bitrix24.by/rest/2110/secret/disk.file.get",
        )

    def test_fills_empty_auth_parameter(self):
        file_url = "https://example.bitrix24.by/bitrix/tools/crm_show_file.php?fileId=42&auth="
        result = add_webhook_auth_to_file_url(
            file_url,
            "https://example.bitrix24.by/rest/2110/secret/profile.json",
        )
        self.assertEqual(parse_qs(urlsplit(result).query)["auth"], ["secret"])

    def test_does_not_overwrite_existing_auth(self):
        file_url = "https://example.bitrix24.by/file?auth=existing"
        result = add_webhook_auth_to_file_url(
            file_url,
            "https://example.bitrix24.by/rest/2110/secret",
        )
        self.assertEqual(parse_qs(urlsplit(result).query)["auth"], ["existing"])

    def test_safe_label_redacts_secret(self):
        label = safe_webhook_label("https://example.bitrix24.by/rest/2110/secret/profile.json")
        self.assertNotIn("secret", label)
        self.assertIn("***", label)

    def test_rejects_non_webhook_url(self):
        with self.assertRaises(ValueError):
            normalize_bitrix_webhook_url("https://example.com/profile.json")

    def test_validates_exact_bitrix_crm_file_url(self):
        file_url = "https://example.bitrix24.by/bitrix/tools/crm_show_file.php?fileId=42&ownerTypeId=6&ownerId=7&auth="
        self.assertEqual(
            validate_bitrix_file_url(file_url, "https://example.bitrix24.by/rest/1/secret"),
            file_url,
        )

    def test_validates_trusted_signed_rest_download_url_with_temporary_auth(self):
        file_url = "https://example.bitrix24.by/rest/download.json?auth=temporary-access-token&token=signed-file-token"
        self.assertEqual(
            validate_bitrix_download_url(file_url, "https://example.bitrix24.by/rest/1/secret/profile.json"),
            file_url,
        )

    def test_stored_url_validator_rejects_rest_download_url(self):
        with self.assertRaises(ValueError):
            validate_bitrix_file_url(
                "https://example.bitrix24.by/rest/download.json?auth=temporary-access-token&token=signed-file-token",
                "https://example.bitrix24.by/rest/1/secret",
            )

    def test_trusted_download_validator_rejects_external_or_malformed_url(self):
        webhook = "https://example.bitrix24.by/rest/1/secret"
        invalid_urls = (
            "https://evil.example/rest/download.json?auth=temporary&token=signed",
            "https://example.bitrix24.by/audio.mp3?auth=temporary&token=signed",
            "https://example.bitrix24.by/rest/download.json?auth=&token=signed",
            "https://example.bitrix24.by/rest/download.json?auth=temporary&token=",
            "https://example.bitrix24.by/rest/download.json?auth=temporary&token=signed&next=evil",
        )
        for file_url in invalid_urls:
            with self.subTest(file_url=file_url), self.assertRaises(ValueError):
                validate_bitrix_download_url(file_url, webhook)

    def test_rejects_external_or_unexpected_file_url(self):
        webhook = "https://example.bitrix24.by/rest/1/secret"
        with self.assertRaises(ValueError):
            validate_bitrix_file_url("https://evil.example/audio.mp3?fileId=42", webhook)
        with self.assertRaises(ValueError):
            validate_bitrix_file_url(
                "https://example.bitrix24.by/bitrix/tools/crm_show_file.php?fileId=42&next=https://evil.example",
                webhook,
            )


if __name__ == "__main__":
    unittest.main()
