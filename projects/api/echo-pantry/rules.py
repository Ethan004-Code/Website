"""Deterministic eligibility, exact pickup matching and handoff state machine."""
import math
import re
import copy
from collections import defaultdict
from datetime import datetime, date, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from flask import g, jsonify, request
from werkzeug.exceptions import BadRequest, Forbidden, NotFound, Conflict

UNITS = {"each", "g", "kg", "oz", "lb", "bag", "can", "box", "bottle", "package"}
DATE_TYPES = {"best_by", "use_by", "sell_by", "expiration"}
FOOD_CATEGORIES = {
    "fruits", "vegetables", "snacks", "grains_pantry",
    "canned_packaged", "beverages", "other",
}


def listing_food_category(item):
    value = item.get("food_category") if isinstance(item, dict) else None
    return value if value in FOOD_CATEGORIES else "other"


def now():
    return datetime.now(timezone.utc)


def instant(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError()
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError):
        raise BadRequest("Choose an exact pickup date/time with timezone.")


def text(value, label, limit=80):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= limit:
        raise BadRequest(f"Enter {label} (up to {limit} characters).")
    return value.strip()


def quantity(value):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not 1 <= value <= 99 or int(value) != value):
        raise BadRequest("Enter a whole-number quantity from 1 to 99.")
    return int(value)


def calendar_years(value, years):
    """Calendar-year bounds, clamping February 29 to February 28."""
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        return value.replace(year=value.year + years, day=28)


def pickup_bounds(listing=None, stamp=None):
    stamp = stamp or now()
    lower, upper = stamp, calendar_years(stamp, 1)
    if listing is not None:
        lower = max(lower, instant(listing["pickup_start"]))
        upper = min(upper, instant(listing["pickup_end"]), instant(listing["deadline"]))
    return lower, upper


def validate_pickup_range(start, end, deadline, listing=None):
    lower, upper = pickup_bounds(listing)
    if not (lower <= start < end <= deadline <= upper):
        raise BadRequest(f"Pick a time between {lower.isoformat()} and {upper.isoformat()}. "
                         "The end must be after the start and no later than Available until.")


def norm(value):
    # Shared case/whitespace normalization; swaps use their explicit food forms below.
    return re.sub(r"\s+", " ", value.strip().casefold())


def pickup_spot(value):
    if isinstance(value, str) and len(value) > 100:
        raise BadRequest("Pickup spot must be 100 characters or fewer.")
    return text(value, "a public pickup spot", limit=100)


def window(data):
    start, end, deadline = (instant(data.get(key)) for key in ("pickup_start", "pickup_end", "deadline"))
    validate_pickup_range(start, end, deadline)
    return start.isoformat(), end.isoformat(), deadline.isoformat()


def eligible(item, tz="America/Los_Angeles"):
    if not isinstance(item, dict):
        raise BadRequest("Each food item needs editable details.")
    name = text(item.get("name"), "a food name")
    amount = quantity(item.get("quantity"))
    unit = str(item.get("unit") or "each").strip().lower()
    if unit not in UNITS:
        raise BadRequest("Choose an explicit supported unit. No automatic unit conversions.")
    food_category = item.get("food_category")
    if food_category not in FOOD_CATEGORIES:
        raise BadRequest("Choose a food category for every listed item.")
    category = item.get("category")
    if category not in {"produce", "shelf_stable"}:
        raise BadRequest("Only whole produce and unopened shelf-stable packaged food are eligible.")
    if category == "shelf_stable" and item.get("unopened") is not True:
        raise BadRequest("Packaged food must be unopened.")
    # Self-reported category never overrides obvious exclusions.
    if re.search(r"\b(homemade|opened|raw|meat|chicken|fish|seafood|milk|cheese|yogurt|dairy|refrigerated|frozen|cooked|cut|chopped)\b", name, re.I):
        raise BadRequest("This food is outside the conservative whole-produce / shelf-stable pilot policy.")
    printed, kind = item.get("printed_date") or None, item.get("date_type") or None
    if bool(printed) != bool(kind) or kind and kind not in DATE_TYPES:
        raise BadRequest("Record both the printed date and its label type, or leave both blank.")
    if printed:
        try:
            parsed = date.fromisoformat(printed)
            today = now().astimezone(ZoneInfo(tz)).date()
            lower, upper = calendar_years(today, -2), calendar_years(today, 10)
            if not lower <= parsed <= upper:
                raise BadRequest(f"Pick a printed date between {lower.isoformat()} and {upper.isoformat()}.")
            if parsed < today and kind in {"use_by", "expiration"}:
                raise BadRequest("Use by and Expiration dates cannot be in the past.")
        except (ValueError, TypeError):
            raise BadRequest("Printed dates must use YYYY-MM-DD.")
    return dict(name=name, quantity=amount, unit=unit, category=category,
                food_category=food_category,
                unopened=item.get("unopened") is True if category == "shelf_stable" else None,
                printed_date=printed, date_type=kind)


def available(listing):
    if listing.get("status") != "open" or listing.get("inedible_confirmed"):
        return False
    if not listing.get("photo_id") or not listing.get("bob_review_id"):
        # Retain legacy rows for their owners, but require re-review before
        # publicly offering food under the new mandatory photo policy.
        return False
    try:
        if instant(listing["deadline"]) <= now() or instant(listing["pickup_end"]) <= now():
            return False
        local_date = now().astimezone(ZoneInfo(listing.get("timezone", "America/Los_Angeles"))).date()
        details = listing.get("item_details")
        return bool(details) and all(not i.get("printed_date")
                                    or i.get("date_type") in {"best_by", "sell_by"}
                                    or date.fromisoformat(i["printed_date"]) >= local_date for i in details)
    except (BadRequest, KeyError, ValueError):
        # Legacy rows without explicit structured pickup/units cannot be matched.
        return False


