import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sales_team import DEFAULT_SALES_TEAM, active_sales_team, ids, names  # noqa: E402


class SalesTeamTests(unittest.TestCase):
    def test_default_team_contains_the_new_sales_managers(self):
        self.assertEqual(ids(DEFAULT_SALES_TEAM), {1286, 2100, 2272, 2274})
        self.assertIn("алена хурсик", names(DEFAULT_SALES_TEAM))
        self.assertIn("ирина базылева", names(DEFAULT_SALES_TEAM))

    def test_missing_database_uses_the_safe_default_team(self):
        self.assertEqual(active_sales_team(""), list(DEFAULT_SALES_TEAM))


if __name__ == "__main__":
    unittest.main()
