"""Verified fallback destinations and server-side driving-time lookup."""
from datetime import date
import json
import os
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from flask import g, jsonify, request
from werkzeug.exceptions import BadGateway, BadRequest, Conflict, ServiceUnavailable

from rules import own_listing, matches, match_incompatibilities, available, now

API_KEY_ENV = "OPENROUTESERVICE_API_KEY"
GEOCODE_URL = "https://api.heigit.org/pelias/v1/search"
MATRIX_URL = "https://api.heigit.org/openrouteservice/v2/matrix/driving-car"
CACHE_TTL_SECONDS = 15 * 60
_CACHE = {}
_CACHE_LOCK = threading.Lock()

DIRECTORY = [
    dict(id="second-harvest-curtner", name="Second Harvest — Curtner Center",
         type="donation", location="750 Curtner Avenue, San Jose, CA 95125",
         address="750 Curtner Avenue, San Jose, CA 95125",
         latitude=37.2916954, longitude=-121.8772186,
         accepted_categories=["shelf_stable"], exclusions=["Opened packages", "Past printed dates", "Glass containers", "Prepared food"],
         policy="Accepts walk-in donations of unopened, unexpired nonperishable food. No glass containers or prepared food.",
         item_rules_status="matched", hours="Published: weekdays 9:30 AM–4:30 PM; closed 11:30 AM–12 PM",
         url="https://www.shfb.org/give-help/donate-food/", checked_at="2026-10-23",
         notice="Confirm current acceptance and opening hours using the official page before travelling."),
    dict(id="second-harvest-cypress", name="Second Harvest of Silicon Valley — Cypress Center",
         type="donation", location="4001 North First Street, San Jose, CA 95134",
         address="4001 North First Street, San Jose, CA 95134",
         latitude=37.4133095, longitude=-121.9526387,
         accepted_categories=["shelf_stable"], exclusions=["Opened packages", "Past printed dates", "Glass containers", "Prepared food"],
         policy="Accepts walk-in donations of unopened, unexpired nonperishable food. No glass containers or prepared food.",
         item_rules_status="matched", hours="Published: weekdays 9:30 AM–4:30 PM",
         url="https://www.shfb.org/give-help/donate-food/", checked_at="2026-10-23",
         notice="Confirm current acceptance and opening hours using the official page before travelling."),
    dict(id="sacred-heart-donation-station", name="Sacred Heart Donation Station",
         type="donation", location="1381 South First Street, San Jose, CA 95110",
         address="1381 South First Street, San Jose, CA 95110",
         latitude=37.3164190, longitude=-121.8748300,
         accepted_categories=["shelf_stable"], exclusions=["Homemade food"],
         policy="Its official page explicitly accepts packaged food donations, but does not publish complete item-level rules. Confirm before visiting.",
         item_rules_status="confirm", hours="Published: Mon–Thu 8 AM–4 PM; Fri 8 AM–12 PM; Sat 9 AM–12 PM",
         url="https://www.sacredheartcs.org/donate-items", checked_at="2026-10-23",
         notice="Confirm your specific item and current opening hours using the official page before travelling."),
    dict(id="zero-waste-berger", name="Zero Waste Silicon Valley — Berger Drive",
         type="compost", location="1560 Berger Dr., San Jose, California",
         accepted_categories=["fruit_vegetable_scraps"], exclusions=["Meat", "Dairy", "Oils", "Greasy food", "Packaged food"],
         hours="Published: Monday–Friday, 8:30 AM–4 PM, excluding major holidays",
         url="https://zerowastesv.org/materials/organics", checked_at="2026-10-23",
         notice="Only accepted fruit and vegetable scraps. Confirm current rules and holidays on the official page. A failed match or expired pickup deadline does not mean food is spoiled."),
]


class ProviderError(RuntimeError):
    """A sanitized external-provider failure safe to return to the client."""


def _cache_get(namespace, key):
    clock = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get((namespace, key))
        if not cached or cached[0] <= clock:
            _CACHE.pop((namespace, key), None)
            return None
        return cached[1]


def _cache_put(namespace, key, value):
    with _CACHE_LOCK:
        if len(_CACHE) >= 128:
            oldest = min(_CACHE, key=lambda entry: _CACHE[entry][0])
            _CACHE.pop(oldest, None)
        _CACHE[(namespace, key)] = (time.monotonic() + CACHE_TTL_SECONDS, value)


