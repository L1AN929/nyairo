#!/usr/bin/env python3
"""场景10：测试/维护动作真实改变 World，却不被接纳为千代的个人生活。

做什么
======
在一个全新的隔离根里起**真实**的 World/Body 服务（真实 socket、真实 registry、真实世界
状态）、**真实** Life Supply owner、**真实** Memory（真实 M0 + 真实控制账本 + 真实删除权威）
和**真实** Production life runtime，然后：

1. 记录基线：世界姿态/revision、Memory 经历条数、AG-2 已持久交接条数。
2. 用现成正式入口 ``WorldBridgeResolver.resolve_action`` 提交一个 POSE，gate 只在本进程树
   显式设为 canonical（默认 off 不动）。断言：世界姿态真的变了、恰好一张票 CONSUMED、
   owner 回执可读；这条维护动作**没有**变成任何 AG-2 交接，Memory 条数不变。
3. 正控：同一个 runtime、同一个 Memory 绑定里跑一轮真实的 Life 活动（Decision/内容为脚本
   替身，AG-1/AG-2/LR-*/AR-0/Supply/Memory 都是真的）。真实 AG-2 owner 签发并持久一条
   handoff，经同一个 consumer 走原 M0 准入 **ACCEPT** 且可回读——不是拿字典拼出来的。
4. 负控（同一扇门，M0 写之前具名拒绝）：
   * 维护回执包成 handoff 形状但没有 Activity 锚点 → ``LIFE_ACTIVITY_REF_MISSING``；
   * 伪造 actv + 伪造 Decision/adoption → ``HANDOFF_SOURCE_NOT_FOUND``；
   * 引用真实存在的 Activity，但 owner 没有这条 handoff → ``HANDOFF_SOURCE_NOT_FOUND``；
   * 抄真实 handoff 的 id 再改来源/目标（塞进维护动作的 execution id）→
     ``HANDOFF_SOURCE_MISMATCH``；
   * 真实 handoff 原样重投 → ``DUPLICATE``，不多出经历。
5. 正规停止后从同一个 home 重启：世界效果仍在，Memory 条数不变，合法交接仍可从 owner 读回，
   原身份重投不新增 Action/经历。

不做什么
========
不调用任何模型（Decision/内容为脚本替身）、不碰任何既有实例、不手改 DB/JSON、不删根、
不裸 unlink、不改默认 gate 与系统权限、不外发；不为「做过」去伪造 origin 旗标或名字前缀。
信任范围是本地：以同一 uid 运行的进程本来就能改写 owner 自己的文件，那不在本检查的承诺里。

退出码
======
0 = 断言全部成立；1 = 任一断言失败或服务起不来；2 = 有明确 BLOCKED 停点。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "scripts"), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "components" / "world" / "service" / "bridge"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "native"),
              str(ROOT / "components" / "native" / "src"),
              str(ROOT / "components" / "native" / "memory_runtime_v1")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import world_body_config as wcfg  # noqa: E402
import world_body_service as wbsvc  # noqa: E402
import world_runtime_data as wrd  # noqa: E402

import fast_day  # noqa: E402
from world_action_bridge_adapter import WorldBridgeResolver  # noqa: E402

from life_experience_memory_consumer import MemoryAdmissionExperienceConsumer  # noqa: E402
from life_memory_service import memory_service_from_env  # noqa: E402

WORLD_RUNTIME_ROOT = ROOT / "components" / "world"
GATE_ENV = "WORLD_BODY_INTENT_MUTATION_GATE"
POSE_TARGET = "bodyact:pose:seated:north"
REGISTRY_NAME = "action_authorizations.json"


class _WorldService:
    """本脚本自己的真实 World 服务（线程 + 真 socket），跑在实例自己的 world home 上。"""

    def __init__(self, home: Path, environment: dict, world_id: str) -> None:
        self.home = Path(home)
        self.config = wcfg.load_config(
            dict(environment), runtime_root=WORLD_RUNTIME_ROOT, world_home=self.home,
            runtime_mode="canonical", authority="production", world_id=world_id)
        self.service = wbsvc.WorldBodyService(
            self.config, tick_seconds=3600.0,
            instance_id="maintenance-boundary-%d" % os.getpid())
        self.startup_report = self.service.startup()
        self.socket_path = self.service.gateway_socket_path
        self._thread = threading.Thread(
            target=self.service.serve, kwargs={"max_seconds": 600.0}, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 10.0
        while not Path(self.socket_path).exists():
            if time.monotonic() > deadline:
                raise RuntimeError("World gateway socket never appeared: %s"
                                   % self.socket_path)
            time.sleep(0.02)

    def stop(self) -> None:
        try:
            self.service.shutdown()
        except Exception:
            pass
        self._thread.join(timeout=5)


def _authorizations(home: Path) -> list:
    path = Path(home) / "run" / REGISTRY_NAME
    if not path.exists():
        return []
    return list(json.loads(path.read_text(encoding="utf-8")).get("records") or [])


def _body_revision(home: Path) -> int:
    path = Path(home) / "data" / "world" / "body_substrate.json"
    if not path.exists():
        return -1
    return int(json.loads(path.read_text(encoding="utf-8")).get("revision") or 0)


def _pose(environment: dict) -> dict:
    import world_body_client as wbc

    result = wbc.WorldBodyClient(environment=environment).get_snapshot()
    if result.outcome != wbc.OK or not isinstance(result.data, dict):
        raise RuntimeError("snapshot read failed: %r / %r"
                           % (result.outcome, result.reason))
    return {"pose": result.data.get("pose"),
            "orientation": result.data.get("orientation"),
            "world_revision": result.data.get("world_revision")}


def _experience_count(service) -> int:
    answer = service.list_experiences()
    if not answer.get("ok"):
        return -1
    return len(list((answer.get("data") or {}).get("experiences") or []))


def _handoff_records(runtime) -> dict:
    state = runtime.integration_read.read_state()
    return dict(state.get("handoff_events_by_id") or {})


def main() -> int:
    import life_integration_ag2 as ag2
    import life_runtime_production as lrp
    import one_day_life as odl
    import result_method_behavior_smoke as harness
    from life_experience_port import LifeExperiencePort

    chain = harness.build_chain("m10")
    instance = chain.root / "instance"
    world_home = Path(str(chain.env["CHIYO_WORLD_HOME"]))
    environment = dict(chain.env)
    environment["WORLD_BODY_INTEGRATION_MODE"] = "canonical"
    environment["NYAIRO_WORLD_BRIDGE_DIR"] = str(
        ROOT / "components" / "world" / "service" / "bridge")

    wrd.reset_cache()
    world = wrd.active_world(environment)
    environment["CHIYO_WORLD_ID"] = world.world_id

    # The isolated chain initialises the real control ledger with its first material import;
    # the Memory product seam must not report "blocked" just because that has not happened yet.
    from app.evidence import EvidenceStore
    from life_experience_port import life_conversation_id
    from memory_control import MemoryControlLedger

    EvidenceStore(str(chain.evidence_db))
    MemoryControlLedger(str(chain.controls_db), str(chain.evidence_db), owner="chiyo",
                        conversation=life_conversation_id("chiyo")).initialize()

    memory = memory_service_from_env(chain.env)
    status = memory.status()
    consumer = chain.runtime.experience_consumer
    summary: dict = {"root": str(chain.root), "world_id": world.world_id,
                     "memory_status": status.get("status"),
                     "consumer": consumer.describe(), "checks": {}}
    checks = summary["checks"]
    if status.get("status") != "READY":
        summary["blocked"] = "MEMORY_NOT_READY"
        summary["memory_reason"] = status.get("reason")
        summary["memory_detail"] = status.get("detail")
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        return 2
    if not consumer.describe().get("source_verified"):
        # 生产装配没把 owner 的只读读口交给 consumer：拒写是对的，但不能假装场景通过。
        summary["blocked"] = "HANDOFF_VERIFIER_NOT_WIRED"
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        return 2

    service = _WorldService(world_home, environment, world.world_id)
    previous_gate = os.environ.get(GATE_ENV)
    runtime2 = None
    try:
        environment["WORLD_BODY_SOCKET"] = str(service.socket_path)
        baseline_pose = _pose(environment)
        baseline_memory = _experience_count(memory)
        baseline_handoffs = len(_handoff_records(chain.runtime))
        summary["baseline"] = {"pose": baseline_pose.get("pose"),
                               "world_revision": baseline_pose.get("world_revision"),
                               "body_revision": _body_revision(world_home),
                               "experiences": baseline_memory,
                               "handoffs": baseline_handoffs}

        # 1. 维护动作经现成正式入口真改 World（gate 只在本进程树显式 canonical）。
        canonical = fast_day.resolve_world_intent_gate({GATE_ENV: "canonical"})
        os.environ[GATE_ENV] = str(canonical["value"])
        receipt = WorldBridgeResolver(environment=environment).resolve_action(
            idempotency_key="idem:maintenance:pose:1", verb="POSE",
            target=POSE_TARGET, expected_dependencies={})
        after_pose = _pose(environment)
        tickets = _authorizations(world_home)
        ticket = tickets[0] if tickets else {}
        execution_id = str(ticket.get("execution_id") or "")
        summary["maintenance"] = {
            "gate": canonical.get("value"),
            "status": receipt.get("status"),
            "pose_before": baseline_pose.get("pose"),
            "pose_after": after_pose.get("pose"),
            "world_revision": after_pose.get("world_revision"),
            "body_revision": _body_revision(world_home),
            "owner_authorization_id": ticket.get("authorization_id"),
            "ticket_state": str(ticket.get("state")).upper() if ticket else None,
            "execution_id": execution_id,
        }
        checks["world_really_changed"] = bool(
            canonical.get("value") == "canonical"
            and receipt.get("status") == "committed"
            and (after_pose.get("pose"), after_pose.get("orientation"))
            != (baseline_pose.get("pose"), baseline_pose.get("orientation"))
            and len(tickets) == 1
            and str(tickets[0].get("state")).upper() == "CONSUMED"
            and execution_id)

        # 2. 这条维护动作没有进入个人 life 链。
        records = _handoff_records(chain.runtime)
        summary["life_chain"] = {
            "handoffs": len(records),
            "handoffs_naming_execution": sum(
                1 for rec in records.values()
                if execution_id and execution_id in (rec.get("action_refs") or [])),
            "experiences": _experience_count(memory),
        }
        checks["not_personal_life"] = bool(
            summary["life_chain"]["handoffs"] == baseline_handoffs
            and summary["life_chain"]["handoffs_naming_execution"] == 0
            and summary["life_chain"]["experiences"] == baseline_memory)

        # 3. 正控：同一 runtime / 同一 Memory 绑定里跑一轮真实活动。
        #    Decision 与内容是脚本替身；AG-1 校验、AG-2 签发与持久化、Supply 写入、
        #    M0 准入都是真的。handoff 从 owner 的持久记录里取，不是手拼的字典。
        material_id, revision = harness.import_material(chain)
        harness.write_question(chain, material_id, revision)
        chain.client.payload = {"kind": "append_paragraph",
                                "text": "第一段：村里的井水在旱季还够全村用。",
                                "self_note": "stub: maintenance boundary write"}
        harness.select_option(chain, review=False)
        round_out = chain.cycle.evaluate_once(
            trigger_ref="evt:maintenance-boundary:1", act=True)
        emitted = round_out.get("experience_handoff") or {}
        emitted_id = str(emitted.get("handoff_id") or "")
        records = _handoff_records(chain.runtime)
        persisted = dict(records.get(emitted_id) or {})
        remembered = _experience_count(memory)
        summary["positive"] = {
            "handoff_id": emitted_id,
            "persisted": bool(persisted),
            "consumer_disposition": emitted.get("disposition"),
            "experiences": remembered,
        }
        checks["real_owner_handoff_admitted"] = bool(
            emitted_id and persisted and emitted.get("disposition") == "ACCEPT"
            and remembered == baseline_memory + 1
            and len(chain.memory_port.recent(limit=10)) == 1)

        # 4. 负控：同一扇门，入 M0 之前具名拒绝。
        maintenance_receipt = {
            "handoff_id": "lexp:maintenance%s" % execution_id[-8:],
            "correlation_id": "corr:maintenance-boundary",
            "activity_revision": 1,
            "activity_kind": "POSE",
            "terminal_or_milestone_kind": "SIGNIFICANT_PROGRESS",
            "decision_refs": [],
            "adoption_refs": [],
            "action_refs": [execution_id],
            "settlement_refs": [],
            "progress_refs": [],
            "completion_evidence_ref": None,
            "fact_refs": [],
            "artifact_refs": [],
            "world_refs": ["world:pose:%s" % POSE_TARGET],
            "occurred_at": "2026-10-29T02:00:00+00:00",
            "selected_candidate": "cand:maintenance-boundary",
        }
        no_activity = consumer.receive_experience_handoff(dict(maintenance_receipt))

        fabricated = dict(maintenance_receipt,
                          handoff_id="lexp:forgedactv0001",
                          activity_ref="actv_boundary_positive",
                          decision_refs=["dec:forged"],
                          adoption_refs=["adp:forged"])
        fabricated_out = consumer.receive_experience_handoff(fabricated)

        # 真实存在的 Activity，但 owner 没有这条 handoff。
        real_activity = str(persisted.get("activity_ref") or "")
        unknown_id = dict(fabricated, handoff_id="lexp:unknownhandoff0001",
                          activity_ref=real_activity)
        unknown_out = consumer.receive_experience_handoff(unknown_id)

        # 抄真实 handoff 的 id，把来源/目标改掉（塞进维护动作）。
        tampered = dict(persisted)
        tampered["action_refs"] = list(tampered.get("action_refs") or []) + [execution_id]
        tampered_out = consumer.receive_experience_handoff(tampered)

        after_negative = _experience_count(memory)
        summary["negative"] = {
            "no_activity": [no_activity.get("disposition"), no_activity.get("reason")],
            "fabricated_activity": [fabricated_out.get("disposition"),
                                    fabricated_out.get("reason")],
            "unknown_handoff_id": [unknown_out.get("disposition"), unknown_out.get("reason")],
            "tampered_real_id": [tampered_out.get("disposition"), tampered_out.get("reason")],
            "experiences": after_negative,
        }
        checks["forgeries_refused_before_m0"] = bool(
            no_activity.get("reason") == "LIFE_ACTIVITY_REF_MISSING"
            and fabricated_out.get("reason") == "HANDOFF_SOURCE_NOT_FOUND"
            and unknown_out.get("reason") == "HANDOFF_SOURCE_NOT_FOUND"
            and tampered_out.get("reason") == "HANDOFF_SOURCE_MISMATCH"
            and all(entry[0] == "IGNORE" for entry in (
                summary["negative"]["no_activity"],
                summary["negative"]["fabricated_activity"],
                summary["negative"]["unknown_handoff_id"],
                summary["negative"]["tampered_real_id"]))
            and after_negative == remembered)

        # 真实 handoff 原样重投：只 DUPLICATE。
        replay = consumer.receive_experience_handoff(dict(persisted))
        summary["replay"] = {"disposition": replay.get("disposition"),
                             "experiences": _experience_count(memory)}
        checks["replay_is_duplicate"] = bool(
            replay.get("disposition") == "DUPLICATE"
            and summary["replay"]["experiences"] == remembered)

        # 5. 正规停止后同 home 重开：世界效果仍在，Memory 不增，合法交接仍可读回。
        pose_at_stop = _pose(environment)
        service.stop()
        service = _WorldService(world_home, environment, world.world_id)
        environment["WORLD_BODY_SOCKET"] = str(service.socket_path)
        pose_after_restart = _pose(environment)

        transitions_before = len(chain.runtime.transitions())
        chain.runtime.close()
        fresh_port = LifeExperiencePort(str(chain.evidence_db),
                                        controls_db=str(chain.controls_db), subject="chiyo")
        fresh = MemoryAdmissionExperienceConsumer(port=fresh_port, enabled=True)
        runtime2 = lrp.build_production_life_runtime(
            odl.life_state_root(instance),
            kill_switches=ag2.LifeRuntimeKillSwitches(
                life_runtime_enabled=True, agency_enabled=True,
                action_execution_enabled=True, proactive_enabled=False),
            install_root=instance / "life", experience_consumer=fresh)
        reread = runtime2.persisted_experience_handoff(emitted_id)
        replay_after = fresh.receive_experience_handoff(dict(persisted))
        final_count = _experience_count(memory)
        transitions_after = len(runtime2.transitions())
        summary["restart"] = {
            "pose_persisted": pose_after_restart.get("pose") == pose_at_stop.get("pose"),
            "world_revision": pose_after_restart.get("world_revision"),
            "handoff_rereadable": bool(reread),
            "verifier_wired": bool(fresh.describe().get("source_verified")),
            "replay_disposition": replay_after.get("disposition"),
            "experiences": final_count,
            "transitions_added": transitions_after - transitions_before,
        }
        checks["restart_keeps_world_without_new_memory"] = bool(
            summary["restart"]["pose_persisted"]
            and summary["restart"]["handoff_rereadable"]
            and summary["restart"]["verifier_wired"]
            and replay_after.get("disposition") == "DUPLICATE"
            and final_count == remembered
            and transitions_after == transitions_before)

        summary["consumer_final"] = consumer.describe()
        return 0 if all(checks.values()) else 1
    except Exception as exc:  # noqa: BLE001 - 冒烟要把失败原因完整交出去
        summary["error"] = "%s: %s" % (type(exc).__name__, exc)
        return 1
    finally:
        if previous_gate is None:
            os.environ.pop(GATE_ENV, None)
        else:
            os.environ[GATE_ENV] = previous_gate
        try:
            service.stop()
        except Exception:
            pass
        if runtime2 is not None:
            try:
                runtime2.close()
            except Exception as exc:
                summary["runtime2_close_error"] = "%s: %s" % (type(exc).__name__, exc)
        try:
            harness.close_chain(chain)
        except Exception as exc:
            summary["chain_close_error"] = "%s: %s" % (type(exc).__name__, exc)
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
