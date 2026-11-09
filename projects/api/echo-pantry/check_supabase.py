"""Explicit, scoped external integration check.

Run only with EP_VERIFY_EXTERNAL=1. Creates and removes its own temporary users
and rows in the configured Supabase project. No emails are sent and no existing
user data is deleted. Credentials and provider messages are never printed.
"""
import concurrent.futures
import json
import os
import secrets
import sys
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from supabase import create_client


def main():
    if os.environ.get("EP_VERIFY_EXTERNAL") != "1":
        raise SystemExit("Set EP_VERIFY_EXTERNAL=1 to authorize temporary external verification records.")
    db = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    if db.rpc("echopantry_snapshot").execute().data.get("schema_version", 0) < 4:
        raise SystemExit("Apply the updated interview_migration.sql before checking multi-item exchange requests.")
    photos_dir = Path(os.environ.get("EP_TEST_PHOTOS_DIR", ""))
    if not os.environ.get("EP_TEST_PHOTOS_DIR") or not all((photos_dir / f"{name}.jpg").is_file() for name in ("onions", "apples", "pears", "rice")):
        raise SystemExit("Set EP_TEST_PHOTOS_DIR to reviewed test fixtures: onions.jpg, apples.jpg, pears.jpg and rice.jpg. Never bypass the real Bob/photo check.")
    root = "http://localhost:80/api"
    users, sessions = [], {}
    suffix = uuid4().hex[:10]
    checks = []
    start = datetime.now(timezone.utc) + timedelta(hours=1)
    end, due = start + timedelta(hours=1), start + timedelta(hours=2)
    base = dict(listing_type="single", location="Library steps", timezone="America/Los_Angeles",
                pickup_start=start.isoformat(), pickup_end=end.isoformat(), deadline=due.isoformat(),
                fresh=True, storage_confirmed=True)
    cookies = {}
    uploaded = []

    def check(label, condition):
        if not condition:
            raise AssertionError(label)
        checks.append(label)
        print("PASS:", label, flush=True)

    def call(method, path, actor=None, body=None):
        headers = {"Content-Type": "application/json"}
        if actor and actor in cookies:
            headers["Cookie"] = cookies[actor]
        req = Request(root + path, data=None if body is None else json.dumps(body).encode(),
                      headers=headers, method=method)
        try:
            with urlopen(req, timeout=65) as response:
                code, payload, hdr = response.status, response.read(), response.headers
        except HTTPError as error:
            code, payload, hdr = error.code, error.read(), error.headers
        if actor and hdr.get("Set-Cookie"):
            # Python 3.13 SimpleCookie rejects the modern Partitioned flag.
            # The app sets just this one host-only auth cookie; preserve its
            # name/value without interpreting browser-only cookie attributes.
            for header in hdr.get_all("Set-Cookie", []):
                pair = header.split(";", 1)[0].strip()
                if pair.startswith("echo_access_token="):
                    cookies[actor] = pair
        return code, json.loads(payload or "{}")

    def make_listing(actor, food, wants=None, category="produce", unit="each"):
        boundary = "ep-" + uuid4().hex
        image = (photos_dir / (food.split()[0].lower() + ".jpg")).read_bytes()
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="photo.jpg"\r\n'
                'Content-Type: image/jpeg\r\n\r\n').encode() + image + f"\r\n--{boundary}--\r\n".encode()
        req = Request(root + "/listing-photos", data=body, method="POST",
                      headers={"Cookie": cookies[actor], "Content-Type": "multipart/form-data; boundary=" + boundary})
        with urlopen(req, timeout=45) as response:
            photo = json.loads(response.read())
            check("required private food photo uploads", response.status == 201)
        uploaded.append((sessions[actor], photo["photo_id"]))
        item = dict(name=food, quantity=3, unit=unit, category=category,
                    unopened=True if category == "shelf_stable" else None,
                    printed_date=None, date_type=None)
        data = dict(base, mode="exchange" if wants else "giveaway", item_details=[item],
                    allergens="Unknown", photo_id=photo["photo_id"])
        if wants:
            data.update(wanted_food=wants, wanted_quantity=1, wanted_unit=unit)
        code, result = call("POST", "/listings/review", actor, data)
        if code == 429:
            time.sleep(8)
            code, result = call("POST", "/listings/review", actor, data)
        check("real Bob approves exact listing and food photo", code == 200 and result.get("approved") is True)
        data["approval_id"] = result["approval_id"]
        code, result = call("POST", "/listings", actor, data)
        if code != 201:
            print("Listing write HTTP status:", code, flush=True)
        check("listing saves through database transaction", code == 201)
        row = result["listing"]
        # Test-only metadata is assigned by the trusted test harness, not end users.
        db.table("listings").update({"is_test": True}).eq("id", row["id"]).execute()
        return row

    try:
        code, initial = call("GET", "/impact")
        check("public impact reads real database", code == 200)
        baseline = {k: initial[k] for k in ("completed_trades", "completed_giveaways", "donations_reported", "compost_reported", "quantities")}
        for label in ("a", "b", "c"):
            email, password = f"verification-{suffix}-{label}@example.com", secrets.token_urlsafe(25)
            user = db.auth.admin.create_user({"email": email, "password": password, "email_confirm": True}).user
            users.append(str(user.id))
            sessions[label] = str(user.id)
            db.table("user_profiles").insert(dict(id=str(user.id), auth_user_id=str(user.id),
                    email=email, display_name="Temporary verification " + label.upper())).execute()
            code, result = call("POST", "/auth/login", label, dict(email=email, password=password))
            check("real Supabase login " + label, code == 200 and result.get("user", {}).get("id") == str(user.id))
            code, result = call("GET", "/auth/me", label)
            check("authenticated session resolves " + label, code == 200 and result["user"]["id"] == str(user.id))

        food_a, food_b = "Onions check-" + suffix, "Apples check-" + suffix
        a = make_listing("a", food_a, food_b)
        b = make_listing("b", food_b, food_a)
        c = make_listing("c", food_b, food_a)
        code, result = call("GET", "/listings?mine=true", "a")
        check("listing persists across independent requests", code == 200 and any(r["id"] == a["id"] for r in result["listings"]))
        code, result = call("GET", f"/listings/{a['id']}/matches", "a")
        check("reciprocal matches returned", code == 200 and len(result["matches"]) >= 2)
        offers = []
        for partner, row in (("b", b), ("c", c)):
            code, result = call("POST", "/offers", "a", dict(listing_id=a["id"], match_id=row["id"]))
            check("server fixes offer contents", code == 201 and result["offer"]["give_items"][0]["name"] == food_a)
            offers.append((partner, result["offer"]["id"]))
            code, _ = call("POST", f"/offers/{offers[-1][1]}/respond", "a", {"accept": True})
            check("first acceptance saves", code == 200)
        outsider = call("POST", f"/offers/{offers[0][1]}/respond", "c", {"accept": True})
        check("nonparticipant cannot accept", outsider[0] == 403)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(call, "POST", f"/offers/{oid}/respond", partner, {"accept": True})
                       for partner, oid in offers]
            answers = [f.result() for f in futures]
        check("concurrent final acceptance has one winner and one conflict",
              sorted(r[0] for r in answers) == [200, 409])
        winning = next(offers[i] for i, answer in enumerate(answers) if answer[0] == 200)
        losing = next(offers[i] for i, answer in enumerate(answers) if answer[0] == 409)
        code, _ = call("POST", f"/offers/{losing[1]}/cancel", "a", {})
        source = db.table("listings").select("status,reservation_id").eq("id", a["id"]).single().execute().data
        check("losing cancellation cannot release winning reservation",
              code == 200 and source["status"] == "reserved" and source["reservation_id"] == winning[1])
        code, _ = call("POST", f"/offers/{winning[1]}/confirm-pickup", "a", {})
        count = db.table("impact_records").select("id").eq("offer_id", winning[1]).execute().data
        check("one party cannot complete exchange", code == 200 and not count)
        for _ in range(2):
            code, _ = call("POST", f"/offers/{winning[1]}/confirm-pickup", winning[0], {})
            check("both-party completion/replay succeeds", code == 200)
        ledger = db.table("impact_records").select("identity,is_test").eq("offer_id", winning[1]).execute().data
        check("completed exchange records exact-once quantities", len(ledger) == 2 and all(r["is_test"] for r in ledger))

        giveaway = make_listing("a", "Pears check-" + suffix)
        code, result = call("POST", f"/listings/{giveaway['id']}/requests", "b",
                            {k: base[k] for k in ("pickup_start", "pickup_end", "location")})
        check("recipient can request giveaway", code == 201)
        rid = result["request"]["id"]
        code, _ = call("POST", f"/giveaway-requests/{rid}/decision", "b", {"accept": True})
        check("only owner can approve giveaway", code == 403)
        code, _ = call("POST", f"/giveaway-requests/{rid}/decision", "a", {"accept": True})
        check("owner approval reserves giveaway", code == 200)
        code, _ = call("POST", f"/giveaway-requests/{rid}/pickup", "b", {})
        count = db.table("impact_records").select("id").eq("listing_id", giveaway["id"]).execute().data
        check("one party cannot complete giveaway", code == 200 and not count)
        for _ in range(2):
            code, _ = call("POST", f"/giveaway-requests/{rid}/pickup", "a", {})
            check("giveaway completion/replay succeeds", code == 200)
        count = db.table("impact_records").select("id").eq("listing_id", giveaway["id"]).execute().data
        check("giveaway impact is exact-once", len(count) == 1)

        donation = make_listing("a", "Rice check-" + suffix, category="shelf_stable", unit="bag")
        code, result = call("GET", f"/listings/{donation['id']}/fallback-options", "a")
        count = db.table("impact_records").select("id").eq("listing_id", donation["id"]).execute().data
        check("recommendations do not record donations", code == 200 and bool(result["options"]) and not count)
        outcome = dict(listing_id=donation["id"], destination_id="second-harvest-cypress",
                       action="donation", confirmed=True, no_glass_confirmed=True,
                       destination_feasible_confirmed=True)
        code, _ = call("POST", "/outcomes", "a", outcome)
        check("explicit reported donation saves", code == 201)
        code, _ = call("POST", "/outcomes", "a", outcome)
        check("reported donation replay is idempotent", code == 200)
        code, impact = call("GET", "/impact")
        check("test handoffs and outcomes excluded from real totals",
              code == 200 and all(impact[k] == baseline[k] for k in baseline))

        code, draft = call("POST", "/interpret-listing", "a",
                           {"text": "I have five onions and one unopened bag of rice, near Library steps, available Friday from 3 to 5.",
                            "timezone": "America/Los_Angeles"})
        check("live Bob works through authenticated website API",
              code == 200 and draft.get("requires_review") is True and len(draft.get("draft", {}).get("item_details", [])) == 2)
        code, result = call("POST", "/auth/logout", "a", {})
        check("logout clears session cookie", code == 200 and cookies.get("a") == "echo_access_token=")
        print("Integration checks passed:", len(checks), flush=True)
    finally:
        if users:
            # Delete only exact IDs linked to this run's own temporary users.
            rows = db.table("listings").select("id").in_("user_id", users).execute().data
            ids = [r["id"] for r in rows]
            for column in ("from_user_id", "to_user_id"):
                db.table("ratings").delete().in_(column, users).execute()
            for column in ("reporter_id", "reported_user_id"):
                db.table("reports").delete().in_(column, users).execute()
            for column in ("user_id", "blocked_user_id"):
                db.table("user_blocks").delete().in_(column, users).execute()
            db.table("notifications").delete().in_("user_id", users).execute()
            if ids:
                db.table("impact_records").delete().in_("listing_id", ids).execute()
                db.table("food_requests").delete().in_("listing_id", ids).execute()
                db.table("trade_offers").delete().in_("listing_id", ids).execute()
                db.table("listings").delete().in_("id", ids).execute()
            db.table("food_requests").delete().in_("user_id", users).execute()
            db.table("listing_approvals").delete().in_("user_id", users).execute()
            db.table("listing_photos").delete().in_("user_id", users).execute()
            db.table("user_profiles").delete().in_("id", users).execute()
            for uid in users:
                db.auth.admin.delete_user(uid)
            from photos import object_blob
            for uid, photo_id in uploaded:
                object_blob(uid, photo_id).delete()
            print("Temporary users and their verification records removed.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except AssertionError as error:
        print("FAILED CHECK:", str(error), flush=True)
        sys.exit(1)
    except Exception as error:
        # Provider errors can include credentials and account details.
        print("Integration check failed:", type(error).__name__, getattr(error, "code", ""), flush=True)
        sys.exit(1)
