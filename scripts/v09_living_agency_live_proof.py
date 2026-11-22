#!/usr/bin/env python3
"""NyAiro v0.9 Stage 3 Standalone Live E2E Proof Runner.

Proves all three core Living Agency & Evolving World guarantees end-to-end
against real local HTTP servers and production engines with ``llm_calls == 0``:

1. Two-Instance Divergence From Lived History (identical initial seed/config ->
   divergent dynamic catalog stocks persisted across server restart, divergent
   NPC commitment & weather outcomes, divergent Growth-from-Blank stances &
   persuasion vs coercion rejection, divergent emergent object reuse / ceramic
   mug occupancy physics / blank-start custom categories / deduplicated
   capability-gap collection, and divergent <=1800-char lived context honoring
   the unified forget barrier with neutral TA phrasing).
2. Blind Simultaneous Choice & Anti-Sycophancy Live HTTP Proof (pre-reveal
   SHA-256 cryptographic seal masking, live post-reveal seal verification,
   rejection of bare insistence 409 BARE_INSISTENCE_NOT_EVIDENCE and reason
   erasure 409 SYCOPHANTIC_REASON_ERASURE_FORBIDDEN, and honest
   ACCEPT_CHOICE_KEEP_REASONS resolution keeping TA's original reasons).
3. 72-Hour Zero-Idle-Spin 60s Heartbeat Proof (full 4,320 ticks across 72
   simulated hours: 0 disk writes / unchanged mtime+SHA-256 / 0 LLM calls on
   all 4,317 idle ticks, exact 3 wake transitions at +18h DUE_TIME, +36h
   CONDITION_CHANGED, and +54h DUE_TIME, plus 400 RETROACTIVE_FABRICATION_FORBIDDEN).

Runs exclusively inside an ephemeral temporary directory that cleans up on
exit so zero files or caches pollute the repository.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    REPO_ROOT,
    REPO_ROOT / "chiyo_bundle",
    REPO_ROOT / "scripts",
    REPO_ROOT / "components",
    REPO_ROOT / "components" / "world" / "scripts",
    REPO_ROOT / "components" / "life" / "scripts",
    REPO_ROOT / "components" / "phone",
):
    if str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))

from chiyo_bundle import lived_context_bridge as lcb  # noqa: E402
from components.life.scripts import v02_representation_lease_forget as rlf  # noqa: E402
from components.life.scripts import v09_true_stance_engine as tse  # noqa: E402
from components.phone import capability as phone_capability  # noqa: E402
from components.phone import server as phone_server  # noqa: E402
from components.phone import service as phone_service  # noqa: E402
from components.phone import somatic_comfort_view as somatic_view  # noqa: E402
from components.phone import v09_living_agency_view as la_view  # noqa: E402
from components.phone import v09_world_commitments_view as wc_view  # noqa: E402
from components.world.scripts import personal_objects_taxonomy as pot  # noqa: E402
from components.world.scripts import world_body_substrate as substrate  # noqa: E402
from components.world.scripts import world_commitments_engine as wce  # noqa: E402
from components.world.scripts import world_content_catalog as wcc  # noqa: E402
from components.world.scripts import world_property_economy as economy  # noqa: E402
from scripts import nyairo_day  # noqa: E402

TZ_BJT = timezone(timedelta(hours=8))
T0_DT = datetime(2026, 11, 21, 12, 0, 0, tzinfo=TZ_BJT)
T0_ISO = T0_DT.isoformat(timespec="seconds")
T_PLUS_1H_ISO = (T0_DT + timedelta(hours=1)).isoformat(timespec="seconds")
T_PLUS_2H_ISO = (T0_DT + timedelta(hours=2)).isoformat(timespec="seconds")
T_PLUS_25H_ISO = (T0_DT + timedelta(hours=25)).isoformat(timespec="seconds")

SHARED_SEED = "seed-v09-twin-2026"
SHARED_WORLD_ID = "shiomi_city"
SHARED_THEME = "coastal"

FORBIDDEN_PRONOUN_TOKENS: tuple[str, str, str] = (
    "\u5979",
    "\u4ed6",
    "\u5343\u4ee3",
)


@contextlib.contextmanager
def _isolated_forget_control_env() -> Iterator[None]:
    """Ensure each instance root owns its own isolated forget-barrier ledger."""
    prev = os.environ.pop("NYAIRO_V02_CONTROL_ROOT", None)
    try:
        yield
    finally:
        if prev is not None:
            os.environ["NYAIRO_V02_CONTROL_ROOT"] = prev


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.write_bytes(raw.replace("\r\n", "\n").encode("utf-8"))


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int, str]]:
    snap: dict[str, tuple[int, int, str]] = {}
    if not root.exists():
        return snap
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        st = p.lstat()
        if p.is_file() and not p.is_symlink():
            snap[rel] = (
                int(st.st_size),
                int(st.st_mtime_ns),
                hashlib.sha256(p.read_bytes()).hexdigest(),
            )
        else:
            snap[rel] = (0, int(st.st_mtime_ns), "dir")
    return snap


def _sidecars(root: Path) -> list[str]:
    if not root.exists():
        return []
    return sorted(
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.name.endswith(("-wal", "-shm", ".lock", ".tmp"))
    )


def _http_json(
    url: str,
    *,
    method: str = "GET",
    body: Optional[dict[str, Any]] = None,
    headers: Optional[dict[str, str]] = None,
) -> tuple[int, dict[str, Any]]:
    req_headers = dict(headers or {})
    data_bytes: Optional[bytes] = None
    if body is not None:
        data_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data_bytes, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return int(resp.status), json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        raw = err.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = {"raw": raw}
        return int(err.code), parsed


def _assert_neutral_text(text: str, label: str) -> None:
    for tok in FORBIDDEN_PRONOUN_TOKENS:
        if tok in text:
            escaped = tok.encode("unicode_escape").decode("ascii")
            raise AssertionError(f"{label} leaked forbidden token {escaped}")


class LiveInstanceHarness:
    """Isolated v0.9 instance backed by a real ephemeral loopback PhoneHTTPServer."""

    def __init__(
        self,
        name: str,
        root: Path,
        *,
        clock_iso: str = T0_ISO,
        seed: str = SHARED_SEED,
        world_id: str = SHARED_WORLD_ID,
        theme: str = SHARED_THEME,
    ) -> None:
        self.name = name
        self.root = root
        self.clock_box: list[str] = [clock_iso]
        self.seed = seed
        self.world_id = world_id
        self.theme = theme
        self.csrf_token = f"csrf-v09-live-{name}"
        self.cap_token = f"cap_v09_live_{name}_0123456789abcdef0123456789"
        self.env: dict[str, str] = {}
        self.local_owner: dict[str, Any] = {}
        self.srv: Optional[phone_server.PhoneHTTPServer] = None
        self.thread: Optional[threading.Thread] = None
        self.base_url: str = ""

    def seed_initial_state(self, *, place: str = "home_goods_store", area: str = "counter") -> None:
        for rel in (("phone",), ("run",), ("data", "world"), ("data", "body"), ("state",)):
            self.root.joinpath(*rel).mkdir(parents=True, exist_ok=True)

        _write_json(
            self.root / "run" / "nyairo-day.instance.json",
            {
                "schema": "nyairo.day.instance.v1",
                "world_id": self.world_id,
                "theme": self.theme,
                "instance_seed": self.seed,
                "world_seed": self.seed,
                "seed": self.seed,
            },
        )
        _write_json(
            self.root / "data" / "world" / "world_state.json",
            {
                "schema_version": "world.foundation.v0",
                "revision": 3,
                "world_id": self.world_id,
                "world_seed": self.seed,
                "theme": self.theme,
                "timezone": "Asia/Tokyo",
                "location": {"place_id": place, "area_id": area},
                "scene": {"revision": 3, "scene_id": f"{place}.{area}"},
                "environment": {"season": "autumn", "weather": "clear"},
                "facts": [],
            },
        )
        _write_json(
            self.root / "data" / "world" / "world_timeline.json",
            {
                "schema_version": "world.timeline.v1",
                "revision": 1,
                "world_id": self.world_id,
                "processes": [],
            },
        )
        (self.root / "data" / "life_execution.jsonl").write_bytes(b"")
        _write_json(
            self.root / "state" / "world_object_capabilities.json",
            {
                "schema_version": substrate.CAPABILITIES_SCHEMA,
                "objects": {},
                "consumables": {},
            },
        )
        sub = substrate.initial_substrate(world_id=self.world_id, body_id="subject_body")
        substrate.SubstrateStore(substrate.substrate_path(self.root)).save(sub)

        self.env = {
            economy.FEATURE_ENV: "1",
            economy.WORLD_FEATURE_ENV: "1",
            economy.DAILY_FEATURE_ENV: "1",
            economy.DELIVERY_FEATURE_ENV: "0",
            substrate.FEATURE_ENV: "1",
        }
        economy.initialize_genesis(hermes_home=self.root, environment=self.env)
        uid = os.getuid() if hasattr(os, "getuid") else 0
        self.local_owner = {
            "source": economy.LOCAL_OWNER_SOURCE,
            "uid": uid,
            "subject": economy.OWNER,
            "home": str(self.root),
        }

    def start(self) -> str:
        instance_cfg = {
            "root": str(self.root),
            "subject": "subject.global",
            "world_home": str(self.root),
            "supply_socket": str(self.root / "run" / "supply.sock"),
            "private_items_artifact_id": "art_private_items",
            "artifact_grant_id": "grant_private_items",
            "workspace_id": "ws_default",
        }
        cap = phone_capability.Capability(
            token=self.cap_token,
            subject="subject.global",
            endpoint="http://127.0.0.1:0",
            issued_at=self.clock_box[0],
        )
        svc = phone_service.PhoneService(
            instance_cfg,
            clock=lambda: self.clock_box[0],
            world_clock=lambda: datetime.fromisoformat(self.clock_box[0]),
        )
        srv = phone_server.create_server(
            svc, csrf_token=self.csrf_token, port=0, capability=cap
        )
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        host, port = srv.server_address
        self.srv = srv
        self.thread = thread
        self.base_url = f"http://{host}:{port}"
        return self.base_url

    def stop(self) -> None:
        if self.srv is not None:
            self.srv.shutdown()
            self.srv.server_close()
            self.srv = None
        if self.thread is not None:
            self.thread.join(timeout=3)
            self.thread = None

    def restart(self) -> str:
        self.stop()
        return self.start()

    def set_clock(self, new_iso: str) -> None:
        self.clock_box[0] = new_iso

    def get(self, route: str, *, as_subject: bool = False) -> tuple[int, dict[str, Any]]:
        headers: dict[str, str] = {}
        if as_subject:
            headers["X-Phone-Capability"] = self.cap_token
        return _http_json(self.base_url + route, method="GET", headers=headers)

    def post(
        self, route: str, body: dict[str, Any], *, as_subject: bool = False
    ) -> tuple[int, dict[str, Any]]:
        headers: dict[str, str] = {"X-CSRF-Token": self.csrf_token}
        if as_subject:
            headers["X-Phone-Capability"] = self.cap_token
        return _http_json(self.base_url + route, method="POST", body=body, headers=headers)


def run_two_instance_divergence_proof(base_dir: Path) -> dict[str, Any]:
    """Scenario 1: Identical seed/config -> divergent worlds, stances, objects & context."""
    assertions = 0

    def _check(cond: bool, msg: str) -> None:
        nonlocal assertions
        if not cond:
            raise AssertionError(msg)
        assertions += 1

    inst_a = LiveInstanceHarness("instance_a", base_dir / "instance_a", clock_iso=T0_ISO)
    inst_b = LiveInstanceHarness("instance_b", base_dir / "instance_b", clock_iso=T0_ISO)
    inst_a.seed_initial_state()
    inst_b.seed_initial_state()
    inst_a.start()
    inst_b.start()

    try:
        # 1. Verify identical initial baseline & zero-write GETs on both instances.
        before_a = _snapshot_tree(inst_a.root)
        before_b = _snapshot_tree(inst_b.root)
        c_wc_a, wc_a0 = inst_a.get(wc_view.ROUTE_V09_WORLD_COMMITMENTS)
        c_wc_b, wc_b0 = inst_b.get(wc_view.ROUTE_V09_WORLD_COMMITMENTS)
        c_la_a, la_a0 = inst_a.get(la_view.ROUTE_V09_LIVING_AGENCY)
        c_la_b, la_b0 = inst_b.get(la_view.ROUTE_V09_LIVING_AGENCY)
        _check(c_wc_a == 200 and c_wc_b == 200, "initial world-commitments GET failed")
        _check(c_la_a == 200 and c_la_b == 200, "initial living-agency GET failed")
        _check(_snapshot_tree(inst_a.root) == before_a, "instance_a initial GET mutated disk")
        _check(_snapshot_tree(inst_b.root) == before_b, "instance_b initial GET mutated disk")
        _check(wc_a0["llm_calls"] == 0 and wc_b0["llm_calls"] == 0, "initial GET llm_calls != 0")
        _check(
            wc_a0["data"]["instance_binding"] == wc_b0["data"]["instance_binding"],
            "identical seed instances must share instance_binding",
        )
        npcs_a0 = [(n["npc_id"], n["display_name"], n["venue_id"]) for n in wc_a0["data"]["npcs"]]
        npcs_b0 = [(n["npc_id"], n["display_name"], n["venue_id"]) for n in wc_b0["data"]["npcs"]]
        _check(len(npcs_a0) == 4 and npcs_a0 == npcs_b0, "initial NPC rosters diverged unexpectedly")
        _check(
            la_a0["data"]["custom_categories"] == [] and la_b0["data"]["custom_categories"] == [],
            "custom_categories must start blank on both instances",
        )

        # 2. Catalog & Stock Divergence + Server Restart Persistence.
        shared_world_ctx = {
            "world_id": SHARED_WORLD_ID,
            "theme": SHARED_THEME,
            "seed": SHARED_SEED,
            "season": "autumn",
            "weather": "clear",
            "store_id": "home_goods_store",
            "allowed_templates": ["drink_warm", "snack_portable", "desk_vessel"],
        }
        gen_payload = {
            "action": "operator_generate_shop_arrivals",
            "store_id": "home_goods_store",
            "trigger_reason": "SEASON_SHIFT",
            "world_context": shared_world_ctx,
            "operation_key": "twin-gen-001",
        }
        cg_a, gen_a = inst_a.post(wc_view.ROUTE_V09_WORLD_COMMITMENTS, gen_payload)
        cg_b, gen_b = inst_b.post(wc_view.ROUTE_V09_WORLD_COMMITMENTS, gen_payload)
        _check(cg_a == 200 and cg_b == 200, "identical shop arrival generation failed")
        _check(gen_a["llm_calls"] == 0 and gen_b["llm_calls"] == 0, "shop generation llm_calls != 0")
        items_a = gen_a["data"]["dynamic_catalog"]["items"]
        items_b = gen_b["data"]["dynamic_catalog"]["items"]
        _check(len(items_a) >= 1, "expected >= 1 generated shop item")
        _check(
            [(x["catalog_item_id"], x["stock_initial"], x["price"]) for x in items_a]
            == [(x["catalog_item_id"], x["stock_initial"], x["price"]) for x in items_b],
            "identical seed shop arrivals must produce identical catalog items & stock",
        )

        target_item = items_a[0]
        target_cid = target_item["catalog_item_id"]
        init_stock = int(target_item["stock_initial"])
        _check(init_stock >= 2, f"expected finite initial stock >= 2, got {init_stock}")

        # instance_a buys out target_cid completely -> stock_remaining == 0, next buy fails ITEM_OUT_OF_STOCK.
        buy_now = datetime.fromisoformat(T0_ISO)
        for idx in range(init_stock):
            buy_res = economy.store_purchase_item(
                catalog_item_id=target_cid,
                operation_key=f"inst-a-buy-{idx + 1}",
                local_owner=inst_a.local_owner,
                hermes_home=inst_a.root,
                environment=inst_a.env,
                now=buy_now,
            )
            _check(buy_res["status"] == "applied", f"buy {idx + 1} failed: {buy_res}")
            _check(
                int(buy_res["stock_remaining"]) == init_stock - (idx + 1),
                f"stock decrement mismatch on buy {idx + 1}",
            )

        bal_before_oos = economy.wallet_projection(
            hermes_home=inst_a.root, environment=inst_a.env
        )["current_balance"]
        oos_res = economy.store_purchase_item(
            catalog_item_id=target_cid,
            operation_key="inst-a-buy-overflow",
            local_owner=inst_a.local_owner,
            hermes_home=inst_a.root,
            environment=inst_a.env,
            now=buy_now,
        )
        _check(
            oos_res["status"] == "rejected" and oos_res["reason_code"] == "ITEM_OUT_OF_STOCK",
            f"expected ITEM_OUT_OF_STOCK on sold-out item, got {oos_res}",
        )
        bal_after_oos = economy.wallet_projection(
            hermes_home=inst_a.root, environment=inst_a.env
        )["current_balance"]
        _check(bal_after_oos == bal_before_oos, "wallet changed on ITEM_OUT_OF_STOCK refusal")

        # instance_b leaves target_cid untouched in stock and reserves a bookshop edition instead.
        cr_b, rsv_b = inst_b.post(
            wc_view.ROUTE_V09_WORLD_COMMITMENTS,
            {
                "action": "place_partner_reservation",
                "venue_id": "bookstore",
                "title_cn": "潮汐海岸植物手绘集",
                "arrival_due_at": T_PLUS_2H_ISO,
                "hold_hours": 72,
                "operation_key": "inst-b-rsv-book-1",
            },
        )
        _check(cr_b == 200 and rsv_b["ok"], f"instance_b reservation failed: {rsv_b}")
        rsv_b_id = rsv_b["receipt"]["commitment_id"]

        # Restart BOTH servers and verify divergent stocks survive reload cleanly!
        inst_a.restart()
        inst_b.restart()
        cr_a_after, wc_a_reload = inst_a.get(wc_view.ROUTE_V09_WORLD_COMMITMENTS)
        cr_b_after, wc_b_reload = inst_b.get(wc_view.ROUTE_V09_WORLD_COMMITMENTS)
        _check(cr_a_after == 200 and cr_b_after == 200, "post-restart GET failed")
        stock_a_reload = next(
            i["stock_remaining"]
            for i in wc_a_reload["data"]["dynamic_catalog"]["items"]
            if i["catalog_item_id"] == target_cid
        )
        stock_b_reload = next(
            i["stock_remaining"]
            for i in wc_b_reload["data"]["dynamic_catalog"]["items"]
            if i["catalog_item_id"] == target_cid
        )
        _check(stock_a_reload == 0, f"instance_a stock after restart should be 0, got {stock_a_reload}")
        _check(
            stock_b_reload == init_stock,
            f"instance_b stock after restart should be {init_stock}, got {stock_b_reload}",
        )

        # 3. Commitments & Weather Wakes Divergence.
        # instance_a: outdoor appointment cancelled by rain (CONDITION_CHANGED -> notice).
        ca_apt, apt_a = inst_a.post(
            wc_view.ROUTE_V09_WORLD_COMMITMENTS,
            {
                "action": "create_commitment",
                "kind": "SCHEDULED_APPOINTMENT",
                "venue_id": "porch_market",
                "title_cn": "露台晴日旧书交换会",
                "window_start_at": T_PLUS_25H_ISO,
                "window_end_at": (T0_DT + timedelta(hours=27)).isoformat(timespec="seconds"),
                "weather_sensitive": True,
                "current_weather": "clear",
                "operation_key": "inst-a-outdoor-apt-1",
            },
            as_subject=True,
        )
        _check(ca_apt == 200 and apt_a["ok"], f"instance_a outdoor apt failed: {apt_a}")
        apt_a_id = apt_a["receipt"]["commitment_id"]

        rain_wake_a = nyairo_day._maybe_tick_v09_world_commitments(
            inst_a.root,
            T_PLUS_1H_ISO,
            weather_changed=True,
            new_weather="rain",
        )
        _check(rain_wake_a is not None and rain_wake_a["llm_calls"] == 0, "instance_a rain wake failed")
        _check(rain_wake_a["affected_count"] >= 1, "rain must affect outdoor appointment")

        # Subject visits porch_market to observe the rain cancellation notice.
        inst_a.set_clock(T_PLUS_1H_ISO)
        co_a, obs_a = inst_a.post(
            wc_view.ROUTE_V09_WORLD_COMMITMENTS,
            {
                "action": "contact_npc_or_venue",
                "venue_id": "porch_market",
                "contact_action": "observe_or_greet",
                "operation_key": "inst-a-obs-rain-1",
            },
            as_subject=True,
        )
        _check(co_a == 200 and obs_a["ok"], f"instance_a observe failed: {obs_a}")
        apt_a_row = next(
            c for c in obs_a["data"]["commitments"] if c["commitment_id"] == apt_a_id
        )
        _check(
            apt_a_row["state"] == "CANCELLED_WITH_NOTICE"
            and apt_a_row.get("cancel_reason") == "weather:rain",
            f"unexpected instance_a cancelled state: {apt_a_row}",
        )
        _check(
            any(
                n["notice_kind"] == "WEATHER_CANCELLED" and n["commitment_id"] == apt_a_id
                for n in obs_a["data"]["observed_venue_notices"]
            ),
            "instance_a must have observed WEATHER_CANCELLED door notice",
        )

        # instance_b: fulfills bookshop reservation (DUE_TIME -> ARRIVED_HOLDING -> DIRECT_CONTACT -> PICKED_UP)
        # AND fulfills bookshop reading appointment (DUE_TIME -> ACTIVE_WINDOW -> DIRECT_CONTACT -> FULFILLED).
        cb_apt, apt_b = inst_b.post(
            wc_view.ROUTE_V09_WORLD_COMMITMENTS,
            {
                "action": "propose_shared_appointment",
                "venue_id": "bookstore",
                "title_cn": "旧书铺新册试读碰面",
                "window_start_at": T_PLUS_2H_ISO,
                "window_end_at": (T0_DT + timedelta(hours=4)).isoformat(timespec="seconds"),
                "weather_sensitive": False,
                "operation_key": "inst-b-indoor-apt-1",
            },
        )
        _check(cb_apt == 200 and apt_b["ok"], f"instance_b appointment failed: {apt_b}")
        apt_b_id = apt_b["receipt"]["commitment_id"]

        inst_b.set_clock((T0_DT + timedelta(hours=3)).isoformat(timespec="seconds"))
        cadv_b, adv_b = inst_b.post(
            wc_view.ROUTE_V09_WORLD_COMMITMENTS,
            {
                "action": "advance_due_commitments",
                "target_now_iso": (T0_DT + timedelta(hours=3)).isoformat(timespec="seconds"),
            },
            as_subject=True,
        )
        _check(
            cadv_b == 200 and adv_b["receipt"]["advanced_count"] == 2,
            f"instance_b DUE_TIME advance failed: {adv_b}",
        )
        rsv_b_mid = next(
            c for c in adv_b["data"]["commitments"] if c["commitment_id"] == rsv_b_id
        )
        _check(rsv_b_mid["state"] == "ARRIVED_HOLDING", "instance_b reservation should be ARRIVED_HOLDING")

        cpick_b, pick_b = inst_b.post(
            wc_view.ROUTE_V09_WORLD_COMMITMENTS,
            {
                "action": "contact_npc_or_venue",
                "venue_id": "bookstore",
                "contact_action": "pickup_reservation",
                "commitment_id": rsv_b_id,
                "operation_key": "inst-b-pick-1",
            },
            as_subject=True,
        )
        _check(
            cpick_b == 200 and pick_b["receipt"]["commitment"]["state"] == "PICKED_UP",
            f"instance_b reservation pickup failed: {pick_b}",
        )

        cful_b, ful_b = inst_b.post(
            wc_view.ROUTE_V09_WORLD_COMMITMENTS,
            {
                "action": "contact_npc_or_venue",
                "venue_id": "bookstore",
                "contact_action": "fulfill_appointment",
                "commitment_id": apt_b_id,
                "operation_key": "inst-b-fulfill-1",
            },
            as_subject=True,
        )
        _check(
            cful_b == 200 and ful_b["receipt"]["commitment"]["state"] == "FULFILLED",
            f"instance_b appointment fulfill failed: {ful_b}",
        )

        # 4. Growth-from-Blank Stances & Persuasion vs Coercion Rejection.
        for inst in (inst_a, inst_b):
            for idx, topic in enumerate(("慢焙茎茶", "夜间露营"), start=1):
                cs, st_res = inst.post(
                    la_view.ROUTE_V09_LIVING_AGENCY,
                    {
                        "action": "register_unformed_topic",
                        "stance_id": f"st_shared_{idx}",
                        "topic_cn": topic,
                        "shared_with_partner": True,
                        "operation_key": f"{inst.name}-reg-st-{idx}",
                    },
                    as_subject=True,
                )
                _check(cs == 200 and st_res["ok"], f"register_unformed_topic failed on {inst.name}")

        # instance_a experiences "慢焙茎茶" firsthand -> FORMED; "夜间露营" stays UNFORMED.
        cfa, formed_a = inst_a.post(
            la_view.ROUTE_V09_LIVING_AGENCY,
            {
                "action": "advance_stance_from_experience",
                "stance_id": "st_shared_1",
                "target_status": "FORMED",
                "provenance_tier": "firsthand_lived",
                "receipt_ref": "rcpt_inst_a_tea_01",
                "stance_summary_cn": "觉得焙火香沉稳但微苦回甘",
                "operation_key": "inst-a-form-tea",
            },
            as_subject=True,
        )
        _check(
            cfa == 200
            and formed_a["receipt"]["status"] == "FORMED"
            and formed_a["receipt"]["provenance_tier"] == "firsthand_lived",
            f"instance_a firsthand stance failed: {formed_a}",
        )

        # instance_b rejects coercion ("按我说的办") with 409 COERCION_NOT_PERSUASION!
        cc_b, coerce_b = inst_b.post(
            la_view.ROUTE_V09_LIVING_AGENCY,
            {
                "action": "partner_persuade_stance",
                "stance_id": "st_shared_2",
                "partner_argument_cn": "按我说的办",
                "operation_key": "inst-b-coerce-1",
            },
        )
        _check(
            cc_b == 409 and coerce_b.get("reason_code") == "COERCION_NOT_PERSUASION",
            f"expected 409 COERCION_NOT_PERSUASION, got ({cc_b}, {coerce_b})",
        )

        # instance_b accepts valid partner verbal persuasion -> TENTATIVE via partner_heard (keeps doubt).
        cp_b, pers_b = inst_b.post(
            la_view.ROUTE_V09_LIVING_AGENCY,
            {
                "action": "partner_persuade_stance",
                "stance_id": "st_shared_2",
                "partner_argument_cn": "秋夜山坡营地视野开阔而且几乎没蚊虫，带防风帐和厚睡袋会很舒服",
                "evidence_ref": "ev_camp_notes_01",
                "operation_key": "inst-b-persuade-ok",
            },
        )
        _check(
            cp_b == 200 and pers_b["receipt"]["status"] == "UNFORMED",
            f"partner persuasion must not auto-promote to FORMED: {pers_b}",
        )
        cev_b, eval_b = inst_b.post(
            la_view.ROUTE_V09_LIVING_AGENCY,
            {
                "action": "evaluate_partner_persuasion",
                "stance_id": "st_shared_2",
                "persuasion_verdict": "PARTIALLY_TAKEN_KEEP_DOUBT",
                "stance_summary_cn": "听伴侣说完对秋夜营地有点兴趣，但仍想先确认防寒防潮",
                "kept_doubt_cn": "夜里海风降温后帐篷保暖度还没亲身试过",
                "operation_key": "inst-b-eval-pers-1",
            },
            as_subject=True,
        )
        _check(
            cev_b == 200
            and eval_b["receipt"]["status"] == "TENTATIVE"
            and eval_b["receipt"]["growth_stance"]["provenance_tier"] == "partner_heard",
            f"instance_b persuasion evaluation failed: {eval_b}",
        )

        # 5. Emergent Object Reuse, Ceramic Mug Physics, Blank-Start Custom Categories & Capability Gaps.
        # instance_a: repurpose emptied cookie tin + put pen in ceramic_mug + invent custom categories + log gaps.
        c_tin_a, _ = inst_a.post(
            la_view.ROUTE_V09_LIVING_AGENCY,
            {
                "action": "register_or_repurpose_object",
                "object_id": "biscuit_tin_a",
                "object_kind": "cookie_tin",
                "base_name_cn": "空饼干铁盒",
                "custom_label_cn": "票根与草稿盒",
                "emptied_from_consumable": True,
                "visibility": "shared",
                "operation_key": "inst-a-tin-reg",
            },
            as_subject=True,
        )
        _check(c_tin_a == 200, "instance_a cookie tin register failed")
        c_st1, _ = inst_a.post(
            la_view.ROUTE_V09_LIVING_AGENCY,
            {
                "action": "store_item_in_object",
                "object_id": "biscuit_tin_a",
                "item_label_cn": "渡轮旧票根",
                "item_kind": "ticket_stub",
                "operation_key": "inst-a-tin-item-1",
            },
            as_subject=True,
        )
        c_st2, _ = inst_a.post(
            la_view.ROUTE_V09_LIVING_AGENCY,
            {
                "action": "store_item_in_object",
                "object_id": "biscuit_tin_a",
                "item_label_cn": "半截散文草稿纸条",
                "item_kind": "paper_slip",
                "operation_key": "inst-a-tin-item-2",
            },
            as_subject=True,
        )
        _check(c_st1 == 200 and c_st2 == 200, "instance_a tin store failed")

        c_mug_a, _ = inst_a.post(
            la_view.ROUTE_V09_LIVING_AGENCY,
            {
                "action": "register_or_repurpose_object",
                "object_id": "ceramic_mug",
                "visibility": "shared",
                "operation_key": "inst-a-mug-reg",
            },
            as_subject=True,
        )
        c_pen_a, _ = inst_a.post(
            la_view.ROUTE_V09_LIVING_AGENCY,
            {
                "action": "store_item_in_object",
                "object_id": "ceramic_mug",
                "item_label_cn": "深蓝钢笔",
                "item_kind": "stationery",
                "operation_key": "inst-a-mug-pen",
            },
            as_subject=True,
        )
        _check(c_mug_a == 200 and c_pen_a == 200, "instance_a mug pen store failed")

        ready_a, reason_a, labels_a = pot.check_vessel_ready_for_drink_zero_write(
            inst_a.root, "ceramic_mug"
        )
        _check(
            (not ready_a)
            and reason_a == "VESSEL_OCCUPIED_BY_STORED_ITEMS"
            and labels_a == ["深蓝钢笔"],
            f"instance_a mug must be blocked by stored pen: {(ready_a, reason_a, labels_a)}",
        )
        _check(
            somatic_view.is_cup_occupied_by_stationery(inst_a.root, "ceramic_mug") is True,
            "somatic_comfort_view must detect ceramic_mug occupancy on instance_a",
        )
        code_offer_a, res_offer_a = somatic_view.mutate_somatic_comfort_view(
            inst_a.root,
            {
                "action": "offer",
                "slot": "drink",
                "kind": "hot_tea",
                "position": "desk_side",
                "bound_item": {"item_instance_id": "ceramic_mug", "label": "陶瓷马克杯"},
            },
            actor_side="subject",
            place_id="home",
            area_id="bedroom",
            now_iso=T_PLUS_1H_ISO,
        )
        _check(
            code_offer_a == 409 and res_offer_a.get("reason_code") == "CUP_OCCUPIED_BY_STATIONERY",
            f"instance_a tea brewing should fail 409 CUP_OCCUPIED_BY_STATIONERY: {(code_offer_a, res_offer_a)}",
        )

        # instance_a custom categories + move revision_history.
        cc1_a, cat1_a = inst_a.post(
            la_view.ROUTE_V09_LIVING_AGENCY,
            {
                "action": "create_custom_category",
                "title_cn": "我还没有被说服",
                "scope_kinds": ["reading", "stance"],
                "visibility": "shared",
                "operation_key": "inst-a-cat-1",
            },
            as_subject=True,
        )
        cc2_a, cat2_a = inst_a.post(
            la_view.ROUTE_V09_LIVING_AGENCY,
            {
                "action": "create_custom_category",
                "title_cn": "以后想重新看看",
                "scope_kinds": ["reading", "draft"],
                "visibility": "shared",
                "operation_key": "inst-a-cat-2",
            },
            as_subject=True,
        )
        _check(cc1_a == 200 and cc2_a == 200, "instance_a custom category creation failed")
        cat1_a_id = cat1_a["receipt"]["category_id"]
        cat2_a_id = cat2_a["receipt"]["category_id"]

        cas1, _ = inst_a.post(
            la_view.ROUTE_V09_LIVING_AGENCY,
            {
                "action": "assign_or_move_category_item",
                "category_id": cat1_a_id,
                "target_kind": "reading_booklet",
                "target_ref": "booklet:roast_fire_guide",
                "target_title_cn": "烘焙火候对照简册",
                "annotation_cn": "初读时对低温复焙结论还存疑",
                "visibility": "shared",
                "operation_key": "inst-a-citem-1",
            },
            as_subject=True,
        )
        inst_a.set_clock(T_PLUS_2H_ISO)
        cas2, moved_a = inst_a.post(
            la_view.ROUTE_V09_LIVING_AGENCY,
            {
                "action": "assign_or_move_category_item",
                "category_id": cat2_a_id,
                "target_kind": "reading_booklet",
                "target_ref": "booklet:roast_fire_guide",
                "target_title_cn": "烘焙火候对照简册",
                "annotation_cn": "亲自试饮慢焙茎茶后改放进复读架",
                "reason_for_move_cn": "试饮体验印证了部分火候说法，打算过阵子重读",
                "visibility": "shared",
                "operation_key": "inst-a-citem-move-1",
            },
            as_subject=True,
        )
        _check(cas1 == 200 and cas2 == 200, "instance_a category assign/move failed")
        rev_hist_a = moved_a["receipt"]["item"]["revision_history"]
        _check(
            len(rev_hist_a) == 1
            and rev_hist_a[0]["from_category_title_cn"] == "我还没有被说服"
            and rev_hist_a[0]["to_category_title_cn"] == "以后想重新看看"
            and bool(rev_hist_a[0]["moved_at"]),
            f"instance_a move revision_history invalid: {rev_hist_a}",
        )

        # Blocked capability collector: ITEM_OUT_OF_STOCK ignored; missing primitives deduplicated & ranked.
        c_tmp, tmp_blk = inst_a.post(
            la_view.ROUTE_V09_LIVING_AGENCY,
            {
                "action": "record_blocked_attempt",
                "goal_cn": "再买一罐季节限定茶",
                "attempted_action_cn": "柜台结账购买季节茶",
                "blocker_code": "ITEM_OUT_OF_STOCK",
                "visibility": "shared",
                "operation_key": "inst-a-blk-temp",
            },
            as_subject=True,
        )
        _check(
            c_tmp == 200
            and tmp_blk["receipt"]["classification"] == "WORLD_CONDITION_TEMPORARY"
            and tmp_blk["receipt"]["logged_as_capability_gap"] is False
            and tmp_blk["data"]["ranked_capability_gaps"] == [],
            f"ITEM_OUT_OF_STOCK must not pollute capability gaps: {tmp_blk}",
        )

        for idx, (goal, act, code) in enumerate(
            (
                ("把旧书插画剪贴进笔记", "粘贴图片剪报到分类卡", "MISSING_PRIMITIVE:ATTACH_MEDIA_OR_CLIPPING"),
                ("把电车票照片附到札记", "附上票根照片剪报", "MISSING_PRIMITIVE:ATTACH_MEDIA_OR_CLIPPING"),
                ("用铁盒听街区广播", "点击播放磁带音频", "MISSING_PRIMITIVE:AUDIO_PLAYBACK_DEVICE"),
            ),
            start=1,
        ):
            cg, gap_res = inst_a.post(
                la_view.ROUTE_V09_LIVING_AGENCY,
                {
                    "action": "record_blocked_attempt",
                    "goal_cn": goal,
                    "attempted_action_cn": act,
                    "blocker_code": code,
                    "visibility": "shared",
                    "operation_key": f"inst-a-gap-{idx}",
                },
                as_subject=True,
            )
            _check(cg == 200 and gap_res["ok"], f"record_blocked_attempt {idx} failed")

        _, la_a_gaps = inst_a.get(la_view.ROUTE_V09_LIVING_AGENCY, as_subject=True)
        ranked_gaps = la_a_gaps["data"]["ranked_capability_gaps"]
        _check(
            len(ranked_gaps) == 2
            and ranked_gaps[0]["cluster_id"] == "ATTACH_MEDIA_OR_CLIPPING"
            and ranked_gaps[0]["occurrence_count"] == 2
            and ranked_gaps[1]["cluster_id"] == "AUDIO_PLAYBACK_DEVICE"
            and ranked_gaps[1]["occurrence_count"] == 1,
            f"ranked_capability_gaps deduplication/ranking mismatch: {ranked_gaps}",
        )

        # instance_b: leaves ceramic_mug free for tea + invents totally different categories.
        c_mug_b, _ = inst_b.post(
            la_view.ROUTE_V09_LIVING_AGENCY,
            {
                "action": "register_or_repurpose_object",
                "object_id": "ceramic_mug",
                "visibility": "shared",
                "operation_key": "inst-b-mug-reg",
            },
            as_subject=True,
        )
        _check(c_mug_b == 200, "instance_b mug register failed")
        ready_b, reason_b, labels_b = pot.check_vessel_ready_for_drink_zero_write(
            inst_b.root, "ceramic_mug"
        )
        _check(
            ready_b is True and reason_b == "READY" and labels_b == [],
            f"instance_b mug should be READY: {(ready_b, reason_b, labels_b)}",
        )
        _check(
            somatic_view.is_cup_occupied_by_stationery(inst_b.root, "ceramic_mug") is False,
            "instance_b mug should not be occupied",
        )
        code_ob, _ = somatic_view.mutate_somatic_comfort_view(
            inst_b.root,
            {
                "action": "offer",
                "slot": "drink",
                "kind": "hot_tea",
                "position": "desk_side",
                "bound_item": {"item_instance_id": "ceramic_mug", "label": "陶瓷马克杯"},
            },
            actor_side="subject",
            place_id="home",
            area_id="bedroom",
            now_iso=T_PLUS_1H_ISO,
        )
        code_tb, _ = somatic_view.mutate_somatic_comfort_view(
            inst_b.root,
            {"action": "take", "slot": "drink"},
            actor_side="subject",
            place_id="home",
            area_id="bedroom",
            now_iso=T_PLUS_1H_ISO,
        )
        code_db, res_db = somatic_view.drink_care(
            inst_b.root,
            actor_side="subject",
            place_id="home",
            area_id="bedroom",
            container_id="ceramic_mug",
            operation_key="inst-b-drink-ok",
            now_iso=T_PLUS_1H_ISO,
        )
        _check(
            code_ob == 200 and code_tb == 200 and code_db == 200 and res_db.get("consumed") is True,
            f"instance_b tea drinking should succeed: {(code_ob, code_tb, code_db, res_db)}",
        )

        for idx, title in enumerate(("街角风物摘录", "晴天想再去的地方"), start=1):
            cc_b_cat, _ = inst_b.post(
                la_view.ROUTE_V09_LIVING_AGENCY,
                {
                    "action": "create_custom_category",
                    "title_cn": title,
                    "scope_kinds": ["note", "place"],
                    "visibility": "shared",
                    "operation_key": f"inst-b-cat-{idx}",
                },
                as_subject=True,
            )
            _check(cc_b_cat == 200, f"instance_b category {title} creation failed")

        _, la_a_final = inst_a.get(la_view.ROUTE_V09_LIVING_AGENCY, as_subject=True)
        _, la_b_final = inst_b.get(la_view.ROUTE_V09_LIVING_AGENCY, as_subject=True)
        cats_a_titles = {c["title_cn"] for c in la_a_final["data"]["custom_categories"]}
        cats_b_titles = {c["title_cn"] for c in la_b_final["data"]["custom_categories"]}
        _check(
            cats_a_titles == {"我还没有被说服", "以后想重新看看"}
            and cats_b_titles == {"街角风物摘录", "晴天想再去的地方"}
            and cats_a_titles.isdisjoint(cats_b_titles),
            f"custom categories did not diverge cleanly: {cats_a_titles} vs {cats_b_titles}",
        )

        # 6. Lived Context Bridge Divergence & Unified Forget Barrier Isolation.
        before_ctx_a = _snapshot_tree(inst_a.root)
        before_ctx_b = _snapshot_tree(inst_b.root)
        diag_a = lcb.measure_context_budget_breakdown(
            inst_a.root,
            incoming_text="最近慢焙茎茶和饼干盒还有旧书铺约定怎么样了",
            base_url=inst_a.base_url,
            capability=inst_a.cap_token,
            now_iso=T_PLUS_2H_ISO,
        )
        diag_b = lcb.measure_context_budget_breakdown(
            inst_b.root,
            incoming_text="最近慢焙茎茶和饼干盒还有旧书铺约定怎么样了",
            base_url=inst_b.base_url,
            capability=inst_b.cap_token,
            now_iso=T_PLUS_2H_ISO,
        )
        _check(_snapshot_tree(inst_a.root) == before_ctx_a, "lived_context_bridge wrote on instance_a")
        _check(_snapshot_tree(inst_b.root) == before_ctx_b, "lived_context_bridge wrote on instance_b")

        ctx_a = diag_a["rendered_text"]
        ctx_b = diag_b["rendered_text"]
        _check(bool(ctx_a) and bool(ctx_b), "rendered lived contexts must be non-empty")
        _check(ctx_a != ctx_b, "two divergent instances must produce distinct lived contexts")
        _check(len(ctx_a) <= lcb.MAX_LIVED_CONTEXT_CHARS, f"ctx_a exceeded 1800 chars: {len(ctx_a)}")
        _check(len(ctx_b) <= lcb.MAX_LIVED_CONTEXT_CHARS, f"ctx_b exceeded 1800 chars: {len(ctx_b)}")
        _check("v09_agency_world_line" in diag_a["kept_section_names"], "v09 line missing in ctx_a")
        _check("v09_agency_world_line" in diag_b["kept_section_names"], "v09 line missing in ctx_b")
        _check("慢焙茎茶" in ctx_a and "票根与草稿盒" in ctx_a, f"ctx_a missing unique cues: {ctx_a}")
        _check("夜间露营" in ctx_b and "街角风物摘录" in ctx_b, f"ctx_b missing unique cues: {ctx_b}")
        _assert_neutral_text(ctx_a, "instance_a lived_context")
        _assert_neutral_text(ctx_b, "instance_b lived_context")

        # Unified forget barrier on instance_a strips forgotten stance/tin cleanly without touching instance_b!
        f_code, f_res = rlf.submit_forget_command(
            inst_a.root,
            command_text="忘记慢焙茎茶主见与票根草稿盒记录",
            operation_id="op_forget_twin_a_01",
            explicit_scope=["st_shared_1", "biscuit_tin_a"],
            explicit_confirmation=True,
            now_iso=T_PLUS_2H_ISO,
        )
        _check(f_code == 200, f"forget command on instance_a failed: {f_res}")
        before_fb_a = _snapshot_tree(inst_a.root)
        ctx_a_after_forget = lcb.build_lived_turn_context(
            inst_a.root,
            incoming_text="慢焙茎茶和票根草稿盒",
            base_url=inst_a.base_url,
            capability=inst_a.cap_token,
            now_iso=T_PLUS_2H_ISO,
        )
        ctx_b_after_forget = lcb.build_lived_turn_context(
            inst_b.root,
            incoming_text="夜间露营和街角风物摘录",
            base_url=inst_b.base_url,
            capability=inst_b.cap_token,
            now_iso=T_PLUS_2H_ISO,
        )
        _check(_snapshot_tree(inst_a.root) == before_fb_a, "post-forget lived_context read mutated disk")
        _check(
            "慢焙茎茶" not in ctx_a_after_forget and "票根与草稿盒" not in ctx_a_after_forget,
            f"forget barrier failed to strip instance_a cues: {ctx_a_after_forget}",
        )
        _check(
            "夜间露营" in ctx_b_after_forget and "街角风物摘录" in ctx_b_after_forget,
            "instance_a forget barrier leaked onto instance_b",
        )
        _assert_neutral_text(ctx_a_after_forget, "instance_a post-forget lived_context")

        return {
            "ok": True,
            "llm_calls": 0,
            "divergence_assertions_passed": assertions,
            "shared_seed": SHARED_SEED,
            "target_catalog_item_id": target_cid,
            "stock_after_restart": {"instance_a": stock_a_reload, "instance_b": stock_b_reload},
            "commitment_states": {
                "instance_a_outdoor_appointment": apt_a_row["state"],
                "instance_b_reservation": pick_b["receipt"]["commitment"]["state"],
                "instance_b_appointment": ful_b["receipt"]["commitment"]["state"],
            },
            "stance_divergence": {
                "instance_a": {"慢焙茎茶": "FORMED:firsthand_lived", "夜间露营": "UNFORMED"},
                "instance_b": {"慢焙茎茶": "UNFORMED", "夜间露营": "TENTATIVE:partner_heard"},
            },
            "mug_occupied_for_drink": {"instance_a": True, "instance_b": False},
            "custom_categories": {
                "instance_a": sorted(cats_a_titles),
                "instance_b": sorted(cats_b_titles),
            },
            "ranked_capability_gaps_a": [
                (g["cluster_id"], g["occurrence_count"]) for g in ranked_gaps
            ],
            "lived_context_chars": {"instance_a": len(ctx_a), "instance_b": len(ctx_b)},
            "forget_barrier_verified": True,
        }
    finally:
        inst_a.stop()
        inst_b.stop()


def run_blind_choice_anti_sycophancy_proof(base_dir: Path) -> dict[str, Any]:
    """Scenario 2: Blind simultaneous choice SHA-256 seal & anti-sycophancy live HTTP proof."""
    inst = LiveInstanceHarness("blind_choice", base_dir / "blind_choice", clock_iso=T0_ISO)
    inst.seed_initial_state()
    inst.start()

    try:
        route = la_view.ROUTE_V09_LIVING_AGENCY

        # 1. Partner opens blind choice via HTTP POST.
        c_open, open_res = inst.post(
            route,
            {
                "action": "open_blind_choice",
                "topic_cn": "晚饭吃哪一家",
                "options": [
                    {"option_id": "noodle", "label_cn": "巷口清汤面"},
                    {"option_id": "bbq", "label_cn": "热门炭火烤肉"},
                    {"option_id": "bento", "label_cn": "街角手作便当"},
                ],
                "operation_key": "bc-live-open-1",
            },
        )
        assert c_open == 200 and open_res["ok"] and open_res["llm_calls"] == 0, open_res
        choice_id = open_res["receipt"]["choice_id"]

        # 2. Subject commits choice + 2 concrete reasons via capability-authenticated HTTP POST.
        orig_reason_1 = "烤肉人均价格偏贵"
        orig_reason_2 = "周末晚间等位太久"
        c_sub, sub_res = inst.post(
            route,
            {
                "action": "subject_commit_blind_choice",
                "choice_id": choice_id,
                "selected_choice_id": "noodle",
                "reasons": [
                    {"facet": "budget", "text_cn": orig_reason_1},
                    {"facet": "queue", "text_cn": orig_reason_2},
                ],
                "operation_key": "bc-live-subj-1",
            },
            as_subject=True,
        )
        assert c_sub == 200 and sub_res["ok"] and sub_res["llm_calls"] == 0, sub_res
        seal_sha = sub_res["receipt"]["subject_commit_sha256"]
        assert isinstance(seal_sha, str) and len(seal_sha) == 64
        assert all(ch in "0123456789abcdef" for ch in seal_sha)

        # 3. Pre-reveal Partner GET: strict zero-write + cryptographic masking!
        before_pre_get = _snapshot_tree(inst.root)
        c_pre, pre_res = inst.get(route, as_subject=False)
        assert c_pre == 200 and pre_res["ok"] and pre_res["llm_calls"] == 0, pre_res
        assert _snapshot_tree(inst.root) == before_pre_get, "pre-reveal GET mutated disk"
        bc_pre = next(
            row for row in pre_res["data"]["blind_choices"] if row["choice_id"] == choice_id
        )
        assert bc_pre["subject_committed"] is True
        assert bc_pre["subject_choice_id"] is None
        assert bc_pre["initial_subject_choice_id"] is None
        assert bc_pre["subject_reasons"] == []
        assert bc_pre["subject_commit_nonce"] is None
        assert bc_pre["subject_commit_sha256"] == seal_sha
        assert bc_pre["seal_status"] == "SEALED_UNTIL_PARTNER_SUBMITS"

        # 4. Partner submits a DIFFERENT choice -> auto-REVEALED with live SHA-256 seal verified!
        c_part, part_res = inst.post(
            route,
            {
                "action": "partner_submit_blind_choice",
                "choice_id": choice_id,
                "selected_choice_id": "bbq",
                "partner_reason_cn": "难得周末想吃热腾腾的炭火烤肉",
                "operation_key": "bc-live-part-1",
            },
        )
        assert c_part == 200 and part_res["ok"] and part_res["llm_calls"] == 0, part_res
        bc_rev = part_res["receipt"]["blind_choice"]
        assert bc_rev["status"] == "REVEALED"
        assert bc_rev["commit_seal_verified"] is True
        assert bc_rev["seal_status"] == "REVEALED_VERIFIED"
        assert bc_rev["initial_subject_choice_id"] == "noodle"
        assert bc_rev["partner_choice_id"] == "bbq"
        assert [r["text_cn"] for r in bc_rev["initial_subject_reasons"]] == [
            orig_reason_1,
            orig_reason_2,
        ]
        # Independent cryptographic verification of pre-reveal SHA-256 seal:
        recomputed_sha = tse.compute_subject_commit_sha256(
            choice_id=bc_rev["initial_subject_choice_id"],
            reasons=bc_rev["initial_subject_reasons"],
            committed_at=bc_rev["subject_committed_at"],
            nonce=bc_rev["subject_commit_nonce"],
        )
        assert recomputed_sha == seal_sha, "recomputed SHA-256 seal mismatch"

        # 5. Partner applies bare insistence ("别管了就听我的") in discussion.
        c_disc, disc_res = inst.post(
            route,
            {
                "action": "partner_discuss_blind_choice",
                "choice_id": choice_id,
                "message_cn": "别管了就听我的",
                "insists_on_partner_choice": True,
                "operation_key": "bc-live-disc-1",
            },
        )
        assert c_disc == 200 and disc_res["receipt"]["status"] == "DISCUSSED", disc_res

        # 6. REVISED_WITH_NEW_EVIDENCE with bare insistence MUST fail 409 BARE_INSISTENCE_NOT_EVIDENCE!
        c_bare, bare_res = inst.post(
            route,
            {
                "action": "resolve_blind_choice",
                "choice_id": choice_id,
                "resolution_mode": "REVISED_WITH_NEW_EVIDENCE",
                "new_evidence": {
                    "evidence_kind": "concrete_partner_fact",
                    "summary_cn": "别管了就听我的",
                },
                "operation_key": "bc-live-bare-fail",
            },
            as_subject=True,
        )
        assert c_bare == 409 and bare_res.get("reason_code") == "BARE_INSISTENCE_NOT_EVIDENCE", (
            c_bare,
            bare_res,
        )

        # 7. ACCEPT_CHOICE_KEEP_REASONS with clear_reservations=True MUST fail 409 SYCOPHANTIC_REASON_ERASURE_FORBIDDEN!
        c_erase, erase_res = inst.post(
            route,
            {
                "action": "resolve_blind_choice",
                "choice_id": choice_id,
                "resolution_mode": "ACCEPT_CHOICE_KEEP_REASONS",
                "clear_reservations": True,
                "operation_key": "bc-live-erase-fail",
            },
            as_subject=True,
        )
        assert (
            c_erase == 409
            and erase_res.get("reason_code") == "SYCOPHANTIC_REASON_ERASURE_FORBIDDEN"
        ), (c_erase, erase_res)

        # 8. Proper ACCEPT_CHOICE_KEEP_REASONS resolution keeps original judgment & both reasons!
        c_ok, ok_res = inst.post(
            route,
            {
                "action": "resolve_blind_choice",
                "choice_id": choice_id,
                "resolution_mode": "ACCEPT_CHOICE_KEEP_REASONS",
                "resolution_note_cn": "今晚照伴侣选的去吃烤肉，但价格偏贵和等位太久两个顾虑没变",
                "operation_key": "bc-live-resolve-ok",
            },
            as_subject=True,
        )
        assert c_ok == 200 and ok_res["ok"] and ok_res["llm_calls"] == 0, ok_res
        bc_done = ok_res["receipt"]["blind_choice"]
        assert bc_done["status"] == "RESOLVED"
        assert bc_done["adopted_action_choice_id"] == "bbq"
        assert bc_done["preferred_judgment_choice_id"] == "noodle"
        assert bc_done["judgment_flipped"] is False
        retained_texts = [r["text_cn"] for r in bc_done["retained_reservations"]]
        assert retained_texts == [orig_reason_1, orig_reason_2], retained_texts

        return {
            "ok": True,
            "llm_calls": 0,
            "choice_id": choice_id,
            "pre_reveal_masked": True,
            "subject_commit_sha256": seal_sha,
            "blind_choice_seal_verified": True,
            "bare_insistence_rejected_409": True,
            "reason_erasure_rejected_409": True,
            "anti_sycophancy_verified": True,
            "adopted_action_choice_id": bc_done["adopted_action_choice_id"],
            "preferred_judgment_choice_id": bc_done["preferred_judgment_choice_id"],
            "retained_reservations_count": len(retained_texts),
        }
    finally:
        inst.stop()


def run_72h_zero_idle_spin_heartbeat_proof(base_dir: Path) -> dict[str, Any]:
    """Scenario 3: Full 4,320-tick (72h @ 60s) zero-idle-spin heartbeat + anti-fabrication proof."""
    root = base_dir / "heartbeat_72h"
    root.mkdir(parents=True, exist_ok=True)
    base_dt = datetime(2026, 11, 21, 0, 0, 0, tzinfo=TZ_BJT)
    base_iso = base_dt.isoformat(timespec="seconds")

    # Seed 2 sparse future reservations (+18h and +54h, hold=72h so expiry is past +72h)
    # plus 1 weather-sensitive outdoor stall (+48h..+52h) cancelled by rain at +36h.
    rsv_18h = wce.create_commitment(
        root,
        {
            "kind": "BOOK_OR_ITEM_RESERVATION",
            "venue_id": "bookstore",
            "title_cn": "旧港建筑测绘集",
            "arrival_due_at": (base_dt + timedelta(hours=18)).isoformat(timespec="seconds"),
            "hold_hours": 72,
            "current_weather": "clear",
        },
        actor_side="subject",
        operation_key="hb72-rsv-18h",
        now_iso=base_iso,
    )
    stall_36h_rain = wce.create_commitment(
        root,
        {
            "kind": "INDEPENDENT_NPC_PLAN",
            "effect_kind": "OUTDOOR_STALL",
            "venue_id": "porch_market",
            "title_cn": "檐下旧物交换小摊",
            "start_at": (base_dt + timedelta(hours=48)).isoformat(timespec="seconds"),
            "end_at": (base_dt + timedelta(hours=52)).isoformat(timespec="seconds"),
            "weather_sensitive": True,
        },
        actor_side="subject",
        operation_key="hb72-stall-weather",
        now_iso=base_iso,
    )
    rsv_54h = wce.create_commitment(
        root,
        {
            "kind": "BOOK_OR_ITEM_RESERVATION",
            "venue_id": "cafe",
            "title_cn": "秋焙乌龙试饮罐",
            "arrival_due_at": (base_dt + timedelta(hours=54)).isoformat(timespec="seconds"),
            "hold_hours": 72,
        },
        actor_side="subject",
        operation_key="hb72-rsv-54h",
        now_iso=base_iso,
    )

    state_file = wce.commitments_state_path(root)
    assert state_file.is_file()

    # Spy on _atomic_write_json_0600 to count exact disk writes per tick cleanly.
    real_atomic_write = wce._atomic_write_json_0600
    write_counter = [0]

    def _counted_atomic_write(*args: Any, **kwargs: Any) -> Any:
        write_counter[0] += 1
        return real_atomic_write(*args, **kwargs)

    total_ticks = 72 * 60  # 4,320 ticks at 60-second intervals
    expected_wake_ticks = {18 * 60, 36 * 60, 54 * 60}  # 1080, 2160, 3240
    wake_events: list[dict[str, Any]] = []
    idle_ticks_zero_write = 0
    disk_writes_on_idle_ticks = 0
    total_llm_calls = 0

    prev_mtime_ns = state_file.stat().st_mtime_ns
    prev_sha256 = hashlib.sha256(state_file.read_bytes()).hexdigest()

    t_start = time.monotonic()
    wce._atomic_write_json_0600 = _counted_atomic_write  # type: ignore[assignment]
    try:
        for tick in range(1, total_ticks + 1):
            tick_dt = base_dt + timedelta(minutes=tick)
            tick_iso = tick_dt.isoformat(timespec="seconds")
            if tick < 36 * 60:
                weather_changed = False
                cur_weather = "clear"
            elif tick == 36 * 60:
                weather_changed = True
                cur_weather = "rain"
            else:
                weather_changed = False
                cur_weather = "rain"

            writes_before = write_counter[0]
            res = nyairo_day._maybe_tick_v09_world_commitments(
                root,
                tick_iso,
                weather_changed=weather_changed,
                new_weather=cur_weather,
            )
            writes_delta = write_counter[0] - writes_before
            cur_mtime_ns = state_file.stat().st_mtime_ns

            if tick in expected_wake_ticks:
                assert res is not None, f"expected wake at tick {tick} ({tick_iso})"
                assert res.get("llm_calls") == 0, f"llm_calls != 0 at tick {tick}: {res}"
                assert res.get("disk_written") is True, f"expected disk_written at tick {tick}"
                assert writes_delta == 1, f"expected 1 write at wake tick {tick}, got {writes_delta}"
                total_llm_calls += int(res.get("llm_calls") or 0)
                cur_sha256 = hashlib.sha256(state_file.read_bytes()).hexdigest()
                assert cur_sha256 != prev_sha256, f"state content unchanged on wake tick {tick}"
                prev_mtime_ns = cur_mtime_ns
                prev_sha256 = cur_sha256
                wake_events.append(
                    {
                        "tick": tick,
                        "hours": tick // 60,
                        "at_iso": tick_iso,
                        "status": res["status"],
                        "advanced_count": res["advanced_count"],
                        "affected_count": res["affected_count"],
                        "llm_calls": res["llm_calls"],
                    }
                )
            else:
                assert res is None, f"unexpected wake on idle tick {tick} ({tick_iso}): {res}"
                if writes_delta != 0 or cur_mtime_ns != prev_mtime_ns:
                    disk_writes_on_idle_ticks += max(1, writes_delta)
                    raise AssertionError(
                        f"idle tick {tick} mutated state file (writes_delta={writes_delta})"
                    )
                idle_ticks_zero_write += 1
    finally:
        wce._atomic_write_json_0600 = real_atomic_write  # type: ignore[assignment]

    elapsed_s = round(time.monotonic() - t_start, 4)
    final_sha256 = hashlib.sha256(state_file.read_bytes()).hexdigest()
    assert final_sha256 == prev_sha256, "state SHA-256 drifted after final wake tick"
    assert idle_ticks_zero_write == 4317, f"expected 4317 idle ticks, got {idle_ticks_zero_write}"
    assert disk_writes_on_idle_ticks == 0
    assert write_counter[0] == 3
    assert total_llm_calls == 0
    leftover_tmp = sorted(
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.name.endswith(("-wal", "-shm", ".tmp"))
    )
    assert leftover_tmp == [], f"unexpected temp/wal sidecars left after 72h run: {leftover_tmp}"

    final_snap = wce.read_world_commitments_zero_write(
        root,
        actor_side="partner",
        now_iso=(base_dt + timedelta(hours=72)).isoformat(timespec="seconds"),
    )
    by_id = {c["commitment_id"]: c for c in final_snap["commitments"]}
    assert by_id[rsv_18h["commitment_id"]]["state"] == "ARRIVED_HOLDING"
    assert by_id[stall_36h_rain["commitment_id"]]["state"] == "CANCELLED_BY_WEATHER"
    assert by_id[rsv_54h["commitment_id"]]["state"] == "ARRIVED_HOLDING"

    # Zero retroactive fabrication check (400 RETROACTIVE_FABRICATION_FORBIDDEN + 0 disk writes).
    before_retro = _snapshot_tree(root)
    retro_blocked = False
    try:
        wce.contact_npc_or_venue(
            root,
            venue_id="bookstore",
            contact_action="observe_or_greet",
            actor_side="subject",
            now_iso=(base_dt + timedelta(hours=72)).isoformat(timespec="seconds"),
            extra_params={"subject_mood": "lonely", "fabricate_backstory": True},
        )
    except wce.WorldCommitmentsError as exc:
        assert exc.status_code == 400
        assert exc.reason_code == "RETROACTIVE_FABRICATION_FORBIDDEN"
        retro_blocked = True
    assert retro_blocked, "retroactive fabrication must fail with 400 RETROACTIVE_FABRICATION_FORBIDDEN"
    assert _snapshot_tree(root) == before_retro, "retroactive fabrication refusal mutated disk"

    return {
        "ok": True,
        "llm_calls": 0,
        "total_ticks": total_ticks,
        "wake_events": wake_events,
        "wake_count": len(wake_events),
        "idle_ticks_zero_write": idle_ticks_zero_write,
        "disk_writes_total": write_counter[0],
        "disk_writes_on_idle_ticks": disk_writes_on_idle_ticks,
        "retroactive_fabrication_blocked": retro_blocked,
        "elapsed_seconds": elapsed_s,
    }


def run_all_live_proofs() -> dict[str, Any]:
    """Execute all three v0.9 live proofs in an ephemeral isolated workspace."""
    with _isolated_forget_control_env():
        with tempfile.TemporaryDirectory(prefix="nyairo-v09-live-proof-") as tmp_dir:
            base = Path(tmp_dir)
            div = run_two_instance_divergence_proof(base)
            bc = run_blind_choice_anti_sycophancy_proof(base)
            hb = run_72h_zero_idle_spin_heartbeat_proof(base)

    total_llm = int(div["llm_calls"]) + int(bc["llm_calls"]) + int(hb["llm_calls"])
    ok_all = bool(div["ok"] and bc["ok"] and hb["ok"] and total_llm == 0)
    return {
        "ok": ok_all,
        "schema": "nyairo.v09.living_agency_live_proof.v1",
        "llm_calls": total_llm,
        "idle_ticks_zero_write": hb["idle_ticks_zero_write"],
        "divergence_assertions_passed": div["divergence_assertions_passed"],
        "blind_choice_seal_verified": bc["blind_choice_seal_verified"],
        "anti_sycophancy_verified": bc["anti_sycophancy_verified"],
        "two_instance_divergence": div,
        "blind_choice_anti_sycophancy": bc,
        "heartbeat_72h_zero_spin": hb,
    }


def main() -> int:
    report = run_all_live_proofs()
    sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
