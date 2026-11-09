import copy
import unittest
from datetime import timedelta

from flask import g
from werkzeug.exceptions import Conflict, Forbidden, NotFound, Unauthorized

import rules
import test_rules


class CommunityRequestTests(unittest.TestCase):
    setUp = test_rules.RuleTests.setUp
    tearDown = test_rules.RuleTests.tearDown
    invoke = test_rules.RuleTests.invoke

    def wanted(self, owner="b", **changes):
        row = dict(id="wanted", user_id=owner, name="Bea", items=["Onions"],
                   quantity=None, unit="each", status="open", location=self.a["location"],
                   pickup_start=self.a["pickup_start"], pickup_end=self.a["pickup_end"],
                   deadline=self.a["deadline"], email="private@example.invalid",
                   natural_language_text="private original draft")
        row.update(changes)
        g.store["food_requests"].append(row)
        return row

    def options(self):
        return self.invoke("community_request_options", request_id="wanted").get_json()["options"]

    def respond(self, **data):
        return self.invoke("community_request_offer", request_id="wanted",
                           data={"listing_id": "one", **data})[0].get_json()

    def test_board_auth_private_fields_and_no_read_writes(self):
        self.wanted()
        self.wanted(owner="a", id="own")
        g.store["food_requests"].append(dict(id="private-handoff", listing_id="one", status="pending"))
        rows = self.invoke("community_requests").get_json()["requests"]
        self.assertEqual({r["id"] for r in rows}, {"wanted", "own"})
        self.assertNotIn("email", rows[0])
        self.assertNotIn("natural_language_text", rows[0])
        self.assertEqual(g.writes, {})
        g.user = None
        with self.assertRaises(Unauthorized):
            self.app.view_functions["community_requests"]()

    def test_board_hides_expired_invalid_inactive_and_blocked_requests(self):
        self.wanted()
        self.wanted(owner="c", id="blocked")
        self.wanted(owner="deleted", id="inactive")
        self.wanted(id="expired", deadline=(rules.now() - timedelta(seconds=1)).isoformat())
        self.wanted(id="bad-date", pickup_end="invalid")
        self.wanted(id="finished-window", pickup_end=(rules.now() - timedelta(seconds=1)).isoformat())
        g.store["user_blocks"].append(dict(user_id="c", blocked_user_id="a"))
        self.assertEqual([r["id"] for r in self.invoke("community_requests").get_json()["requests"]], ["wanted"])

    def test_private_get_requests_remains_scoped_and_enriches_giver_name(self):
        self.wanted(owner="b")
        self.a["mode"] = "giveaway"
        result = self.respond()["request"]
        own = self.invoke("get_requests").get_json()["requests"]
        self.assertEqual([r["id"] for r in own], [result["id"]])
        self.assertEqual(own[0]["owner_name"], self.a["name"])
        self.assertEqual(self.invoke("get_requests", uid="c").get_json()["requests"], [])

    def test_no_responding_to_own_or_blocked_or_expired_request(self):
        wanted = self.wanted(owner="a")
        with self.assertRaises(Forbidden):
            self.options()
        wanted["user_id"] = "b"
        g.store["user_blocks"].append(dict(user_id="b", blocked_user_id="a"))
        with self.assertRaises(NotFound):
            self.options()
        g.store["user_blocks"].clear()
        wanted["status"] = "expired"
        with self.assertRaises(NotFound):
            self.options()

    def test_options_enforce_quantity_food_spot_window_photo_review_and_ownership(self):
        wanted = self.wanted()
        self.a["mode"] = "giveaway"
        self.assertEqual(self.options()[0]["listing"]["id"], "one")
        for key, bad in (("quantity", 99), ("items", ["Apples"]), ("location", "Other steps")):
            old = wanted[key]
            wanted[key] = bad
            self.assertEqual(self.options(), [], key)
            wanted[key] = old
        start, end = wanted["pickup_start"], wanted["pickup_end"]
        wanted["pickup_start"] = self.a["pickup_end"]
        wanted["pickup_end"] = wanted["deadline"]
        self.assertEqual(self.options(), [])
        wanted["pickup_start"], wanted["pickup_end"] = start, end
        for key, bad in (("photo_id", None), ("bob_review_id", None), ("status", "reserved")):
            old = self.a[key]
            self.a[key] = bad
            self.assertEqual(self.options(), [], key)
            self.a[key] = old
        with self.assertRaises(Forbidden):
            self.respond(listing_id="two")

    def test_giveaway_offer_waits_for_receiver_then_both_confirm_exactly_once(self):
        self.wanted()
        self.a["mode"] = "giveaway"
        before = copy.deepcopy(self.a["item_details"])
        payload = self.respond()
        row = g.store["food_requests"][-1]
        self.assertEqual(payload["kind"], "giveaway")
        self.assertEqual(row["accepted_by"], ["a"])
        self.assertEqual(row["item_details"], before)
        self.assertEqual(row["status"], "pending")
        self.assertEqual(self.a["status"], "open")
        self.notifications.assert_called_with("b", "food_offer", unittest.mock.ANY, row["id"])
        self.assertIn(("food_requests", row["id"]), g.writes)
        # Read the same persisted shape, not an in-memory-only response field.
        row = copy.deepcopy(g.writes[("food_requests", row["id"])])
        g.store["food_requests"][-1] = row
        with self.assertRaises(Forbidden):
            self.invoke("decide_giveaway", request_id=row["id"], data={"accept": True})
        with self.assertRaises(Forbidden):
            self.invoke("decide_giveaway", uid="c", request_id=row["id"], data={"accept": True})
        self.invoke("decide_giveaway", uid="b", request_id=row["id"], data={"accept": True})
        self.assertEqual(row["status"], "approved")
        self.assertEqual(self.a["status"], "reserved")
        self.invoke("confirm_giveaway_pickup", request_id=row["id"])
        self.assertEqual(row["status"], "completion_pending")
        self.assertEqual(g.store["impact_records"], [])
        self.invoke("confirm_giveaway_pickup", uid="b", request_id=row["id"])
        self.assertEqual(row["status"], "completed")
        self.assertEqual(self.a["status"], "given_away")
        count = len(g.store["impact_records"])
        self.invoke("confirm_giveaway_pickup", uid="b", request_id=row["id"])
        self.assertEqual(len(g.store["impact_records"]), count)

    def test_duplicate_giveaway_offer_is_rejected_and_decline_allows_retry(self):
        self.wanted()
        self.a["mode"] = "giveaway"
        row = self.respond()["request"]
        self.assertEqual(self.options(), [])
        with self.assertRaises(Conflict):
            self.respond()
        self.invoke("decide_giveaway", uid="b", request_id=row["id"], data={"accept": False})
        self.assertEqual(self.a["status"], "open")
        self.assertEqual(len(self.options()), 1)

    def test_stale_listing_cannot_be_offered_or_accepted(self):
        wanted = self.wanted()
        self.a["mode"] = "giveaway"
        self.options()
        wanted["quantity"] = 99
        with self.assertRaises(Conflict):
            self.respond()
        wanted["quantity"] = None
        row = self.respond()["request"]
        self.a.update(status="reserved", reservation_id="different-handoff")
        with self.assertRaises(Conflict):
            self.invoke("decide_giveaway", uid="b", request_id=row["id"], data={"accept": True})
        self.assertEqual(row["status"], "pending")

    def test_changed_spot_or_window_requires_a_new_offer_before_acceptance(self):
        self.wanted()
        self.a["mode"] = "giveaway"
        row = self.respond()["request"]
        original = self.a["location"]
        self.a["location"] = "Changed public spot"
        with self.assertRaises(Conflict):
            self.invoke("decide_giveaway", uid="b", request_id=row["id"], data={"accept": True})
        self.a["location"] = original
        self.a["pickup_end"] = (rules.instant(row["pickup_end"]) - timedelta(minutes=1)).isoformat()
        with self.assertRaises(Conflict):
            self.invoke("decide_giveaway", uid="b", request_id=row["id"], data={"accept": True})
        self.assertEqual(self.a["status"], "open")
        self.assertEqual(row["accepted_by"], ["a"])

    def test_third_party_return_listing_cannot_be_substituted(self):
        self.wanted()
        stranger = test_rules.listing("third", "c", food="Rice", wants="Onions")
        g.store["listings"].append(stranger)
        self.assertEqual([r["id"] for r in self.options()[0]["return_listings"]], ["two"])
        with self.assertRaises(Conflict):
            self.respond(match_id="third")

    def test_swap_requires_compatible_return_listing_owned_by_requester(self):
        self.wanted()
        options = self.options()
        self.assertEqual([r["id"] for r in options[0]["return_listings"]], ["two"])
        with self.assertRaises(Conflict):
            self.respond()
        with self.assertRaises(Conflict):
            self.respond(match_id="one")
        self.b["wanted_food"] = "Apples"
        self.assertEqual(self.options(), [])
        with self.assertRaises(Conflict):
            self.respond(match_id="two")

    def test_request_swap_works_across_three_locations_and_mixed_capitalization(self):
        self.wanted(items=["ONIONS"], location="Student Union")
        self.a.update(location="Library steps", wanted_food="rICE")
        self.b.update(location="Community Center", wanted_food="oNIONS")
        self.assertEqual(len(self.options()), 1)
        self.respond(match_id="two", location="Park entrance")
        row = g.store["trade_offers"][-1]
        self.assertEqual(row["location"], "Park entrance")
        self.assertEqual(self.a["status"], "open")
        self.invoke("decide_offer", uid="b", offer_id=row["id"], data={"accept":True})
        self.assertEqual(row["status"], "confirmed")
        self.assertEqual(self.a["status"], "reserved")
        self.assertEqual(self.b["status"], "reserved")

    def test_request_swap_rejects_blank_and_overlong_proposed_spot(self):
        self.wanted()
        for location in ("", " ", "x" * 101, None):
            with self.assertRaises(rules.BadRequest):
                self.respond(match_id="two", location=location)
        self.assertEqual(g.store["trade_offers"], [])

    def test_request_swap_records_selected_amounts_and_reopens_leftovers_after_both_confirm(self):
        self.wanted(items=["Onions"])
        created = self.respond(match_id="two", location="Park entrance",
                               give_quantities=[2], receive_quantities=[3])
        row = g.store["trade_offers"][-1]
        self.assertEqual([item["quantity"] for item in row["give_items"]], [2])
        self.assertEqual([item["quantity"] for item in row["receive_items"]], [3])
        self.assertEqual(row["accepted_by"], ["a"])
        self.assertEqual(self.a["status"], "open")
        self.invoke("decide_offer", uid="b", offer_id=row["id"], data={"accept": True})
        self.assertEqual(self.a["status"], "reserved")
        self.invoke("complete_offer", offer_id=row["id"])
        self.invoke("complete_offer", uid="b", offer_id=row["id"])
        self.assertEqual(self.a["status"], "open")
        self.assertEqual(self.a["item_details"][0]["quantity"], 3)
        self.assertEqual(self.b["status"], "open")
        self.assertEqual(self.b["item_details"][0]["quantity"], 2)

    def test_swap_response_preserves_bundles_and_intersects_all_three_windows(self):
        wanted = self.wanted()
        wanted["pickup_start"] = (rules.instant(self.a["pickup_start"]) + timedelta(minutes=10)).isoformat()
        self.b["pickup_end"] = (rules.instant(self.a["pickup_end"]) - timedelta(minutes=10)).isoformat()
        self.a["item_details"][0]["quantity"] = 3
        self.b["item_details"][0]["quantity"] = 2
        self.a["item_details"].append(dict(name="Apples", quantity=4, unit="each", category="produce"))
        self.a["items"].append("Apples")
        payload = self.respond(match_id="two")
        row = g.store["trade_offers"][-1]
        self.assertEqual(payload["kind"], "exchange")
        self.assertEqual(len(row["give_items"]), 2)
        self.assertEqual(row["give_items"][0]["quantity"], 3)
        self.assertEqual(row["receive_items"][0]["quantity"], 2)
        self.assertEqual(row["accepted_by"], ["a"])
        self.assertEqual(rules.instant(row["pickup_start"]), rules.instant(wanted["pickup_start"]))
        self.assertEqual(rules.instant(row["pickup_end"]), rules.instant(self.b["pickup_end"]))
        self.assertEqual(self.a["status"], "open")
        self.assertEqual(self.options(), [])
        self.invoke("decide_offer", uid="b", offer_id=row["id"], data={"accept": True})
        self.assertEqual(row["status"], "confirmed")
        self.assertEqual(self.a["status"], "reserved")
        self.assertEqual(self.b["status"], "reserved")

    def test_request_board_reflects_new_request_in_another_user_session(self):
        data = {key: self.a[key] for key in ("location", "pickup_start", "pickup_end", "deadline")}
        self.invoke("create_request", uid="b", data=dict(data, items=["Onions"], quantity=None))
        rows = self.invoke("community_requests", uid="a").get_json()["requests"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["user_id"], "b")
        self.assertIsNone(rows[0]["quantity"])


if __name__ == "__main__":
    unittest.main()
