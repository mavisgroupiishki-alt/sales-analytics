import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bitrix import ALLOWED_MANAGER_IDS, ALLOWED_MANAGERS, determine_real_manager_id  # noqa: E402


class BitrixManagerAllowlistTests(unittest.TestCase):
    def test_only_sales_managers_are_included(self):
        self.assertEqual(ALLOWED_MANAGER_IDS, [1286, 2100])
        self.assertEqual(ALLOWED_MANAGERS, ["Роман Авсеенко", "Ирина Богомольцева"])

    def test_real_manager_is_selected_when_responsible_is_different(self):
        activity = {
            "AUTHOR_ID": "2100",
            "CREATED_BY_ID": "2100",
            "RESPONSIBLE_ID": "1286",
        }

        self.assertEqual(determine_real_manager_id(activity), 2100)


if __name__ == "__main__":
    unittest.main()
