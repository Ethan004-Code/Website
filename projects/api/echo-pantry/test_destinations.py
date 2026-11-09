"""Donation-route tests use mocked provider responses and no production data."""
import unittest
from unittest.mock import patch

import destinations


class DonationRouteTests(unittest.TestCase):
    def setUp(self):
        destinations._CACHE.clear()
        self.options = [row for row in destinations.DIRECTORY if row["type"] == "donation"]

    @patch("destinations._provider_json")
    def test_results_are_sorted_and_filtered(self, provider_json):
        provider_json.side_effect = [
            {"features": [{"geometry": {"coordinates": [-121.89, 37.33]}, "properties": {"label": "95125, San Jose, CA"}}]},
            {"durations": [[720, 2400, 480]], "distances": [[5.2, 17.5, 3.4]]},
        ]
        result = destinations.search_routes("95125", 15, self.options, "test-key")
        self.assertEqual([row["id"] for row in result["destinations"]],
                         ["sacred-heart-donation-station", "second-harvest-curtner"])
        self.assertEqual(result["destinations"][0]["drive_minutes"], 8)
        self.assertEqual(result["origin"]["label"], "95125, San Jose, CA")

    @patch("destinations._provider_json")
    def test_distant_area_returns_honest_empty_state(self, provider_json):
        provider_json.side_effect = [
            {"features": [{"geometry": {"coordinates": [-73.98, 40.75]}, "properties": {"label": "New York, NY"}}]},
            {"durations": [[200000, 201000, 199000]], "distances": [[2900, 2910, 2895]]},
        ]
        result = destinations.search_routes("10001", 60, self.options, "test-key")
        self.assertEqual(result["destinations"], [])

    def test_invalid_drive_limit_is_rejected_before_provider_use(self):
        with self.assertRaisesRegex(ValueError, "15, 30, or 60"):
            destinations.search_routes("95125", 45, self.options, "test-key")

    @patch("destinations._provider_json")
    def test_geocode_cache_avoids_repeating_search(self, provider_json):
        provider_json.return_value = {"features": [{"geometry": {"coordinates": [-121.89, 37.33]}, "properties": {"label": "95125"}}]}
        first = destinations.geocode_area("95125", "test-key")
        second = destinations.geocode_area(" 95125 ", "test-key")
        self.assertEqual(first, second)
        provider_json.assert_called_once()

    def test_directory_contains_only_explicit_acceptance_sources_for_donations(self):
        self.assertEqual(len(self.options), 3)
        for option in self.options:
            self.assertTrue(option["url"].startswith("https://"))
            self.assertTrue(option["policy"])
            self.assertIn(option["item_rules_status"], {"matched", "confirm"})


if __name__ == "__main__":
    unittest.main()
