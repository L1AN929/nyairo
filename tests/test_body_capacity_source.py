#!/usr/bin/env python3
"""BODY-CAP-1 tests: the body-capacity read adapter.

What is *real* here (nothing in this file claims a live deployment)
------------------------------------------------------------------
The read path under test is the real one, end to end:

    real body_runtime state (body_runtime.initial_runtime + body_runtime.advance)
      -> real WorldBodyService.body_summary / _moment  (bound, not copied)
      -> real GatewaySurface.get_body_signals           (world_body_gateway_api)
      -> real unix socket + real WorldBodyClient        (components/native/integration)
      -> real WorldBodyReadPort                         (world_body_read_port)
      -> body_capacity_source.read_body_capacity

Only the *service object* is a shim: ``GatewaySurface.get_body_signals`` reads
exactly ``service.body_state``, ``service._moment()`` and ``service.body_summary()``,
so those three come from the real class and a ``SimpleNamespace`` supplies the
rest.  No live service, no network, no production instance is touched.

Some cases deliberately answer with a *crafted* payload over that same real
transport (corrupt signal schema, raw-value leak, unknown signal type, transport
failure).  Those are labelled ``crafted payload`` and are not presented as real
body conditions.

Run:  python3 tests/test_body_capacity_source.py
"""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import socket
import sys
import tempfile
import threading
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
LIFE_SCRIPTS = ROOT / "components" / "life" / "scripts"
WORLD_SCRIPTS = ROOT / "components" / "world" / "scripts"
WORLD_SERVICE = ROOT / "components" / "world" / "service"
NATIVE_INTEGRATION = ROOT / "components" / "native" / "integration"

sys.path[:0] = [
    str(ROOT),
    str(LIFE_SCRIPTS),
    str(WORLD_SCRIPTS),
    str(WORLD_SERVICE),
    str(NATIVE_INTEGRATION),
]

import body_capacity_source as bcs  # noqa: E402
import body_reader as bread  # noqa: E402
import body_runtime as br  # noqa: E402
import body_signal as bsig  # noqa: E402
from world_body_gateway_api import GatewaySurface  # noqa: E402
from world_body_service import WorldBodyService  # noqa: E402

def _load_by_path(name: str, path: Path):
    """Load one module from an explicit file (no name ambiguity).

    Registered in ``sys.modules`` first: ``dataclasses`` resolves string
    annotations through the module registry during class creation.
    """

    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
#: Loaded from the same directory as the port, so the client/port pairing under
#: test is exactly the one the adapter documents.
wbc = _load_by_path("bodycap_world_body_client", NATIVE_INTEGRATION / "world_body_client.py")
wbrp = _load_by_path("bodycap_world_body_read_port", NATIVE_INTEGRATION / "world_body_read_port.py")

BODY_ID = br.BODY_ID  # "chiyo_body"


# ---------------------------------------------------------------------------
# real-bridge fixture
# ---------------------------------------------------------------------------


class UnixSocketGatewayServer:
    """A real unix socket in front of a real ``GatewaySurface``.

    Same shape as the tree's existing ``FakeSocketServer`` (bridge tests): the
    transport and the dispatch are real, so a command log here is real wire
    traffic.
    """

    def __init__(self, surface: GatewaySurface, responder=None) -> None:
        self.surface = surface
        self.responder = responder
        self.commands: list[str] = []
        self.directory = Path(tempfile.mkdtemp(prefix="bodycap-sock-"))
        self.path = self.directory / "gateway.sock"
        self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._socket.bind(str(self.path))
        self._socket.listen(8)
        self._socket.settimeout(0.2)
        self._stop = False
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while not self._stop:
            try:
                connection, _ = self._socket.accept()
            except (socket.timeout, OSError):
                continue
            try:
                data = connection.recv(65536)
                request = json.loads(data.decode().strip().splitlines()[0])
                command = request.get("command")
                self.commands.append(command)
                if self.responder is not None:
                    payload = self.responder(command, request.get("argument"))
                else:
                    payload = self.surface.handle(command, request.get("argument"))
            except Exception as error:  # noqa: BLE001 - a broken client is not a crash
                payload = {"ok": False, "error": type(error).__name__}
            try:
                connection.sendall((json.dumps(payload) + "\n").encode())
            except OSError:
                pass
            finally:
                connection.close()

    def close(self) -> None:
        self._stop = True
        self._thread.join(timeout=2)
        self._socket.close()
        try:
            self.path.unlink()
        except OSError:
            pass


def service_shim(state, moment):
    """The smallest object ``GatewaySurface.get_body_signals`` can run against.

    ``_moment`` and ``body_summary`` are the real ``WorldBodyService`` methods
    bound to this object; nothing about signal production is re-implemented.
    """

    shim = NS(body_state=state, _now=moment)
    shim._moment = types.MethodType(WorldBodyService._moment, shim)
    shim.body_summary = types.MethodType(WorldBodyService.body_summary, shim)
    return shim


def burdened_state(hours: int = 3):
    """A real, schema-valid runtime state with a real burden signal.

    ``body_runtime.advance`` is the owner of every somatic value; three bounded
    one-hour advances of heavy load put fatigue above the ``fatigue_noticeable``
    hysteresis threshold (measured: 0.5526 at version 3).
    """

    state = br.initial_runtime(at="2026-10-16T00:00:00+00:00")
    moment = datetime(2026, 10, 16, 0, 0, tzinfo=timezone.utc)
    for _ in range(hours):
        report = br.advance(
            state,
            moment.isoformat(),
            (moment + timedelta(hours=1)).isoformat(),
            {
                "class": "PHYSICAL_RESULT",
                "movement_duration_seconds": 3600,
                "effort": "heavy",
                "pose": "standing",
            },
        )
        state = report["state"]
        moment += timedelta(hours=1)
    return state, moment