def _provider_json(provider_request):
    try:
        with urlopen(provider_request, timeout=12) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        if error.code in (401, 403):
            raise ProviderError("The live-routing key was rejected. Check OPENROUTESERVICE_API_KEY in Replit Secrets.") from None
        if error.code == 429:
            raise ProviderError("The free live-routing quota is temporarily exhausted. Try again later.") from None
        raise ProviderError("The routing provider could not complete this request.") from None
    except (URLError, TimeoutError, json.JSONDecodeError):
        raise ProviderError("The routing provider is temporarily unavailable.") from None


def geocode_area(area, api_key):
    normalized = " ".join(str(area or "").strip().split())
    if len(normalized) < 2 or len(normalized) > 100:
        raise ValueError("Enter a US ZIP code or neighborhood between 2 and 100 characters.")
    cache_key = normalized.casefold()
    cached = _cache_get("geocode", cache_key)
    if cached:
        return cached
    query = urlencode({"api_key": api_key, "text": normalized, "boundary.country": "US", "size": 1})
    provider_request = Request(f"{GEOCODE_URL}?{query}", headers={"Accept": "application/json", "User-Agent": "RePlate/1.0"})
    payload = _provider_json(provider_request)
    features = payload.get("features") or []
    if not features:
        raise ValueError("We could not find that US ZIP code or neighborhood. Try a more specific area.")
    feature = features[0]
    coordinates = feature.get("geometry", {}).get("coordinates") or []
    if len(coordinates) != 2:
        raise ProviderError("The geocoder returned an incomplete location.")
    result = {"label": feature.get("properties", {}).get("label") or normalized,
              "longitude": float(coordinates[0]), "latitude": float(coordinates[1])}
    _cache_put("geocode", cache_key, result)
    return result


def route_destinations(origin, destinations, api_key):
    ids = ",".join(destination["id"] for destination in destinations)
    cache_key = f"{origin['longitude']:.5f},{origin['latitude']:.5f}:{ids}"
    cached = _cache_get("matrix", cache_key)
    if cached:
        return cached
    locations = [[origin["longitude"], origin["latitude"]]] + [
        [destination["longitude"], destination["latitude"]] for destination in destinations
    ]
    body = json.dumps({"locations": locations, "sources": [0], "destinations": list(range(1, len(locations))),
                       "metrics": ["duration", "distance"], "units": "mi"}).encode("utf-8")
    provider_request = Request(MATRIX_URL, data=body, method="POST", headers={
        "Accept": "application/json", "Authorization": api_key,
        "Content-Type": "application/json", "User-Agent": "RePlate/1.0",
    })
    payload = _provider_json(provider_request)
    durations = (payload.get("durations") or [[]])[0]
    distances = (payload.get("distances") or [[]])[0]
    if len(durations) != len(destinations) or len(distances) != len(destinations):
        raise ProviderError("The routing provider returned an incomplete route matrix.")
    routed = []
    for destination, duration, distance in zip(destinations, durations, distances):
        if duration is None or distance is None:
            continue
        routed.append({**destination, "drive_minutes": max(1, round(float(duration) / 60)),
                       "drive_miles": round(float(distance), 1)})
    routed.sort(key=lambda destination: (destination["drive_minutes"], destination["drive_miles"]))
    _cache_put("matrix", cache_key, routed)
    return routed


def search_routes(area, max_drive_minutes, destinations, api_key):
    if max_drive_minutes not in (15, 30, 60):
        raise ValueError("Choose a 15, 30, or 60 minute drive limit.")
    origin = geocode_area(area, api_key)
    routed = route_destinations(origin, destinations, api_key)
    return {"origin": origin, "max_drive_minutes": max_drive_minutes,
            "destinations": [row for row in routed if row["drive_minutes"] <= max_drive_minutes]}


def suitable(listing, option):
    details = listing.get("item_details") or []
    if not details:
        return False
    if option["type"] == "compost":
        return listing.get("inedible_confirmed") is True and all(i["category"] == "produce" for i in details)
    if listing.get("inedible_confirmed"):
        return False
    # The pilot never promises that a directory organization will accept food.
    local_today = now().astimezone(__import__("zoneinfo").ZoneInfo("America/Los_Angeles")).date()
    return all(i["category"] == "shelf_stable" and i.get("unopened") is True
               and i.get("unit") != "bottle" and "glass" not in i["name"].casefold()
               and (not i.get("printed_date") or date.fromisoformat(i["printed_date"]) >= local_today)
               for i in details)


