#!/usr/bin/env python3
"""Same neighbourhood, two business hours: what is open, what is readable, what is refused.

This is one **actual use**, not a test matrix:

* the root is created by the real daily entry (``nyairo_day.py init``) with the
  ``daily_neighborhood_v1`` package; the package's own ``content.json`` supplies
  the public-domain reading text, which becomes a real Supply artifact;
* the World/Body owner, economy wallet, phone HTTP service and Supply owner are
  the repository's real production processes.  Movement is a formal timed
  journey (``chiyo_bundle.world_journey`` -> single-segment owner port ->
  gateway -> Resolver -> ``apply_move_to`` + travel record), never a direct
  state edit;
* one explicit **business clock** (a ``fastday_clock.json`` file) is read by both
  the World child and the phone's World/economy facades.  It is a mechanical
  simulation of 10:30 and 20:30 JST, not the real wall clock and not a claim
  that a whole day passed;
* the choices ("go to the reading corner", "read page 1", "try the counter")
  are named **script stand-ins**.  They are not model choices and make no model
  request.

What it demonstrates, and what it does not:

* the same place rules return OPEN/CLOSED and real affordances at two hours;
* at the open reading corner, one page of the package's public-domain text is
  read through the real Supply reading workbench and an explicit stop is saved;
* a closed physical store refuses a counter purchase, and read-only GETs do not
  charge, consume or record reading;
* after a normal stop, the same root is started again and the reading stop /
  wallet / location are still there.

It does **not** sell coffee or books: the economy owner has one physical store
channel (``home_goods_store``), and widening it to the café/bookshop would be a
new owner contract.  The café therefore shows its real opening state, menu note
and "not sold here" limitation, not a fake purchase.  No product model is
called, nothing is sent out, and no image is generated.

Usage (WSL / Linux, repo root)::

    PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/neighborhood_time_shops_demo.py
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
for entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "components" / "world" / "service"),
    str(ROOT / "components" / "world" / "service" / "bridge"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "scripts"),
):
    if entry not in sys.path:
        sys.path.insert(0, entry)

PACKAGE = "daily_neighborhood_v1"
SUBJECT = "chiyo"
JST = timezone(timedelta(hours=9))
MORNING = datetime(2026, 10, 27, 10, 30, tzinfo=JST)   # Tuesday, all three shops open
EVENING = datetime(2026, 10, 27, 20, 30, tzinfo=JST)   # café 18:00 close; reading/store 20:00
CLOCK_RUN_ID = "neighborhood-time-shops"
READING_PAGE_KEY = "time-shops:read:page-1:v1"
STORE_KEY = "time-shops:store:closed:v1"
RESULT_PATH = ROOT / ".agents" / "worker-runs" / "20261027-neighborhood-time-shops-run.json"


class DemoError(RuntimeError):
    """A named refusal of this demo; nothing is papered over."""


def say(tag: str, text: str) -> None:
    print("[%s] %s" % (tag, text), flush=True)


def dump(tag: str, payload: Any) -> None:
    print("[%s] %s" % (tag, json.dumps(payload, ensure_ascii=False, default=str)),
          flush=True)


# ---------------------------------------------------------------------------------------------
# business clock (mechanical, file-backed; the World child and phone read the same file)
# ---------------------------------------------------------------------------------------------


class FileClock:
    """A frozen logical moment in an existing ``demo_business_clock`` file.

    The World child reads this file on every call; the phone service is given
    the same reader.  ``set()`` only moves the demo's logical moment — it never
    touches real safety/timeout clocks.
    """

    def __init__(self, state_root: Path, *, run_id: str, now: datetime) -> None:
        import demo_business_clock as dbc

        self.dbc = dbc
        self.path = dbc.clock_path(state_root)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self.domain = dbc.domain_for(run_id)
        self.now = now
        self._write()

    def _write(self) -> None:
        payload = {
            "kind": self.dbc.KIND,
            "schema_version": self.dbc.SCHEMA_VERSION,
            "mode": self.dbc.MODE_COMPRESSED_DEMO,
            "domain_id": self.domain,
            "run_id": self.run_id,
            "rate": 1.0,
            "logical_seconds": 24 * 3600,
            "logical_anchor": self.now.isoformat(),
            "logical_checkpoint": self.now.isoformat(),
            "horizon_logical": (self.now + timedelta(days=1)).isoformat(),
            "state": self.dbc.STATE_FROZEN,
            "authority": None,
            "downtime_frozen": False,
            "ticks": 0,
            "created_utc": self.now.isoformat(),
            "updated_utc": self.now.isoformat(),
            "note": "mechanical demo moment; real safety/timeout clocks are not accelerated",
        }
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=1),
                       encoding="utf-8")
        os.replace(tmp, self.path)

    def set(self, moment: datetime) -> datetime:
        self.now = moment
        self._write()
        return self.now

    def advance(self, seconds: float) -> datetime:
        return self.set(self.now + timedelta(seconds=seconds))

    def iso(self) -> str:
        return self.now.isoformat()


# ---------------------------------------------------------------------------------------------
# the real entry + phone tool
# ---------------------------------------------------------------------------------------------


def _entry():
    return importlib.import_module("nyairo_day")


def _namespace(entry, **values: Any) -> argparse.Namespace:
    defaults = dict(
        root=None, memory_authority=None, run_id=None, world_package=None,
        lived_needs=False, lived_needs_hunger=None, lived_needs_thirst=None,
        lived_needs_toilet=None, room_living=None, world_actions=None,
        memory_authority_path=None, foreground=False, json=True, port=None,
        model=False, live_model=False, duration_seconds=None, max_new_requests=None,
    )
    defaults.update(values)
    return argparse.Namespace(**defaults)


def _tool(profile_home: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    from chiyo_bundle import phone_tool

    raw = phone_tool.phone_handler(dict(payload), hermes_home=str(profile_home),
                                   platform="cli")
    return json.loads(raw)


def _envelope(tool_result: Mapping[str, Any]) -> dict[str, Any]:
    result = tool_result.get("result")
    return dict(result) if isinstance(result, Mapping) else dict(tool_result)


def _data(tool_result: Mapping[str, Any]) -> dict[str, Any]:
    data = _envelope(tool_result).get("data")
    return dict(data) if isinstance(data, Mapping) else {}


def _write_profile(home: Path, port: int) -> None:
    target = home / "chiyo"
    target.mkdir(parents=True, exist_ok=True)
    (target / "config.json").write_text(json.dumps({
        "cli_owner": True,
        "local_owner_uid": os.getuid() if hasattr(os, "getuid") else None,
        "phone": {
            "enabled": True,
            "base_url": "http://127.0.0.1:%d" % int(port),
            "write_enabled": True,
            "private_artifacts": True,
        },
    }, ensure_ascii=False, indent=1), encoding="utf-8")


def _start_service(root: Path, *, clock: FileClock, ready_timeout: float = 90.0):
    """Start the real phone service (Supply/World children + HTTP) off the main thread."""

    from components.phone import demo

    stop_event = threading.Event()
    ready = threading.Event()
    announcement: dict[str, Any] = {}
    failure: dict[str, Any] = {}

    def on_ready(payload: Mapping[str, Any]) -> None:
        announcement.update(dict(payload))
        ready.set()

    def runner() -> None:
        try:
            demo.serve_instance(
                root, port=0, room_living=True, world_intent_gate="canonical",
                tick_seconds=3600.0,
                business_clock=clock.path, clock_domain=clock.domain,
                clock_run_id=clock.run_id, on_ready=on_ready,
                stop_event=stop_event, announce=True,
            )
        except Exception as exc:  # noqa: BLE001 - report the real failure
            failure["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:300])
            ready.set()

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    if not ready.wait(ready_timeout):
        raise DemoError("SERVICE_NOT_READY_WITHIN_TIMEOUT")
    if failure:
        raise DemoError(failure["error"])
    return stop_event, thread, announcement


def _stop_service(stop_event: threading.Event, thread: threading.Thread) -> None:
    stop_event.set()
    thread.join(timeout=90.0)
    if thread.is_alive():
        raise DemoError("SERVICE_DID_NOT_STOP")


def _wait_http(port: int, timeout: float = 30.0) -> None:
    import socket
    import urllib.request

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.5)
                if probe.connect_ex(("127.0.0.1", int(port))) == 0:
                    break
        except OSError:
            pass
        time.sleep(0.1)
    else:
        raise DemoError("PHONE_HTTP_NOT_READY")
    # One bounded read after the port really accepts; a short timeout here would
    # close the client mid-answer and leave a noisy BrokenPipe in the server log.
    with urllib.request.urlopen(
            "http://127.0.0.1:%d/api/phone/state" % int(port), timeout=15.0) as answer:
        answer.read()


# ---------------------------------------------------------------------------------------------
# formal journey (same route mechanism as the outing demo)
# ---------------------------------------------------------------------------------------------


def _build_environment(root: Path, instance: Mapping[str, Any]) -> dict[str, str]:
    world_home = Path(str(instance["world_home"]))
    env = dict(os.environ)
    env.update({
        "CHIYO_WORLD_HOME": str(world_home),
        "HERMES_HOME": str(world_home),
        "CHIYO_WORLD_PACKAGE": str(instance.get("world_package") or ""),
        "CHIYO_WORLD_ID": str(instance.get("world_id") or ""),
        "CHIYO_WORLD_RUNTIME": str(ROOT / "components" / "world"),
        "CHIYO_WORLD_LIVING_SKELETON_ENABLED": "1",
        "CHIYO_WORLD_TRAVEL_TIME_ENABLED": "1",
        "CHIYO_WORLD_DAILY_RHYTHM_ENABLED": "1",
        "CHIYO_WORLD_PROPERTY_ECONOMY_ENABLED": "1",
        "CHIYO_WORLD_DELIVERY_TIME_ENABLED": "1",
        "CHIYO_WORLD_LOCAL_OWNER_PHONE": SUBJECT,
        "WORLD_ACTION_AUTH_REGISTRY_DIR": str(world_home / "run"),
        "NYAIRO_WORLD_BRIDGE_DIR": str(ROOT / "components" / "world" / "service" / "bridge"),
        "WORLD_BODY_INTEGRATION_MODE": "canonical",
        "WORLD_BODY_INTENT_MUTATION_GATE": "canonical",
        "BODY_RUNTIME_MODE": "canonical",
        "CHIYO_WORLD_BODY_AUTHORITY": "production",
        "WORLD_BODY_SOCKET": str(instance.get("world_body_socket")
                                 or world_home / "run" / "gateway.sock"),
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    os.environ.update(env)
    return env


def _walk_to(env: Mapping[str, str], clock: FileClock, target: str) -> dict[str, Any]:
    """A formal timed journey; the injected clock advances only on owner evidence."""

    from chiyo_bundle import world_journey as journey
    import world_living_skeleton as skeleton
    import world_runtime_data as world_data

    world = world_data.active_world(dict(env))
    out = journey.accept_journey(
        target=target, hermes_home=env["CHIYO_WORLD_HOME"], environment=dict(env),
        now=clock.now, request_key="time-shops:%s:%s" % (target, uuid.uuid4().hex[:6]),
    )
    if out.get("status") not in ("started", "in_transit", "completed"):
        raise DemoError("JOURNEY_REFUSED:%s" % out.get("reason_code"))
    segments: list[dict[str, Any]] = []
    for _ in range(journey.MAX_ROUTE + 2):
        store = journey.load_state(env["CHIYO_WORLD_HOME"]) or {}
        active = store.get("active")
        if not isinstance(active, dict):
            break
        status = str(active.get("status"))
        if status in ("completed", "blocked", "unknown"):
            break
        index = int(active["current_segment"])
        segment = active["segments"][index]
        previous = active["origin_location_id"] if index == 0 else active["route"][index - 1]
        destination = active["route"][index]
        departure_position = None
        if segment["status"] == "pending" and not segment.get("expected_arrival_at"):
            next_out = journey.advance_journey(
                hermes_home=env["CHIYO_WORLD_HOME"], environment=dict(env), now=clock.now)
        else:
            departure_position = (
                skeleton.read_world_state(hermes_home=env["CHIYO_WORLD_HOME"],
                                          environment=dict(env)) or {}
            ).get("location", {}).get("place_id")
            due = datetime.fromisoformat(
                str(segment["expected_arrival_at"]).replace("Z", "+00:00"))
            clock.set(due + timedelta(seconds=1))
            next_out = journey.advance_journey(
                hermes_home=env["CHIYO_WORLD_HOME"], environment=dict(env), now=clock.now)
        previous_place = (
            world.split_node(str(previous))[0]
            if hasattr(world, "split_node") else str(previous).split("/")[0]
        )
        if departure_position is not None and departure_position != previous_place:
            raise DemoError("DEPARTURE_TREATED_AS_ARRIVAL:%s!=%s"
                            % (departure_position, previous))
        segments.append({
            "from": previous, "to": destination,
            "walk_seconds": world.route_duration_seconds(previous, destination),
            "execution_id": segment.get("execution_id"),
            "departure_position": departure_position,
            "owner_status": segment["status"],
            "advanced_to": next_out.get("status"),
        })
        if next_out.get("status") in ("blocked", "unknown", "unavailable"):
            raise DemoError("JOURNEY_STOPPED:%s" % next_out.get("reason_code"))
    state = skeleton.read_world_state(
        hermes_home=env["CHIYO_WORLD_HOME"], environment=dict(env)) or {}
    place = str((state.get("location") or {}).get("place_id") or "")
    if place != target:
        raise DemoError("JOURNEY_DID_NOT_ARRIVE:%s!=%s" % (place, target))
    total = sum(int(item["walk_seconds"] or 0) for item in segments)
    say("walk", "→ %s：%d 段，世界包步行 %d 秒（注入时钟机械推进到 %s）"
        % (target, len(segments), total, clock.now.astimezone(JST).strftime("%H:%M")))
    for item in segments:
        say("  segment", "%s → %s，%s 秒，owner=%s，到期前位置=%s"
            % (item["from"], item["to"], item["walk_seconds"], item["owner_status"],
               item.get("departure_position") or item["from"]))
    return {"place_id": place, "segments": segments, "walk_seconds": total}


# ---------------------------------------------------------------------------------------------
# reads
# ---------------------------------------------------------------------------------------------


def _place(world_data: Mapping[str, Any], location_id: str) -> dict[str, Any]:
    for item in ((world_data.get("destinations") or {}).get("places") or []):
        if str(item.get("location_id")) == location_id:
            return dict(item)
    return {}


def _content(root: Path) -> dict[str, Any]:
    package = root / "world-package" / "content.json"
    try:
        return json.loads(package.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _entry_content(root: Path, key: str) -> dict[str, Any]:
    entries = _content(root).get("entries") or {}
    entry = entries.get(key) if isinstance(entries, Mapping) else None
    return dict(entry) if isinstance(entry, Mapping) else {}


def _assert(condition: Any, message: str) -> None:
    if not condition:
        raise DemoError(message)


def _reading_items(tool_result: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = _envelope(tool_result).get("items")
    return [dict(row) for row in rows] if isinstance(rows, list) else []


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", action="store_true",
                        help="retain the new root (it is always retained; kept for symmetry)")
    parser.parse_args(argv)
    if os.name != "posix" or not hasattr(os, "getuid"):
        print("这个演示只在 WSL/Linux 里运行（World/Supply 子进程需要 POSIX）。")
        return 2

    entry = _entry()
    parent = Path(tempfile.mkdtemp(prefix="nyairo-time-shops-"))
    root = parent / "instance"
    profile_home = parent / "phone-profile"
    authority = parent / "memory-authority" / "authority.db"
    summary: dict[str, Any] = {
        "kind": "neighborhood_time_shops_demo",
        "root": str(root),
        "package": PACKAGE,
        "product_model_requests": 0,
        "scripted_decision": True,
        "business_clock": {"morning": MORNING.isoformat(), "evening": EVENING.isoformat(),
                           "real_time": False},
        "steps": [],
    }
    stop_event = None
    thread = None
    try:
        # 1. the real daily entry creates the root, world package and Supply reading artifact
        rc = entry.cmd_init(_namespace(
            entry, root=str(root), world_package=PACKAGE,
            memory_authority=str(authority), json=False))
        summary["steps"].append({"step": "init", "exit": rc})
        if rc != 0:
            raise DemoError("INIT_REFUSED")
        from components.phone import service as phone_service
        instance = phone_service.load_instance(root)
        artifact_id = str(instance.get("reading_artifact_id") or "")
        _assert(artifact_id, "the package reading material was not bound by init")
        summary["reading_artifact_id"] = artifact_id
        say("init", "新隔离根 %s；世界包 %s；Supply 阅读材料已绑定 %s"
            % (root, PACKAGE, artifact_id[-12:]))

        reading_entry = _entry_content(root, "reading_material")
        cafe_entry = _entry_content(root, "cafe")
        shelf_entry = _entry_content(root, "reading_point")
        _assert(reading_entry.get("title"), "the package has no reading_material title")
        _assert(reading_entry.get("content"), "the package has no reading_material content")
        say("content", "世界包公开阅读材料：%s（%s，%s，来源 %s）"
            % (reading_entry.get("title"), reading_entry.get("author"),
               reading_entry.get("license"), reading_entry.get("source")))
        say("content", "咖啡店菜单：%s" % (cafe_entry.get("menu_note") or "（没有菜单说明）"))
        say("content", "书店书架：%s" % (shelf_entry.get("shelf_note") or "（没有书架说明）"))
        summary["weather"] = {
            "status": "NOT_MODELED",
            "detail": ("the package only lists static weather_candidates; there is no "
                       "rule-driven weather source or time-of-day weather. The "
                       "configured_v1 clear default is not reported as observed weather."),
        }
        summary["inventory"] = {
            "status": "NOT_MODELED",
            "detail": ("the economy owner has no persistent stock; catalog rows are "
                       "listings, not stock. No stock=1 or automatic restock is shown."),
        }
        say("weather", "NOT_MODELED：世界包只有静态 weather_candidates，没有按规则演进的天气源；"
                       "不把 configured_v1 的 clear 默认当成已观测天气")
        say("stock", "NOT_MODELED：经济 owner 没有持久库存；目录条目不是库存，"
                     "不显示 stock=1 或自动补货")
        say("sale", "咖啡店/书店未接入饮品或图书售卖；书店阅读的是公版阅读材料，"
                    "不需要付款，也不冒充买卖")

        # 2. one explicit business clock; both the World child and the phone read it
        clock = FileClock(parent / "clock-state", run_id=CLOCK_RUN_ID, now=MORNING)
        env = _build_environment(root, instance)
        say("clock", "业务时钟起于 %s（JST %s）；天气/光照不由此钟推断"
            % (clock.iso(), clock.now.astimezone(JST).strftime("%Y-%m-%d %H:%M %Z")))

        stop_event, thread, announcement = _start_service(root, clock=clock)
        port = int(announcement.get("port") or 0)
        _assert(port, "the phone service did not publish a port")
        _write_profile(profile_home, port)
        _wait_http(port)
        say("service", "真 Supply/World + 手机 HTTP 已就绪：port=%s children=%s"
            % (port, announcement.get("children")))

        # 3. morning at home: same rules, three shops really OPEN
        world = _tool(profile_home, {"action": "world"})
        _assert(_envelope(world).get("ok"), "world read refused")
        location = _data(world).get("location") or {}
        say("home", "当前位置：%s（%s）" % (location.get("place_id"), location.get("name")))
        morning_places = {}
        for place_id in ("cafe", "reading_point", "home_goods_store"):
            place = _place(_data(world), place_id)
            morning_places[place_id] = place
            say("morning", "%s：%s，可做=%s"
                % (place.get("name"), (place.get("availability") or {}).get("status"),
                   [a.get("affordance_id") for a in (place.get("affordances") or [])]))
            _assert((place.get("availability") or {}).get("status") == "OPEN",
                    "%s should be OPEN in the morning" % place_id)
        cafe_affordances = [a.get("affordance_id") for a in
                            (morning_places["cafe"].get("affordances") or [])]
        _assert("LOOK_AT_MENU" in cafe_affordances and "BUY_DRINK" not in cafe_affordances,
                "the café must show the menu note, not an unsupported BUY_DRINK")
        reading_affordances = [a.get("affordance_id") for a in
                               (morning_places["reading_point"].get("affordances") or [])]
        _assert("READ_HERE" in reading_affordances, "the reading corner must offer READ_HERE")
        summary["morning_places"] = {
            key: {"status": (value.get("availability") or {}).get("status"),
                  "affordances": [a.get("affordance_id") for a in
                                  (value.get("affordances") or [])]}
            for key, value in morning_places.items()
        }

        # 4. walk to the open reading corner (formal journey, not a state edit)
        scripted = {"stage": "leave_home", "choice": "MOVE place:reading_point",
                    "reason": "reading_point is OPEN at 10:30 and offers READ_HERE"}
        say("decision", "脚本替身（不是模型）：%s" % json.dumps(scripted, ensure_ascii=False))
        _walk_to(env, clock, "reading_point")
        world = _tool(profile_home, {"action": "world"})
        here = _data(world).get("location") or {}
        say("reading-corner", "到达：%s；当地物件=%s"
            % (here.get("name"),
               [item.get("name") for item in
                ((_data(world).get("local_environment") or {}).get("furniture") or [])]))

        # 5. read one real page and save one explicit stop (Supply owner writes)
        before = _tool(profile_home, {"action": "reading"})
        before_items = _reading_items(before)
        page = _tool(profile_home, {"action": "reading_material", "page": 1})
        page_env = _envelope(page)
        _assert(page_env.get("ok"), "the real material page was not readable")
        page_data = _data(page)
        text = str(page_data.get("text") or "")
        _assert(text.strip(), "page 1 is empty")
        _assert(str(reading_entry["content"]).startswith(text[:20]) or text in
                str(reading_entry["content"]),
                "the page is not the package's own public-domain text")
        say("read", "第 1/%s 页《%s》真实正文 %d 字：%s"
            % (page_data.get("pages"), page_data.get("title"), len(text), text[:40] + "…"))
        _assert(page_data.get("understanding") == "UNKNOWN",
                "reading must not claim understanding")
        again = _tool(profile_home, {"action": "reading_material", "page": 1})
        _assert(_envelope(again).get("ok"), "the second read failed")
        after_get = _tool(profile_home, {"action": "reading"})
        _assert(_reading_items(after_get) == before_items,
                "a reading GET recorded a reading operation")
        say("read-get", "连续 GET 正文：取得/停点条目 %d → %d（GET 不记阅读、不扣款）"
            % (len(before_items), len(_reading_items(after_get))))

        position = _tool(profile_home, {
            "action": "reading_position", "page": 1,
            "source_revision": page_data.get("source_revision"),
            "expected_head": page_data.get("private_head"),
            "operation_key": READING_PAGE_KEY,
        })
        position_env = _envelope(position)
        _assert(position_env.get("ok"), "the explicit reading stop was refused")
        read_items = _reading_items(_tool(profile_home, {"action": "reading"}))
        say("read-stop", "显式停点已由 Supply owner 保存：%s；取得/停点条目=%d；理解=%s"
            % (position_env.get("status"), len(read_items),
               (position_env.get("data") or {}).get("understanding")))
        summary["reading"] = {
            "artifact_id": artifact_id,
            "title": page_data.get("title"),
            "author": reading_entry.get("author"),
            "source": reading_entry.get("source"),
            "license": reading_entry.get("license"),
            "page": page_data.get("page"),
            "pages": page_data.get("pages"),
            "source_revision": page_data.get("source_revision"),
            "text_chars": len(text),
            "understanding": page_data.get("understanding"),
            "position_status": position_env.get("status"),
            "items_after_stop": read_items,
        }

        # 6. walk to the physical store while it is still open
        scripted = {"stage": "leave_reading_corner", "choice": "MOVE place:home_goods_store",
                    "reason": "the physical store is still OPEN; we will compare it at closing"}
        say("decision", "脚本替身（不是模型）：%s" % json.dumps(scripted, ensure_ascii=False))
        _walk_to(env, clock, "home_goods_store")
        morning_store = _tool(profile_home, {"action": "store"})
        store_data = _data(morning_store)
        say("store", "到店（早晨）：status=%s at_store=%s 营业=%s 余额=%s %s"
            % (_envelope(morning_store).get("status"), store_data.get("at_store"),
               (store_data.get("availability") or {}).get("status"),
               store_data.get("balance"), store_data.get("currency")))
        wallet_before = store_data.get("balance")

        # 7. evening: the same place rules now say CLOSED; the counter refuses and takes no money
        clock.set(EVENING)
        world = _tool(profile_home, {"action": "world"})
        evening_places = {}
        for place_id in ("cafe", "reading_point", "home_goods_store"):
            place = _place(_data(world), place_id)
            evening_places[place_id] = place
            say("evening", "%s：%s，reach=%s，可做=%s"
                % (place.get("name"), (place.get("availability") or {}).get("status"),
                   place.get("reach"),
                   [a.get("affordance_id") for a in (place.get("affordances") or [])]))
            _assert((place.get("availability") or {}).get("status") == "CLOSED",
                    "%s should be CLOSED at 20:30" % place_id)
            _assert(not (place.get("affordances") or []),
                    "%s should not advertise indoor actions when closed" % place_id)
            _assert(place.get("reach") in ("reachable", "here"),
                    "%s must stay reachable when closed, not become unreachable" % place_id)
        summary["evening_places"] = {
            key: {"status": (value.get("availability") or {}).get("status"),
                  "affordances": [a.get("affordance_id") for a in
                                  (value.get("affordances") or [])]}
            for key, value in evening_places.items()
        }
        closed_store = _tool(profile_home, {"action": "store"})
        closed_data = _data(closed_store)
        say("store-closed", "闭店读店面：status=%s reason=%s at_store=%s"
            % (_envelope(closed_store).get("status"),
               _envelope(closed_store).get("reason_code"), closed_data.get("at_store")))
        attempt = _tool(profile_home, {
            "action": "store_purchase", "catalog_item_id": "floor_cushion",
            "operation_key": STORE_KEY,
        })
        attempt_env = _envelope(attempt)
        say("store-closed", "闭店购买：status=%s reason=%s（无扣款）"
            % (attempt_env.get("status"), attempt_env.get("reason_code")))
        _assert(attempt_env.get("status") not in ("applied", "already_applied"),
                "a closed store must not accept a purchase")
        wallet_after = _data(_tool(profile_home, {"action": "wallet"})).get("balance")
        _assert(wallet_after == wallet_before,
                "the refused purchase changed the wallet")
        say("store-closed", "钱包 %s → %s；GET 和拒绝都没有扣款"
            % (wallet_before, wallet_after))
        summary["closed_purchase"] = {
            "status": attempt_env.get("status"),
            "reason_code": attempt_env.get("reason_code"),
            "wallet_before": wallet_before, "wallet_after": wallet_after,
        }
        read_a = _reading_items(_tool(profile_home, {"action": "reading"}))
        read_b = _reading_items(_tool(profile_home, {"action": "reading"}))
        _assert(read_a == read_items == read_b,
                "a closed-time reading GET recorded or lost a reading operation")
        say("store-closed", "闭店后连续 GET 阅读元数据：取得/停点条目仍为 %d"
            "（GET 不记阅读，也没有因为店面关门改写阅读历史）" % len(read_b))
        summary["reading_location_gate"] = (
            "NOT_MODELED: the reading workbench belongs to her own material and is not "
            "gated by the bookshop's opening hours; the closed-time refusal verified here "
            "is the physical store counter, not a bookshop reading ban."
        )
        say("reading-gate", "阅读工作台读的是她自己的材料，不受书店营业门约束；"
                            "本演示只在店面层验证闭店购买拒绝，不把阅读说成被店门拦住")

        # 8. a normal stop and the same root started again: the real stop is still there
        _stop_service(stop_event, thread)
        stop_event, thread = None, None
        say("stop", "服务已正规关闭（自己的 Supply/World 子进程与 HTTP 都停了）")
        stop_event, thread, announcement2 = _start_service(root, clock=clock)
        port2 = int(announcement2.get("port") or 0)
        _assert(port2, "the restarted phone service did not publish a port")
        _write_profile(profile_home, port2)
        _wait_http(port2)
        reading2 = _tool(profile_home, {"action": "reading_material"})
        page2 = _data(reading2)
        _assert(_envelope(reading2).get("ok"), "the reading material vanished after restart")
        _assert((page2.get("bookmark") or {}).get("page") == 1,
                "the saved reading stop did not survive the restart")
        wallet2 = _data(_tool(profile_home, {"action": "wallet"})).get("balance")
        world2 = _data(_tool(profile_home, {"action": "world"}))
        place2 = (world2.get("location") or {}).get("place_id")
        say("restart", "同根重开：位置=%s 余额=%s 停点=第%s页 版本=%s"
            % (place2, wallet2, (page2.get("bookmark") or {}).get("page"),
               str(page2.get("source_revision") or "")[-12:]))
        _assert(wallet2 == wallet_before, "the restart changed the wallet")
        _assert(place2 == "home_goods_store", "the restart lost the world location")
        summary["restart"] = {
            "location": place2, "wallet": wallet2,
            "bookmark_page": (page2.get("bookmark") or {}).get("page"),
            "source_revision": page2.get("source_revision"),
        }

        summary["ok"] = True
        say("done", "一条真实链跑完：同一街区早晨 OPEN/晚间 CLOSED → 书店闲时读一页真实公版正文"
                    "并保存停点 → 家居店闭店拒买且不扣款 → 同根重开保留")
        summary["local_action_gate"] = (
            "WORLD_BODY_INTENT_MUTATION_GATE=canonical for this process tree only; "
            "the production default stays off and was not changed."
        )
        say("boundary", "决定全部是脚本替身，不是模型选择；业务时钟是机械模拟；钱包是虚拟初始额度；"
                        "本地世界动作门只对本次进程树显式 canonical（生产默认仍关闭）；"
                        "咖啡店/书店没有接入售卖，只显示真实营业状态和未售说明；"
                        "没有调用产品模型、没有外发、没有生成图片")
    except Exception as exc:  # noqa: BLE001 - report the real reason
        summary["ok"] = False
        summary["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:500])
        say("error", summary["error"])
    finally:
        if stop_event is not None and thread is not None:
            try:
                _stop_service(stop_event, thread)
            except Exception as exc:  # noqa: BLE001
                summary.setdefault("cleanup_error", "%s: %s" % (type(exc).__name__, exc))
        RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
        RESULT_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=1,
                                          sort_keys=True), encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        print("结果：%s" % RESULT_PATH)
    return 0 if summary.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