def interrupted_recovery_state():
    """A real state whose recovery session was broken by a real physical load."""

    state = br.initial_runtime(at="2026-10-16T00:00:00+00:00")
    moment = datetime(2026, 10, 16, 0, 0, tzinfo=timezone.utc)

    def step(inputs):
        nonlocal state, moment
        report = br.advance(
            state, moment.isoformat(), (moment + timedelta(hours=1)).isoformat(), inputs
        )
        state = report["state"]
        moment += timedelta(hours=1)

    for _ in range(3):
        step({"class": "PHYSICAL_RESULT", "movement_duration_seconds": 3600,
              "effort": "heavy", "pose": "standing"})
    step({"class": "RECOVERY", "state": "start", "mode": "rest"})
    step({"class": "PHYSICAL_RESULT", "movement_duration_seconds": 600,
          "effort": "moderate", "pose": "standing"})
    return state, moment


def home_manifest(root: Path) -> list[tuple[str, int, int, str]]:
    """Every file under ``root`` with size, mtime and content digest."""

    manifest: list[tuple[str, int, int, str]] = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            blob = path.read_bytes()
            manifest.append((
                str(path.relative_to(root)),
                len(blob),
                path.stat().st_mtime_ns,
                hashlib.sha256(blob).hexdigest(),
            ))
    return manifest


class BridgeTestCase(unittest.TestCase):
    """Shared lifecycle: a real socket server per port, closed afterwards."""

    def setUp(self) -> None:
        self._servers: list[UnixSocketGatewayServer] = []
        self._homes: list[tempfile.TemporaryDirectory] = []

    def tearDown(self) -> None:
        for server in self._servers:
            server.close()
        for home in self._homes:
            home.cleanup()

    def make_home(self) -> Path:
        handle = tempfile.TemporaryDirectory(prefix="bodycap-home-")
        self._homes.append(handle)
        return Path(handle.name)

    def make_port(self, state, moment, responder=None):
        surface = GatewaySurface(service_shim(state, moment))
        server = UnixSocketGatewayServer(surface, responder=responder)
        self._servers.append(server)
        client = wbc.WorldBodyClient(
            socket_path=server.path, mode=wbc.MODE_CANONICAL, read_retries=0
        )
        return wbrp.WorldBodyReadPort(client), server


# ---------------------------------------------------------------------------
# 1. the mapping cannot drift away from its owners
# ---------------------------------------------------------------------------


class MappingDriftGuards(unittest.TestCase):
    def test_capacity_vocabulary_matches_ag1(self):
        import agency_decision_ag1 as ag1

        self.assertEqual(
            set(bcs.VALID_CAPACITY_STATES), set(ag1.VALID_CAPACITY_STATES)
        )
        for name in ("CAPACITY_AVAILABLE", "CAPACITY_LIMITED", "CAPACITY_UNAVAILABLE",
                     "CAPACITY_UNKNOWN"):
            self.assertEqual(getattr(bcs, name), getattr(ag1, name), name)

    def test_structured_ref_pattern_matches_ag1(self):
        import agency_decision_ag1 as ag1

        self.assertEqual(bcs._STRUCTURED_REF_RE.pattern, ag1._STRUCTURED_REF_RE.pattern)

    def test_burden_split_is_exactly_the_body_signal_vocabulary(self):
        """A new rule in body_signal must fail here, not be silently ignored."""

        owner = set(bsig.SIGNAL_RULES) | set(bsig.TREND_RULES)
        declared = set(bcs.BURDEN_SIGNAL_TYPES) | set(bcs.NON_BURDEN_SIGNAL_TYPES)
        self.assertEqual(declared, owner)
        self.assertFalse(set(bcs.BURDEN_SIGNAL_TYPES) & set(bcs.NON_BURDEN_SIGNAL_TYPES))
        self.assertTrue(bcs.BURDEN_SIGNAL_TYPES)

    def test_no_second_freshness_vocabulary_or_threshold(self):
        """Freshness comes from body_reader, not from a local copy."""

        for name in ("FRESHNESS_FRESH", "FRESHNESS_STALE", "FRESHNESS_UNVERIFIED",
                     "FRESHNESS_UNAVAILABLE", "FRESHNESS_MAX_AGE_HOURS"):
            self.assertFalse(hasattr(bcs, name), name)
        self.assertFalse(
            [n for n in dir(bcs) if "MAX_AGE" in n.upper()],
            "the adapter must not define its own freshness bound",
        )

    def test_view_schema_is_not_a_canonical_schema(self):
        import agency_decision_ag1 as ag1

        self.assertTrue(bcs.SCHEMA_BODY_CAPACITY_VIEW.startswith("chiyo.life."))
        for canonical in (ag1.SCHEMA_CAPACITY_SNAPSHOT, ag1.SCHEMA_DECISION_CONTEXT_SNAPSHOT):
            self.assertNotEqual(bcs.SCHEMA_BODY_CAPACITY_VIEW, canonical)


# ---------------------------------------------------------------------------
# 2. real bridge: legal body views differ, with evidence
# ---------------------------------------------------------------------------


