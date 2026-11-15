#!/usr/bin/env python3
"""双人书桌预览夹具：一份全新的本地合成根，全部走既有正常构造函数。

    python scripts/desk_presence_fixture.py --port 3853 [--root /tmp/...]

这里没有手写的 canonical Activity、World 位置或阅读返回值：

* 真实 Life Supply owner（``tests.test_one_day_life.Services``，线程内落盘）跑在自己的
  unix socket 上；
* ``one_day_life.prepare`` 用 owner 自己的命令建实例、作品 A、私人事项文档、多页读物和
  真实 World home（``init_instance`` 写 world_state.json 与 Body 基座）；
* 真实 ``world_body_service`` 作为 World/Body gateway 起在实例自己的 world home 上；
* 第二件作品 B 由 ``LifeArtifactPort.create_artifact`` 写入；
* 一个 ACTIVE 前台 Activity 由 LR-2 ``ActivityCommandService.start_activity`` 真实提交，
  ``origin_ref=B``、``subject_refs=[B, 版本]``；
* 阅读到第 2 页与一条 unclear 页注、短笺与明日便签都走现有 owner 写口。

不启动模型、不读外部 secrets、不复用历史 root。根目录冻结保留，脚本不删除任何文件。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "components" / "world" / "service" / "bridge"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "tests"), str(ROOT / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from components.phone import capability as phone_capability  # noqa: E402
from components.phone import server as phone_server  # noqa: E402
from components.phone import service as phone_service  # noqa: E402

import activity_admission as adm  # noqa: E402
import activity_continuity as ac  # noqa: E402
import life_artifact_port as lap  # noqa: E402
import one_day_life as odl  # noqa: E402

SUBJECT = "chiyo"
DECISION_REF = "dec:desk-presence-fixture"
ACTIVITY_ID = "actv:desk-presence"
START_KEY = "desk-presence-start"
READING_TITLE = "窗边的合成读物"
READING_PAGES = 6
#: 操作人自己的分页标记（与 ``phone_reading.PAGE_MARKER`` / ``one_day_life`` 同值）。
PAGE_MARKER = "%%PAGE%%"
READING_CONTENT = PAGE_MARKER.join(
    "第%d页：窗外的潮声，合上又翻开。" % index for index in range(1, READING_PAGES + 1))
WORK_A_TITLE = "窗边手稿"
WORK_A_TEXT = "作品 A 的合成正文。"
WORK_B_TITLE = "另一件新作品"
WORK_B_TEXT = "作品 B 的合成正文，比 A 新，正被前台续写。"
SHARE_DRAFT_TEXT = "草稿：窗边那一段再改改。"
SHARE_DEFERRED_TEXT = "留着没说的一句。"
SHARE_DEFER_CONDITION = "等她有空的那天"
TOMORROW_TITLE = "明天把窗边的便签写完"


class _DecisionReader:
    def get_decision(self, decision_id: str):
        if decision_id != DECISION_REF:
            return None
        return {"decision_id": decision_id, "revision": 1,
                "snapshot_fingerprint": "fp:" + decision_id}


class _AdoptionReader:
    """The two ports LR-2's admission issuer really reads; grants are explicit test facts."""

    def __init__(self) -> None:
        self.grants: dict[str, tuple[str, str]] = {}

    def grant(self, command_id: str, mutation_kind: str, activity_ref: str) -> None:
        self.grants[command_id] = (mutation_kind, activity_ref)

    def get_adoption_by_id(self, adoption_id: str):
        command_id = adoption_id.split(":", 1)[1] if ":" in adoption_id else ""
        granted = self.grants.get(command_id)
        if granted is None:
            return None
        mutation_kind, activity_ref = granted
        return {
            "adoption_id": adoption_id, "decision_ref": DECISION_REF,
            "decision_revision": 1, "adoption_status": "APPLIED", "revision": 1,
            "authorized_mutation_kind": mutation_kind,
            "authorized_command_id": command_id,
            "created_activity_ref": activity_ref,
            "target_activity_ref": activity_ref,
            "authorized_activity_ref": activity_ref,
        }


def _write_business_clock(state_root: Path, day: str = "2026-11-11") -> Path:
    """An explicit compressed-day business clock: the instance's own existing mechanism.

    Without it ``today_business_day`` falls back to the machine clock, and two GETs one second
    apart would legitimately report different ``logical_at`` values.  The *day* is what this
    desk face compares (next local day for tomorrow items), so the fixture pins it.
    """

    path = state_root / "fastday" / "fastday_clock.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "kind": "explicit_compressed_demo_clock", "schema_version": 1,
        "mode": "COMPRESSED_DEMO", "rate": 12.0, "state": "STOPPED",
        "domain_id": "fastday:run:desk", "run_id": "run:desk",
        "logical_anchor": day + "T00:00:00+08:00",
        "logical_checkpoint": day + "T12:00:00+08:00",
        "horizon_logical": "2026-11-13T00:00:00+08:00",
    }, ensure_ascii=False), encoding="utf-8")
    return path


