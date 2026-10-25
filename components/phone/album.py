"""Narrow phone-side transport for the private album (EP-16, v0.4).

This module only speaks the existing one-line JSON protocol to the Life Supply owner over its
Unix socket.  It never opens the Supply database, never scans the owner's media directory and
never takes a writer identity: the owner derives the caller from ``SO_PEERCRED``.

Why a separate transport instead of ``LifeArtifactPort``: one picture may be ~7 MB of base64 on
the answer, while the generic port deliberately caps a response at 1 MB.  Widening that port
would also widen every other Life call, so the album gets this one narrow client with its own
bounds.  Everything it sends is one of the owner's four album operations.
"""
from __future__ import annotations

import json
import socket
from typing import Any, Mapping

#: The owner's answer can carry a 5 MiB picture as base64 (~6.7 MB) plus its manifest.
MAX_RESPONSE_BYTES = 12 * 1024 * 1024
#: One import is at most 5 MiB of bytes (~7 MiB base64) plus a small envelope.
MAX_REQUEST_BYTES = 9 * 1024 * 1024
DEFAULT_TIMEOUT = 20.0

OPS = frozenset({
    "list_album_images", "read_album_image", "read_album_image_bytes", "import_album_image",
})

STATUS_OK = "OK"
STATUS_NO_CHANGE = "NO_CHANGE"
STATUS_NOT_FOUND = "NOT_FOUND"
STATUS_CONFLICT = "CONFLICT"
STATUS_DENY = "DENY"
STATUS_BLOCKED = "BLOCKED"
STATUS_UNKNOWN = "UNKNOWN"


#: The owner's album marker.  This is a *classification* helper for the phone's project screen:
#: an album manifest must never be shown as a continuable text work.  It is not authentication --
#: every read still goes through the owner and its subject/lifecycle checks.
ALBUM_MARKER = "album.image.v1"


def is_album_manifest(content: Any) -> bool:
    """True only for the owner's own structured album manifest (no other JSON is claimed)."""
    if not isinstance(content, str) or not content or len(content) > 4096:
        return False
    try:
        doc = json.loads(content)
    except (TypeError, ValueError):
        return False
    if not isinstance(doc, dict) or doc.get("kind") != ALBUM_MARKER:
        return False
    media = doc.get("media")
    source = doc.get("source")
    return (
        isinstance(doc.get("caption"), str)
        and doc.get("content_type") in ("image/png", "image/jpeg")
        and isinstance(media, dict)
        and isinstance(media.get("name"), str)
        and isinstance(media.get("sha256"), str)
        and isinstance(source, dict)
        and source.get("origin") == "user_import"
        and source.get("declared") == "self_declared"
    )


class PhoneAlbumUnavailable(RuntimeError):
    """The owner could not be reached, or answered something unusable."""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        super().__init__(("%s:%s" % (reason_code, detail))[:300])
        self.reason_code = str(reason_code)
        self.detail = str(detail)[:200]


class PhoneAlbum:
    """The four album operations, and nothing else."""

    def __init__(self, socket_path: str, subject_id: str, *, grant_id: str = "",
                 timeout: float = DEFAULT_TIMEOUT) -> None:
        if not socket_path or not str(socket_path).startswith("/"):
            raise PhoneAlbumUnavailable("ALBUM_SOCKET_INVALID")
        if not subject_id:
            raise PhoneAlbumUnavailable("ALBUM_SUBJECT_REQUIRED")
        self.socket_path = str(socket_path)
        self.subject_id = str(subject_id)
        self.grant_id = str(grant_id or "")
        self.timeout = float(timeout)

    def _call(self, request: Mapping[str, Any]) -> dict[str, Any]:
        op = request.get("op")
        if op not in OPS:
            raise PhoneAlbumUnavailable("ALBUM_OP_REFUSED", str(op))
        body = json.dumps(dict(request), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(body) > MAX_REQUEST_BYTES:
            raise PhoneAlbumUnavailable("ALBUM_IMPORT_TOO_LARGE",
                                        "request exceeds the album transport bound")
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(self.timeout)
                client.connect(self.socket_path)
                client.sendall(body + b"\n")
                line = client.makefile("rb").readline(MAX_RESPONSE_BYTES + 1)
        except OSError as exc:
            raise PhoneAlbumUnavailable("ALBUM_OWNER_UNREACHABLE", type(exc).__name__) from exc
        if not line:
            raise PhoneAlbumUnavailable("ALBUM_EMPTY_ANSWER")
        if len(line) > MAX_RESPONSE_BYTES:
            raise PhoneAlbumUnavailable("ALBUM_ANSWER_TOO_LARGE")
        try:
            answer = json.loads(line.decode("utf-8"))
        except (UnicodeError, ValueError) as exc:
            raise PhoneAlbumUnavailable("ALBUM_MALFORMED_ANSWER") from exc
        if not isinstance(answer, dict) or not isinstance(answer.get("status"), str):
            raise PhoneAlbumUnavailable("ALBUM_UNTYPED_ANSWER")
        return answer

    # -- reads ---------------------------------------------------------------------------

    def list_images(self) -> dict[str, Any]:
        return self._call({"op": "list_album_images", "subject": self.subject_id})

    def read_image(self, artifact_id: str) -> dict[str, Any]:
        return self._call({"op": "read_album_image", "subject": self.subject_id,
                           "artifact_id": str(artifact_id)})

    def read_image_bytes(self, artifact_id: str) -> dict[str, Any]:
        return self._call({"op": "read_album_image_bytes", "subject": self.subject_id,
                           "artifact_id": str(artifact_id)})

    # -- one explicit write --------------------------------------------------------------

    def import_image(self, *, caption: str, content_type: str, data: str,
                     operation_key: str) -> dict[str, Any]:
        if not self.grant_id:
            raise PhoneAlbumUnavailable("ALBUM_UNBOUND", "no artifact grant on this instance")
        return self._call({
            "op": "import_album_image",
            "grant_id": self.grant_id,
            "operation_key": str(operation_key),
            "image": {
                "subject_id": self.subject_id,
                "caption": str(caption),
                "content_type": str(content_type),
                "data": str(data),
            },
        })


__all__ = [
    "ALBUM_MARKER",
    "DEFAULT_TIMEOUT",
    "is_album_manifest",
    "MAX_REQUEST_BYTES",
    "MAX_RESPONSE_BYTES",
    "OPS",
    "PhoneAlbum",
    "PhoneAlbumUnavailable",
]
