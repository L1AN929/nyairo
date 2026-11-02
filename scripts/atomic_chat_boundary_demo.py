#!/usr/bin/env python3
"""场景 2：她正在做一件不能被打断的事时，普通来话仍然得到一句本地回复。

这一版修的是上一版真正缺的三件事：**生产装配里没有回应实现**（每条消息 только
``LOCAL_REPLY_NOT_WIRED``）、**回应正文只被计字数后丢掉**（没有消费者）、**消息与动作在同一个
线程**（步骤阻塞时消息只能等下一 tick）。

演示的一条链（全部本机，0 产品模型、0 外网、不碰旧实例）
----------------------------------------------------------
0. 走正式工厂 ``resolve_local_contacts``：这个夹具 profile **没有可用模型配置**，所以回复
   接线如实返回 ``LOCAL_REPLY_MODEL_NOT_CONFIGURED``；resident 自己的消息 lane 处理完这条消息，
   结果里 ``responded=False``、回复库里没有正文——不编一句话。
1. 真实 Supply owner + 真实 production Life runtime + ``assemble_autonomous_life``，
   一轮脚本 Decision 让 AG-1→AG-2→AR-0 真的 START 一件作品，写出真版本。
2. 用 LR-3 自己的 ``configure_activity_attention`` 把这个 ACTIVE Activity 标成 ATOMIC。
3. 把真实 AR-0 的文件适配器换成一层受控阻塞壳：下一轮 CONTINUE 在执行缝里真的停在途，
   ``entered``/``release`` 两个同步事件把"步骤没结束"变成可判定的事实（有界 30 秒）。
4. 步骤在途时，用**正式本地 CLI** 投递一条普通消息（子进程跑 ``scripts/local_reply_cli.py send``），
   由**resident 自己的消息 lane**（独立线程，只消费普通消息）认领、调用正式装配里的 responder
   （最底层 client 是脚本替身）、把这一句话存进消息 owner 的回复档案。
   不用 demo 主线程 tick，也不往 ``social_contacts.responder`` 赋值。
5. 用同步事件证明回应发生在步骤释放之前；同时 Activity revision/状态/前台不变、在途 Action 仍唯一。
6. 原子期间显式 PAUSE 只到 ``PAUSE_AT_BOUNDARY`` / 登记待处理，Activity 不被立即暂停。
7. 释放后步骤正常收尾：一次 owner 回执、一个版本，聊天没有造成重新提交或结果丢失。
8. 同一条消息重复投递（同一个 operation_key）不会生成第二句；回复器调用次数不变。
9. 预算拒绝：真实请求账本（上限 0）下 responder 具名 ``LOCAL_REPLY_BUDGET_REFUSED``，
   不假装回过，回复库里也没有正文。
10. 关掉 lane 后，用**新进程的 CLI**（子进程）按消息 id 取回同一句本地回复：正文非空，
    但没有重新生成（client 调用次数不变、``recorded_at`` 不变）。
11. 正控：ATOMIC 边界退出后，普通消息仍然得到回复（这套保护不会把全体消息冻住）。

边界：Decision 与回复都是脚本替身，不证明真实模型自然采用；"回应"是本机保存的一句话，
不是用户已收到/已读（``delivered_to_user=False``），没有外发；不是 G1，也不是 24h。
所有根都用 mkdtemp 建好保留，不删。

Run (WSL/POSIX)::

    PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/atomic_chat_boundary_demo.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Optional

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "phone"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "tests"), str(ROOT / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

os.environ.setdefault("NYAIRO_RESULT_METHOD_FIXTURE_ROOT",
                      "/tmp/nyairo-atomic-chat-fixtures")

import activity_continuity as ac  # noqa: E402
import activity_interruption as ai  # noqa: E402
import autonomous_life_assembly as assembly  # noqa: E402
import autonomous_life_resident as resident  # noqa: E402
import life_local_contact as lc  # noqa: E402
import result_method_behavior_smoke as base  # noqa: E402

TS0 = "2026-11-20T09:00:00+00:00"      # 17:00 Asia/Shanghai
SUBJECT_PROBE = "test:result-method"

#: 普通来话的正文：只在投递/处理时用，绝不出现在任何输出或视图里。
CHAT_BODY = "原子聊天演示里的普通一句话，不输出正文。"

FAILURES: list[str] = []


def say(text: str = "") -> None:
    print(text)
    sys.stdout.flush()


def check(name: str, condition: Any, detail: Any = None) -> bool:
    ok = bool(condition)
    say("CHECK %-70s %s" % (name, "PASS" if ok else "FAIL"))
    if not ok:
        FAILURES.append(name)
        if detail is not None:
            say("  detail: " + json.dumps(detail, ensure_ascii=False, default=str)[:1200])
    return ok


def wait_until(predicate: Callable[[], bool], *, timeout: float = 25.0,
               interval: float = 0.05) -> bool:
    deadline = time.monotonic() + float(timeout)
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


# ---------------------------------------------------------------------------------------------
# chain helpers (the same real-owner fixture the other demos use)
# ---------------------------------------------------------------------------------------------


def candidate_set(chain):
    return chain.runtime.candidate_materializer.build_candidate_set(observed_at=TS0)


def candidate(chain, candidate_id: str) -> dict[str, Any]:
    return dict(chain.runtime.candidate_read.get_candidate(str(candidate_id)) or {})


def find_current_candidate(chain, artifact_id: str) -> Optional[str]:
    """The fresh CONTINUE_CURRENT candidate whose targets name the owner's current head."""

    listed = chain.port.list_versions(str(artifact_id), limit=50) or {}
    head_version = str((chain.port.read_head(str(artifact_id)) or {}).get("version_id") or "")
    want = head_version.split(":")[-1]
    matches: list[tuple[str, dict[str, Any]]] = []
    for ref in candidate_set(chain).candidate_refs:
        payload = candidate(chain, str(ref))
        if str(payload.get("candidate_kind") or "") == "CONTINUE_CURRENT":
            matches.append((str(ref), payload))
    if not matches:
        return None
    if want:
        for ref, payload in matches:
            if any(want in str(value) for value in (payload.get("target_refs") or [])):
                return ref
    return matches[-1][0]


