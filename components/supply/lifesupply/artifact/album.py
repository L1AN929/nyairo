"""Private album media helpers for the ManagedArtifact owner (EP-16, v0.4).

The album is **not** a second owner and it has no table.  One imported picture is one ordinary
``ManagedArtifact`` whose first version is a small, deterministic JSON manifest:

* the manifest names one media file and its SHA-256/size; it never carries the bytes;
* the bytes live under the owner's own ``media`` directory, written by the owner and named only
  by the owner (``<sha256>.<png|jpg>``) -- a caller can never supply a path, a URL or a name;
* the manifest is a self-declared import (``user_import`` / ``self_declared``), never a claim
  about what is in the picture and never an EXIF/metadata reading.

Only PNG and JPEG are accepted.  The checks below are signature/structural checks, not a full
image decode and not content sanitisation: the browser is the one that really decodes the
preview, and a file it cannot decode is never submitted (the phone UI refuses it first).
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import struct
import uuid
from pathlib import Path
from typing import Any

#: The one marker this module recognises.  It is a *classification* marker, never an
#: authentication token: every read still goes through the owner's subject/lifecycle checks.
ALBUM_MARKER = "album.image.v1"

#: Allowed MIME types and their canonical file extension.  Nothing else (no SVG/WebP/GIF).
CONTENT_TYPES: dict[str, str] = {"image/png": "png", "image/jpeg": "jpg"}
ALLOWED_CONTENT_TYPES = frozenset(CONTENT_TYPES)

#: 5 MiB decoded.  The wire limit (8 MiB of base64 JSON) lives in the service, not here.
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_CAPTION_CHARS = 200
MAX_MANIFEST_CHARS = 4096

#: Owner-produced media file names only.  No slashes, no traversal, no caller-supplied paths.
MEDIA_NAME_RE = re.compile(r"^[0-9a-f]{64}\.(?:png|jpg)$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class AlbumImageRefused(ValueError):
    """A picture the owner will not store, with a stable reason code."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__((reason + ":" + detail)[:300])
        self.reason = str(reason)
        self.detail = str(detail)[:300]


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def detect_content_type(data: bytes) -> str | None:
    """The type the bytes themselves claim, or ``None`` when neither signature is present."""
    if data.startswith(PNG_SIGNATURE):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    return None


def validate_image(content_type: Any, data: Any) -> str:
    """Validate one decoded picture and return the detected type.

    The declared content type must be exactly ``image/png`` / ``image/jpeg`` and must agree with
    the bytes.  The structural checks are deliberately shallow (magic, IHDR / segment framing,
    end marker): enough to refuse an arbitrary blob, not a promise that the picture is fully
    decodable or sanitised.
    """
    if not isinstance(content_type, str) or content_type not in CONTENT_TYPES:
        raise AlbumImageRefused("UNSUPPORTED_CONTENT_TYPE", "only image/png and image/jpeg")
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise AlbumImageRefused("IMAGE_EMPTY", "decoded image is empty")
    data = bytes(data)
    if len(data) > MAX_IMAGE_BYTES:
        raise AlbumImageRefused("IMAGE_TOO_LARGE", f"decoded image exceeds {MAX_IMAGE_BYTES} bytes")
    detected = detect_content_type(data)
    if detected is None:
        raise AlbumImageRefused("IMAGE_SIGNATURE_UNKNOWN", "neither PNG nor JPEG signature")
    if detected != content_type:
        raise AlbumImageRefused("IMAGE_TYPE_MISMATCH",
                                f"declared {content_type} but bytes are {detected}")
    if detected == "image/png":
        _check_png(data)
    else:
        _check_jpeg(data)
    return detected


