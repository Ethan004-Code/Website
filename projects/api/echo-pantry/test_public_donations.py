"""Public map browsing reuses routing without reading or mutating private data."""
import unittest
from unittest.mock import MagicMock, patch

from flask import Flask, jsonify

import destinations
import runtime


class PublicDonationTests(unittest.TestCase):
    def test_personal_impact_requires_authentication_before_snapshot_access(self):
        self.app.add_url_rule("/api/impact", "get_impact", lambda: jsonify(completed_trades=99))
        response = self.app.test_client().get("/api/impact")
        self.assertEqual(response.status_code, 401)
        self.auth.assert_called_once()
        self.db.rpc.assert_not_called()

    def setUp(self):
        self.app = Flask(__name__)
        self.auth = MagicMock(side_effect=lambda: (jsonify(error="Sign in required."), 401))
        self.app.before_request(self.auth)
        self.db = MagicMock()
        # Keep the real request/auth/transaction hooks, without installing the
        # unrelated trading and Bob routes in this isolated Flask application.
        with patch("rules.install_rules"):
            runtime.install_runtime(self.app, {
                "authenticate_api_request": self.auth,
                "supabase": self.db,
            })
        self.persist, self.ledger = MagicMock(), MagicMock()
        destinations.install_destinations(self.app, {}, self.persist, self.ledger)
        self.client = self.app.test_client()
        self.options = [o for o in destinations.DIRECTORY if o["type"] == "donation"]

    def tearDown(self):
        self.db.assert_not_called()
        self.assertEqual(self.db.method_calls, [], "Public browsing must not load private snapshots.")
        self.persist.assert_not_called()
        self.ledger.assert_not_called()

    def test_anonymous_directory_uses_existing_donation_entries(self):
        response = self.client.get("/api/donation-locations")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["options"], self.options)
        self.assertIn("San Jose", data["message"])
        self.auth.assert_not_called()

    def test_stale_cookie_does_not_block_public_browsing(self):
        self.client.set_cookie("echo_access_token", "expired-test-session")
        self.assertEqual(self.client.get("/api/donation-locations").status_code, 200)
        self.auth.assert_not_called()

    @patch.dict("os.environ", {"OPENROUTESERVICE_API_KEY": "test-key"})
    @patch("destinations.search_routes")
    def test_anonymous_routes_reuse_existing_search(self, search):
        expected = {"origin": {"label": "95125"}, "max_drive_minutes": 15,
                    "destinations": [{**self.options[0], "drive_minutes": 12, "drive_miles": 5.2}]}
        search.return_value = expected
        response = self.client.post("/api/donation-locations/routes",
                                    json={"area": "95125", "max_drive_minutes": 15})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), expected)
        search.assert_called_once_with("95125", 15, self.options, "test-key")
        self.auth.assert_not_called()

    @patch("destinations.search_routes")
    def test_invalid_limits_and_non_object_bodies_do_not_call_provider(self, search):
        for body in ({"area": "95125", "max_drive_minutes": 45},
                     {"area": "95125", "max_drive_minutes": "invalid"}, ["95125"]):
            with self.subTest(body=body):
                self.assertEqual(self.client.post("/api/donation-locations/routes", json=body).status_code, 400)
        search.assert_not_called()

    @patch.dict("os.environ", {"OPENROUTESERVICE_API_KEY": ""})
    def test_unconfigured_search_preserves_directory_access(self):
        self.assertFalse(self.client.get("/api/donation-locations").get_json()["routing_configured"])
        response = self.client.post("/api/donation-locations/routes",
                                    json={"area": "95125", "max_drive_minutes": 30})
        self.assertEqual(response.status_code, 503)
        self.assertIn("browse donation locations", response.get_json()["error"])
        self.assertEqual(self.client.get("/api/donation-locations").get_json()["options"], self.options)

    @patch.dict("os.environ", {"OPENROUTESERVICE_API_KEY": "test-key"})
    @patch("destinations.search_routes", side_effect=destinations.ProviderError("Routing is temporarily unavailable."))
    def test_provider_failure_is_distinct_from_empty_results(self, search):
        response = self.client.post("/api/donation-locations/routes",
                                    json={"area": "95125", "max_drive_minutes": 30})
        self.assertEqual(response.status_code, 502)
        search.side_effect = None
        search.return_value = {"origin": None, "max_drive_minutes": 30, "destinations": []}
        response = self.client.post("/api/donation-locations/routes",
                                    json={"area": "95125", "max_drive_minutes": 30})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["destinations"], [])

    @patch("destinations.search_routes")
    def test_cross_origin_search_is_rejected(self, search):
        response = self.client.post("/api/donation-locations/routes",
                                    headers={"Origin": "https://unrelated.example"},
                                    json={"area": "95125", "max_drive_minutes": 30})
        self.assertEqual(response.status_code, 403)
        search.assert_not_called()

    def test_public_browsing_does_not_open_listing_or_outcome_endpoints(self):
        for method, path in (("get", "/api/listings/private/fallback-options"),
                             ("post", "/api/listings/private/donation-routes"),
                             ("post", "/api/outcomes")):
            with self.subTest(path=path):
                response = getattr(self.client, method)(path)
                self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