def find_work_candidate(chain, artifact_id: str) -> Optional[str]:
    want = str(artifact_id).split(":")[-1]

    def matches(payload: Mapping[str, Any]) -> bool:
        refs = [str(value) for value in (payload.get("target_refs") or [])]
        return (any(want in value for value in refs)
                and not any(value.startswith("closeout:") for value in refs))

    for ref in candidate_set(chain).candidate_refs:
        payload = candidate(chain, str(ref))
        if matches(payload):
            return str(ref)
    return None


def choose(chain, decision: str, candidate_id: Optional[str], **extra) -> None:
    script = {"decision": decision, "selected_candidate_id": candidate_id,
              "deferred_candidate_ids": []}
    script.update(extra)
    chain.cognition.script = script


def run_round(chain, tag: str) -> dict[str, Any]:
    return dict(chain.cycle.evaluate_once(
        trigger_ref="evt:atomic-chat:%s" % tag, observed_at=TS0, act=True) or {})


def activity(chain, activity_id: str) -> dict[str, Any]:
    return dict(chain.runtime.activity_read.get_activity(str(activity_id)) or {})


def canonical(chain) -> dict[str, Any]:
    state, _journal = chain.runtime.activity_read.read_canonical_state(
        include_journal=False)
    return dict(state or {})


def version_numbers(chain, artifact_id: str) -> list[int]:
    listed = chain.port.list_versions(str(artifact_id), limit=50) or {}
    return sorted(int(row.get("version_number") or 0)
                  for row in (listed.get("entries") or []))


def brief(out: Mapping[str, Any]) -> dict[str, Any]:
    return {key: out.get(key) for key in (
        "outcome", "decision", "adoption_status", "activity_ref", "detail",
        "action_id", "failure_class")}


# ---------------------------------------------------------------------------------------------
# the controlled in-flight seam + the scripted bottom-level reply client
# ---------------------------------------------------------------------------------------------