def _version_ref(head_version_id: str) -> str:
    head = str(head_version_id or "")
    if head.startswith("artifact-version:"):
        head = head[len("artifact-version:"):]
    return "artifactversion:%s" % head if head else ""


def _start_work_activity(state_root: Path, *, artifact_id: str,
                         head_version_id: str = "") -> dict:
    """One real LR-2 START for work B; the owner writes canonical_life_state.json."""

    adoptions = _AdoptionReader()
    issuer = adm.build_admission_issuer(_DecisionReader(), adoptions,
                                        runtime_epoch="desk-presence-fixture")
    lease = ac.ActivityAuthorityLease(state_root)
    lease.acquire()
    try:
        store = ac.CanonicalActivityStore(state_root,
                                          namespace=ac.NAMESPACE_ISOLATED_TEST,
                                          _admission_issuer=issuer)
        capability = lease.issue_capability(namespace=ac.NAMESPACE_ISOLATED_TEST)
        commands = ac.ActivityCommandService(store=store, lease=lease,
                                             capability=capability)
        state, _journal = store.load_verified_state_and_journal()
        adoptions.grant(START_KEY, ac.TRANSITION_START, ACTIVITY_ID)
        subject_refs = [artifact_id]
        version = _version_ref(head_version_id)
        if version:
            subject_refs.append(version)
        result = commands.start_activity(
            activity_kind="CONTINUE_PERSONAL_ARTIFACT",
            title="继续《%s》" % WORK_B_TITLE,
            origin_ref=artifact_id,
            source_refs=[artifact_id],
            subject_refs=subject_refs,
            expected_revision=int(state["revision"]),
            idempotency_key=START_KEY,
            decision_ref=DECISION_REF,
            adoption_ref="adp:" + START_KEY,
            activity_id=ACTIVITY_ID,
        )
        return dict(result.activity or {})
    finally:
        lease.release()


def _commands(state_root: Path):
    adoptions = _AdoptionReader()
    issuer = adm.build_admission_issuer(_DecisionReader(), adoptions,
                                        runtime_epoch="desk-presence-fixture")
    lease = ac.ActivityAuthorityLease(state_root)
    lease.acquire()
    store = ac.CanonicalActivityStore(state_root, namespace=ac.NAMESPACE_ISOLATED_TEST,
                                      _admission_issuer=issuer)
    capability = lease.issue_capability(namespace=ac.NAMESPACE_ISOLATED_TEST)
    commands = ac.ActivityCommandService(store=store, lease=lease, capability=capability)
    return adoptions, store, commands, lease


def switch_to_reading_activity(state_root: Path, *, reading_id: str,
                               private_items_id: str) -> dict:
    """Abandon B and start a real reading Activity; used to prove no fallback to bound work."""

    adoptions, store, commands, lease = _commands(state_root)
    try:
        state, _journal = store.load_verified_state_and_journal()
        adoptions.grant("desk-presence-abandon", ac.TRANSITION_ABANDON, ACTIVITY_ID)
        commands.abandon_activity(
            activity_id=ACTIVITY_ID, expected_revision=int(state["revision"]),
            idempotency_key="desk-presence-abandon", source_refs=[DECISION_REF],
            decision_ref=DECISION_REF, adoption_ref="adp:desk-presence-abandon",
            reason_code="SPONTANEOUS_RECONSIDERATION")
        state, _journal = store.load_verified_state_and_journal()
        adoptions.grant("desk-presence-reading-start", ac.TRANSITION_START,
                        "actv:desk-reading")
        result = commands.start_activity(
            activity_kind="READ_PERSONAL_MATERIAL", title="读一份自己的材料",
            origin_ref=reading_id, source_refs=[reading_id],
            subject_refs=[reading_id, private_items_id],
            expected_revision=int(state["revision"]),
            idempotency_key="desk-presence-reading-start", decision_ref=DECISION_REF,
            adoption_ref="adp:desk-presence-reading-start",
            activity_id="actv:desk-reading")
        return dict(result.activity or {})
    finally:
        lease.release()