def overlap(a, b, *, require_location=True):
    try:
        if require_location and norm(a["location"]) != norm(b["location"]):
            return None
        start = max(instant(a["pickup_start"]), instant(b["pickup_start"]), now())
        end = min(instant(a["pickup_end"]), instant(b["pickup_end"]),
                  instant(a["deadline"]), instant(b["deadline"]))
        return (start.isoformat(), end.isoformat()) if start < end else None
    except (BadRequest, KeyError):
        return None


def counted_food(name, unit="each"):
    """Same count identity in old unit columns and new unit-in-name forms.

    Only recognize the explicit legacy unit vocabulary; never convert weights
    or infer sizes, synonyms, or singular/plural ingredient names.
    """
    name, unit = norm(name), unit or "each"
    plurals = {"bags": "bag", "cans": "can", "boxes": "box",
               "bottles": "bottle", "packages": "package"}
    match = re.fullmatch(r"(bag|bags|can|cans|box|boxes|bottle|bottles|package|packages|g|kg|oz|lb) of (.+)", name)
    if match:
        encoded_unit = plurals.get(match[1], match[1])
        if unit in {"each", encoded_unit}:
            return encoded_unit, match[2]
    return unit, name


_SWAP_PLURALS = {
    "apples": "apple", "bananas": "banana", "oranges": "orange", "onions": "onion",
    "potatoes": "potato", "tomatoes": "tomato", "carrots": "carrot", "cucumbers": "cucumber",
    "peppers": "pepper", "avocados": "avocado", "lemons": "lemon", "limes": "lime",
    "mangos": "mango", "mangoes": "mango", "peaches": "peach", "pears": "pear",
    "berries": "berry", "grapes": "grape", "beans": "bean", "peas": "pea",
    "zucchinis": "zucchini", "courgettes": "courgette", "eggplants": "eggplant",
    "aubergines": "aubergine", "radishes": "radish", "turnips": "turnip",
    "beets": "beet", "pumpkins": "pumpkin", "squashes": "squash", "melons": "melon",
}


def swap_food(name, unit="each"):
    """Compare swap food identity, not the amount/unit embedded in its name.

    Units and quantities stay unchanged on offers. Both participants see and
    choose the actual amounts before accepting; no physical-unit conversion.
    """
    value = norm(name)
    value = re.sub(r"^\d+(?:\.\d+)?\s+", "", value)
    value = re.sub(r"^(?:g|gram|grams|kg|kilogram|kilograms|oz|ounce|ounces|lb|lbs|pound|pounds)\s+of\s+", "", value)
    value = re.sub(r"^(?:each|bag|bags|can|cans|box|boxes|bottle|bottles|package|packages)\s+of\s+", "", value)
    return _SWAP_PLURALS.get(value, value)


_WANTED_UNIT_PREFIXES = {
    "bag": "bag", "bags": "bag", "can": "can", "cans": "can",
    "box": "box", "boxes": "box", "bottle": "bottle", "bottles": "bottle",
    "package": "package", "packages": "package", "g": "g", "gram": "g",
    "grams": "g", "kg": "kg", "kilogram": "kg", "kilograms": "kg",
    "oz": "oz", "ounce": "oz", "ounces": "oz", "lb": "lb", "lbs": "lb",
    "pound": "lb", "pounds": "lb",
}


def swap_identity(item):
    """Return normalized food and exact unit, recognizing only explicit legacy prefixes."""
    if not isinstance(item, dict) or not isinstance(item.get("name"), str):
        return "", ""
    value = norm(item["name"])
    value = re.sub(r"^\d+(?:\.\d+)?\s+", "", value)
    unit = item.get("unit") or "each"
    prefix = re.match(r"^([a-z]+)\s+of\s+(.+)$", value)
    if prefix and prefix[1] in _WANTED_UNIT_PREFIXES:
        stated = _WANTED_UNIT_PREFIXES[prefix[1]]
        value = prefix[2]
        if unit == "each":
            unit = stated
        elif unit != stated:
            unit = f"incompatible:{unit}:{stated}"
    return swap_food(value), unit


def requested_items(listing):
    """New multi-item wants, with a one-item legacy fallback for existing rows."""
    values = listing.get("wanted_items")
    if isinstance(values, list) and values:
        return [item for item in values if isinstance(item, dict)]
    name = listing.get("wanted_food")
    if not isinstance(name, str) or not name.strip():
        legacy_names = listing.get("wants") or []
        name = legacy_names[0] if isinstance(legacy_names, list) and legacy_names else ""
    if not isinstance(name, str) or not name.strip():
        return []
    amount = listing.get("wanted_quantity")
    if isinstance(amount, bool) or not isinstance(amount, int) or not 1 <= amount <= 99:
        amount = 1
    unit = listing.get("wanted_unit")
    return [dict(name=name, quantity=amount, unit=unit if unit in UNITS else "each")]


