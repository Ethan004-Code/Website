"""Request-local Supabase snapshots and atomic optimistic transactions.

Retains the uploaded app's tables, Auth, ratings and reports. No in-memory
production fallback; all multi-row writes commit together or return a conflict.
"""
import copy
import os
from urllib.parse import urlparse

from flask import g, jsonify, request
from werkzeug.local import LocalProxy
from werkzeug.exceptions import HTTPException

TABLES = ("listings", "trade_offers", "food_requests", "notifications",
          "ratings", "reports", "user_blocks", "impact_records", "listing_approvals", "listing_photos")
GLOBALS = ("listings", "offers", "food_requests", "notifications",
           "ratings", "reports", "user_blocks")


def install_runtime(app, legacy):
    app.config["MAX_CONTENT_LENGTH"] = 32 * 1024
    original_auth = legacy["authenticate_api_request"]
    app.before_request_funcs[None].remove(original_auth)

    def save(table, row):
        if table not in TABLES:
            raise ValueError("Unsupported table")
        g.writes[(table, row["id"])] = copy.deepcopy(row)

    legacy["save_row"] = save
    for name, table in zip(GLOBALS, TABLES):
        legacy[name] = LocalProxy(lambda table=table: g.store[table])

    def account_active(user_id):
        return user_id in g.active_users
    legacy["account_is_active"] = account_active

    @app.before_request
    def prepare_request():
        if request.endpoint == "upload_listing_photo":
            request.max_content_length = 6 * 1024 * 1024
        g.writes = {}
        g.new_photo_objects = []
        g.deleted_photo_objects = []
        g.store = {t: [] for t in TABLES}
        g.active_users = set()
        if not request.path.startswith("/api/") or request.method == "OPTIONS":
            return
        if request.endpoint in {"health", "healthz"}:
            return
        # Same-origin browser writes; never trust arbitrary Origin on cookie auth.
        origin = request.headers.get("Origin")
        if request.method not in {"GET", "HEAD"} and origin:
            if urlparse(origin).netloc != request.host:
                return jsonify(error="Cross-origin writes are not allowed."), 403
        # The same curated directory/routing service can be browsed without an
        # account or listing. Never resolve cookies or load private snapshots.
        if request.endpoint in {"donation_locations", "donation_location_routes"}:
            return
        if not legacy["supabase"]:
            return jsonify(error="Supabase setup is invalid or incomplete. Update the existing project URL and keys."), 503
        if request.endpoint not in {"auth_login", "auth_logout", "auth_signup"}:
            # Public reads may optionally resolve a signed-in user.
            public = request.endpoint in {"get_listings", "listing_photo"}
            token = request.cookies.get("echo_access_token")
            if public and not token:
                g.user = None
            else:
                response = original_auth()
                if response is not None:
                    return response
        if request.endpoint in {"auth_login", "auth_logout", "auth_signup", "auth_me", "soft_delete_account"}:
            return
        try:
            snapshot = legacy["supabase"].rpc("echopantry_snapshot").execute().data
            g.revision = snapshot["revision"]
            g.schema_version = snapshot.get("schema_version", 0)
            g.store = {table: snapshot.get(table, []) for table in TABLES}
            g.active_users = set(snapshot["active_users"])
            if request.method not in {"GET", "HEAD"} and g.schema_version < 3:
                return jsonify(error="Apply the updated backend/interview_migration.sql to enable required photo and Bob approvals. Nothing was saved."), 503
        except Exception:
            return jsonify(error="Supabase schema setup is required. Apply backend/supabase_schema.sql, then backend/interview_migration.sql to your existing project."), 503

    @app.after_request
    def commit_request(response):
        def remove_objects(objects):
            for blob in objects:
                try:
                    blob.delete(timeout=15, retry=None)
                except Exception:
                    app.logger.warning("Private photo object cleanup could not finish.")
        if getattr(g, "writes", None) and response.status_code < 400:
            if getattr(g, "schema_version", 0) < 3:
                response = jsonify(error="Apply the updated backend/interview_migration.sql to enable required photo and Bob approvals. Nothing was saved.")
                response.status_code = 503
                response.headers["Cache-Control"] = "no-store"
                return response
            try:
                legacy["supabase"].rpc("echopantry_commit", {
                    "p_revision": g.revision,
                    "p_writes": [{"table": table, "row": row}
                                 for (table, _), row in g.writes.items()],
                }).execute()
                remove_objects(g.deleted_photo_objects)
            except Exception as error:
                if "EP_CONFLICT" in str(error):
                    response = jsonify(error="Availability changed while you were responding. Refresh and review the latest options.")
                    response.status_code = 409
                else:
                    response = jsonify(error="The database could not save this action. Nothing from this action was committed.")
                    response.status_code = 503
        if response.status_code >= 400:
            remove_objects(getattr(g, "new_photo_objects", []))
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        # OSM's public tile service requires a valid web Referer. This policy
        # sends only the deployment origin, never the user's search details.
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response

    @app.errorhandler(Exception)
    def safe_error(error):
        if isinstance(error, HTTPException):
            return jsonify(error=error.description), error.code
        # Do not log raw provider errors or request bodies containing credentials.
        app.logger.error("Request failed (%s)", type(error).__name__)
        return jsonify(error="This action could not be completed. Please retry."), 503

    from rules import install_rules
    install_rules(app, legacy)
