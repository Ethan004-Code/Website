"""Personal-impact privacy: real handoff state machines, isolated in memory."""
import copy
import unittest

from flask import g
from werkzeug.exceptions import Unauthorized

import test_rules as fixtures


class PersonalImpactTests(unittest.TestCase):
    setUp = fixtures.RuleTests.setUp
    tearDown = fixtures.RuleTests.tearDown
    invoke = fixtures.RuleTests.invoke
    offer = fixtures.RuleTests.offer
    accept_both = fixtures.RuleTests.accept_both

    def impact(self, uid):
        return self.invoke("get_impact", uid=uid).get_json()

    def complete_trade(self):
        row = self.offer()
        self.accept_both(row)
        self.invoke("complete_offer", uid="a", offer_id=row["id"])
        self.invoke("complete_offer", uid="b", offer_id=row["id"])
        return row

    def complete_giveaway(self):
        self.a["mode"] = "giveaway"
        self.invoke("request_giveaway", uid="b", listing_id=self.a["id"],
                    data={key: self.a[key] for key in ("location", "pickup_start", "pickup_end")})
        row = g.store["food_requests"][-1]
        self.invoke("decide_giveaway", request_id=row["id"], data={"accept": True})
        self.invoke("confirm_giveaway_pickup", uid="a", request_id=row["id"])
        self.invoke("confirm_giveaway_pickup", uid="b", request_id=row["id"])
        return row

    def test_completed_trade_counts_for_both_participants_not_third_party(self):
        self.complete_trade()
        for uid in ("a", "b"):
            result = self.impact(uid)
            self.assertEqual(result["completed_trades"], 1)
            self.assertEqual({x["name"] for x in result["quantities"]}, {"Onions", "Rice"})
        outsider = self.impact("c")
        self.assertEqual(outsider["completed_trades"], 0)
        self.assertEqual(outsider["records"], [])
        self.assertEqual(outsider["quantities"], [])

    def test_completed_giveaway_counts_for_giver_and_receiver(self):
        self.complete_giveaway()
        for uid in ("a", "b"):
            self.assertEqual(self.impact(uid)["completed_giveaways"], 1)
        self.assertEqual(self.impact("c")["completed_giveaways"], 0)
        self.assertEqual(self.impact("c")["records"], [])

    def test_pending_and_partial_confirmation_do_not_count_even_with_ledger_row(self):
        row = self.complete_trade()
        for state in ("pending", "confirmed", "completion_pending", "declined"):
            row["status"] = state
            self.assertEqual(self.impact("a")["records"], [])
        row["status"], row["completed_by"] = "completed", ["a"]
        self.assertEqual(self.impact("a")["records"], [])

    def test_only_own_external_reports_are_returned(self):
        g.store["impact_records"] = [
            dict(id=f"{uid}-{action}", user_id=uid, listing_id=f"food-{uid}-{action}",
                 action=action, identity=f"report-{uid}-{action}", quantity=3,
                 unit="each", item_name=f"{uid} food", confirmation_type="user_reported")
            for uid in ("a", "b") for action in ("donation", "compost")
        ]
        for uid in ("a", "b"):
            result = self.impact(uid)
            self.assertEqual(result["donations_reported"], 1)
            self.assertEqual(result["compost_reported"], 1)
            self.assertEqual({r["item_name"] for r in result["records"]}, {f"{uid} food"})
        self.assertEqual(self.impact("c")["records"], [])

    def test_test_and_unconfirmed_external_records_excluded(self):
        g.store["impact_records"] = [
            dict(id="test", user_id="a", listing_id="test-food", action="donation",
                 identity="test-report", quantity=3, unit="each", item_name="rice",
                 confirmation_type="user_reported", is_test=True),
            dict(id="suggestion", user_id="a", listing_id="suggestion-food", action="compost",
                 identity="suggestion", quantity=3, unit="each", item_name="onions",
                 confirmation_type="suggestion"),
        ]
        self.assertEqual(self.impact("a")["records"], [])

    def test_anonymous_impact_rejected(self):
        g.user = None
        with self.assertRaises(Unauthorized):
            self.app.view_functions["get_impact"]()

    def test_user_id_query_parameter_cannot_change_scope(self):
        self.complete_trade()
        g.user = {"id": "c", "name": "Third party"}
        with self.app.test_request_context("/api/impact?user_id=a&mine=false"):
            result = self.app.view_functions["get_impact"]().get_json()
        self.assertEqual(result["records"], [])

    def test_unrelated_ledger_rows_cannot_borrow_an_included_trade_id(self):
        self.complete_trade()
        bad = copy.deepcopy(g.store["impact_records"][0])
        bad.update(id="unrelated", listing_id="unrelated-listing", item_name="Private food")
        g.store["impact_records"].append(bad)
        self.assertNotIn("Private food", {r["item_name"] for r in self.impact("a")["records"]})


if __name__ == "__main__":
    unittest.main()