def _create_work_b(port: lap.LifeArtifactPort, data: dict) -> dict:
    outcome = port.create_artifact(
        workspace_id=str(data["workspace_id"]), title=WORK_B_TITLE,
        content=WORK_B_TEXT, grant_id=str(data["artifact_grant_id"]),
        operation_key=lap.operation_key_for("desk-presence-fixture", "work-b", SUBJECT))
    if not outcome.applied:
        raise RuntimeError("WORK_B_CREATE_REFUSED: %s" % (outcome.to_dict(),))
    return {"artifact_id": str(outcome.artifact_id),
            "head_version_id": str(outcome.version_id or outcome.head_version_id or "")}


def _read_to_second_page(service: phone_service.PhoneService) -> dict:
    """Read the real Supply material to page 2 through the formal position/annotation ports."""

    first = service.reading_material()
    if not first.get("ok"):
        raise RuntimeError("READING_MATERIAL_UNAVAILABLE: %s" % (first,))
    data = first["data"]
    revision = str(data.get("source_revision") or "")
    position = service.reading_position(
        2, revision, str(data.get("private_head") or ""),
        "desk-presence-position-2")
    if not position.get("ok"):
        raise RuntimeError("READING_POSITION_REFUSED: %s" % (position,))
    second = service.reading_material()
    annotation = service.reading_annotate(
        "unclear", 2, revision, str(second["data"].get("private_head") or ""),
        "desk-presence-unclear-2", note="合成页注：这里没看懂。")
    if not annotation.get("ok"):
        raise RuntimeError("READING_ANNOTATION_REFUSED: %s" % (annotation,))
    return {"source_revision": revision, "page": 2, "pages": data.get("pages")}


def _seed_notes(service: phone_service.PhoneService, work_a: str, state_root: Path) -> dict:
    """Real DRAFT + DEFERRED shares and one next-business-day FutureItems entry."""

    draft = service.share_prepare(source_artifact_id=work_a, share_text=SHARE_DRAFT_TEXT,
                                  operation_key="desk-presence-draft")
    if not draft.get("ok"):
        raise RuntimeError("SHARE_DRAFT_REFUSED: %s" % (draft,))
    deferred = service.share_prepare(source_artifact_id=work_a,
                                     share_text=SHARE_DEFERRED_TEXT,
                                     operation_key="desk-presence-draft-2")
    if not deferred.get("ok"):
        raise RuntimeError("SHARE_DEFERRED_REFUSED: %s" % (deferred,))
    acted = service.share_action(
        share_id=str(deferred["data"]["share_id"]), action="defer",
        expected_head=str(deferred["data"]["head_version_id"]),
        defer_condition=SHARE_DEFER_CONDITION, operation_key="desk-presence-defer")
    if not acted.get("ok"):
        raise RuntimeError("SHARE_DEFER_REFUSED: %s" % (acted,))

    from life_future_items import FutureItems
    from components.phone import desk_presence_view as desk

    clock = phone_service.today_business_day(state_root)
    next_day = desk.next_business_day(clock)["local_day"]
    due_at = "%sT09:00:00%s" % (next_day, desk.next_business_day(clock)["timezone_offset"])
    item = FutureItems(state_root, subject_id=SUBJECT).propose(
        title=TOMORROW_TITLE, due_at=due_at)
    return {"draft_share_id": str(draft["data"]["share_id"]),
            "deferred_share_id": str(deferred["data"]["share_id"]),
            "tomorrow_item_ref": item["item"]["item_ref"],
            "tomorrow_due_at": due_at}


