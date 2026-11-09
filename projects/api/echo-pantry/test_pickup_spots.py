"""Free-text pickup and request presentation regressions, no network or database."""
import copy
import unittest
from datetime import timedelta
from unittest.mock import patch

from flask import g
from werkzeug.exceptions import BadRequest

import rules
import listing_review
import test_rules as fixtures


class PickupSpotTests(unittest.TestCase):
    tearDown = fixtures.RuleTests.tearDown
    invoke = fixtures.RuleTests.invoke

    def setUp(self):
        original = rules.install_rules
        def notify(user_id, kind, message, entity_id):
            g.store["notifications"].append(dict(id=str(len(g.store["notifications"])),
                user_id=user_id, kind=kind, message=message, entity_id=entity_id, read=False))
        def install(app, legacy):
            original(app, dict(legacy, create_notification=notify))
        with patch.object(rules, "install_rules", side_effect=install):
            fixtures.RuleTests.setUp(self)
        self.spot = "Front steps of the main library"
        self.a.update(mode="giveaway", location=self.spot)

    def request(self, location=None, name="Hyun"):
        start = rules.instant(self.a["pickup_start"]) + timedelta(minutes=15)
        end = start + timedelta(minutes=30)
        data = dict(pickup_start=start.isoformat(), pickup_end=end.isoformat())
        if location is not None:
            data["location"] = location
        with self.app.test_request_context(method="POST", json=data):
            g.user = dict(id="b", name=name)
            result = self.app.view_functions["request_giveaway"](listing_id="one")
        return result[0].get_json()["request"]

    def test_text_limit_and_old_locations(self):
        for value in (self.spot, "Library steps", "a" * 100):
            self.assertEqual(rules.pickup_spot(value), value)
        for value in (None, "", "  ", 12, "a" * 101, "a" * 100 + " "):
            with self.subTest(value=value), self.assertRaises(BadRequest):
                rules.pickup_spot(value)

    def test_listing_review_accepts_free_text_and_rejects_over_limit(self):
        data = dict(self.a, listing_type="single", fresh=True, storage_confirmed=True, allergens="Unknown")
        self.assertEqual(listing_review.validated_fields(data)["location"], self.spot)
        with self.assertRaises(BadRequest):
            listing_review.validated_fields(dict(data, location="a" * 101))

    def test_request_uses_listing_spot_and_exact_requester_times(self):
        row = self.request()
        self.assertEqual(row["location"], self.spot)
        self.assertNotEqual(row["pickup_start"], self.a["pickup_start"])
        self.assertEqual(rules.instant(row["pickup_start"]),
                         rules.instant(self.a["pickup_start"]) + timedelta(minutes=15))
        self.assertEqual(row["requester_name"], "Hyun")
        note = g.store["notifications"][-1]
        self.assertEqual(note["message"], "Hyun requested your giveaway.")
        self.assertEqual(note["user_id"], "a")
        self.assertEqual(self.a["location"], self.spot)

    def test_request_cannot_change_listing_pickup_spot(self):
        with self.assertRaises(BadRequest):
            self.request(location="Someone's house")
        self.assertEqual(g.store["food_requests"], [])
        with self.assertRaises(BadRequest):
            self.request(location="a" * 101)

    def test_notifications_enrich_old_neighbor_message_without_mutating_stored_data(self):
        row = self.request()
        g.store["notifications"][-1]["message"] = "A neighbor requested your whole giveaway."
        before = copy.deepcopy(g.store["notifications"])
        data = self.invoke("get_notifications", uid="a").get_json()["notifications"][0]
        self.assertEqual(data["message"], "Hyun requested your giveaway.")
        self.assertEqual(data["location"], self.spot)
        self.assertEqual(data["pickup_start"], row["pickup_start"])
        self.assertEqual(g.store["notifications"], before)
        self.assertEqual(self.invoke("get_notifications", uid="c").get_json()["notifications"], [])

    def test_standalone_request_accepts_free_text_and_enforces_limit(self):
        data = dict(items=["onions"], quantity=3, location=self.spot,
                    pickup_start=self.a["pickup_start"], pickup_end=self.a["pickup_end"],
                    deadline=self.a["deadline"])
        result = self.invoke("create_request", uid="b", data=data)[0].get_json()["request"]
        self.assertEqual(result["location"], self.spot)
        with self.assertRaises(BadRequest):
            self.invoke("create_request", uid="b", data=dict(data, location="a" * 101))


if __name__ == "__main__":
    unittest.main()
