import json
import os
import unittest
from unittest.mock import Mock, patch

import app as dashboard_app


class FunnelProxyTests(unittest.TestCase):
    def setUp(self):
        self.previous = os.environ.get("BITRIX_PORTAL_URL")
        os.environ["BITRIX_PORTAL_URL"] = "https://mavisgroup.bitrix24.by"
        dashboard_app.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        self.client = dashboard_app.app.test_client()
        with self.client.session_transaction() as session:
            session.update({"username": "rop", "role": "rop", "name": "РОП"})

    def tearDown(self):
        if self.previous is None:
            os.environ.pop("BITRIX_PORTAL_URL", None)
        else:
            os.environ["BITRIX_PORTAL_URL"] = self.previous

    @patch("requests.get")
    def test_ignores_untrusted_drilldown_url_and_builds_bitrix_link(self, get):
        upstream = Mock()
        upstream.headers = {}
        upstream.raise_for_status.return_value = None
        upstream.iter_content.return_value = [json.dumps({
            "count": 1,
            "rows": [{
                "kind": "deal",
                "id": "17",
                "title": "ООО Бобик",
                "stage": "5. КП отправлено",
                "url": "javascript:alert(1)",
                "unexpected": "not returned",
            }],
        }).encode()]
        get.return_value = upstream

        response = self.client.get("/api/funnel-details?metric=deals")

        self.assertEqual(response.status_code, 200)
        row = response.get_json()["rows"][0]
        self.assertEqual(row["url"], "https://mavisgroup.bitrix24.by/crm/deal/details/17/")
        self.assertNotIn("unexpected", row)

    def test_rejects_unknown_metric(self):
        response = self.client.get("/api/funnel-details?metric=anything")
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
