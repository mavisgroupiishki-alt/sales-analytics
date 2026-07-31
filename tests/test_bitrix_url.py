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


if __name__ == "__main__":
    unittest.main()