def build_stack(root: Path | str, *, port: int = 0, start_world: bool = True,
                capability: object = None, seed_notes: bool = True) -> SimpleNamespace:
    """Create one fresh root with the real owners and return the wired phone stack.

    The caller owns ``root`` and must pass a brand-new directory; nothing here deletes it.
    """

    root = Path(str(root))
    if root.exists():
        if not root.is_dir() or any(root.iterdir()):
            raise RuntimeError("ROOT_EXISTS: pass a fresh empty root")
    else:
        root.mkdir(parents=True)

    from test_one_day_life import Services, WorldGateway

    services = Services(root)
    stack = SimpleNamespace(root=root, services=services, world=None, server=None,
                            thread=None, capability=capability)
    try:
        instance_dir = root / "one-day"
        prepared = odl.prepare(
            instance=instance_dir, supply_socket=str(services.supply_path),
            governance_socket=str(services.gov_path), subject=SUBJECT,
            title=WORK_A_TITLE, seed_content=WORK_A_TEXT, world_theme="coastal_town",
            world_seed=929711, reading_title=READING_TITLE,
            reading_content=READING_CONTENT, create_private_items=True,
            create_work_artifact=True)
        data = odl.read_instance(instance_dir)
        state_root = Path(str(data["life_state_root"]))
        stack.instance_dir = instance_dir
        stack.data = data
        stack.state_root = state_root
        _write_business_clock(state_root)
        stack.work_a = str(data["work_artifact_id"])

        supply_port = lap.LifeArtifactPort(str(services.supply_path), SUBJECT)
        stack.port = supply_port
        work_b = _create_work_b(supply_port, data)
        stack.work_b = work_b["artifact_id"]
        stack.work_b_head = work_b["head_version_id"]
        stack.activity = _start_work_activity(
            state_root, artifact_id=stack.work_b, head_version_id=stack.work_b_head)

        phone = {
            "schema_version": phone_service.SCHEMA_INSTANCE,
            "kind": phone_service.KIND_INSTANCE, "name": root.name, "subject": SUBJECT,
            "local_owner_uid": int(os.geteuid()), "profile_home": str(root),
            "root": str(root), "world_home": str(data["world_home"]),
            "world_id": str(data["world_id"]), "world_package": str(data["world_package"]),
            "supply_socket": str(services.supply_path),
            "private_items_artifact_id": str(data["private_items_artifact_id"]),
            "reading_artifact_id": str(data["reading_artifact_id"]),
            "artifact_grant_id": str(data["artifact_grant_id"]),
            "workspace_id": str(data["workspace_id"]),
            "work_artifact_id": stack.work_a,
            "world_body_socket": str(Path(str(data["world_home"])) / "run" / "gateway.sock"),
            "features": {},
        }
        stack.instance = phone
        stack.reading = _read_to_second_page(
            phone_service.PhoneService(phone, environment=dict(data["environment"]),
                                       world_subject=SUBJECT))

        if start_world:
            stack.world = WorldGateway(Path(str(data["world_home"])),
                                       world_id=str(data["world_id"]),
                                       environment=odl.instance_environment(instance_dir))

        stack.service = phone_service.PhoneService(
            phone, environment=dict(data["environment"]), world_subject=SUBJECT)
        stack.service._port = lambda: supply_port
        if seed_notes:
            stack.notes = _seed_notes(stack.service, stack.work_a, state_root)

        if capability is None:
            try:
                _path, capability = phone_capability.issue(
                    root, subject=SUBJECT, endpoint="http://127.0.0.1:%d" % int(port))
            except phone_capability.CapabilityError:
                capability = None
            stack.capability = capability
        stack.csrf_token = os.urandom(16).hex()
        stack.server = phone_server.create_server(
            stack.service, csrf_token=stack.csrf_token, port=int(port),
            capability=stack.capability)
        stack.port_number = stack.server.server_address[1]
        stack.base = "http://127.0.0.1:%d" % stack.port_number
        return stack
    except BaseException:
        if stack.world is not None:
            stack.world.stop()
        services.stop()
        raise


def serve_stack(stack: SimpleNamespace) -> None:
    stack.thread = threading.Thread(
        target=stack.server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True)
    stack.thread.start()


def close_stack(stack: SimpleNamespace) -> None:
    server = getattr(stack, "server", None)
    if server is not None:
        server.shutdown()
        server.server_close()
    thread = getattr(stack, "thread", None)
    if thread is not None:
        thread.join(timeout=10)
    world = getattr(stack, "world", None)
    if world is not None:
        world.stop()
    services = getattr(stack, "services", None)
    if services is not None:
        services.stop()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="desk_presence_fixture")
    parser.add_argument("--port", type=int, default=3853)
    parser.add_argument("--root", default="")
    args = parser.parse_args(argv)
    root = (Path(args.root) if args.root
            else Path(tempfile.gettempdir()) / ("nyairo-desk-presence-%d-%d" % (
                os.getpid(), time.time_ns())))
    stack = build_stack(root, port=int(args.port))
    server = stack.server
    if server.server_address[1] != int(args.port):
        close_stack(stack)
        parser.error("PORT_BIND_FAILED")
    sys.stdout.write(json.dumps({
        "ok": True, "root": str(stack.root), "port": stack.port_number,
        "capability_issued": stack.capability is not None,
        "capability_path": str(phone_capability.capability_path(stack.root)),
        "work_a": stack.work_a, "current_work": stack.work_b,
        "current_work_head": stack.work_b_head,
        "reading_page": stack.reading["page"], "reading_pages": stack.reading["pages"],
        "endpoints": ["/api/phone/desk/presence", "/api/phone/desk/notes"],
    }, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    serve_stack(stack)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        close_stack(stack)
        return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
