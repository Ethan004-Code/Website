"""Targeted rule regressions. No external services or production data are used."""
import copy
import json
import subprocess
import unittest
from datetime import timedelta
from unittest.mock import patch, MagicMock

from flask import Flask, g
from werkzeug.exceptions import BadRequest, Forbidden, Conflict, BadGateway, ServiceUnavailable

import rules
import bob
import destinations
from destinations import suitable, DIRECTORY


def listing(identifier, owner, food="Onions", wants="Rice", mode="exchange", unit="each", amount=5):
    start = rules.now() + timedelta(hours=1)
    end = start + timedelta(hours=2)
    return dict(id=identifier, user_id=owner, name=owner, items=[food],
                photo_id="00000000-0000-0000-0000-000000000011",
                bob_review_id="00000000-0000-0000-0000-000000000012",
                item_details=[dict(name=food, quantity=amount, unit=unit, category="produce",
                                    food_category="other",
                                   unopened=None, printed_date=None, date_type=None)],
                quantities={food: amount}, wants=[wants], wanted_food=wants,
                wanted_quantity=1, wanted_unit="each", mode=mode, status="open",
                location="Library steps", pickup_start=start.isoformat(),
                pickup_end=end.isoformat(), deadline=(end + timedelta(hours=1)).isoformat(),
                timezone="America/Los_Angeles")


class RuleTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        def save(table, row):
            g.writes[(table, row["id"])] = copy.deepcopy(row)
        self.notifications = MagicMock()
        self.legacy = {"save_row": save, "create_notification": self.notifications, "supabase": None}
        rules.install_rules(self.app, self.legacy)
        self.ctx = self.app.test_request_context()
        self.ctx.push()
        g.user = dict(id="a", name="Alice")
        g.active_users = {"a", "b", "c"}
        g.writes = {}
        g.store = {table: [] for table in ("listings", "trade_offers", "food_requests", "notifications", "ratings", "reports", "user_blocks", "impact_records")}
        self.a = listing("one", "a")
        self.b = listing("two", "b", food="Rice", wants="Onions")
        g.store["listings"] = [self.a, self.b]

    def tearDown(self):
        self.ctx.pop()

    def invoke(self, endpoint, uid="a", data=None, **kwargs):
        g.user = dict(id=uid, name=uid)
        with self.app.test_request_context(json=data or {}, method="POST"):
            return self.app.view_functions[endpoint](**kwargs)

    def offer(self):
        self.invoke("create_offer", data={"listing_id": "one", "match_id": "two"})
        return g.store["trade_offers"][-1]

    def test_health_requires_schema_four_for_bundle_exchanges(self):
        db = MagicMock()
        self.legacy["supabase"] = db
        with patch("bob.executable", return_value="/fake/bob"), patch.dict("os.environ", {"BOB_API_KEY": "test-only"}):
            db.rpc.return_value.execute.return_value.data = {"schema_version": 3}
            old = self.invoke("health").get_json()
            self.assertEqual(old["schema_version"], 3)
            self.assertTrue(any("multi-item exchange" in message for message in old["setup_required"]))

            db.rpc.return_value.execute.return_value.data = {"schema_version": 4}
            current = self.invoke("health").get_json()
            self.assertEqual(current["schema_version"], 4)
            self.assertEqual(current["setup_required"], [])
            self.assertEqual(current["status"], "ok")

    @staticmethod
    def set_items(row, items):
        row["item_details"] = items
        row["items"] = [item["name"] for item in items]
        row["quantities"] = {item["name"]: item["quantity"] for item in items}

    def accept_both(self, offer):
        self.invoke("decide_offer", offer_id=offer["id"], data={"accept": True})
        self.invoke("decide_offer", uid="b", offer_id=offer["id"], data={"accept": True})

    def test_swap_matches_ignore_return_quantities(self):
        self.assertEqual(len(rules.matches(self.a)), 1)

    def test_single_requested_item_checks_reciprocal_quantity_and_exposes_minimums(self):
        self.a["wanted_items"] = [dict(name="Rice", quantity=3, unit="each")]
        self.b["wanted_items"] = [dict(name="Onions", quantity=2, unit="each")]
        match = rules.matches(self.a)[0]
        self.assertEqual(match["give_minimum_quantities"], [2])
        self.assertEqual(match["receive_minimum_quantities"], [3])
        with self.assertRaisesRegex(BadRequest, "at least 2"):
            self.invoke("create_offer", data={"listing_id": "one", "match_id": "two",
                                              "give_quantities": [1], "receive_quantities": [3]})
        offer = self.invoke("create_offer", data={"listing_id": "one", "match_id": "two",
                                                  "give_quantities": [2], "receive_quantities": [3]})[0].get_json()["offer"]
        self.assertEqual(offer["give_items"][0]["quantity"], 2)
        self.assertEqual(offer["receive_items"][0]["quantity"], 3)

    def test_two_offered_and_two_requested_items_must_all_be_covered(self):
        self.set_items(self.a, [
            dict(self.a["item_details"][0], name="Onions", quantity=4),
            dict(self.a["item_details"][0], name="Apples", quantity=3),
        ])
        self.set_items(self.b, [
            dict(self.b["item_details"][0], name="Rice", quantity=5),
            dict(self.b["item_details"][0], name="Beans", quantity=4, unit="can"),
        ])
        self.a["wanted_items"] = [
            dict(name="Rice", quantity=3, unit="each"),
            dict(name="Beans", quantity=2, unit="can"),
        ]
        self.b["wanted_items"] = [
            dict(name="Onions", quantity=2, unit="each"),
            dict(name="Apples", quantity=1, unit="each"),
        ]
        match = rules.matches(self.a)[0]
        self.assertEqual(match["give_minimum_quantities"], [2, 1])
        self.assertEqual(match["receive_minimum_quantities"], [3, 2])
        self.set_items(self.b, [self.b["item_details"][0]])
        self.assertEqual(rules.matches(self.a), [])

    def test_five_requested_items_are_matched_as_a_complete_bundle(self):
        wanted = [
            dict(name="Rice", quantity=2, unit="each"),
            dict(name="Beans", quantity=1, unit="can"),
            dict(name="Apples", quantity=3, unit="each"),
            dict(name="Pears", quantity=1, unit="each"),
            dict(name="Carrots", quantity=4, unit="each"),
        ]
        offers = [dict(self.b["item_details"][0], **item) for item in wanted]
        self.set_items(self.b, offers)
        self.a["wanted_items"] = wanted
        self.b["wanted_items"] = [dict(name="Onions", quantity=1, unit="each")]
        self.assertEqual(len(rules.matches(self.a)), 1)
        offers.pop()
        self.set_items(self.b, offers)
        self.assertEqual(rules.matches(self.a), [])

    def test_requested_quantity_and_unit_mismatches_explain_why_a_swap_fails(self):
        self.a["wanted_items"] = [dict(name="Rice", quantity=6, unit="each")]
        response = self.app.view_functions["get_matches_read"](listing_id="one").get_json()
        self.assertTrue(any("Quantity mismatch" in x["message"] and "only 5" in x["message"]
                            for x in response["incompatibilities"]))
        self.a["wanted_items"] = [dict(name="Rice", quantity=1, unit="kg")]
        self.b["item_details"][0].update(name="Rice", unit="g")
        response = self.app.view_functions["get_matches_read"](listing_id="one").get_json()
        self.assertTrue(any("Unit mismatch" in x["message"] and "kg" in x["message"]
                            for x in response["incompatibilities"]))

    def test_one_sided_food_match_does_not_allow_exchange(self):
        self.a["wanted_items"] = [
            dict(name="Rice", quantity=1, unit="each"),
            dict(name="Beans", quantity=1, unit="each"),
        ]
        self.assertEqual(rules.matches(self.a), [])
        response = self.app.view_functions["get_matches_read"](listing_id="one").get_json()
        self.assertTrue(any("Food mismatch" in x["message"] and "Beans" in x["message"]
                            for x in response["incompatibilities"]))

    def test_listing_api_uses_actual_rating_rows_for_each_owner_and_clears_stale_values(self):
        self.a["rating"] = 4.9
        self.b["rating"] = 5.0
        unrated = listing("three", "c")
        unrated["rating"] = 4.8
        g.store["listings"].append(unrated)
        g.store["ratings"] = [
            {"to_user_id": "a", "stars": 5},
            {"to_user_id": "a", "stars": 4},
            {"to_user_id": "a", "stars": 4},
            {"to_user_id": "b", "stars": 2},
            {"to_user_id": "a", "stars": 0},
            {"to_user_id": "a", "stars": True},
            {"to_user_id": "b", "stars": "4"},
        ]

        result = self.app.view_functions["get_listings"]().get_json()["listings"]
        by_id = {row["id"]: row for row in result}
        self.assertEqual((by_id["one"]["rating"], by_id["one"]["rating_count"]), (4.33, 3))
        self.assertEqual((by_id["two"]["rating"], by_id["two"]["rating_count"]), (2.0, 1))
        self.assertIsNone(by_id["three"]["rating"])
        self.assertEqual(by_id["three"]["rating_count"], 0)
        self.b["wanted_quantity"] = 6
        self.assertEqual(len(rules.matches(self.a)), 0)
        self.b["wanted_quantity"] = 5
        self.assertEqual(len(rules.matches(self.a)), 1)
        self.a["wanted_quantity"] = None
        self.b.pop("wanted_quantity")
        self.assertEqual(len(rules.matches(self.a)), 1)

    def test_food_category_api_filter_handles_bundles_and_legacy_items(self):
        self.a["item_details"][0]["food_category"] = "vegetables"
        self.b["item_details"][0]["food_category"] = "grains_pantry"
        bundle = listing("bundle", "c", food="Apples", mode="giveaway")
        bundle["item_details"] = [
            dict(name="Apples", quantity=2, unit="each", category="produce",
                 food_category="fruits", unopened=None, printed_date=None, date_type=None),
            dict(name="Onions", quantity=1, unit="each", category="produce",
                 food_category="vegetables", unopened=None, printed_date=None, date_type=None),
        ]
        legacy = listing("legacy", "c", food="Oats", mode="giveaway")
        legacy["item_details"][0].pop("food_category")
        snack = listing("snacks", "c", food="Crackers", mode="giveaway")
        snack["item_details"][0]["food_category"] = "snacks"
        g.store["listings"] = [self.a, self.b, bundle, legacy, snack]
        g.active_users.add("c")

        def filtered(category):
            with self.app.test_request_context(f"/api/listings?food_category={category}"):
                g.user = None
                g.active_users = {"a", "b", "c"}
                g.store = {"listings": [self.a, self.b, bundle, legacy, snack], "ratings": [],
                           "trade_offers": [], "food_requests": [], "user_blocks": []}
                return self.app.view_functions["get_listings"]().get_json()["listings"]

        self.assertEqual({row["id"] for row in filtered("fruits")}, {"bundle"})
        self.assertEqual({row["id"] for row in filtered("vegetables")}, {"one", "bundle"})
        self.assertEqual({row["id"] for row in filtered("grains_pantry")}, {"two"})
        self.assertEqual({row["id"] for row in filtered("snacks")}, {"snacks"})
        self.assertEqual({row["id"] for row in filtered("other")}, {"legacy"})

    def test_food_category_api_filter_rejects_unknown_values(self):
        with self.app.test_request_context("/api/listings?food_category=not-a-category"):
            g.user = None
            g.active_users = {"a", "b"}
            g.store = {"listings": [self.a, self.b], "ratings": [],
                       "trade_offers": [], "food_requests": [], "user_blocks": []}
            with self.assertRaises(BadRequest):
                self.app.view_functions["get_listings"]()

    def test_swap_food_names_ignore_listing_counts_explicit_units_and_plural_form(self):
        self.a.update(wanted_food="1 g of onions", wanted_unit="each")
        self.a["item_details"][0].update(name="4 bananas", quantity=4)
        self.b.update(wanted_food="4 bananas", wanted_unit="each")
        self.b["item_details"][0].update(name="g of onions", quantity=5)
        self.assertEqual(len(rules.matches(self.a)), 1)
        self.assertEqual(len(rules.matches(self.b)), 1)
        offer = self.invoke("create_offer", data={"listing_id":"one", "match_id":"two"})[0].get_json()["offer"]
        self.assertEqual(offer["give_items"][0]["name"], "4 bananas")
        self.assertEqual(offer["give_items"][0]["quantity"], 4)
        self.assertEqual(offer["receive_items"][0]["name"], "g of onions")
        self.assertEqual(offer["receive_items"][0]["quantity"], 5)

    def test_swap_matching_finds_current_bananas_and_gram_labeled_onions(self):
        self.a["item_details"][0].update(name="4 bananas", quantity=4, unit="each")
        self.a["wanted_food"] = "5 g of onions"
        self.b["item_details"][0].update(name="5 g of onions", quantity=5, unit="each")
        self.b["wanted_food"] = "4 bananas"
        self.assertEqual(len(rules.matches(self.a)), 1)
        response = self.app.view_functions["get_matches_read"](listing_id="one").get_json()
        self.assertEqual(len(response["matches"]), 1)
        self.assertEqual(response["incompatibilities"], [])

    def test_match_diagnostics_explain_reciprocal_food_mismatch_without_quantity_blame(self):
        self.b["wanted_food"] = "Apples"
        response = self.app.view_functions["get_matches_read"](listing_id="one").get_json()
        reasons = [entry["message"] for entry in response["incompatibilities"]]
        self.assertTrue(any("Food mismatch" in message for message in reasons))
        self.assertFalse(any("quantity" in message.casefold() or "amount" in message.casefold() for message in reasons))

    def test_partial_swap_reserves_then_reopens_with_only_leftover_amounts(self):
        self.a["item_details"] = [
            dict(name="bananas", quantity=4, unit="each", category="produce", unopened=None, printed_date=None, date_type=None),
            dict(name="apples", quantity=3, unit="each", category="produce", unopened=None, printed_date=None, date_type=None),
        ]
        self.a.update(items=["bananas", "apples"], quantities={"bananas":4, "apples":3})
        self.b["wanted_food"] = "bananas"
        self.b["item_details"][0]["quantity"] = 5
        self.b["quantities"] = {"Rice": 5}
        response = self.invoke("create_offer", data={"listing_id":"one", "match_id":"two",
            "give_quantities":[2, 1], "receive_quantities":[3]})[0].get_json()["offer"]
        self.assertEqual([i["quantity"] for i in response["give_items"]], [2, 1])
        self.assertEqual([i["listed_quantity"] for i in response["give_items"]], [4, 3])
        self.assertEqual(response["receive_items"][0]["quantity"], 3)
        self.invoke("decide_offer", offer_id=response["id"], data={"accept":True})
        self.assertEqual(self.a["status"], "open")
        self.invoke("decide_offer", uid="b", offer_id=response["id"], data={"accept":True})
        self.assertEqual(self.a["status"], "reserved")
        self.assertEqual(self.b["status"], "reserved")
        self.invoke("complete_offer", offer_id=response["id"])
        self.assertEqual(self.a["status"], "reserved")
        self.invoke("complete_offer", uid="b", offer_id=response["id"])
        self.assertEqual(self.a["status"], "open")
        self.assertEqual(self.a["quantities"], {"bananas":2, "apples":2})
        self.assertEqual(self.b["quantities"], {"Rice":2})
        events = [r for r in g.store["impact_records"] if r.get("offer_id") == response["id"]]
        self.assertEqual({e["quantity"] for e in events}, {1, 2, 3})

    def test_partial_swap_quantities_are_server_validated_and_stale_amounts_rejected(self):
        for invalid in ([0], [6], [1, 2], [1.5], [True], "3"):
            with self.assertRaises(BadRequest):
                self.invoke("create_offer", data={"listing_id":"one", "match_id":"two", "give_quantities":invalid})
        self.assertFalse(g.store["trade_offers"])
        offer = self.invoke("create_offer", data={"listing_id":"one", "match_id":"two", "give_quantities":[2]})[0].get_json()["offer"]
        self.a["item_details"][0]["quantity"] = 6
        with self.assertRaises(Conflict):
            self.invoke("decide_offer", offer_id=offer["id"], data={"accept":True})

    def test_full_amount_swap_accepted_and_completed_without_return_quantities(self):
        self.a["wanted_quantity"] = None
        self.b["wanted_quantity"] = 1
        self.a["item_details"][0]["quantity"] = 3
        self.b["item_details"][0]["quantity"] = 2
        offer = self.offer()
        self.assertEqual(offer["give_items"][0]["quantity"], 3)
        self.assertEqual(offer["receive_items"][0]["quantity"], 2)
        self.accept_both(offer)
        self.assertEqual(offer["status"], "confirmed")
        self.invoke("complete_offer", offer_id=offer["id"])
        self.assertEqual(offer["status"], "completion_pending")
        self.invoke("complete_offer", uid="b", offer_id=offer["id"])
        self.assertEqual(offer["status"], "completed")
        self.assertEqual(self.a["status"], "traded")
        self.assertEqual(self.b["status"], "traded")

    def test_swap_matching_rejects_unit_mismatch_without_changing_recorded_units(self):
        self.b["wanted_unit"] = "kg"
        self.assertEqual(rules.matches(self.a), [])
        response = self.app.view_functions["get_matches_read"](listing_id="one").get_json()
        self.assertTrue(any("Unit mismatch" in x["message"] for x in response["incompatibilities"]))

    def test_nonreciprocal_food_prevents_exchange(self):
        self.b["wanted_food"] = "Apples"
        self.assertEqual(rules.matches(self.a), [])

    def test_nonoverlap_prevents_match(self):
        self.b["pickup_start"] = self.a["pickup_end"]
        self.assertEqual(rules.matches(self.a), [])

    def test_past_deadline_prevents_match(self):
        self.a["deadline"] = (rules.now() - timedelta(seconds=1)).isoformat()
        self.assertEqual(rules.matches(self.a), [])

    def test_missing_legacy_window_is_not_flexible(self):
        del self.b["pickup_start"]
        self.assertEqual(rules.matches(self.a), [])

    def test_different_listing_locations_do_not_block_swaps(self):
        self.b["location"] = "Community Center"
        self.assertEqual(len(rules.matches(self.a)), 1)

    def test_capitalization_whitespace_and_different_locations_allow_full_swap(self):
        self.a.update(location="Library steps", wanted_food="  rIcE  ")
        self.b.update(location="Community Center", wanted_food="  oNIONS ")
        self.a["item_details"][0]["name"] = "ONIONS"
        self.b["item_details"][0]["name"] = "rice"
        self.invoke("create_offer", data={"listing_id":"one", "match_id":"two", "location":"Park entrance"})
        offer = g.store["trade_offers"][-1]
        self.assertEqual(offer["location"], "Park entrance")
        self.invoke("decide_offer", offer_id=offer["id"], data={"accept":True})
        self.assertEqual(self.a["status"], "open")
        self.assertEqual(self.b["status"], "open")
        self.invoke("decide_offer", uid="b", offer_id=offer["id"], data={"accept":True})
        self.assertEqual(offer["status"], "confirmed")
        self.assertEqual(offer["location"], "Park entrance")
        self.invoke("complete_offer", offer_id=offer["id"])
        self.invoke("complete_offer", uid="b", offer_id=offer["id"])
        self.assertEqual(offer["status"], "completed")

    def test_swap_spot_is_required_and_limited_on_server(self):
        for location in ("", " ", "x" * 101, None, 123):
            with self.assertRaises(BadRequest):
                self.invoke("create_offer", data={"listing_id":"one","match_id":"two","location":location})
        self.assertEqual(g.store["trade_offers"], [])
        offer = self.offer()
        self.assertEqual(offer["location"], self.a["location"])

    def test_shared_swap_spot_is_independent_of_original_listing_spots(self):
        offer = self.offer()
        self.a["location"] = "A different public spot"
        self.b["location"] = "Another public spot"
        self.accept_both(offer)
        self.assertEqual(offer["status"], "confirmed")
        self.assertEqual(offer["location"], "Library steps")

    def test_bundle_all_items_fixed_server_side(self):
        self.invoke("create_offer", data={"listing_id": "one", "match_id": "two",
                                         "give": ["Invented"], "receive": [], "user_id": "b"})
        offer = g.store["trade_offers"][-1]
        self.assertEqual(offer["give_items"][0]["name"], self.a["item_details"][0]["name"])
        self.assertEqual(offer["give_items"][0]["quantity"], self.a["item_details"][0]["quantity"])
        self.assertEqual(offer["give_items"][0]["requested_minimum"], 1)
        self.assertEqual(offer["give_items"][0]["requested_unit"], "each")
        self.assertEqual(offer["give"], ["Onions"])

    def test_outsider_cannot_accept_or_confirm(self):
        offer = self.offer()
        with self.assertRaises(Forbidden):
            self.invoke("decide_offer", uid="c", offer_id=offer["id"], data={"accept": True})
        with self.assertRaises(Forbidden):
            self.invoke("complete_offer", uid="c", offer_id=offer["id"])

    def test_final_acceptance_checks_reservation_again(self):
        offer = self.offer()
        self.invoke("decide_offer", offer_id=offer["id"], data={"accept": True})
        self.a.update(status="reserved", reservation_id="another-offer")
        with self.assertRaises(Conflict):
            self.invoke("decide_offer", uid="b", offer_id=offer["id"], data={"accept": True})
        self.assertEqual(self.a["reservation_id"], "another-offer")

    def test_competing_exchange_offers_cannot_reserve_the_same_food(self):
        competitor = listing("three", "c", food="Rice", wants="Onions", amount=4)
        g.store["listings"].append(competitor)
        first = self.invoke("create_offer", data={"listing_id": "one", "match_id": "two"})[0].get_json()["offer"]
        second = self.invoke("create_offer", data={"listing_id": "one", "match_id": "three"})[0].get_json()["offer"]
        self.invoke("decide_offer", offer_id=first["id"], data={"accept": True})
        self.invoke("decide_offer", offer_id=second["id"], data={"accept": True})
        self.invoke("decide_offer", uid="b", offer_id=first["id"], data={"accept": True})
        with self.assertRaises(Conflict):
            self.invoke("decide_offer", uid="c", offer_id=second["id"], data={"accept": True})
        self.assertEqual(self.a["reservation_id"], first["id"])
        self.assertEqual(self.b["reservation_id"], first["id"])
        self.assertNotEqual(competitor.get("reservation_id"), second["id"])

    def test_fallback_options_include_specific_exchange_incompatibility(self):
        self.a["wanted_items"] = [dict(name="Rice", quantity=7, unit="each")]
        result = self.invoke("fallback_options", listing_id="one").get_json()
        self.assertTrue(any("Quantity mismatch" in item["message"] and "Rice" in item["message"]
                            for item in result["incompatibilities"]))

    def test_reserved_listing_reports_availability_as_the_blocker(self):
        self.b["status"] = "reserved"
        result = self.app.view_functions["get_matches_read"](listing_id="one").get_json()
        issue = next(item for item in result["incompatibilities"] if item["listing_id"] == "two")
        self.assertIn("not currently available", issue["message"])

    def test_decline_preserves_food(self):
        offer = self.offer()
        self.invoke("decide_offer", uid="b", offer_id=offer["id"], data={"accept": False})
        self.assertEqual(self.a["status"], "open")
        self.assertEqual(self.b["status"], "open")

    def test_cancel_releases_only_this_offers_reservations(self):
        offer = self.offer()
        self.accept_both(offer)
        self.b["reservation_id"] = "someone-else"
        self.invoke("cancel_offer", offer_id=offer["id"])
        self.assertEqual(self.a["status"], "open")
        self.assertEqual(self.b["status"], "reserved")

    def test_two_confirmations_and_idempotent_impact(self):
        offer = self.offer()
        self.accept_both(offer)
        self.invoke("complete_offer", offer_id=offer["id"])
        self.assertEqual(g.store["impact_records"], [])
        self.invoke("complete_offer", uid="b", offer_id=offer["id"])
        self.assertEqual(len(g.store["impact_records"]), 2)
        self.invoke("complete_offer", uid="b", offer_id=offer["id"])
        self.assertEqual(len(g.store["impact_records"]), 2)
        with self.assertRaises(Conflict):
            self.invoke("decide_offer", offer_id=offer["id"], data={"accept": False})
        self.assertEqual(self.a["status"], "traded")

    def test_giveaway_needs_no_reciprocal_food_but_both_confirmations(self):
        self.a["mode"] = "giveaway"
        data = {k: self.a[k] for k in ("location", "pickup_start", "pickup_end")}
        self.invoke("request_giveaway", uid="b", listing_id="one", data=data)
        row = g.store["food_requests"][-1]
        with self.assertRaises(Forbidden):
            self.invoke("decide_giveaway", uid="b", request_id=row["id"], data={"accept": True})
        self.invoke("decide_giveaway", request_id=row["id"], data={"accept": True})
        self.invoke("confirm_giveaway_pickup", uid="b", request_id=row["id"])
        self.assertEqual(row["status"], "completion_pending")
        self.assertEqual(g.store["impact_records"], [])
        self.invoke("confirm_giveaway_pickup", request_id=row["id"])
        self.invoke("confirm_giveaway_pickup", request_id=row["id"])
        self.assertEqual(row["status"], "completed")
        self.assertEqual(len(g.store["impact_records"]), 1)

    def test_recommendation_is_not_outcome_and_expiry_not_spoilage(self):
        self.invoke("fallback_options", listing_id="one")
        self.assertEqual(g.store["impact_records"], [])
        self.a["deadline"] = (rules.now() - timedelta(days=1)).isoformat()
        compost = next(option for option in DIRECTORY if option["type"] == "compost")
        self.assertFalse(suitable(self.a, compost))
        self.a["inedible_confirmed"] = True
        self.assertTrue(suitable(self.a, compost))

    def test_donation_route_requires_server_key_and_listing_owner(self):
        self.a["item_details"] = [dict(name="Rice", quantity=1, unit="bag", category="shelf_stable",
                                       unopened=True, printed_date=None, date_type=None)]
        with patch.dict("os.environ", {}, clear=True), self.assertRaises(ServiceUnavailable):
            self.invoke("donation_routes", listing_id="one", data={"area": "95125", "max_drive_minutes": 30})
        with patch.dict("os.environ", {"OPENROUTESERVICE_API_KEY": "test-key"}, clear=False), self.assertRaises(Forbidden):
            self.invoke("donation_routes", uid="b", listing_id="one", data={"area": "95125", "max_drive_minutes": 30})

    def test_donation_route_returns_only_compatible_verified_options(self):
        self.a["item_details"] = [dict(name="Rice", quantity=1, unit="bag", category="shelf_stable",
                                       unopened=True, printed_date=None, date_type=None)]
        expected = {"origin": {"label": "95125", "longitude": -121.89, "latitude": 37.33},
                    "max_drive_minutes": 30, "destinations": []}
        with patch.dict("os.environ", {"OPENROUTESERVICE_API_KEY": "test-key"}, clear=False), \
             patch.object(destinations, "search_routes", return_value=expected) as search:
            response = self.invoke("donation_routes", listing_id="one", data={"area": "95125", "max_drive_minutes": 30})
        self.assertEqual(response.get_json(), expected)
        routed = search.call_args.args[2]
        self.assertEqual({row["id"] for row in routed}, {"second-harvest-curtner", "second-harvest-cypress", "sacred-heart-donation-station"})

    def test_unknown_destination_rejected(self):
        with self.assertRaises(BadRequest):
            self.invoke("external_outcome", data={"listing_id": "one", "destination_id": "invented",
                        "action": "donation", "confirmed": True, "no_glass_confirmed": True,
                        "destination_feasible_confirmed": True})
        self.assertEqual(g.store["impact_records"], [])

    def test_food_and_quantities_policy(self):
        base = self.a["item_details"][0].copy()
        for amount in (0, -1, True, float("inf"), 1000):
            with self.assertRaises(BadRequest):
                rules.eligible(dict(base, quantity=amount))
        with self.assertRaises(BadRequest):
            rules.eligible(dict(base, name="Homemade soup"))
        with self.assertRaises(BadRequest):
            rules.eligible(dict(base, category="shelf_stable", unopened=False))
        self.assertIsNone(rules.eligible(base)["unopened"])

    def test_test_records_not_in_real_impact(self):
        offer = self.offer()
        self.a["is_test"] = self.b["is_test"] = True
        self.accept_both(offer)
        self.invoke("complete_offer", offer_id=offer["id"])
        self.invoke("complete_offer", uid="b", offer_id=offer["id"])
        result = self.invoke("get_impact").get_json()
        self.assertEqual(result["completed_trades"], 0)
        self.assertEqual(result["quantities"], [])

    def test_mixed_units_not_summed(self):
        g.store["impact_records"] = [
            dict(id="r1", identity="r1", user_id="a", confirmation_type="user_reported", action="donation", item_name="Rice", quantity=1, unit="bag", listing_id="one"),
            dict(id="r2", identity="r2", user_id="a", confirmation_type="user_reported", action="donation", item_name="Rice", quantity=2, unit="kg", listing_id="two"),
        ]
        result = self.invoke("get_impact").get_json()
        self.assertEqual(len(result["quantities"]), 2)
        self.assertNotIn("waste_avoided_kg_estimate", result)


