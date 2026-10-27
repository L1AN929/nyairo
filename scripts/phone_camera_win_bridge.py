#!/usr/bin/env python3
"""Windows-side transport bridge for the phone camera (no policy, no storage, no prompt).

This script is *only* started when the phone process runs in WSL and its own loopback cannot
reach the Windows-side Stable Diffusion runtime.  It reads one JSON request from stdin::

    {"url": "http://127.0.0.1:7860/sdapi/v1/txt2img",
     "method": "POST", "body": {...}, "timeout": 300}

and writes one JSON answer to stdout::

    {"status": 200, "body": {...}}          or          {"error": "..."}

The URL is checked again here: loopback HTTP only, and only the two local service ports this
repository already uses -- the picture runtime (7860) and the existing text runtime (18190) that
converts her Chinese scene into English tags.  It is not a general URL proxy.  Redirects are
refused.  The bridge never prints the request or the picture, never touches a file, and never
makes a second attempt.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit

ALLOWED_HOSTS = {"127.0.0.1", "localhost"}
#: The existing local runtimes only: Stable Diffusion WebUI (7860) and llama.cpp (18190).
ALLOWED_PORTS = {7860, 18190}
MAX_ANSWER_BYTES = 8 * 1024 * 1024


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        raise ValueError("redirect_refused:%d" % int(code))


def _emit(payload: dict) -> int:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.flush()
    return 0


def main() -> int:
    try:
        request = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    except (UnicodeError, ValueError):
        return _emit({"error": "request_not_json"})
    if not isinstance(request, dict):
        return _emit({"error": "request_not_an_object"})
    url = str(request.get("url") or "")
    method = str(request.get("method") or "GET").upper()
    body = request.get("body")
    try:
        timeout = float(request.get("timeout") or 300.0)
    except (TypeError, ValueError):
        timeout = 300.0
    parts = urlsplit(url)
    if parts.scheme != "http" or (parts.hostname or "").lower() not in ALLOWED_HOSTS \
            or not parts.port or int(parts.port) not in ALLOWED_PORTS:
        return _emit({"error": "url_not_loopback"})
    if method not in ("GET", "POST"):
        return _emit({"error": "method_refused"})
    data = None
    if method == "POST":
        if not isinstance(body, dict):
            return _emit({"error": "body_required"})
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    http_request = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        http_request.add_header("Content-Type", "application/json")
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(http_request, timeout=min(timeout, 300.0)) as response:
            status = int(response.status)
            raw = response.read(MAX_ANSWER_BYTES + 1)
    except urllib.error.HTTPError as exc:
        return _emit({"error": "http_%d" % int(exc.code)})
    except Exception as exc:  # transport failure only; the caller classifies the reason
        return _emit({"error": type(exc).__name__})
    if len(raw) > MAX_ANSWER_BYTES:
        return _emit({"error": "answer_too_large"})
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError):
        return _emit({"error": "answer_not_json"})
    # llama.cpp ``/slots`` answers with an array; SD WebUI and chat completions with objects.
    if not isinstance(parsed, (dict, list)):
        return _emit({"error": "answer_not_an_object"})
    return _emit({"status": status, "body": parsed})


if __name__ == "__main__":
    raise SystemExit(main())
