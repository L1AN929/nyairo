#!/usr/bin/env python3
"""Round A1 convergence: one slice adapter for the phone projections.

Covers ticket ``roundA1``:

* ``PhoneService._read_slice_view`` / ``_mutate_slice_view`` are the single
  entry point behind the v0.2/v0.3/v0.4/v0.8 slice methods, while every public
  method name and signature (and therefore every existing caller) stays put.
* ``PhoneHandler._read_slice_json_body`` is the one strict body reader; the two
  older readers are thin aliases with their original empty-body rules.
* ``PhoneHandler._serve_slice_get`` / ``_serve_slice_post`` own the guard,
  query, capability, parse, dispatch and send sequence.
* The copy-paste status drift is repaired: v0.3 clock/ecology/embodied-social
  keep their explicit 403/409 code and ``reason_code`` instead of flattening to
  a generic 400.
* ``PhoneService.body_health`` reads the runtime through the canonical
  ``body_health_projection`` reader and stays zero-write, with the same wire
  shape (including ``energy``).
* Partner-only and subject-view routes stay strictly separated.

Run (WSL): ``python -m unittest tests.test_v08_slice_adapter_convergence -v``.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import sys
import tempfile
import types
import unittest
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "components" / "world" / "service"),
    str(ROOT / "components" / "world" / "service" / "bridge"),
    str(ROOT / "components" / "supply"),
    str(ROOT / "tests"),
    str(ROOT / "scripts"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from components.phone import capability as phone_capability  # noqa: E402
from components.phone import service as phone_service  # noqa: E402
from components.phone import v03_embodied_social_view as embodied_view  # noqa: E402
from components.life.scripts import v03_clock_evaporation as clock_mod  # noqa: E402
from components.world.scripts import v03_economy_illness_bridge as ecology_bridge  # noqa: E402
from components.world.scripts import body_health_projection as health_proj  # noqa: E402

SUBJECT = "chiyo"
CAP_TOKEN = "slice-convergence-cap-" + uuid.uuid4().hex
MOMENT = datetime(2026, 11, 16, 9, 0, tzinfo=timezone(timedelta(hours=8)))

POSIX_ONLY = unittest.skipUnless(
    hasattr(socket, "AF_UNIX") and os.name == "posix",
    "Requires POSIX domain sockets (run inside WSL)",
)

SLICE_GET_ROUTES = (
    "/api/phone/v03/clock",
    "/api/phone/v03/ecology",
    "/api/phone/v03/embodied-social",
    "/api/phone/v04/neighborhood",
    "/api/phone/v04/builder-trylive",
    "/api/phone/v04/elastic-play",
    "/api/phone/v02/agency-depth",
    "/api/phone/v02/crossmodal",
    "/api/phone/v02/runtime-migration",
    "/api/phone/v08/living-traces",
    "/api/phone/v08/composable-body",
    "/api/phone/v03/encounters",
    "/api/phone/v08/emergent-echoes",
)


@POSIX_ONLY
class SliceAdapterHTTPTest(unittest.TestCase):
    """The real loopback surface, driven through the converged helpers."""

    @classmethod
    def setUpClass(cls) -> None:
        import autonomy_rhythm_fixture as fixture

        cls.fixture = fixture
        cls._saved_env = dict(os.environ)
        os.environ["CHIYO_WORLD_LOCAL_OWNER_PHONE"] = SUBJECT
        cls.root = Path(tempfile.mkdtemp(prefix="v08-slice-convergence-"))
        cls.capability = phone_capability.Capability(
            token=CAP_TOKEN,
            subject=SUBJECT,
            endpoint="http://127.0.0.1:1",
            issued_at="2026-11-12T12:00:00+08:00",
        )
        cls.stack = fixture.build_stack(cls.root, port=0, capability=cls.capability)
        fixture.serve_stack(cls.stack)
        cls.base = cls.stack.base

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fixture.close_stack(cls.stack)
        shutil.rmtree(cls.root, ignore_errors=True)
        os.environ.clear()
        os.environ.update(cls._saved_env)

    def setUp(self) -> None:
        for state in (
            clock_mod.v03_clock_state_path(self.stack.root),
            ecology_bridge.ecology_state_path(self.stack.root),
            embodied_view.v03_embodied_social_state_path(self.stack.root),
        ):
            state.unlink(missing_ok=True)
        self.stack.service._world_clock = lambda: MOMENT

    def _call(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        *,
        csrf: bool = False,
        capability: str | None = None,
    ) -> tuple[int, dict]:
        data = (
            None
            if body is None
            else json.dumps(body, ensure_ascii=False).encode("utf-8")
        )
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if csrf:
            req.add_header("X-CSRF-Token", self.stack.csrf_token)
        if capability is not None:
            req.add_header("X-Phone-Capability", capability)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    # -- shared GET helper ------------------------------------------------------

    def test_01_shared_get_guards_are_uniform_and_read_only(self) -> None:
        for path in SLICE_GET_ROUTES:
            code, body = self._call("GET", path)
            self.assertEqual(code, 200, path)
            self.assertTrue(body.get("ok"), path)
            self.assertIn(body.get("status"), ("ok", "applied"), path)

            c403, _ = self._call("GET", path, capability="wrong-token")
            self.assertEqual(c403, 403, path)

            c400, _ = self._call("GET", path + "?extra=1")
            self.assertEqual(c400, 400, path)
            self.assertEqual(self._call("GET", path + "?extra=1")[1].get("reason_code"),
                             "UNKNOWN_QUERY", path)

        # The projections above are pure reads: no slice state file appeared.
        for state in (
            clock_mod.v03_clock_state_path(self.stack.root),
            ecology_bridge.ecology_state_path(self.stack.root),
            embodied_view.v03_embodied_social_state_path(self.stack.root),
        ):
            self.assertFalse(state.exists(), f"GET wrote state file: {state}")

    def test_02_body_health_is_credential_free_but_query_strict(self) -> None:
        # ``desk=False``: the body-health door never consumed a credential.
        code, body = self._call("GET", "/api/phone/body/health")
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["writes_performed"], 0)
        self.assertEqual(body["read_only"], True)

        c_mismatch, _ = self._call(
            "GET", "/api/phone/body/health", capability="wrong-token"
        )
        self.assertEqual(c_mismatch, 200)

        c400, bad = self._call("GET", "/api/phone/body/health?extra=1")
        self.assertEqual(c400, 400)
        self.assertEqual(bad["reason_code"], "UNKNOWN_QUERY")

    # -- shared POST helper -----------------------------------------------------

    def test_03_shared_post_fences_and_partner_mutation(self) -> None:
        path = "/api/phone/v08/composable-body"

        # CSRF first.
        c_no_csrf, _ = self._call("POST", path, {"action": "offer_ordinary_care"})
        self.assertEqual(c_no_csrf, 403)

        # A wrong capability is a refusal, never a quiet partner downgrade.
        c_wrong, wrong = self._call(
            "POST",
            path,
            {"action": "offer_ordinary_care", "care_kind": "warm_drink"},
            csrf=True,
            capability="wrong-token",
        )
        self.assertEqual(c_wrong, 403)
        self.assertEqual(wrong["reason_code"], "CAPABILITY_INVALID")

        # The identity fence survives the merge and still names the field.
        c_identity, identity = self._call(
            "POST",
            path,
            {"action": "offer_ordinary_care", "care_kind": "warm_drink", "owner": "me"},
            csrf=True,
        )
        self.assertEqual(c_identity, 409)
        self.assertEqual(identity["reason_code"], "CLIENT_IDENTITY_REFUSED:owner")

        # The v0.8 doors keep their original "empty object is not a command".
        c_empty, empty = self._call("POST", path, {}, csrf=True)
        self.assertEqual(c_empty, 400)
        self.assertEqual(empty["reason_code"], "BODY_REQUIRED")

        # Unknown query is refused before the body is parsed.
        c_query, _ = self._call(
            "POST",
            path + "?extra=1",
            {"action": "offer_ordinary_care", "care_kind": "warm_drink"},
            csrf=True,
        )
        self.assertEqual(c_query, 400)

        # A real partner-door mutation still works through the shared helper.
        c_ok, ok_body = self._call(
            "POST",
            path,
            {"action": "offer_ordinary_care", "care_kind": "warm_drink"},
            csrf=True,
        )
        self.assertEqual(c_ok, 200, ok_body)
        self.assertEqual(ok_body["status"], "applied")

    def test_04_shared_post_is_idempotent_on_replay(self) -> None:
        path = "/api/phone/v03/clock"
        body = {
            "action": "record_evaporable",
            "text": "刚才窗外飞过一只灰鸽子",
            "operation_key": "convergence-op-1",
        }
        c1, r1 = self._call("POST", path, body, csrf=True, capability=CAP_TOKEN)
        self.assertEqual(c1, 200, r1)
        c2, r2 = self._call("POST", path, body, csrf=True, capability=CAP_TOKEN)
        self.assertEqual(c2, 200, r2)
        self.assertEqual(r2["status"], "already_applied")

    # -- repaired status normalisation -----------------------------------------

    def test_05_v03_refusals_keep_their_code_and_reason(self) -> None:
        forbidden_cases = (
            (
                "/api/phone/v03/clock",
                {"action": "record_evaporable", "text": "主体专属"},
                "SUBJECT_CAPABILITY_REQUIRED",
            ),
            (
                "/api/phone/v03/ecology",
                {"action": "complete_independent_work", "job_kind": "proofreading",
                 "work_ref": "w1", "amount_cents": 2000, "title": "校对补贴"},
                "FORBIDDEN_ACTOR_SIDE",
            ),
            (
                "/api/phone/v03/embodied-social",
                {"action": "record_style_migration", "title": "淡彩速写笔触",
                 "origin_context": "alley_sketch", "stage": "NOTICED", "shared": False},
                "FORBIDDEN_ACTOR_SIDE",
            ),
        )
        for path, body, reason in forbidden_cases:
            code, out = self._call("POST", path, body, csrf=True)
            self.assertEqual(code, 403, f"{path} -> {out}")
            self.assertEqual(out["reason_code"], reason, path)
            self.assertEqual(out["status"], "rejected", path)

        # 409 conflicts are not flattened either: a re-used operation key with a
        # different intent is a named conflict on the entity door.
        first = {"action": "set_partner_presence", "presence_mode": "same_room_apart",
                 "operation_key": "convergence-op-2"}
        c1, r1 = self._call("POST", "/api/phone/v03/embodied-social", first, csrf=True)
        self.assertEqual(c1, 200, r1)
        second = dict(first, presence_mode="same_room_together")
        c2, r2 = self._call("POST", "/api/phone/v03/embodied-social", second, csrf=True)
        self.assertEqual(c2, 409, r2)
        self.assertEqual(r2["reason_code"], "OPERATION_KEY_CONFLICT")

    # -- no cross-route permission relaxation ----------------------------------

    def test_06_partner_and_subject_doors_stay_separated(self) -> None:
        # Partner door: no credential, partner-owned action is allowed.
        c_partner, _ = self._call(
            "POST",
            "/api/phone/v08/composable-body",
            {"action": "offer_ordinary_care", "care_kind": "warm_drink"},
            csrf=True,
        )
        self.assertEqual(c_partner, 200)

        # The same door cannot act as the subject.
        c_refused, refused = self._call(
            "POST",
            "/api/phone/v08/composable-body",
            {"action": "configure_body_pack", "pack_id": "nocturnal_emission",
             "enabled": True},
            csrf=True,
        )
        self.assertEqual(c_refused, 403)
        self.assertEqual(refused["reason_code"], "FORBIDDEN_PARTNER_ACTION")

        # Subject door with the real capability succeeds on the same action.
        c_subject, subject_body = self._call(
            "POST",
            "/api/phone/v08/composable-body",
            {"action": "configure_body_pack", "pack_id": "nocturnal_emission",
             "enabled": True},
            csrf=True,
            capability=CAP_TOKEN,
        )
        self.assertEqual(c_subject, 200, subject_body)
        self.assertEqual(subject_body["status"], "applied")

        # A bad credential is still a 403 on both GET and POST entity doors.
        c_get, _ = self._call(
            "GET", "/api/phone/v08/living-traces", capability="wrong-token"
        )
        self.assertEqual(c_get, 403)


class BodyHealthConvergenceTest(unittest.TestCase):
    """``body_health`` delegates to the canonical runtime reader and writes nothing."""

    def _shim(self, world_home: Path):
        shim = NS(world_home=world_home, root=world_home,
                  _somatic_now_iso=lambda: MOMENT.isoformat())
        shim.body_health = types.MethodType(
            phone_service.PhoneService.body_health, shim
        )
        shim._load_body_health_module = (
            phone_service.PhoneService._load_body_health_module
        )
        return shim

    @staticmethod
    def _manifest(root: Path) -> list[tuple[str, int, int]]:
        rows: list[tuple[str, int, int]] = []
        for path in sorted(root.rglob("*")):
            if path.is_file():
                stat = path.stat()
                rows.append((str(path.relative_to(root)), stat.st_size,
                             stat.st_mtime_ns))
        return rows

    @staticmethod
    def _home(fatigue: float) -> Path:
        root = Path(tempfile.mkdtemp(prefix="v08-slice-health-"))
        body = root / "data" / "body"
        body.mkdir(parents=True)
        (body / "runtime.json").write_text(
            json.dumps({"schema_version": "body.runtime.v2",
                        "somatic": {"fatigue": fatigue}}),
            encoding="utf-8",
        )
        return root

    def test_10_energy_uses_the_canonical_runtime_reader(self) -> None:
        root = self._home(0.81)
        try:
            code, body = self._shim(root).body_health()
            self.assertEqual(code, 200)
            self.assertEqual(body["energy"]["fatigue_band"], "high")
            self.assertEqual(body["energy"]["range"], [19, 34])

            # The file says 0.81; the canonical reader says 0.5.  The answer
            # follows the reader, proving body_health no longer parses the file
            # itself.
            with mock.patch.object(
                health_proj, "_read_runtime_state",
                return_value=({"somatic": {"fatigue": 0.5}}, None),
            ):
                code2, body2 = self._shim(root).body_health()
            self.assertEqual(code2, 200)
            self.assertEqual(body2["energy"]["fatigue_band"], "mid")
            self.assertEqual(body2["energy"]["range"], [50, 65])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_11_shape_and_zero_write_invariant_hold(self) -> None:
        root = self._home(0.2)
        try:
            before = self._manifest(root)
            code, body = self._shim(root).body_health()
            self.assertEqual(self._manifest(root), before)
            self.assertEqual(code, 200)
            for key in ("health_status", "attested", "episode", "capacity",
                        "health_source", "reason_code", "read_only",
                        "writes_performed", "energy", "status"):
                self.assertIn(key, body)
            self.assertEqual(body["writes_performed"], 0)
            self.assertEqual(body["read_only"], True)
            self.assertEqual(body["status"], phone_service.STATUS_OK)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_12_missing_runtime_stays_a_named_unknown(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="v08-slice-health-empty-"))
        try:
            code, body = self._shim(root).body_health()
            self.assertEqual(code, 200)
            self.assertEqual(body["health_status"], "UNATTESTED")
            self.assertEqual(body["reason_code"], "RUNTIME_UNAVAILABLE")
            self.assertIsNone(body["energy"])
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
