import sys
import unittest
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bitrix_health import BitrixHealthClient, BitrixHealthError, build_health_snapshots  # noqa: E402


class _Response:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status
        self.status_code = status

    def raise_for_status(self):
        if self.status >= 400:
            error = requests.HTTPError(f"HTTP {self.status}")
            error.response = self
            raise error

    def json(self):
        return self.payload


class _Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, json, timeout):
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        return self.responses.pop(0)


class BitrixHealthClientTests(unittest.TestCase):
    def test_paginates_item_list(self):
        session = _Session(
            [
                _Response({"result": {"items": [{"id": "1"}]}, "next": 50}),
                _Response({"result": {"items": [{"id": "2"}]}}),
            ]
        )
        client = BitrixHealthClient("https://example.bitrix24.by/rest/1/token/", session=session)

        items = list(client.iter_items("crm.item.list", {"entityTypeId": 2}))

        self.assertEqual([item["id"] for item in items], ["1", "2"])
        self.assertEqual([call["json"]["start"] for call in session.calls], [0, 50])

    def test_retries_only_temporary_error(self):
        session = _Session([_Response({}, 429), _Response({"result": {"categories": []}})])
        pauses = []
        client = BitrixHealthClient(
            "https://example.bitrix24.by/rest/1/token/", session=session, sleep=pauses.append
        )

        self.assertEqual(client.list_categories(), [])
        self.assertEqual(pauses, [1])
        self.assertEqual(len(session.calls), 2)

    def test_refuses_write_method(self):
        client = BitrixHealthClient("https://example.bitrix24.by/rest/1/token/", session=_Session([]))

        with self.assertRaises(BitrixHealthError):
            client.call("crm.item.update", {"entityTypeId": 2})

    def test_normalizes_boolean_active_user_from_live_api_shape(self):
        session = _Session([_Response({"result": [{"ID": "2100", "ACTIVE": True}]})])
        client = BitrixHealthClient("https://example.bitrix24.by/rest/1/token/", session=session)

        self.assertEqual(client.user_activity(["2100"]), {"2100": True})

    def test_builds_normalized_snapshot_without_title_or_comment(self):
        class _Client:
            def list_categories(self):
                return [{"id": 0, "name": "1. Продажи"}]

            def list_stages(self, category_id):
                if category_id != 0:
                    raise AssertionError("Expected the main sales category")
                return [{"STATUS_ID": "PREPARATION"}]

            def list_active_main_sales_deals(self):
                return [
                    {
                        "id": "77",
                        "categoryId": 0,
                        "stageId": "PREPARATION",
                        "assignedById": "2100",
                        "companyId": "3",
                        "sourceId": "WEB",
                        "ufCrm_1756973967704": "new",
                        "ufCrm_1765113071": "ISO",
                        "movedTime": "2026-09-08T10:00:00+00:00",
                        "lastCommunicationTime": "2026-09-08T11:00:00+00:00",
                        "title": "must never be copied",
                    }
                ]

            def list_open_activities(self, deal_ids):
                if list(deal_ids) != ["77"]:
                    raise AssertionError("Expected one normalised deal id")
                return [{"OWNER_ID": "77", "DEADLINE": "2026-09-09T10:00:00+00:00", "TYPE_ID": "6"}]

            def user_activity(self, user_ids):
                if list(user_ids) != ["2100"]:
                    raise AssertionError("Expected one responsible user id")
                return {"2100": True}

        snapshots, metadata = build_health_snapshots(_Client())

        self.assertEqual(metadata["active_deals"], 1)
        self.assertEqual(snapshots[0]["product_or_service"], "ISO")
        self.assertEqual(snapshots[0]["open_activities"][0]["due_at"], "2026-09-09T10:00:00+00:00")
        self.assertNotIn("title", snapshots[0])


if __name__ == "__main__":
    unittest.main()