def requested_coverage(offered_items, wanted_listing):
    """Return (reason, per-offer minimum quantities) for all requested items."""
    required = requested_items(wanted_listing)
    if not required:
        return "food", []
    offered = list(offered_items or [])
    minimums = [1] * len(offered)
    used = set()
    for need in required:
        need_name, need_unit = swap_identity(need)
        same_food, same_unit = [], []
        for index, item in enumerate(offered):
            food, unit = swap_identity(item)
            if food == need_name and food:
                same_food.append((index, item, unit))
                if unit == need_unit:
                    same_unit.append((index, item))
        if not same_food:
            return {"kind": "food", "item": need}, []
        if not same_unit:
            return {"kind": "unit", "item": need,
                    "available_units": sorted({unit for _, _, unit in same_food})}, []
        amount = need.get("quantity")
        if isinstance(amount, bool) or not isinstance(amount, int) or not 1 <= amount <= 99:
            amount = 1
        candidates = [(index, item) for index, item in same_unit
                      if index not in used and isinstance(item.get("quantity"), int)
                      and not isinstance(item.get("quantity"), bool)
                      and item["quantity"] >= amount]
        if not candidates:
            available = max((item.get("quantity", 0) for _, item in same_unit
                             if isinstance(item.get("quantity"), int)
                             and not isinstance(item.get("quantity"), bool)), default=0)
            return {"kind": "quantity", "item": need, "unit": need_unit,
                    "available": available}, []
        index, _ = min(candidates, key=lambda pair: pair[1]["quantity"])
        used.add(index)
        minimums[index] = max(minimums[index], amount)
    return "", minimums


def coverage_message(reason, wanted_listing):
    detail = reason if isinstance(reason, dict) else {"kind": reason}
    item = detail.get("item") or (requested_items(wanted_listing) or [{"name": "the requested food"}])[0]
    name = item.get("name") or "the requested food"
    unit = detail.get("unit") or swap_identity(item)[1] or item.get("unit") or "each"
    amount = item.get("quantity") or 1
    if detail["kind"] == "unit":
        available = ", ".join(detail.get("available_units") or ["another unit"])
        return f'Unit mismatch: "{name}" is requested in {unit}, but the available listing uses {available}. No unit conversions are applied.'
    if detail["kind"] == "quantity":
        available = detail.get("available", 0)
        return f'Quantity mismatch: at least {amount} {unit} of "{name}" is requested, but only {available} is available.'
    return f'Food mismatch: no offered item satisfies "{name}".'


def annotate_requested_items(offered_items, wanted_listing):
    """Keep each fulfilled minimum with the corresponding offer item for clear consent."""
    required = requested_items(wanted_listing)
    for item in offered_items:
        identity = swap_identity(item)
        need = next((entry for entry in required if swap_identity(entry) == identity), None)
        if need:
            amount = need.get("quantity")
            item["requested_minimum"] = amount if isinstance(amount, int) and not isinstance(amount, bool) else 1
            item["requested_unit"] = identity[1]
    return offered_items


def selected_items(items, values, field):
    """Return immutable, item-order-preserving amounts selected for a swap."""
    if values is None:
        return copy.deepcopy(items)
    if not isinstance(values, list) or len(values) != len(items):
        raise BadRequest(f"Choose a quantity for every {field} item.")
    result = []
    for item, value in zip(items, values):
        amount = quantity(value)
        if amount > item["quantity"]:
            raise BadRequest(f"A {field} quantity can't exceed the available amount ({item['quantity']}).")
        selected = copy.deepcopy(item)
        if amount < item["quantity"]:
            selected["listed_quantity"] = item["quantity"]
        selected["quantity"] = amount
        result.append(selected)
    return result


def check_selected_items(snapshot, current):
    """Reject stale offers and quantities that exceed today's remaining stock."""
    if not isinstance(snapshot, list) or len(snapshot) != len(current):
        raise Conflict("Food contents changed. Create a new offer.")
    for offer_item, current_item in zip(snapshot, current):
        if not isinstance(offer_item, dict):
            raise Conflict("Food contents changed. Create a new offer.")
        listed_quantity = offer_item.get("listed_quantity", offer_item.get("quantity"))
        expected = copy.deepcopy(offer_item)
        expected.pop("listed_quantity", None)
        expected.pop("requested_minimum", None)
        expected.pop("requested_unit", None)
        expected["quantity"] = listed_quantity
        if expected != current_item or not isinstance(offer_item.get("quantity"), int) or offer_item["quantity"] > current_item["quantity"]:
            raise Conflict("Food contents or available quantities changed. Create a new offer.")


def remaining_items(listing, traded):
    remaining = []
    for source, selected in zip(listing["item_details"], traded):
        amount = source["quantity"] - selected["quantity"]
        if amount > 0:
            item = copy.deepcopy(source)
            item["quantity"] = amount
            remaining.append(item)
    listing["item_details"] = remaining
    listing["items"] = [item["name"] for item in remaining]
    listing["quantities"] = {item["name"]: item["quantity"] for item in remaining}
    listing["status"] = "open" if remaining else "traded"
    listing["reservation_id"] = None
    return listing


def fulfills(listing, wanted, *, require_quantity=True):
    wanted_name, unit = wanted.get("wanted_food", ""), wanted.get("wanted_unit")
    amount = wanted.get("wanted_quantity")
    return any(counted_food(i["name"], i.get("unit")) == counted_food(wanted_name, unit)
               and (not require_quantity or amount is None or i["quantity"] >= amount)
               for i in listing.get("item_details", []))