def _check_png(data: bytes) -> None:
    # signature(8) + IHDR length(4) + "IHDR"(4) + width/height(8) = 24; plus IEND trailer.
    if len(data) < 33:
        raise AlbumImageRefused("IMAGE_STRUCTURE_INVALID", "PNG is too short")
    if data[12:16] != b"IHDR":
        raise AlbumImageRefused("IMAGE_STRUCTURE_INVALID", "PNG lacks an IHDR chunk")
    width, height = struct.unpack(">II", data[16:24])
    if width <= 0 or height <= 0:
        raise AlbumImageRefused("IMAGE_STRUCTURE_INVALID", "PNG has no usable dimensions")
    if data[-12:-8] != b"\x00\x00\x00\x00" or b"IEND" not in data[-16:]:
        raise AlbumImageRefused("IMAGE_STRUCTURE_INVALID", "PNG lacks its IEND trailer")


def _check_jpeg(data: bytes) -> None:
    if len(data) < 4 or not data.endswith(b"\xff\xd9"):
        raise AlbumImageRefused("IMAGE_STRUCTURE_INVALID", "JPEG lacks its EOI marker")
    # The first marker after SOI must be a real segment start (0xFF followed by a non-zero,
    # non-0xFF marker byte); this refuses a three-byte prefix with arbitrary trailing bytes.
    if len(data) < 6 or data[2] != 0xFF or data[3] in (0x00, 0xFF):
        raise AlbumImageRefused("IMAGE_STRUCTURE_INVALID", "JPEG lacks its first segment")


