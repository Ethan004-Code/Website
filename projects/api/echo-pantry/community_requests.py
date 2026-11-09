"""Looking for board and responses using existing, atomically saved handoffs."""
from uuid import uuid4

from flask import g, jsonify, request
from werkzeug.exceptions import BadRequest, Conflict, Forbidden, NotFound, Unauthorized

from rules import available, blocked, fulfills, get_row, instant, matches, now, overlap, own_listing, pickup_spot, requested_coverage, coverage_message, annotate_requested_items, selected_items, swap_food


PUBLIC_FIELDS = ("id", "user_id", "name", "items", "quantity", "unit",
                 "location", "pickup_start", "pickup_end", "deadline", "status")


def signed_in():
    if not getattr(g, "user", None):
        raise Unauthorized("Sign in to see what neighbors are looking for.")


def open_request(row):
    if row.get("listing_id") or not row.get("user_id") or row.get("status") != "open":
        return False
    if row["user_id"] not in g.active_users:
        return False
    try:
        return (isinstance(row.get("items"), list) and len(row["items"]) == 1
                and isinstance(row["items"][0], str) and bool(row["items"][0].strip())
                and bool(row.get("location"))
                and instant(row["pickup_start"]) < instant(row["pickup_end"])
                and min(instant(row["deadline"]), instant(row["pickup_end"])) > now())
    except (BadRequest, KeyError):
        return False


def public_request(row):
    return {key: row.get(key) for key in PUBLIC_FIELDS}


def accessible_request(identifier):
    signed_in()
    row = get_row("food_requests", identifier)
    if not open_request(row) or blocked(g.user["id"], row.get("user_id")):
        raise NotFound("This request is no longer available.")
    if row["user_id"] == g.user["id"]:
        raise Forbidden("You cannot offer food to your own request.")
    return row


def offer_options(wanted):
    """Offer only owned reviewed food; swaps retain reciprocal full-bundle rules."""
    result = []
    for listing in g.store["listings"]:
        if (listing["user_id"] != g.user["id"] or not available(listing)
                or listing["user_id"] not in g.active_users):
            continue
        swap = listing.get("mode") == "exchange"
        pickup = overlap(listing, wanted, require_location=not swap)
        fits_food = (any(swap_food(item["name"], item.get("unit")) == swap_food(wanted["items"][0], wanted.get("unit"))
                          for item in listing.get("item_details", [])) if swap else
                     fulfills(listing, {"wanted_food": wanted["items"][0], "wanted_quantity": wanted.get("quantity"),
                                        "wanted_unit": wanted.get("unit")}))
        if not pickup or not fits_food:
            continue
        returns = []
        if listing["mode"] == "giveaway":
            if any(r.get("listing_id") == listing["id"]
                   and r.get("requester_id") == wanted["user_id"]
                   and r.get("status") not in {"declined", "expired", "completed"}
                   for r in g.store["food_requests"]):
                continue
        elif listing["mode"] == "exchange":
            for match in matches(listing):
                other = match["listing"]
                if other["user_id"] != wanted["user_id"]:
                    continue
                exact = overlap(other, dict(wanted, pickup_start=pickup[0], pickup_end=pickup[1]), require_location=False)
                if exact:
                    minimums = list(match["give_minimum_quantities"])
                    requested_amount = wanted.get("quantity") or 1
                    for index, item in enumerate(listing.get("item_details", [])):
                        if swap_food(item["name"], item.get("unit")) == swap_food(wanted["items"][0], wanted.get("unit")):
                            minimums[index] = max(minimums[index], requested_amount)
                            break
                    returns.append(dict(other, offer_pickup_start=exact[0], offer_pickup_end=exact[1],
                                        minimum_give_quantities=minimums,
                                        minimum_receive_quantities=match["receive_minimum_quantities"]))
            if not returns:
                continue
        else:
            continue
        result.append(dict(listing=listing, return_listings=returns, location=listing["location"],
                           pickup_start=pickup[0], pickup_end=pickup[1]))
    return result