class RealBridgeReads(BridgeTestCase):
    def test_burdened_real_body_yields_limited_with_evidence(self):
        home = self.make_home()
        state, moment = burdened_state(3)
        port, server = self.make_port(state, moment)

        view = bcs.read_body_capacity(port, home=home, now=moment.isoformat())

        self.assertEqual(view["body_capacity_state"], bcs.CAPACITY_LIMITED)
        self.assertEqual(view["reason_code"], bcs.REASON_OK_LIMITED)
        self.assertEqual(view["freshness"], bread.FRESHNESS_FRESH)
        self.assertEqual(view["observed_body_id"], BODY_ID)
        self.assertEqual(view["observed_revision"], "bodyrev:3")
        self.assertEqual(view["evidence"], ["fatigue_noticeable:mild"])
        self.assertEqual(view["source_outcome"], "OK")
        self.assertFalse(view["degraded"])
        for flag in ("identity_bound", "revision_bound", "observation_time_bound"):
            self.assertTrue(view[flag], flag)
        self.assertEqual(view["body_capacity_ref"], "bodycap:chiyo_body:bodyrev:3")
        self.assertEqual(view["schema_version"], bcs.SCHEMA_BODY_CAPACITY_VIEW)
        # one read, and only the body read
        self.assertEqual(server.commands, ["get_body_signals"])

    def test_interrupted_recovery_is_also_burden_evidence(self):
        home = self.make_home()
        state, moment = interrupted_recovery_state()
        port, _ = self.make_port(state, moment)

        view = bcs.read_body_capacity(port, home=home, now=moment.isoformat())

        self.assertEqual(view["body_capacity_state"], bcs.CAPACITY_LIMITED)
        self.assertIn("recovery_interrupted:noticeable", view["evidence"])
        self.assertEqual(view["observed_recovery_state"], "interrupted")

    def test_resting_real_body_is_unknown_never_available(self):
        """No signal is not health: an unburdened body is UNKNOWN, not AVAILABLE."""

        home = self.make_home()
        moment = datetime(2026, 10, 16, 6, 0, tzinfo=timezone.utc)
        state = br.initial_runtime(at="2026-10-16T00:00:00+00:00")
        port, _ = self.make_port(state, moment)

        view = bcs.read_body_capacity(port, home=home, now=moment.isoformat())

        self.assertEqual(view["body_capacity_state"], bcs.CAPACITY_UNKNOWN)
        self.assertEqual(view["reason_code"], bcs.REASON_NO_BURDEN_EVIDENCE)
        self.assertEqual(view["freshness"], bread.FRESHNESS_UNVERIFIED)
        self.assertEqual(view["evidence"], [])
        self.assertIsNone(view["body_capacity_ref"])
        self.assertFalse(view["identity_bound"])
        self.assertNotEqual(view["body_capacity_state"], bcs.CAPACITY_AVAILABLE)

    def test_two_legal_body_views_differ(self):
        """A/B: the same read path, two real body conditions, two answers."""

        home = self.make_home()
        burdened, burdened_at = burdened_state(3)
        port_a, _ = self.make_port(burdened, burdened_at)
        resting = br.initial_runtime(at="2026-10-16T00:00:00+00:00")
        port_b, _ = self.make_port(resting, burdened_at)

        view_a = bcs.read_body_capacity(port_a, home=home, now=burdened_at.isoformat())
        view_b = bcs.read_body_capacity(port_b, home=home, now=burdened_at.isoformat())

        self.assertNotEqual(view_a["body_capacity_state"], view_b["body_capacity_state"])
        self.assertEqual(view_a["body_capacity_state"], bcs.CAPACITY_LIMITED)
        self.assertEqual(view_b["body_capacity_state"], bcs.CAPACITY_UNKNOWN)

    def test_strong_band_never_becomes_unavailable(self):
        """A crafted strong-band payload stays LIMITED; a band is not a hard stop."""

        home = self.make_home()
        state, moment = burdened_state(3)
        payload = {
            "ok": True,
            "result": {
                "available": True,
                "recovery": {"state": "inactive", "mode": None, "trend": "stalled"},
                "signals": [
                    {"signal_type": "fatigue_high", "qualitative_band": "strong",
                     "body_id": BODY_ID, "source_body_version": 9,
                     "occurred_at": moment.isoformat(), "carries_raw_values": False}
                ],
            },
        }
        port, _ = self.make_port(state, moment, responder=lambda c, a: payload)

        view = bcs.read_body_capacity(port, home=home, now=moment.isoformat())

        self.assertEqual(view["body_capacity_state"], bcs.CAPACITY_LIMITED)
        self.assertNotEqual(view["body_capacity_state"], bcs.CAPACITY_UNAVAILABLE)
        self.assertEqual(view["evidence"], ["fatigue_high:strong"])


# ---------------------------------------------------------------------------
# 3. freshness, revision, identity and clock
# ---------------------------------------------------------------------------