class BobTests(unittest.TestCase):
    def draft(self):
        return dict(item_details=[dict(name="onions", quantity=None, unit=None, category="produce",
                    unopened=None, printed_date=None, date_type=None)], location=None,
                    availability=None, deadline_text=None, ambiguities=[])

    def test_unknown_quantities_remain_unknown(self):
        self.assertIsNone(bob.validate_draft(self.draft(), "I have some onions")["item_details"][0]["quantity"])

    def test_invented_quantity_cleared(self):
        draft = self.draft()
        draft["item_details"][0]["quantity"] = 5
        self.assertIsNone(bob.validate_draft(draft, "I have some onions")["item_details"][0]["quantity"])

    def test_hallucinated_food_rejected(self):
        with self.assertRaises(ValueError):
            bob.validate_draft(self.draft(), "I have rice")

    @patch("bob.os.killpg")
    @patch("bob.subprocess.Popen")
    @patch("bob.executable", return_value="/fake/bob")
    @patch.dict("os.environ", {"BOB_API_KEY": "test-only", "SUPABASE_SERVICE_ROLE_KEY": "must-not-reach-worker"})
    def test_timeout_and_worker_secret_boundary(self, executable, popen, kill):
        worker = MagicMock(pid=123)
        worker.communicate.side_effect = [subprocess.TimeoutExpired("bob", 45), ("", None)]
        popen.return_value = worker
        with self.assertRaises(ServiceUnavailable):
            bob.interpret("I have onions", "America/Los_Angeles")
        kwargs = popen.call_args.kwargs
        self.assertNotIn("SUPABASE_SERVICE_ROLE_KEY", kwargs["env"])
        self.assertNotIn("shell", kwargs)
        self.assertIn("--disable-tool-groups", popen.call_args.args[0])
        kill.assert_called_once()

    @patch("bob.subprocess.Popen")
    @patch("bob.executable", return_value="/fake/bob")
    @patch.dict("os.environ", {"BOB_API_KEY": "test-only"})
    def test_malformed_output_rejected(self, executable, popen):
        popen.return_value = MagicMock(returncode=0)
        popen.return_value.communicate.return_value = ('{"status":"success","last_message":"not json"}', None)
        with self.assertRaises(BadGateway):
            bob.interpret("I have onions", "America/Los_Angeles")


if __name__ == "__main__":
    unittest.main()
