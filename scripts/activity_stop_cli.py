#!/usr/bin/env python3
"""Local operator CLI: stop one Activity on this instance, or read its stop state.

This is the *only* caller shape the external-stop route accepts: the narrow local operator.
It never starts a model, never sends anything outside loopback, and never prints the token.

Before a single byte is sent it proves the credential it read is the one for *this* instance
and *this* endpoint:

* the credential file must be the operator-stop kind (the diary credential is a different
  kind and is refused by its reader);
* ``credential.instance`` must be the resolved ``--instance``;
* ``credential.subject`` must be the instance's own subject;
* ``credential.life_subject`` must be the canonical Life subject of this instance;
* the target endpoint must be loopback and *exactly* ``credential.endpoint``.

A mismatch is refused locally, so a wrong ``--endpoint`` can never receive the token -- the
server never gets the chance to reject it.  Redirects and environment proxies are disabled:
a 3xx answer ends the call instead of forwarding the header to another host or port.

    python scripts/activity_stop_cli.py --instance /path/to/phone --state --activity actv_...
    python scripts/activity_stop_cli.py --instance /path/to/phone --activity actv_... \\
        --revision 7 --reason OPERATOR_REQUEST
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "components" / "phone") not in sys.path:
    sys.path.insert(0, str(ROOT / "components" / "phone"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from components.phone import operator_stop as phone_operator_stop  # noqa: E402

REASONS = ("OPERATOR_REQUEST", "USER_REQUEST", "SAFETY_HOLD", "SERVICE_SHUTDOWN")


class _Refused(Exception):
    """A local refusal; the reason code is the only thing that is ever printed."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(str(reason_code))
        self.reason_code = str(reason_code)


def _endpoint(instance: Path, explicit: Optional[str]) -> str:
    raw = str(explicit or "").strip()
    if not raw:
        for name in ("run/ready.json", "run/phone.serve.json"):
            path = instance / name
            if not path.is_file():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            url = str(payload.get("url") or "").strip()
            if url:
                raw = url
                break
    if not raw:
        raise _Refused("ENDPOINT_NOT_FOUND")
    try:
        return phone_operator_stop.normalize_loopback_endpoint(raw)
    except Exception as exc:
        raise _Refused(getattr(exc, "reason_code", "ENDPOINT_INVALID")) from None


def _instance_files(instance: Path) -> tuple[dict[str, Any], str]:
    """The instance payload and the canonical Life subject; both must be readable."""

    payload: dict[str, Any] = {}
    path = instance / "instance.json"
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise _Refused("INSTANCE_UNREADABLE") from None
    if not isinstance(loaded, dict):
        raise _Refused("INSTANCE_UNREADABLE")
    payload = loaded
    # ``one_day_life`` names it ``subject_id``; a phone-written instance uses ``subject``.
    subject = str(payload.get("subject_id") or payload.get("subject") or "").strip()
    if not subject:
        raise _Refused("INSTANCE_SUBJECT_MISSING")
    state_path = instance / "life" / "state" / "canonical_life_state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise _Refused("LIFE_STATE_UNREADABLE") from None
    life_subject = str((state or {}).get("subject_id") or "").strip() if isinstance(
        state, dict) else ""
    if not life_subject:
        raise _Refused("LIFE_SUBJECT_MISSING")
    return payload, life_subject


def _bound_credential(instance: Path, endpoint: str):
    """Read the credential and prove it belongs to this instance and endpoint."""

    path = phone_operator_stop.credential_path(instance)
    try:
        credential = phone_operator_stop.read_credential(path)
    except phone_operator_stop.OperatorStopInvalid as exc:
        raise _Refused(exc.reason_code) from None
    if str(Path(credential.instance).resolve()) != str(instance):
        raise _Refused("CREDENTIAL_INSTANCE_MISMATCH")
    _payload, life_subject = _instance_files(instance)
    instance_subject = str(_payload.get("subject_id") or _payload.get("subject") or "")
    if credential.subject and credential.subject != instance_subject:
        raise _Refused("CREDENTIAL_SUBJECT_MISMATCH")
    if credential.life_subject != life_subject:
        raise _Refused("CREDENTIAL_LIFE_SUBJECT_MISMATCH")
    if not phone_operator_stop.endpoint_matches(credential, endpoint):
        # The exact reason the first delivery was wrong: a caller could aim the token at
        # another port or host.  The bound endpoint is the only place it may go.
        raise _Refused("ENDPOINT_NOT_BOUND")
    return credential


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: a 3xx must not carry the operator header anywhere."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


