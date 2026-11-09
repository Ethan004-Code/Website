"""Private App Storage photos: normalized bytes only; unposted photos are owner-only."""
import hashlib
import io
import os
from functools import lru_cache
from uuid import UUID, uuid4
import warnings

from flask import g, jsonify, request, send_file
from google.auth import identity_pool
from google.cloud import storage
from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.exceptions import BadRequest, Forbidden, NotFound, ServiceUnavailable

MAX_UPLOAD = 5 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 16_000_000


def identifier(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError):
        raise BadRequest("Choose a valid uploaded photo.")


@lru_cache(maxsize=1)
def client():
    credentials = identity_pool.Credentials.from_info({
        "type": "external_account", "audience": "replit",
        "subject_token_type": "access_token",
        "token_url": "http://127.0.0.1:1106/token",
        "credential_source": {"url": "http://127.0.0.1:1106/credential",
                              "format": {"type": "json", "subject_token_field_name": "access_token"}},
        "universe_domain": "googleapis.com",
    }, scopes=["https://www.googleapis.com/auth/devstorage.read_write"])
    return storage.Client(project="replit", credentials=credentials)


def object_blob(owner_id, photo_id):
    root = os.environ.get("PRIVATE_OBJECT_DIR", "").strip("/")
    if not root or "/" not in root:
        raise ServiceUnavailable("Private photo storage is not configured. Nothing can be posted.")
    bucket, prefix = root.split("/", 1)
    return client().bucket(bucket).blob(f"{prefix}/listing-photos/{identifier(owner_id)}/{identifier(photo_id)}.jpg")


def normalize_photo(raw):
    if not raw or len(raw) > MAX_UPLOAD:
        raise BadRequest("Choose a JPG, PNG or WebP photo no larger than 5 MB.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                if image.format not in {"JPEG", "PNG", "WEBP"} or getattr(image, "n_frames", 1) != 1:
                    raise BadRequest("Use a single JPG, PNG or WebP photo, not an animation or document.")
                if min(image.size) < 160 or image.width * image.height > 16_000_000:
                    raise BadRequest("Use a clear photo at least 160 pixels on each side and at most 16 megapixels.")
                image.load()
                image = ImageOps.exif_transpose(image)
                image.thumbnail((1200, 1200))
                if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
                    rgba = image.convert("RGBA")
                    clean = Image.new("RGB", image.size, "white")
                    clean.paste(rgba, mask=rgba.getchannel("A"))
                else:
                    clean = image.convert("RGB")
                output = io.BytesIO()
                # No original EXIF, comments, GPS or filename is carried over.
                clean.save(output, format="JPEG", quality=85, optimize=True)
                result = output.getvalue()
                if len(result) > 2_000_000:
                    raise BadRequest("This photo is too complex. Resize it and try again.")
                return result, clean.width, clean.height
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise BadRequest("The photo could not be decoded safely. Choose a valid JPG, PNG or WebP.")


def owned_photo(photo_id, include_bytes=False):
    photo_id = identifier(photo_id)
    row = next((r for r in g.store["listing_photos"] if r["id"] == photo_id), None)
    if row is None or row["state"] != "active":
        raise NotFound("Your photo is unavailable. Upload it again before requesting approval.")
    if row["user_id"] != g.user["id"]:
        raise Forbidden("That photo does not belong to this account.")
    blob = object_blob(g.user["id"], photo_id)
    try:
        blob.reload(timeout=15, retry=None)
    except Exception:
        raise NotFound("Your photo upload was not found. Upload it again before requesting approval.")
    meta = blob.metadata or {}
    if meta.get("owner") != g.user["id"] or meta.get("photo_id") != photo_id:
        raise Forbidden("That photo does not belong to this account.")
    if not blob.size or blob.size > 2_000_000 or blob.content_type != "image/jpeg" or meta.get("sha256") != row["sha256"]:
        raise BadRequest("The uploaded photo is invalid. Please replace it.")
    data = None
    if include_bytes:
        try:
            data = blob.download_as_bytes(if_generation_match=blob.generation, timeout=15, retry=None)
        except Exception:
            raise ServiceUnavailable("The photo could not be read for review. Nothing was posted.")
        if hashlib.sha256(data).hexdigest() != meta["sha256"]:
            raise BadRequest("The photo changed. Upload it again and request a new review.")
    return photo_id, meta["sha256"], data


def install_photos(app, persist):
    @app.post("/api/listing-photos")
    def upload_listing_photo():
        file = request.files.get("photo")
        if file is None:
            raise BadRequest("Choose a food photo before requesting Bob approval.")
        data, width, height = normalize_photo(file.stream.read(MAX_UPLOAD + 1))
        photo_id = str(uuid4())
        digest = hashlib.sha256(data).hexdigest()
        blob = object_blob(g.user["id"], photo_id)
        blob.metadata = {"owner": g.user["id"], "photo_id": photo_id, "sha256": digest}
        try:
            blob.upload_from_string(data, content_type="image/jpeg", if_generation_match=0, timeout=20, retry=None)
        except Exception:
            raise ServiceUnavailable("The photo could not be uploaded. Your listing has not been posted.")
        from rules import now
        g.new_photo_objects.append(blob)
        persist("listing_photos", dict(id=photo_id, user_id=g.user["id"], sha256=digest,
                                       state="active", created_at=now().isoformat()))
        return jsonify(photo_id=photo_id, preview_url=f"/api/listing-photos/{photo_id}",
                       width=width, height=height), 201

    @app.get("/api/listing-photos/<photo_id>")
    def listing_photo(photo_id):
        from rules import available, blocked
        photo_id = identifier(photo_id)
        uid = g.user["id"] if getattr(g, "user", None) else None
        registry = next((r for r in g.store["listing_photos"] if r["id"] == photo_id and r["state"] == "active"), None)
        if registry is None:
            raise NotFound("The photo is not available.")
        owner = registry["user_id"]
        if uid != owner:
            listing = next((r for r in g.store["listings"] if r.get("photo_id") == photo_id
                        and available(r) and r["user_id"] in g.active_users
                        and (not uid or not blocked(uid, r["user_id"]))), None)
            if listing is None:
                raise NotFound("This photo is not available.")
        blob = object_blob(owner, photo_id)
        try:
            data = blob.download_as_bytes(timeout=15, retry=None)
        except Exception:
            raise NotFound("The photo is not available.")
        response = send_file(io.BytesIO(data), mimetype="image/jpeg", download_name="food-photo.jpg")
        response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
        return response

    @app.delete("/api/listing-photos/<photo_id>")
    def delete_listing_photo(photo_id):
        photo_id, _, _ = owned_photo(photo_id)
        if any(r.get("photo_id") == photo_id for r in g.store["listings"]):
            raise BadRequest("A photo attached to a saved listing cannot be deleted here.")
        row = next(r for r in g.store["listing_photos"] if r["id"] == photo_id)
        row["state"] = "deleted"
        persist("listing_photos", row)
        # The transaction wrapper removes bytes only after this tombstone wins
        # the same revision lock as review/publish. A stale DELETE cannot race a
        # successful publish and remove its checked photo.
        g.deleted_photo_objects.append(object_blob(g.user["id"], photo_id))
        return jsonify(deleted=True)