class BlockingAdapter:
    """The real AR-0 file adapter behind a two-event gate -- and nothing else."""

    def __init__(self, inner: Any, *, wait_seconds: float = 30.0) -> None:
        self._inner = inner
        self._wait_seconds = float(wait_seconds)
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0
        self.entered_at: Optional[float] = None
        self.released_at: Optional[float] = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def submit(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        self.entered_at = time.monotonic()
        self.entered.set()
        got = self.release.wait(timeout=self._wait_seconds)
        self.released_at = time.monotonic()
        if not got:
            raise RuntimeError(
                "atomic chat demo: the in-flight step was never released within %.0fs"
                % self._wait_seconds)
        return self._inner.submit(*args, **kwargs)


class ScriptedReplyClient:
    """最底层 model client 的脚本替身：固定一句话，一次调用。

    它替换的只是 provider 那一层；上面（预算预留、prompt、边界、回复归档）全部是生产代码。
    """

    send_split_capable = True
    provider = "scripted"
    model = "scripted-local-reply"

    def __init__(self, text: str = "（脚本替身）在的，我先把手上这一步收完，马上回你。") -> None:
        self.text = text
        self.calls = 0

    def complete(self, *, system_prompt: str, user_prompt: str,
                 max_output_tokens: Optional[int] = None) -> Any:
        self.calls += 1
        return SimpleNamespace(text=self.text, provider=self.provider, model=self.model,
                               purpose="local_reply", latency_ms=3, status=200)


class GatedReplyClient(ScriptedReplyClient):
    """同一个底层替身，多一个受控闸门：打开后下一次调用会停在原地等人放开。

    只用来把"消息已被认领、回复还没落盘"这一段变成可判定的事实，不替代任何生产路径。
    """

    model = "scripted-local-reply-gated"

    def __init__(self, text: str = "（脚本替身）在的，我先把手上这一步收完，马上回你。") -> None:
        super().__init__(text)
        self.gate = threading.Event()
        self.entered = threading.Event()
        self.release = threading.Event()

    def complete(self, *, system_prompt: str, user_prompt: str,
                 max_output_tokens: Optional[int] = None) -> Any:
        self.calls += 1
        if self.gate.is_set():
            self.entered.set()
            if not self.release.wait(timeout=30.0):
                raise RuntimeError("gated reply client: never released within 30s")
        return SimpleNamespace(text=self.text, provider=self.provider, model=self.model,
                               purpose="local_reply", latency_ms=3, status=200)


def local_cli(state_root: str, subject: str, home: str, *args: str) -> tuple[int, dict[str, Any]]:
    """Run the real local CLI as a separate process; return (exit code, last JSON line)."""

    command = [sys.executable, "-B", str(ROOT / "scripts" / "local_reply_cli.py"),
               "--state-root", state_root, "--subject", subject, "--home", home]
    command.extend(args)
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(command, capture_output=True, text=True, timeout=180,
                          cwd=str(ROOT), env=environment)
    lines = [line for line in proc.stdout.strip().splitlines() if line.strip()]
    payload: dict[str, Any] = {}
    if lines:
        try:
            payload = json.loads(lines[-1])
        except ValueError:
            payload = {}
    return int(proc.returncode), payload


def submit_message(state_root: str, subject: str, home: str, body: str, *,
                   tag: str) -> tuple[int, dict[str, Any]]:
    """投递一条普通消息：走正式本地 CLI，而不是在进程里塞字段。"""

    return local_cli(state_root, subject, home, "send", "--text", body,
                     "--operation-key", "atomic-chat:%s" % tag)


def event_row(inbox: Any, event_ref: str) -> dict[str, Any]:
    answer = dict(inbox.get_event(str(event_ref)) or {})
    return dict(((answer.get("data") or {}).get("event")) or {})


def reply_view(inbox: Any, ref: str, *, include_text: bool = False) -> dict[str, Any]:
    answer = dict(inbox.reply(str(ref), include_text=include_text) or {})
    return dict(((answer.get("data") or {}).get("reply")) or {})


def start_lane(chain, port: Any, name: str) -> tuple[Any, dict[str, Any]]:
    """resident 自己的消息 lane：用 resident 的方法建、resident 的方法起。"""

    loop = resident.AutonomousLifeResident(state_root=chain.cycle.state_root,
                                           environment=chain.env)
    loop.assembled = {"state": "READY", "contact_port": port}
    started = dict(loop.start_message_lane() or {})
    started["lane_name"] = name
    return loop, started


# ---------------------------------------------------------------------------------------------
# the demo
# ---------------------------------------------------------------------------------------------


def main() -> int:
    chain = base.build_chain("atomic-chat-boundary")
    chain.cycle._business_clock = lambda: __import__("datetime").datetime.fromisoformat(TS0)
    if chain.cycle.social_contacts is None:
        say("FAIL: this profile assembled no local-contact port")
        return 1

    say("== 0. 生产装配的回复接线：没有可用模型配置就叫名字，不编一句话 ==")
    probe_root = Path(tempfile.mkdtemp(prefix="nyairo-local-reply-unwired-"))
    probe = assembly.resolve_local_contacts(
        chain.runtime, cycle=chain.cycle, state_root=probe_root, env=chain.env,
        subject_id=SUBJECT_PROBE)
    probe_port = probe.get("port")
    probe_reply = dict(probe.get("local_reply") or {})
    check("the formal factory builds no responder without a usable provider, and says why",
          probe.get("status") == "READY" and probe_port is not None
          and probe_reply.get("wired") is False
          and probe_reply.get("reason_code") == "LOCAL_REPLY_MODEL_NOT_CONFIGURED",
          {"status": probe.get("status"), "local_reply": probe_reply})
    loop_probe, lane_probe = start_lane(chain, probe_port, "unwired-lane")
    check("the resident's own lane starts on the assembled port",
          bool(lane_probe.get("ok")) and bool(lane_probe.get("started"))
          and loop_probe.message_lane_stats().get("running") is True,
          {"lane": lane_probe, "stats": loop_probe.message_lane_stats()})
    rc_unwired, sent_unwired = submit_message(
        str(probe_root), SUBJECT_PROBE, str(probe.get("home_ref") or ""), CHAT_BODY,
        tag="unwired")
    unwired_ref = str(sent_unwired.get("event_ref") or "")
    wait_until(lambda: event_row(probe_port.inbox, unwired_ref).get(
        "processing_state") == "PROCESSED", timeout=20.0)
    unwired_row = event_row(probe_port.inbox, unwired_ref)
    unwired_reply = reply_view(probe_port.inbox, unwired_ref)
    check("an ordinary message is processed, not answered, and no words are invented",
          rc_unwired == 0 and bool(sent_unwired.get("ok")) and unwired_ref
          and unwired_row.get("processing_state") == "PROCESSED"
          and unwired_row.get("outcome") == "OBSERVED"
          and unwired_row.get("reply_present") is False
          and unwired_row.get("outcome_reason_code") == "OBSERVED_ONLY"
          and unwired_reply.get("reply_present") is False,
          {"rc": rc_unwired, "state": unwired_row.get("processing_state"),
           "outcome": unwired_row.get("outcome"),
           "reason": unwired_row.get("outcome_reason_code"),
           "reply_present": unwired_reply.get("reply_present")})
    loop_probe.stop_message_lane()

    say("\n== 1. 正式装配接上回复：只在最底层 client 注入脚本替身 ==")
    import life_request_budget as lrb

    # 回复预留走的是**这个实例自己的**持久账本（同 state root、同 run），
    # 不另造一个独立上限的演示预算：上面的回复器和下面的决策/内容共用一本账。
    budget = assembly.request_budget(chain.env, chain.cycle.state_root)
    ledger_path = str(Path(chain.cycle.state_root) / lrb.BUDGET_FILENAME)
    scripted = ScriptedReplyClient()
    contacts = assembly.resolve_local_contacts(
        chain.runtime, cycle=chain.cycle, state_root=chain.cycle.state_root, env=chain.env,
        subject_id=str(chain.data.get("subject_id") or SUBJECT_PROBE),
        local_reply_client=scripted, local_reply_requests=budget)
    port = contacts.get("port")
    wired = dict(contacts.get("local_reply") or {})
    chain.cycle.social_contacts = port
    chain.cycle.contact_inbox = contacts.get("inbox")
    chain.cycle.co_presence = contacts.get("protocol")
    inbox = contacts.get("inbox")
    state_root = str(chain.cycle.state_root)
    home = str(contacts.get("home_ref") or "")
    subject = str(contacts.get("subject_id") or "")
    check("the production port now holds a responder over the instance's own budget",
          contacts.get("status") == "READY" and wired.get("wired") is True
          and bool((wired.get("describe") or {}).get("budgeted")) is True
          and port.describe().get("local_reply", {}).get("wired") is True
          and str((budget.view() or {}).get("path") or "") == ledger_path,
          {"local_reply": wired, "describe": port.describe().get("local_reply"),
           "budget_path": (budget.view() or {}).get("path")})
    loop, lane = start_lane(chain, port, "reply-lane")
    check("the resident's lane is the only message consumer here (main thread does not tick it)",
          bool(lane.get("ok")) and loop.message_lane_stats().get("running") is True,
          {"lane": lane})

    work = str(chain.data["artifact_id"])
    say("\n== 2. a real START writes a real version ==")
    choose(chain, "START", find_work_candidate(chain, work))
    started = run_round(chain, "start")
    activity_id = str(started.get("activity_ref") or "")
    versions_before = version_numbers(chain, work)
    check("the work Activity is ACTIVE with a real stop point and a real version",
          started.get("outcome") == "ACTED" and started.get("decision") == "START"
          and bool(activity_id)
          and activity(chain, activity_id).get("status") == ac.STATUS_ACTIVE
          and bool((started.get("step_receipt") or {}).get("version_id"))
          and len(versions_before) >= 2,
          {"out": brief(started), "versions": versions_before})
    if not activity_id:
        say("FAIL: no Activity to attach the atomic region to")
        loop.stop_message_lane()
        base.close_chain(chain)
        return 1

    say("\n== 3. the Activity really enters an ATOMIC region (LR-3 owner write) ==")
    profile = chain.runtime.interruption_coordinator.configure_activity_attention(
        activity_id=activity_id, attention_occupancy=ai.OCCUPANCY_ATOMIC,
        interruptibility=ai.INTERRUPTIBILITY_ATOMIC,
        atomic_region_ref="atomic_region:atomic-chat:1", max_atomic_duration_seconds=60,
        action_ref="action:atomic-chat-step-1")
    view = chain.runtime.interruption_coordinator.get_current_life_view()
    check("the owner reports ATOMIC attention and a BOUNDARY_DEFERRED comm availability",
          profile.attention_occupancy == ai.OCCUPANCY_ATOMIC
          and str(view.get("attention_occupancy")) == ai.OCCUPANCY_ATOMIC
          and str(view.get("communication_availability"))
          == ai.COMM_AVAILABILITY_BOUNDARY_DEFERRED,
          {"attention": view.get("attention_occupancy"),
           "availability": view.get("communication_availability")})

    say("\n== 4. one real step is in flight behind a controlled gate ==")
    blocking = BlockingAdapter(chain.runtime.file_adapter)
    chain.runtime.action_command.register_adapter(base_ag2_kind_file(), blocking)
    chain.runtime.file_adapter = blocking
    chain.runtime.artifact_action_adapter = blocking
    before = activity(chain, activity_id)
    choose(chain, "CONTINUE", find_current_candidate(chain, work))
    outcome_box: dict[str, Any] = {}

    def _round() -> None:
        try:
            outcome_box["round"] = run_round(chain, "in-flight")
        except Exception as exc:  # the thread must not swallow a real failure
            outcome_box["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:300])

    worker = threading.Thread(target=_round, name="atomic-step", daemon=True)
    worker.start()
    entered = blocking.entered.wait(timeout=30.0)
    check("the owner's step really reached the submission boundary and stopped there",
          entered, {"calls": blocking.calls, "error": outcome_box.get("error")})
    if not entered:
        blocking.release.set()
        worker.join(timeout=10.0)
        loop.stop_message_lane()
        base.close_chain(chain)
        return 1

    say("\n== 5. 步骤在途时，一条普通来话由 resident 的 lane 真的得到本地回复 ==")
    rc_mid, sent_mid = submit_message(state_root, subject, home, CHAT_BODY, tag="mid-step")
    mid_ref = str(sent_mid.get("event_ref") or "")
    # 等到"回复已归档 + 这条消息已收尾"两件事都落盘：归档先写、运输结果后写，
    # 只看其中一件会读到中间态（CLAIMED + reply_present）。
    got_reply = wait_until(
        lambda: bool(reply_view(inbox, mid_ref).get("reply_present"))
        and event_row(inbox, mid_ref).get("processing_state") == "PROCESSED", timeout=25.0)
    responded_at = time.monotonic()
    mid_row = event_row(inbox, mid_ref)
    mid_reply = reply_view(inbox, mid_ref)
    check("the formal local CLI accepted one ordinary message (queued, not an engagement)",
          rc_mid == 0 and bool(sent_mid.get("ok")) and mid_ref
          and str(sent_mid.get("processing_state") or "").upper() == "PENDING"
          and sent_mid.get("pause_requested") is False
          and int(sent_mid.get("owners_written") or 0) == 0,
          {"rc": rc_mid, "status": sent_mid.get("status"),
           "processing_state": sent_mid.get("processing_state")})
    check("the resident's own lane archived one bounded local reply while the step was parked",
          got_reply and mid_row.get("processing_state") == "PROCESSED"
          and mid_row.get("outcome") == "OBSERVED"
          and mid_row.get("reply_present") is True
          and int(mid_reply.get("reply_chars") or 0) > 0
          and mid_reply.get("text_included") is False
          and str(mid_row.get("outcome_reason_code")) == "LOCAL_REPLY_RECORDED",
          {"state": mid_row.get("processing_state"), "outcome": mid_row.get("outcome"),
           "reason": mid_row.get("outcome_reason_code"),
           "reply_chars": mid_reply.get("reply_chars")})
    check("the scripted bottom-level client was asked exactly once, and no row echoes text",
          scripted.calls == 1 and "text" not in mid_reply
          and "text" not in dict(mid_row.get("reply") or {}),
          {"calls": scripted.calls, "reply_keys": sorted(mid_reply)})

    say("\n== 6. 回应发生在步骤释放之前；Activity/前台/在途 Action 都没动 ==")
    after = activity(chain, activity_id)
    inflight = [row_ for row_ in chain.runtime.action_read.list_actions()
                if str(row_.get("activity_ref") or "") == activity_id
                and str(row_.get("status")) in {"PROPOSED", "PREPARED", "SCHEDULED",
                                                 "SUBMITTED", "ACKNOWLEDGED"}]
    open_activities = [a for a in canonical(chain).get("activities", {}).values()
                       if str(a.get("status")) in {"ACTIVE", "WAITING", "PAUSED"}]
    check("answering did not pause, switch or re-key the Activity",
          bool(before) and after.get("activity_id") == before.get("activity_id")
          and after.get("status") == ac.STATUS_ACTIVE
          and after.get("revision") == before.get("revision")
          and str(canonical(chain).get("foreground_activity_ref")) == activity_id,
          {"before": {k: before.get(k) for k in ("activity_id", "status", "revision")},
           "after": {k: after.get(k) for k in ("activity_id", "status", "revision")}})
    check("no second Activity was created and the in-flight Action is still the only one",
          len(open_activities) == 1 and len(inflight) == 1,
          {"open_activities": len(open_activities),
           "inflight_actions": [{"action_id": a.get("action_id"),
                                 "status": a.get("status")} for a in inflight]})
    check("the step was still parked at the boundary when the reply was archived",
          blocking.release.is_set() is False and worker.is_alive() is True
          and blocking.entered_at is not None,
          {"released": blocking.release.is_set(), "alive": worker.is_alive()})

    say("\n== 7. an explicit PAUSE stays at the ATOMIC safe boundary ==")
    assessments = chain.runtime.interruption_coordinator.assess_events(
        [{"event_kind": ai.EVENT_KIND_COMMUNICATION,
          "event_id": "comm_evt:atomic-chat:explicit-hold",
          "channel_ref": "channel:chat_ui",
          "message_ref": "msg:atomic-chat:explicit-hold",
          "message_class": ai.MESSAGE_CLASS_EXPLICIT_HOLD_REQUEST,
          "request_boundary_pause": True,
          "source_refs": ["evt:atomic-chat:explicit-hold"],
          "occurred_at": TS0, "observed_at": TS0}],
        apply_immediate_pause=False)
    hold = dict(assessments[-1]) if assessments else {}
    check("the explicit hold only registers a pending boundary pause; nothing is paused now",
          str(hold.get("activity_effect")) == ai.EFFECT_PAUSE_AT_BOUNDARY
          and bool(hold.get("pending_interruption_ref"))
          and activity(chain, activity_id).get("status") == ac.STATUS_ACTIVE,
          {"effect": hold.get("activity_effect"),
           "status": activity(chain, activity_id).get("status")})
    check("an ordinary message is not a pause request (the two branches stay distinct)",
          str(hold.get("communication_availability"))
          == ai.COMM_AVAILABILITY_BOUNDARY_DEFERRED
          and mid_row.get("outcome") == "OBSERVED"
          and int(mid_row.get("reply_chars") or 0) > 0,
          {"hold_availability": hold.get("communication_availability"),
           "message_reply_chars": mid_row.get("reply_chars")})

    say("\n== 8. releasing the step settles exactly once via the real owner ==")
    blocking.release.set()
    worker.join(timeout=60.0)
    final = dict(outcome_box.get("round") or {})
    versions_after = version_numbers(chain, work)
    check("the in-flight round finished as one adopted step with one owner receipt",
          worker.is_alive() is False and outcome_box.get("error") is None
          and final.get("outcome") == "ACTED"
          and str(final.get("adoption_status") or "") in {"APPLIED", "NOOP"}
          and bool((final.get("step_receipt") or {}).get("version_id")),
          {"error": outcome_box.get("error"), "round": brief(final)})
    check("the chat caused no re-submit and no lost result: one call, one new version",
          blocking.calls == 1
          and versions_after == sorted(set(versions_after))
          and versions_after[-1] == versions_before[-1] + 1,
          {"submit_calls": blocking.calls, "before": versions_before,
           "after": versions_after})
    actions_after = [a for a in chain.runtime.action_read.list_actions()
                     if str(a.get("activity_ref") or "") == activity_id]
    check("the Activity owns exactly its two steps (START + CONTINUE), no third from chat",
          len(actions_after) == 2
          and len([a for a in actions_after
                   if str(a.get("status")) in {"PROPOSED", "PREPARED", "SCHEDULED",
                                               "SUBMITTED", "ACKNOWLEDGED"}]) == 0,
          {"actions": [{"action_id": a.get("action_id"), "status": a.get("status")}
                       for a in actions_after]})

    say("\n== 9. 同一条消息重复投递：同结果，不生成第二句 ==")
    rc_dup, dup = submit_message(state_root, subject, home, CHAT_BODY, tag="mid-step")
    time.sleep(0.6)
    dup_row = event_row(inbox, mid_ref)
    summary_dup = dict((inbox.summary().get("data") or {}))
    check("the replayed submit is the same arrival, one reply, one responder call",
          rc_dup == 0 and str(dup.get("status")) == "already_applied"
          and str(dup.get("event_ref") or "") == mid_ref
          and scripted.calls == 1
          and int(summary_dup.get("replies_stored") or 0) == 1
          and dup_row.get("reply_present") is True,
          {"rc": rc_dup, "status": dup.get("status"), "event_ref_same":
           str(dup.get("event_ref") or "") == mid_ref,
           "calls": scripted.calls, "replies_stored": summary_dup.get("replies_stored")})

    say("\n== 10. 预算拒绝：不假成功，也不往回复库里写半句 ==")
    zero_budget_root = Path(tempfile.mkdtemp(prefix="nyairo-local-reply-refused-"))
    zero_budget = lrb.LifeRequestBudget(
        Path(tempfile.mkdtemp(prefix="nyairo-local-reply-zero-budget-")), utc_day_max=0)
    refused_contacts = assembly.resolve_local_contacts(
        chain.runtime, cycle=chain.cycle, state_root=zero_budget_root, env=chain.env,
        subject_id=subject, local_reply_client=ScriptedReplyClient(),
        local_reply_requests=zero_budget)
    refused_port = refused_contacts["port"]
    refused_inbox = refused_contacts["inbox"]
    rc_ref, sent_ref = submit_message(str(zero_budget_root), subject,
                                      str(refused_contacts.get("home_ref") or ""), CHAT_BODY,
                                      tag="budget-refused")
    refused_row: dict[str, Any] = {}
    # The lane's own entry point, on an isolated root: one ordinary message, no Life writes.
    for item in (dict(refused_port.tick_ordinary_messages() or {}).get("processed") or []):
        refused_row = dict(item)
    check("a budget refusal is named, nothing is archived, nothing pretends to be an answer",
          rc_ref == 0 and bool(sent_ref.get("ok"))
          and refused_row.get("outcome") == "OBSERVED"
          and refused_row.get("responded") is False
          and str((refused_row.get("response") or {}).get("reason_code"))
          == "LOCAL_REPLY_BUDGET_REFUSED"
          and reply_view(refused_inbox, str(sent_ref.get("event_ref") or "")
                         ).get("reply_present") is False,
          {"rc": rc_ref, "outcome": refused_row.get("outcome"),
           "responded": refused_row.get("responded"),
           "reply": (refused_row.get("response") or {}).get("reason_code")})

    say("\n== 11. ATOMIC 退出后普通消息照常得到回复（正控） ==")
    chain.runtime.interruption_coordinator.handle_boundary_reached({
        "boundary_ref": "atomic_exit:atomic-chat:1",
        "activity_ref": activity_id,
        "source_ref": "executor_boundary:atomic-chat:1",
        "boundary_kind": "ATOMIC_EXIT",
        "atomic_region_ref": "atomic_region:atomic-chat:1",
        "occurred_at": TS0, "observed_at": TS0, "recorded_at": TS0, "enqueued_at": TS0,
        "causal_parent_refs": [],
        "source_refs": ["atomic_exit:atomic-chat:1", "executor_boundary:atomic-chat:1"],
    })
    idle_view = chain.runtime.interruption_coordinator.get_current_life_view()
    check("the ATOMIC exit left no foreground slot held",
          canonical(chain).get("foreground_activity_ref") in (None, ""),
          {"foreground": canonical(chain).get("foreground_activity_ref"),
           "availability": idle_view.get("communication_availability")})
    rc_after, sent_after = submit_message(state_root, subject, home, CHAT_BODY,
                                          tag="after-boundary")
    after_ref = str(sent_after.get("event_ref") or "")
    got_after = wait_until(
        lambda: bool(reply_view(inbox, after_ref).get("reply_present"))
        and event_row(inbox, after_ref).get("processing_state") == "PROCESSED", timeout=25.0)
    check("with no atomic step in flight an ordinary message is still answered (positive control)",
          rc_after == 0 and bool(sent_after.get("ok")) and got_after
          and str(idle_view.get("communication_availability")) == ai.COMM_AVAILABILITY_OPEN
          and scripted.calls == 2,
          {"rc": rc_after, "reply_present": got_after,
           "availability": idle_view.get("communication_availability"),
           "calls": scripted.calls})

    say("\n== 12. 关掉 lane 后，新进程的 CLI 读回同一句：有正文，但不重新生成 ==")
    recorded_at = reply_view(inbox, mid_ref).get("recorded_at")
    stopped = loop.stop_message_lane()
    rc_read, read_payload = local_cli(state_root, subject, home, "reply",
                                      "--event-ref", mid_ref, "--include-text")
    text_len = len(str(read_payload.get("text") or ""))
    fresh_ok = False
    fresh_len = 0
    fresh_recorded = None
    try:
        fresh = lc.LocalContactInbox(Path(state_root), subject_id=subject, home_ref=home)
        fresh_view = reply_view(fresh, mid_ref, include_text=True)
        fresh_ok = bool(fresh_view.get("reply_present"))
        fresh_len = len(str(fresh_view.get("text") or ""))
        fresh_recorded = reply_view(fresh, mid_ref).get("recorded_at")
    except Exception as exc:  # a read failure is reported, never guessed
        say("  fresh inbox error: %s: %s" % (type(exc).__name__, str(exc)[:200]))
    check("a new process reads the archived reply by message id, without minting a new one",
          stopped.get("stopped") is True and loop.message_lane_stats().get("running") is False
          and rc_read == 0 and read_payload.get("reply_present") is True and text_len > 0
          and fresh_ok and fresh_len > 0 and fresh_recorded == recorded_at
          and scripted.calls == 2,
          {"stopped": stopped.get("stopped"), "rc": rc_read, "cli_chars": text_len,
           "fresh_chars": fresh_len, "calls": scripted.calls})
    check("the CLI output of a plain read carries no text (explicit --include-text only)",
          "text" not in local_cli(state_root, subject, home, "reply",
                                  "--event-ref", mid_ref)[1],
          {"keys": sorted(read_payload)})

    say("\n== 13. 正式 resident.start()/stop()：lane 随生命周期起停，回复扣实例那一本账 ==")
    formal_chain = base.build_chain("atomic-chat-formal-lifecycle")
    formal_root = str(formal_chain.cycle.state_root)
    formal_env = dict(formal_chain.env)
    # 同一个 state root 只能有一个 writer：先放掉装配夹具自己的 runtime/lease，
    # 再由正式 resident 按标准路径重新拿 lease、重新装配。这不是"未来未验"，
    # 就是真实启动路径本来的样子。
    formal_chain.runtime.close()
    formal_chain.runtime = None
    formal_client = GatedReplyClient()
    life = resident.AutonomousLifeResident(
        state_root=formal_root, environment=formal_env, act=False,
        offline_scripted=True, local_reply_client=formal_client)
    started_life = dict(life.start() or {})
    life_cycle = (life.assembled or {}).get("cycle")
    formal_port = getattr(life_cycle, "social_contacts", None)
    formal_inbox = getattr(life_cycle, "contact_inbox", None)
    life_contacts = dict((life.assembled or {}).get("contacts") or {})
    life_state_root = formal_root
    life_subject = str(life_contacts.get("subject_id") or "")
    life_home = str(life_contacts.get("home_ref") or "")
    life_ledger = str(Path(formal_root) / lrb.BUDGET_FILENAME)
    responder = getattr(formal_port, "responder", None)
    responder_desc = dict(getattr(responder, "describe", lambda: {})() or {})
    responder_budget = getattr(getattr(responder, "client", None), "budget", None)
    responder_view = dict(responder_budget.view() or {}) if responder_budget is not None else {}
    instance_view = dict(assembly.request_budget(formal_env, formal_root).view() or {})
    calls_before = int(instance_view.get("utc_day_calls") or 0)
    run_calls_before = int(instance_view.get("run_window_calls") or 0)
    check("标准 resident.start() 自己把消息 lane 起起来，正式工厂也接上了回复",
          started_life.get("ok") is True
          and (started_life.get("message_lane") or {}).get("running") is True
          and life.message_lane is not None
          and str(life.message_lane.consumer) == "local_message_lane"
          and bool((life_contacts.get("local_reply") or {}).get("wired")) is True,
          {"start": started_life.get("reason"), "lane": started_life.get("message_lane"),
           "wired": (life_contacts.get("local_reply") or {}).get("wired")})
    check("回复预算就是本实例那一本账：同一文件、同一 run、上限不是另造的 8",
          responder_desc.get("budgeted") is True
          and str(responder_view.get("path") or "") == life_ledger
          and str(instance_view.get("path") or "") == life_ledger
          and str(responder_view.get("run_id") or "") == str(instance_view.get("run_id") or "")
          and int(instance_view.get("utc_day_max") or 0) > 8,
          {"responder_path": responder_view.get("path"),
           "instance_path": instance_view.get("path"),
           "run_id": instance_view.get("run_id"),
           "utc_day_max": instance_view.get("utc_day_max")})

    # 受控的"步骤在途"：主线程外的这一个等待事件就是那段不能被打断的时间，
    # 它不是任何 Life 写入，也不产生 tick。
    step_release = threading.Event()

    def _held_step() -> None:
        step_release.wait(timeout=60.0)

    holder = threading.Thread(target=_held_step, name="bounded-atomic-step", daemon=True)
    holder.start()
    rc_life, sent_life = submit_message(life_state_root, life_subject, life_home, CHAT_BODY,
                                        tag="formal-lifecycle")
    life_ref = str(sent_life.get("event_ref") or "")
    got_life = wait_until(
        lambda: bool(reply_view(formal_inbox, life_ref).get("reply_present"))
        and event_row(formal_inbox, life_ref).get("processing_state") == "PROCESSED",
        timeout=30.0)
    life_row = event_row(formal_inbox, life_ref)
    life_reply = reply_view(formal_inbox, life_ref)
    check("步骤还被受控阻塞着的时候，正式 lane 已经存好一句回复（主线程没有 tick）",
          rc_life == 0 and bool(sent_life.get("ok")) and got_life
          and step_release.is_set() is False and holder.is_alive() is True
          and life_row.get("processing_state") == "PROCESSED"
          and life_row.get("outcome") == "OBSERVED"
          and int(life_reply.get("reply_chars") or 0) > 0
          and str(life_row.get("outcome_reason_code")) == "LOCAL_REPLY_RECORDED"
          and formal_client.calls == 1,
          {"rc": rc_life, "state": life_row.get("processing_state"),
           "reason": life_row.get("outcome_reason_code"),
           "reply_chars": life_reply.get("reply_chars"), "calls": formal_client.calls})
    step_release.set()
    holder.join(timeout=10.0)
    after_view = dict(assembly.request_budget(formal_env, formal_root).view() or {})
    check("一次脚本回复只扣同一本账一次（同 run，计数 +1）",
          int(after_view.get("utc_day_calls") or 0) == calls_before + 1
          and int(after_view.get("run_window_calls") or 0) == run_calls_before + 1
          and str(after_view.get("run_id") or "") == str(instance_view.get("run_id") or "")
          and formal_client.calls == 1,
          {"before": calls_before, "after": after_view.get("utc_day_calls"),
           "run_before": run_calls_before, "run_after": after_view.get("run_window_calls")})
    rc_dup_life, dup_life = submit_message(life_state_root, life_subject, life_home, CHAT_BODY,
                                           tag="formal-lifecycle")
    time.sleep(0.6)
    dup_view = dict(assembly.request_budget(formal_env, formal_root).view() or {})
    check("同一条消息再投一次：不生成第二句，也不扣第二次",
          rc_dup_life == 0 and str(dup_life.get("status")) == "already_applied"
          and str(dup_life.get("event_ref") or "") == life_ref
          and formal_client.calls == 1
          and int(dup_view.get("utc_day_calls") or 0) == calls_before + 1,
          {"status": dup_life.get("status"), "calls": formal_client.calls,
           "utc_day_calls": dup_view.get("utc_day_calls")})

    say("\n== 14. 主 contacts tick 与 lane 同时看到同一条消息：只允许一个认领 ==")
    formal_client.gate.set()
    formal_client.entered.clear()
    formal_client.release.clear()
    rc_two, sent_two = submit_message(life_state_root, life_subject, life_home, CHAT_BODY,
                                      tag="formal-lifecycle-claim")
    two_ref = str(sent_two.get("event_ref") or "")
    entered_two = formal_client.entered.wait(timeout=25.0)
    other_tick = dict(formal_port.tick_ordinary_messages(
        consumer="resident-contact-pass") or {})
    other_claim = dict(formal_inbox.claim(two_ref, consumer="resident-contact-pass") or {})
    check("lane 已认领并卡在生成里，主 tick 认领不到同一条（consumer 名字不是互斥证明）",
          rc_two == 0 and entered_two and bool(two_ref)
          and int(other_tick.get("processed_count") or 0) == 0
          and other_claim.get("ok") is False
          and "CLAIMED" in str(other_claim.get("reason_code") or ""),
          {"tick_processed": other_tick.get("processed_count"),
           "claim_ok": other_claim.get("ok"),
           "claim_reason": other_claim.get("reason_code")})
    formal_client.release.set()
    got_two = wait_until(
        lambda: bool(reply_view(formal_inbox, two_ref).get("reply_present"))
        and event_row(formal_inbox, two_ref).get("processing_state") == "PROCESSED",
        timeout=30.0)
    two_view = dict(assembly.request_budget(formal_env, formal_root).view() or {})
    check("闸门放开后只落一条回复、client 只被调第二次、账本只再扣一次",
          got_two and formal_client.calls == 2
          and int(two_view.get("utc_day_calls") or 0) == calls_before + 2
          and str(event_row(formal_inbox, two_ref).get("outcome_reason_code"))
          == "LOCAL_REPLY_RECORDED",
          {"calls": formal_client.calls, "utc_day_calls": two_view.get("utc_day_calls")})

    say("\n== 15. stop()：先停 lane，再放 runtime/lease ==")
    stopped_life = dict(life.stop() or {})
    check("stop() 自己把 lane 停下并把 runtime 放掉（没有残留线程读已关对象）",
          stopped_life.get("ok") is True
          and life.message_lane_stats().get("running") is False
          and life.runtime is None,
          {"stop": stopped_life.get("reason"),
           "lane": life.message_lane_stats()})
    formal_chain.services.stop()

    say("\n== summary ==")
    say("fixture root: %s" % chain.root)
    say("answered mid-step: %s" % (mid_row.get("reply_present") is True))
    say("reply before release: %s" % (
        blocking.entered_at is not None and responded_at < float(blocking.released_at or 0)))
    say("submit calls: %d" % blocking.calls)
    say("scripted reply calls: %d" % scripted.calls)
    say("lane stats: %s" % json.dumps(loop.message_lane_stats() or {}, sort_keys=True))

    loop.stop_message_lane()
    base.close_chain(chain)
    if FAILURES:
        say("\nATOMIC_CHAT_BOUNDARY_DEMO_FAILED: %s" % ", ".join(FAILURES))
        return 1
    say("\nATOMIC_CHAT_BOUNDARY_DEMO_OK")
    return 0


def base_ag2_kind_file() -> str:
    """``ACTION_KIND_FILE`` from the same owner module the runtime uses (no new constant)."""

    import life_integration_ag2 as ag2

    return ag2.ACTION_KIND_FILE


if __name__ == "__main__":
    sys.exit(main())