def decode_base64(data_b64: Any) -> bytes:
    """Strict base64 -> bytes.  Whitespace and a data-URL prefix are refused, not repaired."""
    if not isinstance(data_b64, str) or not data_b64:
        raise AlbumImageRefused("IMAGE_DATA_REQUIRED", "data must be non-empty base64 text")
    try:
        return base64.b64decode(data_b64.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise AlbumImageRefused("IMAGE_DATA_INVALID", "data is not strict base64") from exc


def media_name_for(data: bytes, content_type: str) -> str:
    extension = CONTENT_TYPES.get(content_type) if isinstance(content_type, str) else None
    if extension is None:
        raise AlbumImageRefused("UNSUPPORTED_CONTENT_TYPE", "only image/png and image/jpeg")
    return hashlib.sha256(bytes(data)).hexdigest() + "." + extension


def build_manifest(*, caption: str, content_type: str, media_name: str, sha256: str,
                   size: int) -> str:
    """The deterministic first-version body of one album artifact (no bytes, no timestamps)."""
    if not isinstance(content_type, str) or content_type not in CONTENT_TYPES:
        raise AlbumImageRefused("UNSUPPORTED_CONTENT_TYPE", "only image/png and image/jpeg")
    if not MEDIA_NAME_RE.match(str(media_name or "")):
        raise AlbumImageRefused("MEDIA_NAME_INVALID", "media name is not owner-shaped")
    if not _SHA256_RE.match(str(sha256 or "")):
        raise AlbumImageRefused("MEDIA_DIGEST_INVALID", "media digest is not sha256 hex")
    if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= MAX_IMAGE_BYTES:
        raise AlbumImageRefused("MEDIA_SIZE_INVALID", "media size is out of bounds")
    if not isinstance(caption, str) or len(caption) > MAX_CAPTION_CHARS:
        raise AlbumImageRefused("CAPTION_TOO_LONG", f"caption exceeds {MAX_CAPTION_CHARS} chars")
    manifest = {
        "kind": ALBUM_MARKER,
        "caption": caption,
        "content_type": content_type,
        "media": {"name": media_name, "sha256": sha256, "bytes": int(size)},
        "source": {"origin": "user_import", "declared": "self_declared"},
    }
    return _json(manifest)


def parse_manifest(content: Any) -> dict[str, Any] | None:
    """Parse one artifact head as an album manifest, or ``None`` when it is anything else.

    ``None`` is the honest answer for a material, a work, a review or a corrupt manifest; the
    caller must not treat it as "an album with no pictures".
    """
    if not isinstance(content, str) or not content or len(content) > MAX_MANIFEST_CHARS:
        return None
    try:
        doc = json.loads(content)
    except (ValueError, TypeError):
        return None
    if not isinstance(doc, dict) or doc.get("kind") != ALBUM_MARKER:
        return None
    caption = doc.get("caption")
    content_type = doc.get("content_type")
    media = doc.get("media")
    source = doc.get("source")
    if not isinstance(caption, str) or len(caption) > MAX_CAPTION_CHARS:
        return None
    if not isinstance(content_type, str) or content_type not in CONTENT_TYPES:
        return None
    if not isinstance(media, dict) or not isinstance(source, dict):
        return None
    name = media.get("name")
    digest = media.get("sha256")
    size = media.get("bytes")
    if not isinstance(name, str) or not MEDIA_NAME_RE.match(name):
        return None
    if not isinstance(digest, str) or not _SHA256_RE.match(digest):
        return None
    if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= MAX_IMAGE_BYTES:
        return None
    if name.split(".", 1)[0] != digest or not name.endswith("." + CONTENT_TYPES[content_type]):
        return None
    if source.get("origin") != "user_import" or source.get("declared") != "self_declared":
        return None
    return {
        "kind": ALBUM_MARKER,
        "caption": caption,
        "content_type": content_type,
        "media": {"name": name, "sha256": digest, "bytes": int(size)},
        "source": {"origin": "user_import", "declared": "self_declared"},
    }


def write_media(media_root: Path, data: bytes, content_type: str) -> dict[str, Any]:
    """Write one media file atomically under ``media_root`` and return its owner facts.

    The name is derived from the bytes, so importing the same picture twice reuses one file
    (same owner, same bytes).  An existing file with that name is reused only after its size and
    digest are verified; a mismatch is an error, never an overwrite.
    """
    root = Path(media_root)
    root.mkdir(parents=True, exist_ok=True)
    payload = bytes(data)
    name = media_name_for(payload, content_type)
    digest = hashlib.sha256(payload).hexdigest()
    path = root / name
    if path.exists():
        existing = read_media(root, name, digest, len(payload))
        if existing is None:
            raise AlbumImageRefused("MEDIA_FILE_CONFLICT",
                                    "an existing media file does not match its name")
        return {"name": name, "sha256": digest, "bytes": len(payload)}
    tmp = root / ("." + name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with open(tmp, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise AlbumImageRefused("MEDIA_WRITE_FAILED", type(exc).__name__) from exc
    return {"name": name, "sha256": digest, "bytes": len(payload)}


def read_media(media_root: Path | None, name: Any, sha256: Any, size: Any) -> bytes | None:
    """Read exactly one owner-named media file, verifying name, size and digest.

    ``None`` covers every honest failure (no root, bad name, missing file, size/digest mismatch,
    unreadable root).  The caller must not turn it into an empty image.
    """
    if media_root is None:
        return None
    if not isinstance(name, str) or not MEDIA_NAME_RE.match(name):
        return None
    if not isinstance(sha256, str) or not _SHA256_RE.match(sha256):
        return None
    if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= MAX_IMAGE_BYTES:
        return None
    root = Path(media_root)
    path = root / name
    try:
        if not path.is_file():
            return None
        if path.stat().st_size != size:
            return None
        payload = path.read_bytes()
    except OSError:
        return None
    if len(payload) != size or hashlib.sha256(payload).hexdigest() != sha256:
        return None
    return payload


def encode_base64(data: bytes) -> str:
    return base64.b64encode(bytes(data)).decode("ascii")


__all__ = [
    "ALBUM_MARKER",
    "ALLOWED_CONTENT_TYPES",
    "AlbumImageRefused",
    "CONTENT_TYPES",
    "MAX_CAPTION_CHARS",
    "MAX_IMAGE_BYTES",
    "MEDIA_NAME_RE",
    "build_manifest",
    "decode_base64",
    "detect_content_type",
    "encode_base64",
    "media_name_for",
    "parse_manifest",
    "read_media",
    "validate_image",
    "write_media",
]