def blocked(a, b):
    return any({r["user_id"], r["blocked_user_id"]} == {a, b} for r in g.store["user_blocks"])


def matches(listing):
    if not available(listing) or listing["mode"] != "exchange":
        return []
    result = []
    for other in g.store["listings"]:
        if other["user_id"] == listing["user_id"] or other["mode"] != "exchange" or not available(other):
            continue
        if other["user_id"] not in g.active_users or blocked(listing["user_id"], other["user_id"]):
            continue
        pickup = overlap(listing, other, require_location=False)
        give_error, give_minimums = requested_coverage(listing.get("item_details"), other)
        receive_error, receive_minimums = requested_coverage(other.get("item_details"), listing)
        if not pickup or give_error or receive_error:
            continue
        if any(o["status"] not in {"declined", "completed"} and
               {o["listing_id"], o["match_id"]} == {listing["id"], other["id"]}
               for o in g.store["trade_offers"]):
            continue
        result.append(dict(listing=other, give=listing["items"], receive=other["items"],
                           give_items=listing["item_details"], receive_items=other["item_details"],
                           give_minimum_quantities=give_minimums,
                           receive_minimum_quantities=receive_minimums,
                           pickup_start=pickup[0], pickup_end=pickup[1], location=listing["location"],
                           reason="Every requested item is covered in its listed unit and minimum quantity. Choose amounts in each item's original unit; both people must accept. Pickup times overlap."))
    # Urgency first, then longer pickup overlap; never claims travel optimization.
    result.sort(key=lambda m: (m["listing"]["deadline"],
                -(instant(m["pickup_end"]) - instant(m["pickup_start"])).total_seconds(), m["listing"]["id"]))
    return result


def food_matches(offer, wanted):
    reason, _ = requested_coverage(offer.get("item_details"), wanted)
    return not reason


def match_incompatibilities(listing):
    diagnostics = []
    for target in g.store["listings"]:
        if (target["user_id"] == listing["user_id"] or target.get("mode") != "exchange"
                or target.get("status") not in {"open", "reserved", "expired"}
                or target["user_id"] not in g.active_users
                or blocked(listing["user_id"], target["user_id"])):
            continue
        if not available(target):
            reason = "This listing has expired or is not currently available."
        elif not available(listing):
            reason = "Your listing is expired, reserved, or no longer eligible."
        else:
            give_error, _ = requested_coverage(listing.get("item_details"), target)
            receive_error, _ = requested_coverage(target.get("item_details"), listing)
            if give_error:
                reason = coverage_message(give_error, target)
            elif receive_error:
                reason = coverage_message(receive_error, listing)
            elif not overlap(listing, target, require_location=False):
                reason = "Pickup windows do not overlap before the listings expire."
            elif any(o["status"] not in {"declined", "completed"} and
                     {o["listing_id"], o["match_id"]} == {listing["id"], target["id"]}
                     for o in g.store["trade_offers"]):
                reason = "An offer already exists for these listings. Review it in Activity."
            else:
                continue
        diagnostics.append({"listing_id": target["id"],
                            "items": [item.get("name", "") for item in target.get("item_details", [])],
                            "message": reason})
    return diagnostics


def get_row(table, identifier):
    row = next((r for r in g.store[table] if r["id"] == identifier), None)
    if row is None:
        raise NotFound("This record was not found.")
    return row


def own_listing(identifier):
    row = get_row("listings", identifier)
    if row["user_id"] != g.user["id"]:
        raise Forbidden("Only the listing owner can do this.")
    return row


def participants(row, giveaway=False):
    return {row["owner_id"], row["requester_id"]} if giveaway else {p["user_id"] for p in row["participants"]}


def validate_actor(row, giveaway=False):
    if g.user["id"] not in participants(row, giveaway):
        raise Forbidden("Only a participant can act on this handoff.")


