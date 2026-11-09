"""Count-only quantities and calendar bounds, without network or database writes."""
import copy
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from flask import g
from werkzeug.exceptions import BadRequest

import rules
import listing_review
import test_rules as fixtures


class QuantityDateTests(unittest.TestCase):
    def setUp(self):
        self.stamp = datetime(2026, 10, 24, 18, 30, tzinfo=timezone.utc)
        self.clock = patch.object(rules, "now", return_value=self.stamp)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.item = dict(name="onions", quantity=3, category="produce", food_category="vegetables")
        self.valid = fixtures.listing("test", "owner", mode="giveaway")
        self.valid.update(listing_type="single", fresh=True, storage_confirmed=True,
                          allergens="Unknown")

    def test_whole_quantities_only_and_no_unit_required(self):
        for value in (1, 3.0, 99):
            with self.subTest(value=value):
                item = rules.eligible(dict(self.item, quantity=value))
                self.assertEqual(item["quantity"], value)
                self.assertEqual(item["unit"], "each")
                self.assertIsInstance(item["quantity"], int)
        for value in (0, -1, 100, 1.5, True, "3", None, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(BadRequest):
                rules.quantity(value)

    def test_unit_in_name_matches_old_units_without_converting_sizes(self):
        old = dict(item_details=[dict(name="rice", unit="bag", quantity=3)])
        new = dict(item_details=[dict(name="bags of rice", unit="each", quantity=3)])
        wanted = dict(wanted_food="bag of rice", wanted_quantity=1, wanted_unit="each")
        self.assertTrue(rules.fulfills(old, wanted))
        self.assertTrue(rules.fulfills(new, wanted))
        self.assertTrue(rules.fulfills(new, dict(wanted_food="rice", wanted_quantity=2, wanted_unit="bag")))
        self.assertFalse(rules.fulfills(old, dict(wanted, wanted_food="kg of rice")))
        self.assertFalse(rules.fulfills(old, dict(wanted, wanted_quantity=4)))

    def test_swap_requires_food_and_quantity_with_legacy_single_item_fallback(self):
        data = dict(self.valid, mode="exchange", wanted_food="bags of rice")
        result = listing_review.validated_fields(data)
        self.assertEqual(result["wanted_unit"], "each")
        self.assertEqual(result["wanted_quantity"], 1)
        self.assertEqual(result["wanted_items"], [dict(name="bags of rice", quantity=1, unit="each")])
        self.assertEqual(listing_review.validated_fields(dict(data, wanted_quantity=99))["wanted_quantity"], 99)
        with self.assertRaises(BadRequest):
            listing_review.validated_fields(dict(data, wanted_food=""))

    def test_listing_dates_rejected_before_photo_review(self):
        for field in ("pickup_start", "pickup_end", "deadline"):
            for value in (self.stamp - timedelta(minutes=1),
                          rules.calendar_years(self.stamp, 1) + timedelta(seconds=1)):
                with self.subTest(field=field, value=value), self.assertRaises(BadRequest):
                    listing_review.validated_fields(dict(self.valid, **{field: value.isoformat()}))

    def test_inclusive_calendar_bounds_and_window_order(self):
        upper = rules.calendar_years(self.stamp, 1)
        rules.window(dict(pickup_start=self.stamp.isoformat(),
                          pickup_end=(upper - timedelta(minutes=1)).isoformat(),
                          deadline=upper.isoformat()))
        for start, end, due in ((2, 1, 3), (1, 1, 2), (1, 3, 2)):
            with self.assertRaises(BadRequest):
                rules.window(dict(zip(("pickup_start", "pickup_end", "deadline"),
                                      ((self.stamp + timedelta(hours=x)).isoformat() for x in (start, end, due)))))

    def test_leap_year_clamps(self):
        leap = datetime(2028, 2, 29, 12, tzinfo=timezone.utc)
        self.assertEqual(rules.calendar_years(leap, 1), leap.replace(year=2029, day=28))

    def test_printed_date_calendar_range(self):
        today = self.stamp.date()
        lower, upper = rules.calendar_years(today, -2), rules.calendar_years(today, 10)
        for value in (lower, today, upper):
            rules.eligible(dict(self.item, date_type="best_by", printed_date=value.isoformat()))
        for value in (lower - timedelta(days=1), upper + timedelta(days=1)):
            with self.assertRaisesRegex(BadRequest, "printed date between"):
                rules.eligible(dict(self.item, date_type="best_by", printed_date=value.isoformat()))

    def test_past_best_by_allowed_but_use_by_expiration_blocked(self):
        yesterday = (self.stamp.date() - timedelta(days=1)).isoformat()
        for kind in ("best_by", "sell_by"):
            item = rules.eligible(dict(self.item, printed_date=yesterday, date_type=kind))
            row = copy.deepcopy(self.valid)
            row["item_details"] = [item]
            self.assertTrue(rules.available(row))
        for kind in ("use_by", "expiration"):
            with self.assertRaisesRegex(BadRequest, "cannot be in the past"):
                rules.eligible(dict(self.item, printed_date=yesterday, date_type=kind))
            row = copy.deepcopy(self.valid)
            row["item_details"] = [dict(self.item, printed_date=yesterday, date_type=kind)]
            self.assertFalse(rules.available(row))

    def test_printed_date_uses_listing_timezone(self):
        # At 01:00 UTC it is still the previous day in Los Angeles.
        with patch.object(rules, "now", return_value=self.stamp.replace(hour=1)):
            rules.eligible(dict(self.item, printed_date="2026-10-23", date_type="use_by"))
            with self.assertRaises(BadRequest):
                rules.eligible(dict(self.item, printed_date="2026-10-22", date_type="use_by"))

    def test_cleared_or_invalid_pickup_dates_rejected(self):
        for value in ("", None, "not-a-date", "2026-10-24T19:00"):
            with self.subTest(value=value), self.assertRaises(BadRequest):
                rules.window(dict(self.valid, pickup_start=value))


class RequestDateTests(unittest.TestCase):
    setUp = fixtures.RuleTests.setUp
    tearDown = fixtures.RuleTests.tearDown
    invoke = fixtures.RuleTests.invoke

    def test_optional_request_amounts_and_existing_listing_matches(self):
        valid = dict(items=["onions"], location=self.a["location"],
                     pickup_start=self.a["pickup_start"], pickup_end=self.a["pickup_end"],
                     deadline=self.a["deadline"])
        for extra, expected_match in (({}, True), (dict(quantity=None), True),
                                      (dict(quantity=1), True), (dict(quantity=6), False)):
            self.notifications.reset_mock()
            row = self.invoke("create_request", uid="b", data=dict(valid, **extra))[0].get_json()["request"]
            self.assertEqual(row["quantity"], extra.get("quantity"))
            self.assertEqual(row["quantities"], {} if row["quantity"] is None else {"onions": row["quantity"]})
            self.assertEqual(self.notifications.called, expected_match)
            if expected_match:
                self.assertEqual(self.notifications.call_args.args[0:2], ("b", "request_match"))
        for amount in (0, -1, 100, 1.5, True, "", "2"):
            with self.subTest(amount=amount), self.assertRaises(BadRequest):
                self.invoke("create_request", uid="b", data=dict(valid, quantity=amount))

    def test_new_listing_notifies_any_amount_requests_but_not_unmet_minimums(self):
        valid = dict(items=["onions"], location=self.a["location"],
                     pickup_start=self.a["pickup_start"], pickup_end=self.a["pickup_end"],
                     deadline=self.a["deadline"])
        self.invoke("create_request", uid="b", data=valid)
        self.invoke("create_request", uid="c", data=dict(valid, quantity=6))
        self.notifications.reset_mock()
        data = dict(self.a, listing_type="single", fresh=True, storage_confirmed=True, allergens="None")
        with patch.object(listing_review, "consume_approval", return_value={
            "photo_id": self.a["photo_id"], "bob_review_id": self.a["bob_review_id"],
        }):
            self.invoke("create_listing", uid="a", data=data)
        self.assertEqual(self.notifications.call_count, 1)
        self.assertEqual(self.notifications.call_args.args[0:2], ("b", "request_match"))

    def test_giveaway_request_must_be_entirely_within_listing(self):
        self.a["mode"] = "giveaway"
        start, end = rules.instant(self.a["pickup_start"]), rules.instant(self.a["pickup_end"])
        for request_start, request_end in (
                (start - timedelta(minutes=1), end),
                (start, end + timedelta(minutes=1)),
                (rules.now() - timedelta(minutes=1), end),
                (start, start),
                (end, start),
                (start, rules.calendar_years(rules.now(), 1) + timedelta(days=1))):
            with self.subTest(start=request_start, end=request_end):
                with self.assertRaisesRegex(BadRequest, "Pick a time between"):
                    self.invoke("request_giveaway", uid="b", listing_id="one",
                                data=dict(location=self.a["location"],
                                          pickup_start=request_start.isoformat(),
                                          pickup_end=request_end.isoformat()))
        self.assertEqual(g.store["food_requests"], [])

    def test_available_until_is_a_request_upper_bound(self):
        self.a["mode"] = "giveaway"
        start = rules.instant(self.a["pickup_start"])
        self.a["deadline"] = (start + timedelta(minutes=20)).isoformat()
        with self.assertRaises(BadRequest):
            self.invoke("request_giveaway", uid="b", listing_id="one",
                        data=dict(location=self.a["location"], pickup_start=self.a["pickup_start"],
                                  pickup_end=(start + timedelta(minutes=30)).isoformat()))

    def test_standalone_request_dates_and_quantities_cannot_bypass(self):
        valid = dict(items=["onions"], quantity=3, location=self.a["location"],
                     pickup_start=self.a["pickup_start"], pickup_end=self.a["pickup_end"],
                     deadline=self.a["deadline"])
        for changes in (dict(quantity=2.5), dict(quantity=100),
                        dict(pickup_start=(rules.now() - timedelta(minutes=1)).isoformat()),
                        dict(deadline=rules.calendar_years(rules.now(), 2).isoformat())):
            with self.subTest(changes=changes), self.assertRaises(BadRequest):
                self.invoke("create_request", data=dict(valid, **changes))
        self.assertEqual(g.store["food_requests"], [])
        response = self.invoke("create_request", data=valid)
        self.assertEqual(response[0].get_json()["request"]["unit"], "each")


if __name__ == "__main__":
    unittest.main()
