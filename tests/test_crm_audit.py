import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from crm_audit import build_crm_audit_snapshot  # noqa: E402


class _AuditClient:
    portal_url = "https://mavisgroup.bitrix24.by"

    def __init__(self, _webhook):
        pass

    def call(self, method, _payload=None):
        raise AssertionError(f"Unexpected method: {method}")

    def open_deals(self):
        return [
            {"id": "38946", "categoryId": "0", "assignedById": "10", "sourceId": "", "contactId": "", "companyId": "", "lastCommunicationTime": ""},
            {"id": "43", "categoryId": "30", "assignedById": "11", "sourceId": "web", "contactId": "5", "companyId": "", "lastCommunicationTime": "2026-09-10T08:00:00+03:00"},
        ]

    def paged_leads(self, status_id):
        return [{"ID": "50", "CONTACT_ID": "", "COMPANY_ID": ""}] if status_id == "NEW" else []

class CrmAuditTests(unittest.TestCase):
    @patch("crm_audit.BitrixAuditClient", _AuditClient)
    def test_snapshot_matches_daily_audit_metrics_and_card_rows(self):
        snapshot = build_crm_audit_snapshot("https://mavisgroup.bitrix24.by/rest/1/token/")

        self.assertEqual(snapshot["summary"]["activeDeals"], 2)
        self.assertEqual(snapshot["summary"]["missingSource"], 1)
        self.assertEqual(snapshot["summary"]["missingLastCommunication"], 1)
        self.assertEqual(snapshot["summary"]["stalledFunnelDeals"], 1)
        self.assertEqual(snapshot["summary"]["inactiveOwner"], 1)
        self.assertEqual(snapshot["summary"]["openLeadsMissingClient"], 1)
        self.assertEqual(snapshot["details"][0]["priority"], "Критичный")
        self.assertEqual(snapshot["details"][0]["url"], "https://mavisgroup.bitrix24.by/crm/deal/details/38946/")


if __name__ == "__main__":
    unittest.main()