def _opener() -> urllib.request.OpenerDirector:
    # ``ProxyHandler({})`` drops every proxy the environment names: loopback must stay
    # loopback, and an ``http_proxy`` must not be able to see the operator header.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def _call(url: str, *, token: str, method: str = "GET", body: Optional[dict] = None
          ) -> tuple[int, dict[str, Any]]:
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("X-Phone-Operator-Stop", token)
    request.add_header("Sec-Fetch-Site", "none")
    if body is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with _opener().open(request, timeout=15) as response:
            return int(response.status), json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if 300 <= int(exc.code) < 400:
            return int(exc.code), {"ok": False, "status": "refused",
                                   "reason_code": "HTTP_REDIRECT_REFUSED"}
        raw = exc.read().decode("utf-8", "replace")
        try:
            return int(exc.code), json.loads(raw)
        except ValueError:
            return int(exc.code), {"raw": raw[:300]}
    except urllib.error.URLError as exc:
        return 0, {"ok": False, "status": "unreachable",
                   "reason_code": "ENDPOINT_UNREACHABLE",
                   "detail": type(exc.reason).__name__}


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="activity_stop_cli.py")
    parser.add_argument("--instance", required=True)
    parser.add_argument("--activity", required=True)
    parser.add_argument("--revision", type=int)
    parser.add_argument("--reason", default="OPERATOR_REQUEST", choices=REASONS)
    parser.add_argument("--operation-key", default="")
    parser.add_argument("--endpoint", default="")
    parser.add_argument("--state", action="store_true")
    parsed = parser.parse_args(argv)

    instance = Path(parsed.instance).expanduser().resolve()
    try:
        endpoint = _endpoint(instance, parsed.endpoint or None)
        credential = _bound_credential(instance, endpoint)
    except _Refused as exc:
        # Nothing has been sent: no request, no token, no header.
        print(json.dumps({"ok": False, "status": "refused", "reason_code": exc.reason_code,
                          "sent": False}, ensure_ascii=False))
        return 2
    except phone_operator_stop.OperatorStopInvalid as exc:
        print(json.dumps({"ok": False, "status": "unavailable",
                          "reason_code": exc.reason_code, "sent": False},
                         ensure_ascii=False))
        return 2

    if parsed.state:
        code, payload = _call(
            "%s/api/phone/control/activity/stop/state?activity_id=%s"
            % (endpoint, urllib.parse.quote(parsed.activity, safe="")),
            token=credential.token)
        print(json.dumps({"http": code, "result": payload}, ensure_ascii=False, indent=1))
        return 0 if code == 200 else 1
    if parsed.revision is None:
        print(json.dumps({"ok": False, "status": "rejected",
                          "reason_code": "EXPECTED_REVISION_REQUIRED"}, ensure_ascii=False))
        return 2
    key = parsed.operation_key.strip() or "opstop-cli:%s:%s" % (
        parsed.activity, uuid.uuid4().hex[:12])
    code, payload = _call(
        "%s/api/phone/control/activity/stop" % endpoint, method="POST",
        token=credential.token,
        body={"activity_id": parsed.activity, "expected_revision": int(parsed.revision),
              "operation_key": key, "reason_code": parsed.reason})
    print(json.dumps({"http": code, "result": payload}, ensure_ascii=False, indent=1))
    return 0 if code == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())