class FreshnessAndBinding(BridgeTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.home = self.make_home()
        self.state, self.moment = burdened_state(3)
        self.port, _ = self.make_port(self.state, self.moment)

    def test_stale_view_is_unknown(self):
        later = self.moment + timedelta(hours=20)
        view = bcs.read_body_capacity(self.port, home=self.home, now=later.isoformat())

        self.assertEqual(view["body_capacity_state"], bcs.CAPACITY_UNKNOWN)
        self.assertEqual(view["reason_code"], bcs.REASON_VIEW_STALE)
        self.assertEqual(view["freshness"], bread.FRESHNESS_STALE)
        self.assertEqual(view["evidence"], ["fatigue_noticeable:mild"])

    def test_freshness_boundary_comes_from_body_reader(self):
        inside = self.moment + timedelta(hours=bread.FRESHNESS_MAX_AGE_HOURS - 0.5)
        outside = self.moment + timedelta(hours=bread.FRESHNESS_MAX_AGE_HOURS + 0.5)

        self.assertEqual(
            bcs.read_body_capacity(self.port, home=self.home, now=inside.isoformat())["freshness"],
            bread.FRESHNESS_FRESH,
        )
        self.assertEqual(
            bcs.read_body_capacity(self.port, home=self.home, now=outside.isoformat())["freshness"],
            bread.FRESHNESS_STALE,
        )

    def test_missing_reference_instant_is_unverified(self):
        view = bcs.read_body_capacity(self.port, home=self.home)

        self.assertEqual(view["body_capacity_state"], bcs.CAPACITY_UNKNOWN)
        self.assertEqual(view["reason_code"], bcs.REASON_FRESHNESS_UNVERIFIED)
        self.assertEqual(view["freshness"], bread.FRESHNESS_UNVERIFIED)

    def test_observation_in_the_future_is_not_freshness(self):
        earlier = self.moment - timedelta(hours=3)
        view = bcs.read_body_capacity(self.port, home=self.home, now=earlier.isoformat())

        self.assertEqual(view["body_capacity_state"], bcs.CAPACITY_UNKNOWN)
        self.assertEqual(view["reason_code"], bcs.REASON_OBSERVATION_TIME_AHEAD)

    def test_old_revision_is_refused(self):
        view = bcs.read_body_capacity(
            self.port, home=self.home, now=self.moment.isoformat(),
            expected_revision="bodyrev:2",
        )

        self.assertEqual(view["body_capacity_state"], bcs.CAPACITY_UNKNOWN)
        self.assertEqual(view["reason_code"], bcs.REASON_REVISION_MISMATCH)
        self.assertEqual(view["observed_revision"], "bodyrev:3")

    def test_current_revision_is_accepted(self):
        view = bcs.read_body_capacity(
            self.port, home=self.home, now=self.moment.isoformat(),
            expected_revision="bodyrev:3",
        )

        self.assertEqual(view["body_capacity_state"], bcs.CAPACITY_LIMITED)

    def test_other_body_identity_is_refused(self):
        view = bcs.read_body_capacity(
            self.port, home=self.home, now=self.moment.isoformat(),
            expected_body_id="some_other_body",
        )

        self.assertEqual(view["body_capacity_state"], bcs.CAPACITY_UNKNOWN)
        self.assertEqual(view["reason_code"], bcs.REASON_IDENTITY_MISMATCH)
        self.assertEqual(view["observed_body_id"], BODY_ID)

    def test_home_ref_is_stable_and_path_free(self):
        first = bcs.read_body_capacity(self.port, home=self.home, now=self.moment.isoformat())
        second = bcs.read_body_capacity(self.port, home=str(self.home), now=self.moment.isoformat())

        self.assertEqual(first["home_ref"], second["home_ref"])
        self.assertTrue(first["home_ref"].startswith("home:"))
        self.assertNotIn(str(self.home), json.dumps(first))
        # a different home is a different binding
        other = self.make_home()
        third = bcs.read_body_capacity(self.port, home=other, now=self.moment.isoformat())
        self.assertNotEqual(first["home_ref"], third["home_ref"])


# ---------------------------------------------------------------------------
# 4. fail-closed: source faults, corrupt payloads, bad subject/home
# ---------------------------------------------------------------------------


class FailClosedReads(BridgeTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.home = self.make_home()
        self.state, self.moment = burdened_state(3)

    def crafted(self, payload):
        port, _ = self.make_port(self.state, self.moment, responder=lambda c, a: payload)
        return port

    def read(self, port, **kwargs):
        kwargs.setdefault("now", self.moment.isoformat())
        return bcs.read_body_capacity(port, home=self.home, **kwargs)

    def test_source_unavailable_is_unknown(self):
        client = wbc.WorldBodyClient(
            socket_path="/nonexistent/instance/gateway.sock",
            mode=wbc.MODE_CANONICAL, read_retries=0,
        )
        view = self.read(wbrp.WorldBodyReadPort(client))

        self.assertEqual(view["body_capacity_state"], bcs.CAPACITY_UNKNOWN)
        self.assertEqual(view["reason_code"], "BODY_SOURCE_UNAVAILABLE")
        self.assertEqual(view["source_outcome"], "UNAVAILABLE")

    def test_source_failure_is_reported_with_its_own_outcome(self):
        view = self.read(self.crafted({"ok": False, "error": "boom"}))

        self.assertEqual(view["body_capacity_state"], bcs.CAPACITY_UNKNOWN)
        self.assertEqual(view["reason_code"], "BODY_SOURCE_FAILURE")
        self.assertEqual(view["source_outcome"], "FAILURE")

    def test_integration_off_client_degrades_to_unknown(self):
        client = wbc.WorldBodyClient(mode=wbc.MODE_OFF)
        view = self.read(wbrp.WorldBodyReadPort(client))

        self.assertEqual(view["body_capacity_state"], bcs.CAPACITY_UNKNOWN)
        self.assertEqual(view["reason_code"], "BODY_SOURCE_UNAVAILABLE")

    def test_non_mapping_result_is_malformed(self):
        view = self.read(self.crafted({"ok": True, "result": "not-an-object"}))

        self.assertEqual(view["body_capacity_state"], bcs.CAPACITY_UNKNOWN)
        self.assertEqual(view["reason_code"], bcs.REASON_PAYLOAD_MALFORMED)

    def test_body_state_absent_is_unknown(self):
        view = self.read(self.crafted({"ok": True, "result": {"available": False,
                                                             "signals": []}}))

        self.assertEqual(view["body_capacity_state"], bcs.CAPACITY_UNKNOWN)
        self.assertEqual(view["reason_code"], bcs.REASON_STATE_NOT_PRESENT)

    def test_signals_not_a_list_is_invalid(self):
        view = self.read(self.crafted({"ok": True, "result": {"available": True,
                                                             "signals": {}}}))

        self.assertEqual(view["body_capacity_state"], bcs.CAPACITY_UNKNOWN)
        self.assertEqual(view["reason_code"], bcs.REASON_SIGNAL_SCHEMA_INVALID)

    def test_missing_band_is_invalid(self):
        view = self.read(self.crafted({"ok": True, "result": {"available": True, "signals": [
            {"signal_type": "fatigue_noticeable", "body_id": BODY_ID,
             "source_body_version": 3, "occurred_at": self.moment.isoformat()}]}}))

        self.assertEqual(view["body_capacity_state"], bcs.CAPACITY_UNKNOWN)
        self.assertEqual(view["reason_code"], bcs.REASON_SIGNAL_SCHEMA_INVALID)

    def test_unknown_band_is_invalid(self):
        view = self.read(self.crafted({"ok": True, "result": {"available": True, "signals": [
            {"signal_type": "fatigue_noticeable", "qualitative_band": "severe",
             "body_id": BODY_ID, "source_body_version": 3,
             "occurred_at": self.moment.isoformat()}]}}))

        self.assertEqual(view["body_capacity_state"], bcs.CAPACITY_UNKNOWN)
        self.assertEqual(view["reason_code"], bcs.REASON_SIGNAL_SCHEMA_INVALID)

    def test_unclassified_signal_type_fails_closed(self):
        view = self.read(self.crafted({"ok": True, "result": {"available": True, "signals": [
            {"signal_type": "fatigue_critical", "qualitative_band": "strong",
             "body_id": BODY_ID, "source_body_version": 3,
             "occurred_at": self.moment.isoformat()}]}}))

        self.assertEqual(view["body_capacity_state"], bcs.CAPACITY_UNKNOWN)
        self.assertEqual(view["reason_code"], bcs.REASON_SIGNAL_TYPE_UNCLASSIFIED)

    def test_raw_value_leak_is_refused(self):
        view = self.read(self.crafted({"ok": True, "result": {"available": True, "signals": [
            {"signal_type": "fatigue_noticeable", "qualitative_band": "mild",
             "body_id": BODY_ID, "source_body_version": 3,
             "occurred_at": self.moment.isoformat(), "value": 0.62}]}}))

        self.assertEqual(view["body_capacity_state"], bcs.CAPACITY_UNKNOWN)
        self.assertEqual(view["reason_code"], bcs.REASON_SIGNAL_RAW_VALUES)

    def test_missing_identity_fails_closed(self):
        view = self.read(self.crafted({"ok": True, "result": {"available": True, "signals": [
            {"signal_type": "fatigue_noticeable", "qualitative_band": "mild",
             "source_body_version": 3, "occurred_at": self.moment.isoformat()}]}}))

        self.assertEqual(view["body_capacity_state"], bcs.CAPACITY_UNKNOWN)
        self.assertEqual(view["reason_code"], bcs.REASON_IDENTITY_MISSING)

    def test_missing_revision_fails_closed(self):
        view = self.read(self.crafted({"ok": True, "result": {"available": True, "signals": [
            {"signal_type": "fatigue_noticeable", "qualitative_band": "mild",
             "body_id": BODY_ID, "occurred_at": self.moment.isoformat()}]}}))

        self.assertEqual(view["body_capacity_state"], bcs.CAPACITY_UNKNOWN)
        self.assertEqual(view["reason_code"], bcs.REASON_REVISION_MISSING)

    def test_inconsistent_body_identity_fails_closed(self):
        view = self.read(self.crafted({"ok": True, "result": {"available": True, "signals": [
            {"signal_type": "fatigue_noticeable", "qualitative_band": "mild",
             "body_id": BODY_ID, "source_body_version": 3,
             "occurred_at": self.moment.isoformat()},
            {"signal_type": "tension_elevated", "qualitative_band": "mild",
             "body_id": "someone_else", "source_body_version": 3,
             "occurred_at": self.moment.isoformat()}]}}))

        self.assertEqual(view["body_capacity_state"], bcs.CAPACITY_UNKNOWN)
        self.assertEqual(view["reason_code"], bcs.REASON_IDENTITY_MISSING)

    def test_unparseable_observation_time_fails_closed(self):
        view = self.read(self.crafted({"ok": True, "result": {"available": True, "signals": [
            {"signal_type": "fatigue_noticeable", "qualitative_band": "mild",
             "body_id": BODY_ID, "source_body_version": 3,
             "occurred_at": "yesterday-ish"}]}}))

        self.assertEqual(view["body_capacity_state"], bcs.CAPACITY_UNKNOWN)
        self.assertEqual(view["reason_code"], bcs.REASON_OBSERVATION_TIME_INVALID)

    def test_contract_owner_unresolvable_fails_closed(self):
        port, _ = self.make_port(self.state, self.moment)
        saved = {name: sys.modules.get(name) for name in ("body_reader", "body_signal")}
        try:
            sys.modules["body_reader"] = None
            sys.modules["body_signal"] = None
            view = self.read(port)
        finally:
            for name, module in saved.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module

        self.assertEqual(view["body_capacity_state"], bcs.CAPACITY_UNKNOWN)
        self.assertEqual(view["reason_code"], bcs.REASON_CONTRACT_UNRESOLVED)

    def test_wrong_subject_is_refused(self):
        port, _ = self.make_port(self.state, self.moment)
        with self.assertRaises(bcs.BodyCapacitySourceError):
            bcs.read_body_capacity(port, home=self.home, subject_id="someone.else",
                                   now=self.moment.isoformat())
        with self.assertRaises(bcs.BodyCapacitySourceError):
            bcs.BodyCapacitySource(port=port, home=self.home, subject_id="someone.else")

    def test_canonical_subject_is_accepted(self):
        import activity_continuity as ac

        port, _ = self.make_port(self.state, self.moment)
        view = bcs.read_body_capacity(port, home=self.home, subject_id=ac.GLOBAL_SUBJECT_ID,
                                      now=self.moment.isoformat())
        self.assertEqual(view["subject_id"], ac.GLOBAL_SUBJECT_ID)
        self.assertEqual(view["body_capacity_state"], bcs.CAPACITY_LIMITED)

    def test_missing_home_is_unknown(self):
        port, _ = self.make_port(self.state, self.moment)
        view = bcs.read_body_capacity(port, home="/nonexistent/instance-home",
                                      now=self.moment.isoformat())

        self.assertEqual(view["body_capacity_state"], bcs.CAPACITY_UNKNOWN)
        self.assertEqual(view["reason_code"], bcs.REASON_HOME_UNBOUND)

    def test_bad_home_and_port_are_caller_faults(self):
        port, _ = self.make_port(self.state, self.moment)
        with self.assertRaises(bcs.BodyCapacitySourceError):
            bcs.BodyCapacitySource(port=port, home="/nonexistent/instance-home")
        with self.assertRaises(bcs.BodyCapacitySourceError):
            bcs.read_body_capacity(port, home=self.home, now="not-a-time")
        with self.assertRaises(bcs.BodyCapacitySourceError):
            bcs.read_body_capacity(port, home=self.home, expected_revision="bodyrev")
        with self.assertRaises(bcs.BodyCapacitySourceError):
            bcs.read_body_capacity(NS(), home=self.home)

    def test_a_raising_port_is_a_result_not_a_crash(self):
        class Exploding:
            def body(self):
                raise RuntimeError("transport died")

        view = self.read(Exploding())

        self.assertEqual(view["body_capacity_state"], bcs.CAPACITY_UNKNOWN)
        self.assertEqual(view["reason_code"], bcs.REASON_SOURCE_RAISED)


# ---------------------------------------------------------------------------
# 5. budget independence, zero writes, restart, no authority
# ---------------------------------------------------------------------------


class BudgetIndependenceAndReadOnly(BridgeTestCase):
    def test_the_adapter_has_no_budget_input(self):
        for name, parameter in inspect.signature(bcs.read_body_capacity).parameters.items():
            self.assertNotRegex(
                name, r"budget|quota|token|credit|allowance|spend",
                f"{name} looks like a resource-budget input",
            )
            self.assertNotRegex(parameter.annotation.__str__(), r"budget|quota|token")

    def test_a_view_never_reports_budget_as_a_body_fact(self):
        """Negative control: budget vocabulary must not appear anywhere."""

        home = self.make_home()
        state, moment = burdened_state(3)
        port, _ = self.make_port(state, moment)
        view = bcs.read_body_capacity(port, home=home, now=moment.isoformat())

        blob = json.dumps(view, sort_keys=True).lower()
        for word in ("budget", "quota", "token", "credit", "allowance", "exhausted"):
            self.assertNotIn(word, blob, word)

    def test_only_body_facts_change_the_body_answer(self):
        """The one non-body input (the reference instant) cannot move the facts."""

        home = self.make_home()
        state, moment = burdened_state(3)
        port, _ = self.make_port(state, moment)

        early = bcs.read_body_capacity(port, home=home, now=moment.isoformat())
        later = bcs.read_body_capacity(
            port, home=home, now=(moment + timedelta(hours=1)).isoformat()
        )

        for key in ("observed_body_id", "observed_revision", "evidence",
                    "observed_at", "body_capacity_ref"):
            self.assertEqual(early[key], later[key], key)
        self.assertEqual(early["body_capacity_state"], later["body_capacity_state"])

    def test_repeated_reads_write_nothing(self):
        home = self.make_home()
        (home / "state").mkdir()
        (home / "state" / "world.json").write_text('{"revision": 7}', encoding="utf-8")
        (home / "body.jsonl").write_text("", encoding="utf-8")
        before = home_manifest(home)

        state, moment = burdened_state(3)
        port, server = self.make_port(state, moment)
        for _ in range(4):
            view = bcs.read_body_capacity(port, home=home, now=moment.isoformat())

        self.assertEqual(home_manifest(home), before)
        self.assertEqual(server.commands, ["get_body_signals"] * 4)
        self.assertTrue(view["read_only"])
        self.assertEqual(view["reads_performed"], 1)
        self.assertEqual(view["writes_performed"], 0)
        self.assertEqual(view["mutations_attempted"], 0)

    def test_restart_reads_the_owner_again(self):
        home = self.make_home()
        state, moment = burdened_state(3)
        port, server = self.make_port(state, moment)

        first = bcs.BodyCapacitySource(port=port, home=home).read(now=moment.isoformat())
        second = bcs.BodyCapacitySource(port=port, home=home).read(now=moment.isoformat())

        self.assertEqual(first, second)
        self.assertEqual(server.commands, ["get_body_signals"] * 2)
        self.assertEqual(home_manifest(home), [])

    def test_no_candidate_and_no_action_authority(self):
        home = self.make_home()
        state, moment = burdened_state(3)
        port, _ = self.make_port(state, moment)
        view = bcs.read_body_capacity(port, home=home, now=moment.isoformat())

        self.assertEqual(view["candidates_emitted"], 0)
        self.assertEqual(view["actions_authorized"], 0)
        for name in dir(bcs):
            if name.startswith("_"):
                continue
            self.assertNotRegex(
                name.lower(),
                r"candidate|adopt|submit|authoriz|authoris|dispatch|write|save|persist",
                f"public name {name!r} looks like an adoption/authority verb",
            )
            self.assertFalse(
                name.lower().startswith("act"), f"public name {name!r} looks like a verb"
            )
        # the port this adapter is given cannot mutate anything either
        self.assertFalse(hasattr(port, "submit_action"))

    def test_ag1_kwargs_helper_rejects_a_non_view(self):
        with self.assertRaises(bcs.BodyCapacitySourceError):
            bcs.ag1_capacity_kwargs({"body_capacity_state": "BROKEN"})
        with self.assertRaises(bcs.BodyCapacitySourceError):
            bcs.ag1_capacity_kwargs({"nope": 1})


# ---------------------------------------------------------------------------
# 6. the seam: the real AG-1 CapacityEvaluator accepts the view as-is
# ---------------------------------------------------------------------------


class Ag1Seam(BridgeTestCase):
    def test_evaluate_capacity_accepts_the_view_directly(self):
        import agency_decision_ag1 as ag1
        import candidate_sources_ag0 as ag0

        home = self.make_home()
        state, moment = burdened_state(3)
        port, _ = self.make_port(state, moment)
        view = bcs.read_body_capacity(port, home=home, now=moment.isoformat())

        now = moment.isoformat()
        candidate = ag0.CandidateRecord(
            candidate_id="cand:bodycap-seam:1",
            candidate_kind=ag0.CANDIDATE_KIND_CONSIDER_PERSONAL_OPPORTUNITY,
            source_kind=ag0.SOURCE_PERSONAL_OPPORTUNITY,
            source_ref="src:bodycap-seam:1",
            availability_status=ag0.CANDIDATE_STATUS_OPEN,
            created_at=now, observed_at=now, recorded_at=now,
            idempotency_key="idem:bodycap-seam:1",
            causal_parent_refs=["src:bodycap-seam:1"],
            provenance_refs=["src:bodycap-seam:1"],
        )
        snapshot = ag1.DecisionContextSnapshot(
            context_snapshot_id="dctx:bodycap-seam:1",
            opportunity_ref="dopp:bodycap-seam:1",
            candidate_set_ref="cset:bodycap-seam:1",
            life_frame_ref="lframe:bodycap-seam:1",
            captured_at=now,
            source_revisions={},
            snapshot_fingerprint="sfp:bodycap-seam:1",
        )

        result = ag1.CapacityEvaluator.evaluate_capacity(
            allowed_candidates=[candidate],
            context_snapshot=snapshot,
            captured_at=now,
            **bcs.ag1_capacity_kwargs(view),
        )

        self.assertEqual(result.body_capacity_state, ag1.CAPACITY_LIMITED)
        self.assertEqual(result.overall_capacity_state, ag1.CAPACITY_LIMITED)
        self.assertEqual(result.body_capacity_ref, view["body_capacity_ref"])
        executability = result.candidate_executability[candidate.candidate_id]
        self.assertEqual(executability["executability"], "EXECUTABLE_WITH_LIMITS")
        # capacity limits, it never deletes the candidate or starts anything
        self.assertTrue(executability["candidate_remains_open"])
        self.assertEqual(executability["blocking_reasons"], [])

    def test_unknown_view_does_not_become_available(self):
        """AG-1 fabricates AVAILABLE from a ref without a state; we always pass both."""

        home = self.make_home()
        moment = datetime(2026, 10, 16, 6, 0, tzinfo=timezone.utc)
        state = br.initial_runtime(at="2026-10-16T00:00:00+00:00")
        port, _ = self.make_port(state, moment)
        view = bcs.read_body_capacity(port, home=home, now=moment.isoformat())

        kwargs = bcs.ag1_capacity_kwargs(view)

        self.assertEqual(kwargs["body_capacity_state"], bcs.CAPACITY_UNKNOWN)
        self.assertIsNone(kwargs["body_capacity_ref"])

    def test_kwargs_match_the_evaluator_signature(self):
        import agency_decision_ag1 as ag1

        parameters = inspect.signature(
            ag1.CapacityEvaluator.evaluate_capacity
        ).parameters
        self.assertIn("body_capacity_state", parameters)
        self.assertIn("body_capacity_ref", parameters)
        self.assertEqual(
            set(bcs.ag1_capacity_kwargs({"body_capacity_state": bcs.CAPACITY_UNKNOWN,
                                         "body_capacity_ref": None})),
            {"body_capacity_state", "body_capacity_ref"},
        )

    def test_emitted_reference_passes_the_ag1_validator(self):
        import agency_decision_ag1 as ag1

        home = self.make_home()
        state, moment = burdened_state(3)
        port, _ = self.make_port(state, moment)
        view = bcs.read_body_capacity(port, home=home, now=moment.isoformat())

        self.assertEqual(
            ag1._validate_structured_ref(view["body_capacity_ref"], "body_capacity_ref"),
            view["body_capacity_ref"],
        )



class Ag1UnknownPolicy(BridgeTestCase):
    """How AG-1 treats the honest UNKNOWN this adapter returns.

    ``agency_decision_ag1.py:1971`` defaults to ``ALLOW_WITH_UNKNOWN``: an UNKNOWN
    dimension becomes ``EXEC_UNKNOWN`` and the candidate stays open.  The
    ``BLOCK_ON_UNKNOWN`` branch (:2047-2049) is the only thing that turns the same
    view into a defer, and that is the caller's policy choice, not a body fact.
    """

    def setUp(self) -> None:
        super().setUp()
        self.home = self.make_home()
        self.moment = datetime(2026, 10, 16, 6, 0, tzinfo=timezone.utc)
        self.state = br.initial_runtime(at="2026-10-16T00:00:00+00:00")
        self.port, _ = self.make_port(self.state, self.moment)
        self.view = bcs.read_body_capacity(
            self.port, home=self.home, now=self.moment.isoformat()
        )

    def _ag1(self, view, *, now=None, **kwargs):
        """Run the real ``CapacityEvaluator`` over one view; return its verdict."""

        import agency_decision_ag1 as ag1
        import candidate_sources_ag0 as ag0

        captured = now or self.moment.isoformat()
        candidate = ag0.CandidateRecord(
            candidate_id="cand:bodycap-policy:1",
            candidate_kind=ag0.CANDIDATE_KIND_CONSIDER_PERSONAL_OPPORTUNITY,
            source_kind=ag0.SOURCE_PERSONAL_OPPORTUNITY,
            source_ref="src:bodycap-policy:1",
            availability_status=ag0.CANDIDATE_STATUS_OPEN,
            created_at=captured, observed_at=captured, recorded_at=captured,
            idempotency_key="idem:bodycap-policy:1",
            causal_parent_refs=["src:bodycap-policy:1"],
            provenance_refs=["src:bodycap-policy:1"],
        )
        snapshot = ag1.DecisionContextSnapshot(
            context_snapshot_id="dctx:bodycap-policy:1",
            opportunity_ref="dopp:bodycap-policy:1",
            candidate_set_ref="cset:bodycap-policy:1",
            life_frame_ref="lframe:bodycap-policy:1",
            captured_at=captured,
            source_revisions={},
            snapshot_fingerprint="sfp:bodycap-policy:1",
        )
        result = ag1.CapacityEvaluator.evaluate_capacity(
            allowed_candidates=[candidate],
            context_snapshot=snapshot,
            captured_at=captured,
            **bcs.ag1_capacity_kwargs(view, **kwargs),
        )
        return ag1, result.candidate_executability[candidate.candidate_id]

    def test_the_mirrored_policies_match_the_ag1_owner(self):
        import agency_decision_ag1 as ag1

        parameters = inspect.signature(ag1.CapacityEvaluator.evaluate_capacity).parameters
        self.assertEqual(
            parameters["unknown_capacity_policy"].default,
            bcs.AG1_UNKNOWN_CAPACITY_POLICY_ALLOW,
        )
        source = inspect.getsource(ag1.CapacityEvaluator.evaluate_capacity)
        self.assertIn('"%s"' % bcs.AG1_UNKNOWN_CAPACITY_POLICY_BLOCK, source)
        self.assertIn(ag1.REASON_CAPACITY_UNKNOWN_DEFERRED, source)

    def test_the_honest_unknown_is_not_a_defer_by_default(self):
        self.assertEqual(self.view["body_capacity_state"], bcs.CAPACITY_UNKNOWN)
        self.assertEqual(self.view["reason_code"], bcs.REASON_NO_BURDEN_EVIDENCE)

        ag1, executability = self._ag1(self.view)

        self.assertEqual(executability["executability"], ag1.EXEC_UNKNOWN)
        self.assertEqual(executability["blocking_reasons"], [])
        self.assertTrue(executability["candidate_remains_open"])

    def test_block_on_unknown_is_the_only_defer_path(self):
        ag1, executability = self._ag1(
            self.view, unknown_capacity_policy=bcs.AG1_UNKNOWN_CAPACITY_POLICY_BLOCK
        )

        self.assertEqual(executability["executability"], ag1.EXEC_NOT_NOW)
        self.assertEqual(
            executability["blocking_reasons"], [ag1.REASON_CAPACITY_UNKNOWN_DEFERRED]
        )
        # a defer still does not delete the candidate
        self.assertTrue(executability["candidate_remains_open"])

    def test_limited_stays_executable_with_limits_under_either_policy(self):
        home = self.make_home()
        state, moment = burdened_state(3)
        port, _ = self.make_port(state, moment)
        limited = bcs.read_body_capacity(port, home=home, now=moment.isoformat())
        self.assertEqual(limited["body_capacity_state"], bcs.CAPACITY_LIMITED)

        for policy in (None, bcs.AG1_UNKNOWN_CAPACITY_POLICY_ALLOW,
                       bcs.AG1_UNKNOWN_CAPACITY_POLICY_BLOCK):
            kwargs = {} if policy is None else {"unknown_capacity_policy": policy}
            ag1, executability = self._ag1(limited, now=moment.isoformat(), **kwargs)
            self.assertEqual(executability["executability"], ag1.EXEC_WITH_LIMITS, policy)
            self.assertEqual(executability["blocking_reasons"], [], policy)

    def test_an_illegal_policy_is_refused_and_the_default_adds_no_key(self):
        self.assertEqual(
            set(bcs.ag1_capacity_kwargs(self.view)),
            {"body_capacity_state", "body_capacity_ref"},
        )
        with self.assertRaises(bcs.BodyCapacitySourceError):
            bcs.ag1_capacity_kwargs(self.view, unknown_capacity_policy="DEFER_FOREVER")
        with self.assertRaises(bcs.BodyCapacitySourceError):
            bcs.BodyCapacitySource(port=self.port, home=self.home).ag1_capacity_kwargs(
                self.view, unknown_capacity_policy="DEFER_FOREVER"
            )

if __name__ == "__main__":
    unittest.main(verbosity=2)