def install_community_requests(app, persist, notify):
    @app.get("/api/community-requests")
    def community_requests():
        signed_in()
        rows = [public_request(r) for r in g.store["food_requests"]
                if open_request(r) and not blocked(g.user["id"], r["user_id"])]
        return jsonify(requests=sorted(rows, key=lambda r: r["deadline"]))

    @app.get("/api/community-requests/<request_id>/options")
    def community_request_options(request_id):
        wanted = accessible_request(request_id)
        return jsonify(request=public_request(wanted), options=offer_options(wanted))

    @app.post("/api/community-requests/<request_id>/offers")
    def community_request_offer(request_id):
        wanted = accessible_request(request_id)
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict) or not isinstance(data.get("listing_id"), str):
            raise BadRequest("Choose one of your food listings.")
        listing = own_listing(data["listing_id"])
        option = next((o for o in offer_options(wanted) if o["listing"]["id"] == listing["id"]), None)
        if not option:
            raise Conflict("This food no longer matches the request or already has an offer. Refresh the available options.")
        if listing["mode"] == "giveaway":
            # Sending is the giver's acceptance. The receiver must explicitly
            # accept before reservation; both must still confirm the handoff.
            row = dict(id=str(uuid4()), listing_id=listing["id"], owner_id=g.user["id"],
                       requester_id=wanted["user_id"], requester_name=wanted["name"],
                       accepted_by=[g.user["id"]], completed_by=[], status="pending",
                       item_details=listing["item_details"], location=option["location"],
                       pickup_start=option["pickup_start"], pickup_end=option["pickup_end"],
                       created_at=now().isoformat())
            persist("food_requests", row)
            notify(wanted["user_id"], "food_offer",
                   f"{g.user['name']} offered food for your request. Review it in Activity → Getting.", row["id"])
            return jsonify(kind="giveaway", request=row), 201
        other = next((r for r in option["return_listings"] if r["id"] == data.get("match_id")), None)
        if not other:
            raise Conflict("Choose a compatible return listing owned by the requester.")
        pickup = overlap(other, dict(wanted, pickup_start=option["pickup_start"], pickup_end=option["pickup_end"]), require_location=False)
        location = pickup_spot(data.get("location", option["location"]))
        give_items = selected_items(listing["item_details"], data.get("give_quantities"), "offered")
        receive_items = selected_items(other["item_details"], data.get("receive_quantities"), "received")
        error, _ = requested_coverage(give_items, other)
        if not error and not fulfills(dict(item_details=give_items), dict(
                wanted_food=wanted["items"][0], wanted_unit=wanted.get("unit"),
                wanted_quantity=wanted.get("quantity"))):
            error = "quantity"
            other_wanted = dict(wanted_items=[dict(name=wanted["items"][0],
                                                   quantity=wanted.get("quantity") or 1,
                                                   unit=wanted.get("unit") or "each")])
        else:
            other_wanted = other
        if error:
            raise BadRequest("The selected amounts don't satisfy every required item. "
                             + coverage_message(error, other_wanted))
        error, _ = requested_coverage(receive_items, listing)
        if error:
            raise BadRequest("The selected return amounts don't satisfy every item you requested. "
                             + coverage_message(error, listing))
        give_items = annotate_requested_items(give_items, other)
        receive_items = annotate_requested_items(receive_items, listing)
        row = dict(id=str(uuid4()), listing_id=listing["id"], match_id=other["id"],
                   give=[item["name"] for item in give_items], receive=[item["name"] for item in receive_items],
                   give_items=give_items, receive_items=receive_items,
                   participants=[dict(user_id=r["user_id"], name=r["name"]) for r in (listing, other)],
                   accepted_by=[g.user["id"]], completed_by=[], status="awaiting_second_acceptance",
                   pickup_start=pickup[0], pickup_end=pickup[1], location=location,
                   created_at=now().isoformat())
        persist("trade_offers", row)
        notify(wanted["user_id"], "trade_offer",
               f"{g.user['name']} proposed a swap for your request. Review the selected amounts in Activity → Getting.", row["id"])
        return jsonify(kind="exchange", offer=row), 201
