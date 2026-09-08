import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bitrix import enrich_with_deal_info  # noqa: E402


class BitrixStageLabelTests(unittest.TestCase):
    def test_portal_stage_names_replace_technical_ids(self):
        class Client:
            def call(self, method, params):
                if method == "crm.deal.list":
                    return {"result": [{"ID": "10", "STAGE_ID": "C28:WON"}, {"ID": "11", "STAGE_ID": "UC_NSAZEE"}]}
                if method == "crm.activity.list":
                    return {"result": []}
                raise AssertionError(method)

            def call_all(self, method, params):
                self.assert_status_method(method)
                entity = params["filter"]["ENTITY_ID"]
                return {
                    "DEAL_STAGE": [{"STATUS_ID": "UC_NSAZEE", "NAME": "Согласование договора"}],
                    "DEAL_STAGE_28": [{"STATUS_ID": "C28:WON", "NAME": "Сделка успешно завершена"}],
                }.get(entity, [])

            @staticmethod
            def assert_status_method(method):
                if method != "crm.status.list":
                    raise AssertionError(method)

        calls = [
            {"crm": {"owner_type": "deal", "owner_id": "10"}},
            {"crm": {"owner_type": "deal", "owner_id": "11"}},
        ]

        enriched = enrich_with_deal_info(Client(), calls)

        self.assertEqual(enriched[0]["crm"]["stage_name"], "Сделка успешно завершена")
        self.assertEqual(enriched[1]["crm"]["stage_name"], "Согласование договора")


if __name__ == "__main__":
    unittest.main()
