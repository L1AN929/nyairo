#!/usr/bin/env python3
"""出门走走：从家到公园看花，再到实体店买一件东西，回家收下并摆放。

这是一条**实际使用**演示，不是测试矩阵：

* 世界、身体、经济、手机面全部是仓库里真实的生产 owner（唯一的 World 位置写口、
  唯一的钱包/产权文档、唯一的手机 ``PhoneWorld``/``PhoneShopping`` facade）；
* 移动走正式的计时旅程（``chiyo_bundle.world_journey`` -> 单段 owner 提交口 ->
  gateway -> Resolver -> ``apply_move_to`` + travel 记录），每次跨段都是新的正式
  授权，没有瞬移、没有直写 ``world_state.json``；
* 时间用**注入的业务时钟**按世界包声明的走道秒数机械推进（不是真睡）；服务、
  授权消费和租约仍用真实时间；
* 决定由本脚本的 ``脚本决定`` 做出，只读取 owner 当场给出的可行选项；它代表不了
  真模型自然选择，也不产生任何模型请求。

可重复的初始条件：新隔离根（``/tmp/nyairo-outing-*``，保留不删）、经济创世
（虚拟初始钱包 10000 JPY）、世界包里的一间实体家居店和公园花圃。没有真实用户
数据，没有外发，没有下载，没有图片生成。

用法（WSL 仓库根）::

    PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/world_outing_demo.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "components" / "world" / "service"),
    str(ROOT / "components" / "world" / "service" / "bridge"),
    str(ROOT / "components" / "life" / "scripts"),
):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from components.worldgen.instance import init_instance  # noqa: E402

import action_intent as ai  # noqa: E402
import life_live_context_view as live_view  # noqa: E402
import world_ambient_events as ambient  # noqa: E402
import world_body_client as body_client  # noqa: E402
import world_body_config as wconfig  # noqa: E402
import world_body_service as wservice  # noqa: E402
import world_daily_opportunities as opportunities  # noqa: E402
import world_living_skeleton as skeleton  # noqa: E402
import world_move_opportunity_source as move_source  # noqa: E402
import world_phone_shopping as phone_shopping  # noqa: E402
import world_phone_world as phone_world  # noqa: E402
import world_property_economy as economy  # noqa: E402
import world_runtime_data as world_data  # noqa: E402
from chiyo_bundle import world_journey as journey  # noqa: E402

PACKAGE = ROOT / "examples" / "worlds" / "daily_neighborhood_v1"
BRIDGE_DIR = ROOT / "components" / "world" / "service" / "bridge"
RUNTIME_ROOT = ROOT / "components" / "world"
WORLD_ID = "daily_neighborhood_v1"
SUBJECT = "chiyo"
ITEM = "floor_cushion"
PURCHASE_KEY = "outing:buy:floor-cushion:1"
ARRANGE_KEY = "outing:arrange:floor-cushion:1"

#: 2026-10-27（周二）11:00 JST。商店 10–20 点营业，公园 6–22 点营业。
START = datetime(2026, 10, 27, 2, 0, tzinfo=timezone.utc)
JST = timezone(timedelta(hours=9))


class Clock:
    """一个显式的业务时钟；只用于演示里的逻辑时刻，不改变真实时间。"""

    def __init__(self, start: datetime):
        self.now = start

    def set(self, value: datetime) -> None:
        self.now = value

    def advance(self, seconds: float) -> datetime:
        self.now = self.now + timedelta(seconds=seconds)
        return self.now

    def iso(self) -> str:
        return self.now.isoformat()


def say(tag: str, text: str) -> None:
    print("[%s] %s" % (tag, text), flush=True)


def dump(tag: str, payload) -> None:
    print("[%s] %s" % (tag, json.dumps(payload, ensure_ascii=False, default=str)),
          flush=True)


def short(data, keys):
    return {key: data.get(key) for key in keys if key in data}


def build_environment(home: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update({
        "CHIYO_WORLD_HOME": str(home),
        "HERMES_HOME": str(home),
        "CHIYO_WORLD_PACKAGE": str(PACKAGE),
        "CHIYO_WORLD_ID": WORLD_ID,
        "CHIYO_WORLD_RUNTIME": str(RUNTIME_ROOT),
        "CHIYO_WORLD_LIVING_SKELETON_ENABLED": "1",
        "CHIYO_WORLD_TRAVEL_TIME_ENABLED": "1",
        "CHIYO_WORLD_DAILY_RHYTHM_ENABLED": "1",
        "CHIYO_WORLD_AMBIENT_EVENTS_ENABLED": "1",
        "CHIYO_WORLD_PROPERTY_ECONOMY_ENABLED": "1",
        "CHIYO_WORLD_DELIVERY_TIME_ENABLED": "1",
        "CHIYO_WORLD_LOCAL_OWNER_PHONE": SUBJECT,
        "WORLD_ACTION_AUTH_REGISTRY_DIR": str(home / "run"),
        "NYAIRO_WORLD_BRIDGE_DIR": str(BRIDGE_DIR),
        "WORLD_BODY_INTEGRATION_MODE": "canonical",
        ai.GATE_ENV: ai.GATE_CANONICAL,
        "BODY_RUNTIME_MODE": "canonical",
        "CHIYO_WORLD_BODY_AUTHORITY": "production",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    os.environ.update(env)
    world_data.reset_cache()
    return env


def start_service(env: dict[str, str], clock: Clock, label: str):
    config = wconfig.load_config(
        env, runtime_root=RUNTIME_ROOT, world_home=env["CHIYO_WORLD_HOME"],
        runtime_mode="canonical", authority="production", world_id=WORLD_ID,
    )
    service = wservice.WorldBodyService(
        config, clock=lambda: clock.now, tick_seconds=3600.0,
        instance_id="world-outing-%s-%s" % (label, uuid.uuid4().hex[:6]),
    )
    service.startup()
    gateway = service.gateway_socket_path
    thread = threading.Thread(
        target=service.serve, kwargs={"max_seconds": 900.0}, daemon=True
    )
    thread.start()
    deadline = time.monotonic() + 10.0
    while not gateway.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    if not gateway.exists():
        raise RuntimeError("World/Body gateway socket did not appear")
    env["WORLD_BODY_SOCKET"] = str(gateway)
    say("service", "World/Body 已启动（pid 同本进程），gateway=%s" % gateway)
    return service, gateway, thread


def stop_service(service, thread) -> None:
    try:
        report = service.shutdown()
    finally:
        thread.join(timeout=5.0)
    say("service", "已正规关闭，lease_released=%s" % (report or {}).get("lease_released"))


def phone_facades(env: dict[str, str], gateway: Path, clock: Clock):
    world = phone_world.PhoneWorld(
        env["CHIYO_WORLD_HOME"], env, local_owner_uid=os.getuid(),
        subject=SUBJECT, gateway_socket=gateway, clock=lambda: clock.now,
    )
    shopping = phone_shopping.PhoneShopping(
        env["CHIYO_WORLD_HOME"], env, local_owner_uid=os.getuid(),
        subject=SUBJECT, gateway_socket=gateway, clock=lambda: clock.now,
    )
    return world, shopping


def make_provider(env: dict[str, str], clock: Clock):
    return move_source.WorldMoveOpportunityProvider(
        client=body_client.WorldBodyClient(environment=env),
        action_intent=ai,
        bridge_dir=str(BRIDGE_DIR),
        environment=env,
        world_home=env["CHIYO_WORLD_HOME"],
        now=lambda: clock.now,
        observed_now=lambda: clock.now,
        valid_for_seconds=300,
    )


def here_and_places(provider):
    """One live observation, the bounded decision view, and the local scene facts."""

    provider.observe()
    context = live_view.read_live_context(provider)
    catalog = provider.last_catalog or {}
    here = str((context.get("location") or {}).get("place_id") or "")
    entry = next(
        (item for item in (catalog.get("opportunities") or [])
         if item.get("location_id") == here),
        None,
    )
    return context, (entry or {}), catalog


def walk_to(env: dict[str, str], clock: Clock, target: str) -> dict:
    """正式计时旅程：一次接受目标，逐段提交/结算，直到 owner 确认到达。"""

    world = world_data.active_world(env)
    out = journey.accept_journey(
        target=target, hermes_home=env["CHIYO_WORLD_HOME"], environment=env,
        now=clock.now, request_key="outing:%s:%s" % (target, uuid.uuid4().hex[:6]),
    )
    if out.get("status") not in ("started", "in_transit", "completed"):
        dump("walk-refused", short(out, ("status", "reason_code")))
        raise RuntimeError("journey refused: %s" % out.get("reason_code"))
    segments: list[dict] = []
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
                hermes_home=env["CHIYO_WORLD_HOME"], environment=env, now=clock.now
            )
        else:
            # 出发不等于到达：到期之前她仍在段起点；只有 owner 的 travel 记录
            # 到了自己的到期时刻，settle 才会把位置推到终点。
            departure_position = (
                skeleton.read_world_state(
                    hermes_home=env["CHIYO_WORLD_HOME"], environment=env
                ) or {}
            ).get("location", {}).get("place_id")
            due = datetime.fromisoformat(
                str(segment["expected_arrival_at"]).replace("Z", "+00:00")
            )
            clock.set(due + timedelta(seconds=1))
            next_out = journey.advance_journey(
                hermes_home=env["CHIYO_WORLD_HOME"], environment=env, now=clock.now
            )
        if departure_position is not None and departure_position != previous:
            raise RuntimeError(
                "departure was treated as arrival: %s != %s"
                % (departure_position, previous)
            )
        segments.append({
            "from": previous,
            "to": destination,
            "walk_seconds": world.route_duration_seconds(previous, destination),
            "execution_id": segment.get("execution_id"),
            "departure_position": departure_position,
            "expected_arrival_at": segment.get("expected_arrival_at"),
            "owner_status": segment["status"],
            "advanced_to": next_out.get("status"),
        })
        if next_out.get("status") in ("blocked", "unknown", "unavailable"):
            dump("walk-stop", short(next_out, ("status", "reason_code")))
            break
    state = skeleton.read_world_state(
        hermes_home=env["CHIYO_WORLD_HOME"], environment=env
    ) or {}
    place = str((state.get("location") or {}).get("place_id") or "")
    if place != target:
        raise RuntimeError("journey did not arrive: %s != %s" % (place, target))
    total = sum(int(item["walk_seconds"] or 0) for item in segments)
    say("walk", "→ %s：%d 段，世界包步行 %d 秒（注入时钟机械推进）"
        % (target, len(segments), total))
    for item in segments:
        say("  segment", "%s → %s，%s 秒，owner=%s，execution=%s，到期前位置=%s"
            % (item["from"], item["to"], item["walk_seconds"], item["owner_status"],
               str(item.get("execution_id") or "")[-12:],
               item.get("departure_position") or item["from"]))
    dump("position", {"place_id": place, "area_id": (state.get("location") or {}).get("area_id"),
                      "business_clock": clock.iso()})
    return {"place_id": place, "segments": segments, "walk_seconds": total}


def scripted_choice(stage: str, choice: str, reason: str) -> None:
    say("decision", "脚本替身（不是模型）：stage=%s choice=%s reason=%s"
        % (stage, choice, reason))


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="nyairo-outing-", dir="/tmp"))
    home = root / "world-home"
    init_instance(PACKAGE, home)
    (home / "run").mkdir(parents=True, exist_ok=True)
    env = build_environment(home)
    clock = Clock(START)
    say("root", "新隔离根 %s（保留不删）" % root)
    say("clock", "业务时钟起点 %s（JST %s）；按走道秒数机械快进，不是真实等待"
        % (clock.iso(), clock.now.astimezone(JST).strftime("%Y-%m-%d %H:%M %Z")))
    genesis = economy.initialize_genesis(hermes_home=home, environment=env)
    wallet0 = economy.wallet_projection(hermes_home=home, environment=env)
    say("economy", "经济创世 %s；虚拟初始钱包 %s %s"
        % (genesis.get("status"), wallet0.get("current_balance"), wallet0.get("currency")))

    service, gateway, thread = start_service(env, clock, "a")
    world_face, shop = phone_facades(env, gateway, clock)
    from chiyo_bundle import phone_tool  # noqa: E402

    missing = [
        name for name in ("world", "store", "inventory", "wallet", "store_purchase")
        if name not in phone_tool.ACTIONS
    ]
    if missing:
        raise RuntimeError("手机工具未登记: %s" % missing)
    say("phone", "手机面使用 PhoneWorld/PhoneShopping（PhoneService 与 chiyo_phone 工具"
                 " world/store/inventory/wallet/store_purchase 委托的同一对 owner facade）")
    provider = make_provider(env, clock)
    try:
        # -- 1. 在家：看当前位置与可达 ----------------------------------------
        context, here_entry, catalog = here_and_places(provider)
        say("home", "当前位置：%s（%s）"
            % ((context.get("location") or {}).get("place_id"),
               ((context.get("location") or {}).get("name") or "?")))
        places = (context.get("nearby") or [])
        dump("nearby", [
            {"place_id": item.get("place_id"), "name": item.get("name"),
             "walk_seconds": item.get("walk_seconds"),
             "availability": item.get("availability"), "reach": item.get("reach")}
            for item in places[:8]
        ])
        scripted_choice("leave_home", "MOVE place:park",
                        "公园在可达列表里且此刻 OPEN；去看那里声明的东西")
        walk_to(env, clock, "park")

        # -- 2. 在公园：当地声明的花圃 + 当前环境 --------------------------------
        context, here_entry, catalog = here_and_places(provider)
        observed = here_entry.get("observed") or {}
        flowers = [
            item for item in (observed.get("furniture") or [])
            if str(item.get("object_id")) == "flowerbed_park"
        ]
        say("park", "当地可见物件来源=%s（世界包 scene 声明）"
            % observed.get("source"))
        dump("park-scene", [
            {"object_id": item.get("object_id"), "name": item.get("name"),
             "kind": item.get("kind"), "note": item.get("note")}
            for item in observed.get("furniture") or []
        ])
        if not flowers:
            raise RuntimeError("公园花圃没有出现在当地场景声明里")
        say("flowers", "看到「%s」：%s"
            % (flowers[0].get("name"), flowers[0].get("note")))
        takeable: set[str] = set()
        for entry in ((provider.last_available_actions or {}).get("actions") or []):
            if entry.get("verb") != "PICK_UP" or entry.get("available") is not True:
                continue
            takeable.update(
                str(item.get("object_id"))
                for item in (entry.get("candidates") or [])
                if item.get("object_id")
            )
        if "flowerbed_park" in takeable:
            raise RuntimeError("花圃被错误地列为可拿取")
        say("flowers", "可拿取对象=%s；花圃不在其中（观察不消费，也不能摘）"
            % (sorted(takeable) or "无"))
        phone_snap = world_face.snapshot()
        phone_env = ((phone_snap.get("data") or {}).get("local_environment") or {})
        say("phone-park", "手机 world GET 的当地物件来源=%s；物件=%s"
            % (phone_env.get("source"),
               [item.get("name") for item in (phone_env.get("furniture") or [])]))
        local_day = clock.now.astimezone(JST).date()
        events = ambient.active_ambient_events(
            local_day, clock.now, relevant_location_ids={"park"}, environment=env
        )
        if events:
            dump("park-ambient", [
                {"event_type": item.get("event_type"),
                 "summary": (item.get("payload") or {}).get("summary"),
                 "provenance": item.get("provenance")}
                for item in events
            ])
        else:
            say("park-ambient", "本时段世界包没有给公园选中额外环境事件（不是没读到）")
        stays = [item for item in (provider.last_opportunity_view or [])
                 if str(item.get("verb") or "") == "STAY"]
        if stays:
            say("park-stay", "可选停留：%s（不需要产出）" % stays[0].get("target_ref"))
        scripted_choice("park", "NO_ACTION",
                        "看花和停留都可以，但不强迫她产出或继续行动；本次选择不行动")
        say("park", "NO_ACTION 之后位置与场景不变，没有写任何作品/日记")

        # -- 3. 走到实体店 -----------------------------------------------------
        context, here_entry, catalog = here_and_places(provider)
        store_place = next(
            (item for item in (context.get("nearby") or [])
             if item.get("place_id") == "home_goods_store"), None
        )
        if store_place is not None:
            say("store-reach", "可达的实体店：%s（%s 秒，%s）"
                % (store_place.get("name"), store_place.get("walk_seconds"),
                   store_place.get("availability")))
        scripted_choice("leave_park", "MOVE place:home_goods_store",
                        "实体店在可达列表里且此刻 OPEN；到店看实际在卖的东西")
        walk_to(env, clock, "home_goods_store")

        # -- 4. 到店：只读目录/余额，正式购买 ---------------------------------
        first = shop.store()
        data = first.get("data") or {}
        say("store", "GET 到店：status=%s at_store=%s 营业=%s 余额=%s %s"
            % (first.get("status"), data.get("at_store"),
               (data.get("availability") or {}).get("status"),
               data.get("balance"), data.get("currency")))
        dump("store-shelf", [
            {"catalog_item_id": item.get("catalog_item_id"), "name": item.get("name"),
             "price": item.get("price"), "affordable": item.get("affordable")}
            for item in (data.get("catalog") or [])
        ])
        second = shop.store()
        data2 = second.get("data") or {}
        say("store-get", "连续两次 GET：余额 %s → %s，待交付 %s（读不会扣款/发货）"
            % (data.get("balance"), data2.get("balance"),
               data2.get("pending_delivery_count")))
        chosen = next(
            (item for item in (data.get("catalog") or [])
             if item.get("catalog_item_id") == ITEM), None
        )
        if chosen is None or not chosen.get("affordable"):
            raise RuntimeError("选定的商品不在货架上或买不起")
        scripted_choice("at_store", "PURCHASE %s" % ITEM,
                        "在营业的实体店里、余额 %s ≥ 价格 %s；买一件付得起的"
                        % (data.get("balance"), chosen.get("price")))
        bought = shop.store_purchase(ITEM, PURCHASE_KEY)
        bdata = bought.get("data") or {}
        say("purchase", "购买 status=%s 余额 %s → %s；receipt=%s"
            % (bought.get("status"), data.get("balance"), bdata.get("current_balance"),
               bdata.get("transaction_id")))
        dump("purchase-result", short(bdata, (
            "catalog_item_id", "price", "currency", "ownership", "location",
            "delivery_location", "delivery_status", "expected_delivery_at",
            "materialization", "current_balance")))
        replay = shop.store_purchase(ITEM, PURCHASE_KEY)
        rdata = replay.get("data") or {}
        say("purchase-replay", "同 operation_key 重发：status=%s 余额=%s（不二扣）"
            % (replay.get("status"), rdata.get("current_balance")))
        after_get = shop.store()
        adata = after_get.get("data") or {}
        say("store-get-after", "购买后 GET：余额=%s，待交付=%s（GET 不推进到货）"
            % (adata.get("balance"), adata.get("pending_delivery_count")))

        # -- 5. 回家：待交付 -> owner tick 到货 -> 查看 -> 摆放 ----------------
        scripted_choice("leave_store", "MOVE place:home", "带着已付款的产权回家")
        walk_to(env, clock, "home")
        pending = economy.deliveries_projection(hermes_home=home, environment=env)
        item_id = str(bdata.get("item_instance_id") or "")
        before = shop.inventory()
        before_item = next(
            (item for item in ((before.get("data") or {}).get("items") or [])
             if item.get("item_instance_id") == item_id), None
        )
        say("delivery-wait", "到家后：待交付 %s 件；取得物状态=%s，位置=%s"
            % (pending.get("pending_count"),
               (before_item or {}).get("delivery_status"),
               (before_item or {}).get("location")))
        if not pending.get("pending"):
            raise RuntimeError("expected one pending delivery")
        expected = datetime.fromisoformat(
            str(pending["pending"][0]["expected_delivery_at"]).replace("Z", "+00:00")
        )
        clock.set(expected + timedelta(seconds=1))
        say("delivery-tick", "业务时钟推进到交付时窗之后 %s，调用 owner tick" % clock.iso())
        tick = service.tick() or {}
        deliveries = (tick.get("deliveries") or {}).get("realized") or {}
        say("delivery", "tick 结果：realized=%s（唯一到货写口）"
            % deliveries.get("realized"))
        after = shop.inventory()
        after_item = next(
            (item for item in ((after.get("data") or {}).get("items") or [])
             if item.get("item_instance_id") == item_id), None
        )
        say("delivery", "取得物：name=%s ownership=%s delivery=%s materialization=%s 位置=%s"
            % ((after_item or {}).get("name"), (after_item or {}).get("ownership"),
               (after_item or {}).get("delivery_status"),
               (after_item or {}).get("materialization"),
               (after_item or {}).get("location")))
        snap = world_face.snapshot()
        owned = ((snap.get("data") or {}).get("objects") or {}).get("owned_furniture") or {}
        owned_item = next(
            (item for item in (owned.get("items") or [])
             if item.get("item_instance_id") == item_id), None
        )
        say("home-object", "手机 world 面看到：%s，placement=%s；hands=%s，held=%s"
            % ((owned_item or {}).get("name"), (owned_item or {}).get("placement"),
               ((snap.get("data") or {}).get("objects") or {}).get("hands"),
               ((snap.get("data") or {}).get("objects") or {}).get("held")))
        scripted_choice("at_home", "ARRANGE %s -> home/living" % ITEM,
                        "已到货且已物化；把它摆到起居室，不改产权、不伪造'在手上'")
        arranged = world_face.act(
            "ARRANGE",
            {"object_id": item_id,
             "target": {"kind": "room", "location_id": "home", "area_id": "living"}},
            ARRANGE_KEY,
        )
        say("arrange", "ARRANGE status=%s reason=%s"
            % (arranged.get("status"),
               (arranged.get("data") or {}).get("reason_code")))
        snap2 = world_face.snapshot()
        owned2 = ((snap2.get("data") or {}).get("objects") or {}).get("owned_furniture") or {}
        moved = next(
            (item for item in (owned2.get("items") or [])
             if item.get("item_instance_id") == item_id), None
        )
        say("arrange", "摆放后 placement=%s" % ((moved or {}).get("placement")))

        # -- 6. 后续正式判断材料看得见 -----------------------------------------
        context2, here2, _catalog2 = here_and_places(provider)
        wallet_now = shop.wallet()
        say("later", "后续观察：位置=%s；钱包余额=%s；取得物位置=%s"
            % (((context2.get("location") or {}).get("place_id")),
               ((wallet_now.get("data") or {}).get("balance")),
               ((moved or {}).get("placement") or {}).get("area_id")))
        say("later", "Life 的 AG1 上下文只带当前位置/可达/可行选项；余额与已购列表"
                     "由已登记的 chiyo_phone 工具 world/store/inventory/wallet 读取，"
                     "没有为购物强造 Decision 字段")
        dump("later-context", {
            "location": context2.get("location"),
            "nearby": [
                {"place_id": item.get("place_id"), "name": item.get("name")}
                for item in (context2.get("nearby") or [])[:5]
            ],
            "opportunities": [
                item.get("label") for item in (context2.get("opportunities") or [])[:6]
            ],
        })
        remote = shop.store()
        say("later-store", "在家里读实体店：status=%s reason=%s（不能远程当到店）"
            % (remote.get("status"), remote.get("reason_code")))
        balance_before_restart = (wallet_now.get("data") or {}).get("balance")

        # -- 7. 同根正规停服重开：余额/位置/产权保留 ---------------------------
        stop_service(service, thread)
        service2, gateway2, thread2 = start_service(env, clock, "b")
        world2, shop2 = phone_facades(env, gateway2, clock)
        try:
            wallet2 = shop2.wallet()
            inv2 = shop2.inventory()
            item2 = next(
                (item for item in ((inv2.get("data") or {}).get("items") or [])
                 if item.get("item_instance_id") == item_id), None
            )
            state2 = skeleton.read_world_state(
                hermes_home=home, environment=env
            ) or {}
            say("restart", "同根重开：位置=%s 余额=%s→%s 取得物=%s ownership=%s "
                           "materialization=%s"
                % ((state2.get("location") or {}).get("place_id"),
                   balance_before_restart,
                   (wallet2.get("data") or {}).get("balance"),
                   (item2 or {}).get("location"), (item2 or {}).get("ownership"),
                   (item2 or {}).get("materialization")))
            replay2 = shop2.store_purchase(ITEM, PURCHASE_KEY)
            say("restart", "重开后同 key 重发：status=%s 余额=%s"
                % (replay2.get("status"),
                   ((replay2.get("data") or {}).get("current_balance"))))
            if (wallet2.get("data") or {}).get("balance") != balance_before_restart:
                raise RuntimeError("restart changed the balance")
            if (item2 or {}).get("location") != "room:home/living":
                raise RuntimeError("restart did not keep the arranged placement")
        finally:
            stop_service(service2, thread2)
        service, thread = None, None

        say("done", "一条真实 owner 链跑完：家 → 公园（看花圃/选择不行动）→ 实体店"
                    "（看货/付款）→ 家（到货/摆放）→ 同根重开保留")
        say("boundary", "决定全部是脚本替身，不是真实模型选择；业务时钟是机械快进；"
                        "钱包是虚拟初始额度；花圃来自仓库世界包内容；没有外发/图片/"
                        "真实用户数据；本演示未启动 Supply（本链不涉及作品/阅读）")
        return 0
    finally:
        if service is not None:
            stop_service(service, thread)


if __name__ == "__main__":
    raise SystemExit(main())