def install_destinations(app, legacy, persist, ledger):
    def route_response(destinations):
        """One routing implementation for public browsing and eligible listings."""
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            raise BadRequest("Send a city or ZIP code and a 15, 30, or 60 minute drive limit.")
        try:
            max_drive_minutes = int(data.get("max_drive_minutes", 30))
        except (TypeError, ValueError):
            raise BadRequest("Choose a 15, 30, or 60 minute drive limit.") from None
        if max_drive_minutes not in (15, 30, 60):
            raise BadRequest("Choose a 15, 30, or 60 minute drive limit.")
        api_key = os.getenv(API_KEY_ENV)
        if not api_key:
            raise ServiceUnavailable("Live driving times are unavailable right now. You can still browse donation locations and their official links.")
        if not destinations:
            return jsonify(origin=None, max_drive_minutes=max_drive_minutes, destinations=[])
        try:
            result = search_routes(data.get("area"), max_drive_minutes, destinations, api_key)
        except ValueError as error:
            raise BadRequest(str(error)) from None
        except ProviderError as error:
            raise BadGateway(str(error)) from None
        return jsonify(result)

    @app.get("/api/donation-locations")
    def donation_locations():
        return jsonify(
            options=[option for option in DIRECTORY if option["type"] == "donation"],
            routing_configured=bool(os.getenv(API_KEY_ENV)),
            message="Directory coverage is limited to San Jose / Silicon Valley. These are researched locations, not confirmation that your food will be accepted. Check current item rules and opening hours before visiting.",
        )

    @app.post("/api/donation-locations/routes")
    def donation_location_routes():
        return route_response([option for option in DIRECTORY if option["type"] == "donation"])

    @app.get("/api/listings/<listing_id>/fallback-options")
    def fallback_options(listing_id):
        row = own_listing(listing_id)
        options = [option for option in DIRECTORY if suitable(row, option)]
        return jsonify(matches=matches(row), incompatibilities=match_incompatibilities(row),
                       can_switch_to_giveaway=row["mode"] == "exchange" and available(row),
                       inedible_confirmed=row.get("inedible_confirmed", False),
                       routing_configured=bool(os.getenv(API_KEY_ENV)),
                       options=options,
                       message="Verified directory coverage is currently limited to San Jose / Silicon Valley. Confirm the destination's current rules before travelling. Recommendations are not completed donations.")

    @app.post("/api/listings/<listing_id>/donation-routes")
    def donation_routes(listing_id):
        row = own_listing(listing_id)
        destinations = [option for option in DIRECTORY if option["type"] == "donation" and suitable(row, option)]
        return route_response(destinations)

    @app.post("/api/outcomes")
    def external_outcome():
        data = request.get_json(silent=True) or {}
        row = own_listing(data.get("listing_id"))
        action = data.get("action")
        option = next((o for o in DIRECTORY if o["id"] == data.get("destination_id") and o["type"] == action), None)
        if data.get("confirmed") is not True:
            raise BadRequest("Confirm that you actually completed the drop-off. Viewing a recommendation does not count.")
        if data.get("destination_feasible_confirmed") is not True:
            raise BadRequest("Confirm you checked this San Jose destination's current rules and completed the drop-off during its published hours.")
        if action == "donation" and data.get("no_glass_confirmed") is not True:
            raise BadRequest("Confirm that none of the donated packages contain glass.")
        # Replays return the existing outcome but never change completed food.
        prior = [r for r in g.store["impact_records"] if r["listing_id"] == row["id"] and r.get("identity") and r["action"] in {"donation", "compost"}]
        if prior:
            if all(r["action"] == action and r.get("destination_id") == data.get("destination_id") for r in prior):
                return jsonify(listing=row, message="This user-reported outcome is already recorded."), 200
            raise Conflict("This food already has a different completed outcome.")
        if row["status"] not in {"open", "expired", "withdrawn"} or row.get("reservation_id"):
            raise Conflict("Reserved or completed food cannot be reported as an external outcome.")
        if option is None or not suitable(row, option):
            raise BadRequest("No verified, compatible directory destination is listed for this food.")
        if action == "compost" and (data.get("inedible_confirmed") is not True or data.get("material") != "fruit_vegetable_scraps"):
            raise BadRequest("Confirm food is no longer edible and the material is accepted fruit/vegetable scraps.")
        row["status"] = "composted" if action == "compost" else "donated"
        persist("listings", row)
        ledger(row, action, f"external:{row['id']}", destination_id=option["id"])
        return jsonify(listing=row, message="Completed drop-off recorded as user-reported, not organization-verified."), 201

    # Retire the unsafe shortcut that recorded a donation with no destination.
    def require_outcome(listing_id):
        raise BadRequest("Choose a verified destination and explicitly report the completed drop-off through /api/outcomes.")
    app.view_functions["mark_donated"] = require_outcome