def install_rules(app, legacy):
    save = legacy["save_row"]
    notify = legacy["create_notification"]

    def replace(endpoint):
        def decorate(fn):
            app.view_functions[endpoint] = fn
            return fn
        return decorate

    def persist(table, row):
        if not any(r["id"] == row["id"] for r in g.store[table]):
            g.store[table].append(row)
        save(table, row)
        return row

    def ledger(listing, action, identity, offer_id=None, destination_id=None):
        for index, item in enumerate(listing["item_details"]):
            # UUID5 deterministic identity + DB unique key -> exactly once.
            from uuid import uuid5, NAMESPACE_URL
            key = f"{identity}:{listing['id']}:{index}"
            persist("impact_records", dict(
                id=str(uuid5(NAMESPACE_URL, key)), listing_id=listing["id"],
                offer_id=offer_id, user_id=listing["user_id"], action=action,
                item_count=0, item_name=item["name"], quantity=item["quantity"],
                unit=item["unit"], identity=key, destination_id=destination_id,
                confirmation_type="both_participants" if action in {"exchange", "giveaway"} else "user_reported",
                is_test=listing.get("is_test", False), created_at=now().isoformat()))

    @replace("health")
    def health():
        import shutil
        from bob import executable
        import os
        setup = []
        db = legacy["supabase"]
        storage = "unavailable"
        schema_version = None
        if db:
            try:
                snapshot = db.rpc("echopantry_snapshot").execute().data
                storage = "supabase"
                schema_version = snapshot.get("schema_version", 0)
                if schema_version < 4:
                    setup.append("Apply backend/interview_migration.sql to enable multi-item exchange requests and current backend features. Existing records are preserved.")
            except Exception:
                setup.append("Supabase connection or schema unavailable. Apply the supplied schema and interview migration to the existing project.")
        else:
            setup.append("Supabase URL or keys are invalid or incomplete.")
        bob_ready = bool(executable() and os.environ.get("BOB_API_KEY"))
        if not bob_ready:
            setup.append("IBM Bob runtime is unavailable; manual entry remains available after database setup.")
        return jsonify(status="ok" if not setup else "setup_required", backend="Flask",
                       storage=storage, authentication="supabase-auth",
                       bob="configured_not_verified" if bob_ready else "unavailable",
                       schema_version=schema_version, setup_required=setup)

    app.add_url_rule("/api/healthz", "healthz", lambda: jsonify(status="ok"))

    @replace("get_listings")
    def get_listings():
        mine = request.args.get("mine") == "true"
        food_category = request.args.get("food_category")
        if food_category and food_category not in FOOD_CATEGORIES:
            raise BadRequest("Choose a supported food category.")
        if mine and not getattr(g, "user", None):
            raise Forbidden("Sign in to see your listings.")
        uid = g.user["id"] if getattr(g, "user", None) else None
        rows = [r for r in g.store["listings"] if
                (r["user_id"] == uid if mine else available(r))
                and r["user_id"] in g.active_users
                and (not uid or not blocked(uid, r["user_id"]))
                and (not food_category or any(
                    listing_food_category(item) == food_category
                    for item in (r.get("item_details") or [])
                ))]
        received_by_user = {}
        for rating in g.store["ratings"]:
            target_id, stars = rating.get("to_user_id"), rating.get("stars")
            if (not isinstance(target_id, str) or not target_id
                    or not isinstance(stars, int) or isinstance(stars, bool)
                    or not 1 <= stars <= 5):
                continue
            received_by_user.setdefault(target_id, []).append(stars)
        enriched = []
        for row in sorted(rows, key=lambda r: r["deadline"]):
            scores = received_by_user.get(row["user_id"], [])
            enriched.append({
                **row,
                "rating": round(sum(scores) / len(scores), 2) if scores else None,
                "rating_count": len(scores),
            })
        return jsonify(listings=enriched)

    @replace("create_listing")
    def create_listing():
        from listing_review import validated_fields, consume_approval
        data = request.get_json(silent=True) or {}
        fields = validated_fields(data)
        listing_id = str(uuid4())
        approval = consume_approval(data, fields, listing_id, persist)
        row = dict(id=listing_id, user_id=g.user["id"], name=g.user["name"],
                   **fields, **approval,
                   rating=None, trades=0, status="open", created_at=now().isoformat())
        persist("listings", row)
        # Standalone requests can ask for any amount or a minimum quantity.
        for r in g.store["food_requests"]:
            if r.get("status") != "open" or not r.get("user_id") or r["user_id"] == row["user_id"]:
                continue
            if (not blocked(r["user_id"], row["user_id"]) and
                    fulfills(row, {"wanted_food": (r.get("items") or [""])[0], "wanted_unit": r.get("unit"), "wanted_quantity": r.get("quantity")}) and overlap(row, r, require_location=row["mode"] != "exchange")):
                notify(r["user_id"], "request_match", "A quantity-compatible listing overlaps your public pickup window.", row["id"])
        return jsonify(listing=row, matches=matches(row)), 201

    @replace("get_matches")
    def get_matches(listing_id):
        listing = own_listing(listing_id)
        result = {"matches": matches(listing), "incompatibilities": match_incompatibilities(listing)}
        return jsonify(result)
    app.add_url_rule("/api/listings/<listing_id>/matches", "get_matches_read", get_matches, methods=["GET"])

    @app.route("/api/listings/<listing_id>", methods=["PATCH", "DELETE"])
    def change_listing(listing_id):
        row = own_listing(listing_id)
        if row["status"] not in {"open", "expired"}:
            raise Conflict("Only an unreserved, unresolved listing can change.")
        data = request.get_json(silent=True) or {}
        if request.method == "PATCH" and data.get("approval_id"):
            from listing_review import validated_fields, consume_approval
            fields = validated_fields(data)
            approval = consume_approval(data, fields, listing_id, persist)
            row.update(**fields, **approval, status="open", reservation_id=None)
            persist("listings", row)
            return jsonify(listing=row)
        if request.method == "DELETE":
            row["status"] = "withdrawn"
        elif data.get("inedible_confirmed") is True:
            row["inedible_confirmed"] = True
            row["status"] = "withdrawn"
        else:
            raise BadRequest("Changed listing details, including a giveaway switch, require a new Bob/photo review.")
        persist("listings", row)
        return jsonify(listing=row)

    @replace("create_offer")
    def create_offer():
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            raise BadRequest("Choose a valid swap.")
        row = own_listing(data.get("listing_id"))
        candidate = next((m for m in matches(row) if m["listing"]["id"] == data.get("match_id")), None)
        if not candidate:
            raise Conflict("That match is no longer compatible or available.")
        other = candidate["listing"]
        location = pickup_spot(data.get("location", row["location"]))
        give_items = selected_items(row["item_details"], data.get("give_quantities"), "offered")
        receive_items = selected_items(other["item_details"], data.get("receive_quantities"), "received")
        error, _ = requested_coverage(give_items, other)
        if error:
            raise BadRequest("Your selected amounts must satisfy every item requested by the other neighbor. "
                             + coverage_message(error, other))
        error, _ = requested_coverage(receive_items, row)
        if error:
            raise BadRequest("Your selected amounts must satisfy every item you requested. "
                             + coverage_message(error, row))
        give_items = annotate_requested_items(give_items, other)
        receive_items = annotate_requested_items(receive_items, row)
        offer = dict(id=str(uuid4()), listing_id=row["id"], match_id=other["id"],
                     give=[item["name"] for item in give_items], receive=[item["name"] for item in receive_items],
                     give_items=give_items, receive_items=receive_items, pickup_start=candidate["pickup_start"],
                     pickup_end=candidate["pickup_end"], location=location,
                     participants=[dict(user_id=r["user_id"], name=r["name"]) for r in [row, other]],
                     accepted_by=[], completed_by=[], status="pending", created_at=now().isoformat())
        persist("trade_offers", offer)
        notify(other["user_id"], "trade_offer", "Review the exact items and overlapping pickup window before responding.", offer["id"])
        return jsonify(offer=offer), 201

    def transition(identifier, giveaway=False, action="respond"):
        table = "food_requests" if giveaway else "trade_offers"
        row = get_row(table, identifier)
        validate_actor(row, giveaway)
        uid = g.user["id"]
        involved = [get_row("listings", row["listing_id"])]
        if not giveaway:
            involved.append(get_row("listings", row["match_id"]))
        members = participants(row, giveaway)
        if action == "complete":
            if row["status"] == "completed":
                return row
            if row["status"] not in {"confirmed", "approved", "completion_pending"}:
                raise Conflict("Only an approved handoff can be confirmed.")
            if any(r.get("reservation_id") != row["id"] or r["status"] != "reserved" for r in involved):
                raise Conflict("The reservation no longer belongs to this handoff.")
            row["completed_by"] = sorted(set(row.get("completed_by", [])) | {uid})
            if set(row["completed_by"]) == members:
                row["status"] = "completed"
                for index, listing in enumerate(involved):
                    if giveaway:
                        traded = listing
                        listing["status"] = "given_away"
                    else:
                        traded_items = row["give_items" if index == 0 else "receive_items"]
                        traded = copy.deepcopy(listing)
                        traded["item_details"] = [{k: v for k, v in item.items() if k != "listed_quantity"} for item in traded_items]
                        remaining_items(listing, traded_items)
                    ledger(traded, "giveaway" if giveaway else "exchange", row["id"], None if giveaway else row["id"])
                    persist("listings", listing)
            else:
                row["status"] = "completion_pending"
                for other in members - {uid}:
                    notify(other, "giveaway_confirmation" if giveaway else "trade_completion", "Your partner confirmed pickup. Confirm only after your own handoff.", row["id"])
        elif action == "cancel" or (request.get_json(silent=True) or {}).get("accept") is False:
            if row["status"] == "completed" or row.get("completed_by"):
                raise Conflict("A completed or partially confirmed handoff cannot be cancelled or reopened.")
            row["status"] = "declined"
            for listing in involved:
                if listing.get("reservation_id") == row["id"] and listing["status"] == "reserved":
                    listing.update(status="open", reservation_id=None)
                    persist("listings", listing)
            for other in members - {uid}:
                own = next((l for l in involved if l["user_id"] == other), involved[0])
                notify(other, "trade_declined", "Handoff declined or cancelled. Review alternative matches or donation options.", own["id"])
        else:
            if (request.get_json(silent=True) or {}).get("accept") is not True:
                raise BadRequest("Choose accept or decline explicitly.")
            if row["status"] in {"confirmed", "approved", "completion_pending", "completed"}:
                return row
            if row["status"] == "declined":
                raise Conflict("A declined offer cannot be accepted again.")
            if giveaway and uid in (row.get("accepted_by") or [row["requester_id"]]):
                raise Forbidden("You already accepted. The other person must accept this giveaway.")
            if any(not available(l) or l["user_id"] not in g.active_users for l in involved):
                raise Conflict("Food is expired, unavailable or already reserved by another handoff.")
            if any(blocked(a, b) for a in members for b in members if a != b):
                raise Forbidden("This handoff is blocked.")
            if instant(row["pickup_end"]) <= now():
                raise Conflict("The pickup window has passed.")
            # Verify selected amounts against a snapshot of the entire listing.
            for index, listing in enumerate(involved):
                snapshot = row.get("item_details") if giveaway else row["give_items" if index == 0 else "receive_items"]
                if giveaway:
                    if snapshot != listing["item_details"]:
                        raise Conflict("Food contents changed. Create a new offer.")
                else:
                    check_selected_items(snapshot, listing["item_details"])
                if ((giveaway and norm(row["location"]) != norm(listing["location"]))
                        or instant(row["pickup_start"]) < instant(listing["pickup_start"])
                        or instant(row["pickup_end"]) > min(instant(listing["pickup_end"]), instant(listing["deadline"]))):
                    raise Conflict("The listing's pickup spot or window changed. Create a new offer.")
            if not giveaway:
                pickup_spot(row.get("location"))
            if not giveaway and (any(l["mode"] != "exchange" for l in involved) or not food_matches(involved[0], involved[1]) or not food_matches(involved[1], involved[0]) or not overlap(*involved, require_location=False)):
                raise Conflict("A requested item, unit, quantity, or pickup window changed. Refresh the compatible alternatives.")
            if not giveaway:
                error, _ = requested_coverage(row.get("give_items"), involved[1])
                if not error:
                    error, _ = requested_coverage(row.get("receive_items"), involved[0])
                if error:
                    raise Conflict("A requested item is no longer satisfied by the selected quantities. Refresh the compatible alternatives.")
            # Legacy receiver-initiated requests implicitly include the receiver.
            # Owner-initiated responses explicitly include the owner instead.
            accepted = row.get("accepted_by") or ([row["requester_id"]] if giveaway else [])
            row["accepted_by"] = sorted(set(accepted) | {uid})
            if set(row["accepted_by"]) == members:
                row["status"] = "approved" if giveaway else "confirmed"
                for listing in involved:
                    listing.update(status="reserved", reservation_id=row["id"])
                    persist("listings", listing)
            else:
                row["status"] = "pending" if giveaway else "awaiting_second_acceptance"
            for other in members - {uid}:
                notify(other, "giveaway_approved" if giveaway else "trade_offer", "Review the current handoff status in My handoffs.", row["id"])
        persist(table, row)
        return row

    @replace("decide_offer")
    def decide_offer(offer_id):
        return jsonify(offer=transition(offer_id))
    app.add_url_rule("/api/offers/<offer_id>/respond", "respond_offer", decide_offer, methods=["POST"])

    @replace("complete_offer")
    def complete_offer(offer_id):
        return jsonify(offer=transition(offer_id, action="complete"))
    app.add_url_rule("/api/offers/<offer_id>/confirm-pickup", "confirm_offer", complete_offer, methods=["POST"])
    app.add_url_rule("/api/offers/<offer_id>/cancel", "cancel_offer", lambda offer_id: jsonify(offer=transition(offer_id, action="cancel")), methods=["POST"])

    @replace("request_giveaway")
    def request_giveaway(listing_id):
        listing = get_row("listings", listing_id)
        if listing["mode"] != "giveaway" or not available(listing):
            raise Conflict("This giveaway is no longer available.")
        if listing["user_id"] == g.user["id"] or blocked(listing["user_id"], g.user["id"]):
            raise Forbidden("You cannot request this giveaway.")
        data = request.get_json(silent=True) or {}
        location = pickup_spot(listing.get("location"))
        if data.get("location") is not None and norm(pickup_spot(data["location"])) != norm(location):
            raise BadRequest("Use this listing's pickup spot. Choose your pickup time, not a different location.")
        recipient = dict(location=location, pickup_start=data.get("pickup_start"),
                         pickup_end=data.get("pickup_end"), deadline=listing["deadline"])
        start, end = instant(recipient["pickup_start"]), instant(recipient["pickup_end"])
        # Do not silently clip an invalid request to an overlapping window.
        validate_pickup_range(start, end, end, listing)
        pickup = overlap(listing, recipient)
        if not pickup:
            raise BadRequest("Your pickup zone and exact availability must overlap the owner's window.")
        if any(r.get("listing_id") == listing_id and r.get("requester_id") == g.user["id"] and r["status"] not in {"declined", "completed"} for r in g.store["food_requests"]):
            raise Conflict("You already have a pending request for this giveaway.")
        row = dict(id=str(uuid4()), listing_id=listing_id, requester_id=g.user["id"],
                   owner_id=listing["user_id"], requester_name=g.user["name"],
                   status="pending", completed_by=[], accepted_by=[g.user["id"]],
                   item_details=listing["item_details"], location=listing["location"],
                   pickup_start=pickup[0], pickup_end=pickup[1], created_at=now().isoformat())
        persist("food_requests", row)
        notify(listing["user_id"], "giveaway_request", f"{g.user['name']} requested your giveaway.", row["id"])
        return jsonify(request=row), 201

    @replace("decide_giveaway")
    def decide_giveaway(request_id):
        return jsonify(request=transition(request_id, True))

    @replace("confirm_giveaway_pickup")
    def confirm_giveaway_pickup(request_id):
        return jsonify(request=transition(request_id, True, "complete"))
    app.add_url_rule("/api/giveaway-requests/<request_id>/cancel", "cancel_giveaway", lambda request_id: jsonify(request=transition(request_id, True, "cancel")), methods=["POST"])

    @replace("create_request")
    def create_request():
        data = request.get_json(silent=True) or {}
        names = data.get("items")
        names = [names] if isinstance(names, str) else names
        if not isinstance(names, list) or len(names) != 1 or not isinstance(names[0], str) or "," in names[0]:
            raise BadRequest("Choose exactly one wanted ingredient for a standalone request.")
        name = text(names[0], "one wanted ingredient")
        amount = quantity(data["quantity"]) if data.get("quantity") is not None else None
        unit = data.get("unit") or "each"
        if unit not in UNITS:
            raise BadRequest("Choose an explicit quantity unit.")
        start, end, due = window(data)
        row = dict(id=str(uuid4()), user_id=g.user["id"], name=g.user["name"],
                   items=[name], quantity=amount, unit=unit, quantities={name: amount} if amount is not None else {},
                   location=pickup_spot(data.get("location")), pickup_start=start, pickup_end=end,
                   deadline=due, status="open", created_at=now().isoformat())
        persist("food_requests", row)
        for listing in g.store["listings"]:
            if (available(listing) and listing["user_id"] != g.user["id"] and
                not blocked(listing["user_id"], g.user["id"]) and overlap(listing, row, require_location=listing["mode"] != "exchange") and
                fulfills(listing, dict(wanted_food=name, wanted_quantity=amount, wanted_unit=unit))):
                notify(g.user["id"], "request_match", "A compatible quantity and pickup window is available.", listing["id"])
        return jsonify(request=row), 201

    @replace("get_requests")
    def get_requests():
        uid = g.user["id"]
        rows = []
        for original in g.store["food_requests"]:
            if uid not in {original.get("user_id"), original.get("owner_id"), original.get("requester_id")}:
                continue
            row = dict(original)
            if row.get("listing_id"):
                listing = next((l for l in g.store["listings"] if l["id"] == row["listing_id"]), None)
                row["owner_name"] = listing.get("name") if listing else None
            elif row.get("status") == "open":
                try:
                    if min(instant(row["deadline"]), instant(row["pickup_end"])) <= now():
                        row["status"] = "expired"
                except (BadRequest, KeyError):
                    row["status"] = "expired"
            rows.append(row)
        return jsonify(requests=rows)

    from community_requests import install_community_requests
    install_community_requests(app, persist, notify)

    @replace("get_notifications")
    def get_notifications():
        rows = []
        for original in g.store["notifications"]:
            if original.get("user_id") != g.user["id"]:
                continue
            row = dict(original)
            handoff = next((r for r in g.store["food_requests"]
                            if r["id"] == row.get("entity_id") and r.get("listing_id")
                            and g.user["id"] in {r.get("owner_id"), r.get("requester_id")}), None)
            if handoff:
                row.update(location=handoff.get("location"), pickup_start=handoff.get("pickup_start"),
                           pickup_end=handoff.get("pickup_end"), requester_name=handoff.get("requester_name"))
                if row.get("kind") == "giveaway_request":
                    row["message"] = f"{handoff.get('requester_name') or 'The requester'} requested your giveaway."
            else:
                trade = next((o for o in g.store["trade_offers"] if o["id"] == row.get("entity_id")
                              and g.user["id"] in {p["user_id"] for p in o.get("participants", [])}), None)
                if trade:
                    row.update(location=trade.get("location"), pickup_start=trade.get("pickup_start"),
                               pickup_end=trade.get("pickup_end"))
                elif row.get("kind") == "request_match":
                    listing = next((l for l in g.store["listings"] if l["id"] == row.get("entity_id")
                                    and (l["user_id"] == g.user["id"] or available(l))), None)
                    if listing:
                        row.update(location=listing.get("location"), pickup_start=listing.get("pickup_start"),
                                   pickup_end=listing.get("pickup_end"))
            rows.append(row)
        return jsonify(notifications=rows)

    @replace("get_impact")
    def get_impact():
        from werkzeug.exceptions import Unauthorized
        if not getattr(g, "user", None):
            raise Unauthorized("Sign in to see your impact")
        uid = g.user["id"]
        # Ledger rows identify the giver. Resolve completed handoffs as well so
        # recipients see their impact without exposing unrelated users' rows.
        trades = {
            row["id"]: {row["listing_id"], row["match_id"]}
            for row in g.store["trade_offers"]
            if row.get("status") == "completed"
            and uid in {p["user_id"] for p in row.get("participants", [])}
            and {p["user_id"] for p in row.get("participants", [])}
                <= set(row.get("completed_by", []))
        }
        giveaways = {
            (row["id"], row["listing_id"])
            for row in g.store["food_requests"]
            if row.get("status") == "completed"
            and uid in {row.get("owner_id"), row.get("requester_id")}
            and {row.get("owner_id"), row.get("requester_id")}
                <= set(row.get("completed_by", []))
        }
        records = []
        for r in g.store["impact_records"]:
            identity = r.get("identity")
            if r.get("is_test") or not isinstance(identity, str) or not identity or r.get("quantity") is None:
                continue
            action = r.get("action")
            if action in {"donation", "compost"}:
                belongs = r.get("user_id") == uid and r.get("confirmation_type") == "user_reported"
            elif action == "exchange":
                belongs = (r.get("listing_id") in trades.get(r.get("offer_id"), set())
                           and r.get("confirmation_type") == "both_participants")
            elif action == "giveaway":
                belongs = ((identity.split(":", 1)[0], r.get("listing_id")) in giveaways
                           and r.get("confirmation_type") == "both_participants")
            else:
                belongs = False
            if belongs:
                records.append(r)
        totals = defaultdict(float)
        for r in records:
            totals[(r["action"], r["item_name"], r["unit"])] += r["quantity"]
        events = lambda action: len({r.get("offer_id") or r["listing_id"] for r in records if r["action"] == action})
        return jsonify(completed_trades=events("exchange"), completed_giveaways=events("giveaway"),
                       donations_reported=events("donation"), compost_reported=events("compost"),
                       quantities=[dict(action=a, name=n, unit=u, quantity=q) for (a, n, u), q in sorted(totals.items())],
                       records=[{key: r.get(key) for key in ("action", "item_name", "quantity", "unit", "confirmation_type", "created_at")} for r in records],
                       note="Your completed handoffs as giver or receiver, confirmed by both participants. Only donation and compost reports you made are included. Unlike units are never summed; no waste, money or emissions estimates.")

    from destinations import install_destinations
    install_destinations(app, legacy, persist, ledger)
    from bob import install_bob
    install_bob(app)
    from photos import install_photos
    from listing_review import install_review
    install_photos(app, persist)
    install_review(app, persist)
