"""F-154 isolated demonstration: typed source authority, governed erasure, real derived
lineage and an activated recovery, on a brand-new synthetic Memory.

Runs on a real M0 ``EvidenceStore``, the real control ledger, the real retained deletion
authority, a real ``LifeSupplyService`` on its own Unix socket with a provisioned workspace and
issued grants, the real ``LifeArtifactPort`` transport, the real ``PrivateProjectService`` /
``PersonalPracticeService`` / ``LifeSharingService`` chain and the real operator CLI -- never a
live instance, never a model, never the network.  The fixture root is kept under
``.agents/worker-runs/f154-complete-demo-<utc>-<pid>`` (override with ``FORGET_DEMO_ROOT``);
nothing is deleted or unlinked here.

What this proves, and what it deliberately does not:

1. **Typed authority, exact scope.**  ``{kind, namespace, ref}`` facts live in the retained
   authority.  A grant names one committed decision (case + seq + digest + closure); a sibling
   event or an unrelated artifact under the same owner/seq is refused by M0 and by Supply.
   The same opaque ref under another kind/namespace is a *different* source and stays visible.
2. **Governed M0 erasure.**  ``EvidenceStore.erase_payload`` has no ungoverned path: without an
   owner grant it refuses.  The body is cleared, the row identity/audit stays, and the file is
   compacted (``secure_delete`` + WAL checkpoint + ``VACUUM``).
3. **Real derived lineage.**  A practice method is created by the real practice service from a
   Memory experience, and a share draft by the real sharing service from that method.  Both go
   through ``LifeArtifactPort`` onto the owner's socket, and their source edges are written by the
   same owner commits.  The Memory event lists **neither** document; the reverse closure finds
   both (two layers) plus the reading material itself.  Titles and version bodies are cleared; an
   unrelated work of the same subject survives.
4. **Recovery is an activation gate, bound by the production launcher.**  The recovered copy is
   served by ``scripts/supply_service.py`` from the instance environment; it refuses reads *and
   new writes* until ``recovery_apply`` has swept that exact copy and written its activation
   marker, and the same launcher then serves the swept copy (erased body empty, unrelated work
   intact).  A later authority head would close it again.  Activation must bind the control
   ledger it read: omitting it is refused, and a copy with real governance rows is read,
   bound as ``VERIFIED`` and still enforced after activation.
5. **Physical / retention honesty.**  Only booleans are reported.  The pre-deletion backups are
   deliberately retained, so the case stays ``WAITING_RETENTION`` while the governed current
   copy reports ``local_erasure_complete``.
"""
from __future__ import annotations

import dataclasses
import json
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / "components" / "life" / "scripts",
              ROOT / "components" / "native",
              ROOT / "components" / "native" / "src",
              ROOT / "components" / "native" / "memory_runtime_v1",
              ROOT / "components" / "supply",
              ROOT / "scripts"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from app.evidence import EvidenceStore  # noqa: E402
from forget_request import (  # noqa: E402
    ForgetCaseStore,
    RECOVERY_STATE_FILE,
)
from forget_request_cli import validate_instance_root  # noqa: E402
from life_artifact_port import (  # noqa: E402
    ArtifactOwnerRefused,
    EffectState,
    LifeArtifactPort,
)
from lifesupply.service.life_supply import (  # noqa: E402
    LifeSupplyService,
    LifeSupplySocketServer,
)
from lifesupply.service.switches import FeatureSwitches  # noqa: E402
from life_experience_port import (  # noqa: E402
    MemoryReadUnavailable,
    build_outcome_event,
    life_conversation_id,
)
from life_memory_service import memory_service_from_env  # noqa: E402
from life_sharing_service import LifeSharingService  # noqa: E402
from lifesupply.artifact.store import ManagedArtifactStore  # noqa: E402
from memory_authority import MemoryDeletionAuthority  # noqa: E402
from memory_control import MemoryControlLedger  # noqa: E402
from personal_practice_service import PersonalPracticeService  # noqa: E402
from private_project_service import PrivateProjectService  # noqa: E402

OWNER = "chiyo"
CONVERSATION = life_conversation_id(OWNER)
FAILURES: list[str] = []

#: Synthetic bodies.  Never printed; only their presence in stored bytes is checked as a bool.
SENTINEL_M0 = "隔离演示：这条阅读经历将会被忘记。"
SENTINEL_READING = "隔离演示：这段被读到的正文必须一起消失。"
SENTINEL_PRACTICE = "隔离演示：这条做法正文将被删除。"
SENTINEL_SHARE = "隔离演示：这条分享草稿正文将被删除。"
SENTINEL_UNRELATED = "隔离演示：一件与来源无关的作品必须保留。"


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        FAILURES.append(name)
    print("%s %s%s" % ("PASS" if condition else "FAIL", name,
                       ("" if not detail else " — " + detail)))


def append_event(store: EvidenceStore, text: str, source: str,
                 refs: list[tuple[str, str]] | None = None) -> str:
    event = build_outcome_event(subject=OWNER, source_ref=source, text=text,
                                occurred_at=datetime.now(timezone.utc).isoformat(),
                                refs=refs or ())
    store.append(event)
    return event.event_id


def count_rows(path: Path) -> int:
    connection = sqlite3.connect(path)
    try:
        return int(connection.execute("SELECT count(*) FROM evidence_events").fetchone()[0])
    finally:
        connection.close()


def payload_present(path: Path, event_id: str) -> bool:
    """Boolean only: a body (not merely an erased row identity) still exists."""

    connection = sqlite3.connect(path)
    try:
        row = connection.execute(
            "SELECT payload_erased,length(content) FROM evidence_events WHERE event_id=?",
            (event_id,)).fetchone()
        if row is None:
            return False
        return (not int(row[0])) or int(row[1]) > 0
    finally:
        connection.close()


def sentinel_in_file(path: Path, sentinel: str) -> bool:
    """Whether the synthetic body is still readable in one file's bytes (never printed)."""

    try:
        if not path.is_file():
            return False
        return sentinel.encode("utf-8") in path.read_bytes()
    except OSError:
        return True


def run_cli(instance: Path, *argv: str) -> tuple[int, dict]:
    command = [sys.executable, "-B", str(ROOT / "scripts" / "forget_request_cli.py"),
               "--instance", str(instance), *argv]
    completed = subprocess.run(command, capture_output=True, text=True,
                               env=dict(os.environ), cwd=str(ROOT))
    payload: dict = {}
    for line in reversed((completed.stdout or "").strip().splitlines()):
        try:
            payload = json.loads(line)
            break
        except ValueError:
            continue
    return int(completed.returncode), payload


def copy_db(source: Path, target: Path) -> None:
    """Copy a live SQLite database page-consistently (a WAL file may be outstanding)."""

    target.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(str(source))
    try:
        dst = sqlite3.connect(str(target))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def start_supply_service(environment: dict) -> subprocess.Popen:
    """Start the *production* Supply launcher and wait for its socket."""

    process = subprocess.Popen(
        [sys.executable, "-B", str(ROOT / "scripts" / "supply_service.py")],
        env={**os.environ, **environment}, cwd=str(ROOT),
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    socket_path = Path(environment["LIFE_SUPPLY_SOCKET"])
    deadline = time.time() + 20
    while time.time() < deadline:
        if process.poll() is not None:
            detail = process.stderr.read()[-800:] if process.stderr else ""
            raise AssertionError("supply launcher exited %s: %s" % (process.returncode, detail))
        if socket_path.exists():
            return process
        time.sleep(0.05)
    stop_supply_service(process)
    raise AssertionError("supply launcher did not create its socket in time")


def stop_supply_service(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.send_signal(signal.SIGTERM)
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
    if process.stderr is not None:
        process.stderr.close()


def owner_call(socket_path: Path | str, request: dict, *, timeout: float = 20.0) -> dict:
    """One request to the real owner over its real Unix socket."""

    body = json.dumps(dict(request), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(float(timeout))
        client.connect(str(socket_path))
        client.sendall(body + b"\n")
        line = client.makefile("rb").readline(1 << 20)
    if not line:
        raise AssertionError("owner closed the connection without an answer")
    answer = json.loads(line.decode("utf-8"))
    if not isinstance(answer, dict):
        raise AssertionError("owner answer is not an object")
    return answer


class SupplyRuntime:
    """The real Life Supply owner: one canonical database, one socket, three owners.

    Subject writes in this demo go through ``LifeArtifactPort`` onto this socket -- the same
    transport the resident uses -- so a read body or a write answer is the owner's own, never a
    local store handle pretending to be one.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        # AF_UNIX paths are bounded; the fixture root can be deep, so the socket itself lives in
        # /tmp under a unique name.  Nothing is unlinked here.
        SupplyRuntime.count = getattr(SupplyRuntime, "count", 0) + 1
        self.socket_path = Path("/tmp") / ("f154-%d-%d.sock" % (os.getpid(),
                                                                SupplyRuntime.count))
        self.db_path = self.root / "life_supply.sqlite"
        self.projection_root = self.root / "projection"
        uid = os.geteuid()
        self.service = LifeSupplyService(self.root, operator_uids={uid}, service_uids={uid},
                                         switches=FeatureSwitches.all_on(),
                                         writer_instance="f154-demo")
        self.server = LifeSupplySocketServer(str(self.socket_path), self.service)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def call(self, request: dict) -> dict:
        return owner_call(self.socket_path, request)

    def port(self) -> LifeArtifactPort:
        return LifeArtifactPort(str(self.socket_path), OWNER)

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=10)
        self.service.close()


def issue_grant(runtime: SupplyRuntime, *, capability: str, scope: str, target: str,
                authority_scope: str, use_mode: str, operation_id: str) -> str:
    answer = runtime.call({"op": "issue_grant", "grant": {
        "subject": OWNER, "capability": capability, "scope": scope, "target": target,
        "authority_scope": authority_scope, "use_mode": use_mode,
        "operation_id": operation_id}})
    if answer.get("status") != "ALLOW":
        raise AssertionError("issue_grant refused: %s" % json.dumps(answer)[:300])
    return str(answer["grant_id"])


def provision_workspace(runtime: SupplyRuntime) -> str:
    grant = issue_grant(runtime, capability="PROVISION_PERSONAL_WORKSPACE",
                        scope="workspace:personal", target="workspace:%s" % OWNER,
                        authority_scope="life-supply.workspace.v1", use_mode="ONE_SHOT",
                        operation_id="issue:workspace:%s" % OWNER)
    answer = runtime.call({"op": "provision_workspace", "grant_id": grant,
                           "operation_key": "f154:workspace:%s" % OWNER,
                           "workspace": {"subject_id": OWNER}})
    if answer.get("status") != "OK":
        raise AssertionError("provision_workspace refused: %s" % json.dumps(answer)[:300])
    return str(answer["workspace"]["workspace_id"])


def main() -> int:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = Path(os.environ.get("FORGET_DEMO_ROOT") or
                (ROOT / ".agents" / "worker-runs" /
                 ("f154-complete-demo-%s-%d" % (stamp, os.getpid()))))
    instance = base / "instance"
    business = base / "memory"
    retained = base / "retained"
    coordination = base / "forget"
    supply_root = base / "supply"
    projection_root = base / "projections"
    for directory in (instance, business, retained, coordination, supply_root, projection_root):
        directory.mkdir(parents=True, exist_ok=True)
    evidence = business / "evidence.db"
    controls = business / "controls.db"
    authority = retained / "authority.db"
    cases = coordination / "cases.db"
    store = EvidenceStore(evidence)
    MemoryControlLedger(controls, evidence, owner=OWNER,
                        conversation=CONVERSATION).initialize()
    MemoryDeletionAuthority(authority, owner=OWNER, conversation=CONVERSATION).initialize()

    # The canonical Supply file the launcher will create and the operator CLI will open.  Both
    # halves are named *before* any process runs, so the CLI never opens a second writer against a
    # database the service already holds (that would install the legacy 0-arg fence triggers).
    supply_db = supply_root / "life_supply.sqlite"
    projection_root = supply_root / "projection"
    environment = {
        "NYA02_MEMORY_DB": str(evidence),
        "NYA02_MEMORY_CONTROLS_DB": str(controls),
        "NYA02_MEMORY_AUTHORITY_DB": str(authority),
        "NYA02_MEMORY_SUBJECT": OWNER,
        "NYA02_FORGET_CASE_DB": str(cases),
        "NYA02_SUPPLY_ARTIFACT_DB": str(supply_db),
        "NYA02_SUPPLY_PROJECTION_ROOT": str(projection_root),
    }
    nested = instance / "one-day"
    nested.mkdir()
    (nested / "instance.json").write_text(
        json.dumps({"schema_version": "life.one_day.v1", "environment": environment},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(nested / "instance.json", 0o600)
    validate_instance_root(instance)

    list_rc, _ = run_cli(instance, "list")
    check("CLI 入口可用：nested 配置 + 保留路径绑定通过", list_rc == 0)
    memory = memory_service_from_env(environment)

    runtime = SupplyRuntime(supply_root)
    try:
        workspace_id = provision_workspace(runtime)
        artifact_grant = issue_grant(
            runtime, capability="COMMIT_MANAGED_ARTIFACT", scope="artifact:personal",
            target="workspace:%s" % workspace_id, authority_scope="life-supply.artifact.v1",
            use_mode="REUSABLE", operation_id="issue:artifact:%s" % OWNER)
        port = runtime.port()
        projects = PrivateProjectService(port, workspace_id, artifact_grant, subject_id=OWNER)
        practice = PersonalPracticeService(projects, memory_service=memory)
        sharing = LifeSharingService(projects)

        # -- real source material the experience is about ------------------------------------
        reading = port.create_artifact(
            workspace_id=workspace_id, title="隔离演示阅读材料", content=SENTINEL_READING,
            grant_id=artifact_grant, operation_key="demo:reading:v1")
        if not reading.applied:
            raise AssertionError("reading material refused: %s/%s/%s %s"
                                 % (reading.status, reading.reason, reading.detail,
                                    json.dumps(reading.raw)[:400]))
        reading_id = str(reading.artifact_id)
        reading_version = str(reading.version_id or "")

        forgotten_id = append_event(
            store, SENTINEL_M0, "reading:artifact:demo:1",
            refs=[("artifact", reading_id), ("artifactversion", reading_version)])
        other_id = append_event(store, "隔离演示：另一条更早的经历。", "practice:demo:2")
        kept_id = append_event(store, "隔离演示：与删除无关的保留经历。", "practice:demo:keep")

        baseline = memory.list_experiences()
        check("基线：三条经历都可读", baseline["status"] == "READY"
              and baseline["data"]["count"] == 3)

        # -- real derived documents: practice from the experience, share from the practice ---
        method = practice.create_method(
            title="隔离演示做法", instruction=SENTINEL_PRACTICE, scope="阅读",
            experience_ids=[forgotten_id], operation_key="demo:practice:method")
        check("真实 Practice 服务建立方法（引用那条经历）", method.get("ok") is True)
        method_id = ""
        method_version = ""
        if method.get("ok"):
            data = dict(method.get("data") or {})
            method_id = str(data.get("artifact_id")
                            or (data.get("project") or {}).get("artifact_id") or "")
            method_version = str(data.get("version_id") or
                                 (data.get("project") or {}).get("head_version_id") or "")
        draft = sharing.prepare_share(source_artifact_id=method_id, share_text=SENTINEL_SHARE,
                                      title="隔离演示分享", operation_key="demo:sharing:prepare")
        check("真实 Sharing 服务从方法派生草稿", draft.get("ok") is True)
        share_id = ""
        share_version = ""
        if draft.get("ok"):
            data = dict(draft.get("data") or {})
            share_id = str(data.get("share_id") or data.get("artifact_id") or "")
            share_version = str(data.get("head_version_id") or data.get("version_id") or "")

        unrelated = port.create_artifact(
            workspace_id=workspace_id, title="隔离演示无关作品", content=SENTINEL_UNRELATED,
            grant_id=artifact_grant, operation_key="demo:unrelated:v1")
        if not unrelated.applied:
            raise AssertionError("unrelated work refused: %s" % unrelated.status)
        unrelated_id = str(unrelated.artifact_id)
        unrelated_version = str(unrelated.version_id or "")
    finally:
        runtime.stop()
    # The owner-side tooling below (closure, erase, recovery) opens its own canonical store, as
    # the operator CLI does; the subject-side service is stopped so no two writers overlap.  This
    # handle is a *reader*; every write below takes the owner's real writer fence first.
    supply = ManagedArtifactStore(supply_db, projection_root=projection_root, read_only=True)

    edges = supply.source_closure([
        {"kind": "memory", "namespace": "memory", "ref": forgotten_id}])
    check("真实血缘：从 Memory 经历逆向找到做法与分享（至少两层），未手列",
          edges["status"] == "OK" and method_id in edges["artifacts"]
          and share_id in edges["artifacts"],
          "closure=%s" % json.dumps(edges.get("artifacts") or [])[:160])

    # -- the pre-deletion copies, kept for the recovery check --------------------------------
    evidence_before = business / "evidence.before.db"
    controls_before = business / "controls.before.db"
    authority_before = retained / "authority.before.db"
    supply_before = supply_root / "artifacts.before.db"
    for source, target in ((evidence, evidence_before), (controls, controls_before),
                           (authority, authority_before), (supply_db, supply_before)):
        copy_db(source, target)

    # -- segment 1: typed authority ----------------------------------------------------------
    hold_rc, hold = run_cli(instance, "request", "--operation-key", "op-hold-scope",
                            "--source-ref", "reading:artifact:demo:1", "--hold",
                            "--retention-pending")
    check("hold：来源被扣留但正文未动、不写 DELETE",
          hold_rc == 0 and hold.get("status") == "HELD"
          and memory.port.read(forgotten_id) is None
          and memory.read_experience(other_id)["status"] == "READY")
    before_reingest = count_rows(evidence)
    reingested = build_outcome_event(
        subject=OWNER, source_ref="reading:artifact:demo:1:new",
        text="隔离演示：换 id 的同源重投。",
        occurred_at=datetime.now(timezone.utc).isoformat(),
        refs=[("artifact", reading_id)])
    check("hold 不是删除：删除权威 seq 仍为 0",
          MemoryDeletionAuthority(authority, owner=OWNER,
                                  conversation=CONVERSATION).projection().seq == 0)

    confirm_rc, confirmed = run_cli(instance, "confirm", "--case", hold["case_id"])
    projection = MemoryDeletionAuthority(authority, owner=OWNER,
                                        conversation=CONVERSATION).projection()
    typed = set(projection.blocked_sources)
    check("confirm：typed 来源写入保留权威（kind/namespace/ref 三元组）",
          confirm_rc == 0 and ("memory", "memory", forgotten_id) in typed
          and ("artifact", "supply", reading_id) in typed and projection.healthy)
    check("confirm：备份仍在 → WAITING_RETENTION（不冒“所有副本已擦”）",
          confirmed.get("status") == "WAITING_RETENTION"
          and confirmed.get("reason") == "FORGET_OFFLINE_BACKUP_RETAINED")
    check("逻辑读取：被忘内容不可读", memory.read_experience(forgotten_id)["status"] != "READY")
    admission = memory.port.append_outcome(reingested)
    check("删除后同源 ref 换 event id 重投在写入前被拒，M0 行数不变",
          admission["status"] == "refused" and count_rows(evidence) == before_reingest,
          "admission=%s rows=%d" % (admission.get("status"), count_rows(evidence) - before_reingest))

    # -- segment 2: physical M0 erasure ------------------------------------------------------
    row = sqlite3.connect(evidence).execute(
        "SELECT payload_erased,length(content) FROM evidence_events WHERE event_id=?",
        (forgotten_id,)).fetchone()
    check("M0 物理：行身份保留，正文清空并标记 payload_erased",
          row is not None and int(row[0]) == 1 and int(row[1]) == 0)
    check("M0 物理：主库字节不再含合成正文",
          not sentinel_in_file(evidence, SENTINEL_M0))
    check("M0 物理：未删经历仍在（布尔检查不等于全库清空）",
          payload_present(evidence, other_id) and payload_present(evidence, kept_id))

    # -- segment 3: real derived owners found by their own edges -----------------------------
    def version_erased(version_id: str) -> bool:
        view = supply.read_version(version_id) or {}
        return view.get("content") == "" and int(view.get("content_erased") or 0) == 1

    check("派生：供给版本正文被真实 owner 清空（阅读材料/做法/分享）",
          version_erased(reading_version) and version_erased(method_version)
          and version_erased(share_version))
    check("派生：无关作品版本正文与标题保留",
          (supply.read_version(unrelated_version) or {}).get("content") == SENTINEL_UNRELATED
          and "无关" in str((supply.read_artifact(unrelated_id) or {}).get("title", "")))
    check("派生：被擦作品的标题只剩不透明墓碑",
          (supply.read_artifact(reading_id) or {}).get("title") == "[erased]"
          and (supply.read_artifact(method_id) or {}).get("title") == "[erased]")
    check("派生：Supply 主库字节不再含做法/分享正文",
          not sentinel_in_file(supply_db, SENTINEL_PRACTICE)
          and not sentinel_in_file(supply_db, SENTINEL_SHARE))
    practice_binding = supply.read_projection_binding(method_id) or {}
    projection_path = projection_root / str(practice_binding.get("relative_path") or "")
    check("派生：投影文件为空（representation 不是第二份正文）",
          bool(practice_binding) and projection_path.is_file()
          and not sentinel_in_file(projection_path, SENTINEL_PRACTICE))

    # The post-deletion draft is asked of the *running owner* again: a stopped socket would make
    # the refusal meaningless, so this leg goes back over a real service on the same canonical db.
    review_runtime = SupplyRuntime(supply_root)
    try:
        blocked_draft = LifeSharingService(
            PrivateProjectService(review_runtime.port(), workspace_id, artifact_grant,
                                  subject_id=OWNER)).prepare_share(
            source_artifact_id=method_id, share_text="隔离演示：删除后再准备。",
            title="隔离演示：删除后草稿", operation_key="demo:sharing:after-delete")
    finally:
        review_runtime.stop()
    check("派生：源屏障落定后新的 Sharing 草稿被真实 owner 拒（不读已擦正文）",
          blocked_draft.get("ok") is False)

    # -- strict negative controls: a sibling's decision is not a licence ---------------------
    authority_now = MemoryDeletionAuthority(authority, owner=OWNER,
                                            conversation=CONVERSATION)
    grant = authority_now.authorize(case_id=hold["case_id"], event_ids=[forgotten_id])
    sibling_refused = False
    try:
        MemoryControlLedger(controls, evidence, owner=OWNER, conversation=CONVERSATION,
                            authority=str(authority)).erase(
            operation_id="demo:sibling", event_ids=[other_id], actor=OWNER, grant=grant)
    except PermissionError:
        sibling_refused = True
    check("严格负控：同一 owner/seq 的授权不能擦另一条事件（M0 拒且正文保留）",
          sibling_refused and payload_present(evidence, other_id))
    supply_refused = False
    fenced = ManagedArtifactStore(
        supply_db, projection_root=projection_root,
        fences_root=supply_root / "recovery" / "fences", writer_instance="f154-demo-operator")
    try:
        answer = fenced.erase_source(operation_key="demo:sibling:supply",
                                     authorized_subject_id=OWNER, grant=grant,
                                     authority=authority_now,
                                     artifact_ids=[unrelated_id])
        supply_refused = answer.get("status") == "DENY"
    except Exception:
        supply_refused = False
    finally:
        fenced.close()
    check("严格负控：同一授权不能擦闭包外的无关作品",
          supply_refused and (supply.read_version(unrelated_version) or {}).get("content")
          == SENTINEL_UNRELATED)

    # -- NO_CHANGE 携带来源：不许静默丢边；不带来源的旧重放保留 ------------------------------
    def _supply_counts() -> tuple[int, int]:
        connection = sqlite3.connect(supply_db.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            return (int(connection.execute("SELECT COUNT(*) FROM artifact_versions").fetchone()[0]),
                    int(connection.execute("SELECT COUNT(*) FROM artifact_source_edges").fetchone()[0]))
        finally:
            connection.close()

    # 同一副本上的正式 owner 再次起来（前一轮 live 端口已停），走真实 socket 而不是本地替身。
    no_change_runtime = SupplyRuntime(supply_root)
    try:
        no_change_port = no_change_runtime.port()
        unrelated_head = no_change_port.read_head(unrelated_id)
        counts_before = _supply_counts()
        # 正文与 head 完全相同，但这次带着**已删除来源**的引用：owner 必须具名拒绝，不能答
        # NO_CHANGE 让调用方以为血缘已登记。拒绝发生在任何写入之前，版本行与边都不得增加。
        no_change_refused = no_change_port.commit_version(
            artifact_id=unrelated_id, expected_head=unrelated_head["version_id"],
            content=SENTINEL_UNRELATED, grant_id=artifact_grant,
            operation_key="demo:nochange:refs",
            source_refs=[{"kind": "artifact", "namespace": "supply", "ref": reading_id}])
        check("NO_CHANGE 带已删来源：正式端口具名拒绝且版本/边零增",
              no_change_refused.status == "BLOCKED"
              and no_change_refused.reason == "SOURCE_REFS_REQUIRE_NEW_VERSION"
              and _supply_counts() == counts_before,
              "status=%s reason=%s" % (no_change_refused.status, no_change_refused.reason))
        plain_replay = no_change_port.commit_version(
            artifact_id=unrelated_id, expected_head=unrelated_head["version_id"],
            content=SENTINEL_UNRELATED, grant_id=artifact_grant,
            operation_key="demo:nochange:plain")
        check("NO_CHANGE 不带来源：旧只读重放语义保留且零写",
              plain_replay.status == "NO_CHANGE" and _supply_counts() == counts_before,
              "status=%s" % plain_replay.status)
    finally:
        no_change_runtime.stop()
    naked_refused = False
    try:
        EvidenceStore(evidence).erase_payload([other_id])
    except Exception:
        naked_refused = False
    check("严格负控：低层 EvidenceStore 无授权时不擦（返回 DENY，正文保留）",
          EvidenceStore(evidence).erase_payload([other_id]).get("status") == "DENY"
          and payload_present(evidence, other_id))
    # The same opaque id in a different kind/namespace is another source and stays visible.
    alias_event = dataclasses.replace(
        build_outcome_event(
            subject=OWNER, source_ref="reading:artifact:demo:alias",
            text="隔离演示：同名但不同种类的来源仍可写。",
            occurred_at=datetime.now(timezone.utc).isoformat(),
            refs=[("artifact", reading_id)]),
        source_refs=[{"kind": "artifact", "id": reading_id,
                      "namespace": "other-owner"}])
    alias = memory.port.append_outcome(alias_event)
    check("typed 精确：相同 ref 不同 namespace 的无关来源不被误扣留",
          alias.get("status") in ("appended", "inserted", "APPLIED", "OK", "duplicate"))

    # -- recovery: the *production launcher* refuses a recovered copy until its sweep opens it --
    recovery_dir = base / "restore-recovered"
    recovered_data = recovery_dir / "supply"
    recovered_data.mkdir(parents=True, exist_ok=True)
    recovered_socket = Path("/tmp") / ("f154-recovered-%d.sock" % os.getpid())
    recovered_evidence = recovery_dir / "evidence.db"
    recovered_controls = recovery_dir / "controls.db"
    recovered_supply_db = recovered_data / "life_supply.sqlite"
    recovered_supply_proj = recovered_data / "projection"
    copy_db(evidence_before, recovered_evidence)
    copy_db(controls_before, recovered_controls)
    copy_db(supply_before, recovered_supply_db)

    recovered_env = dict(environment)
    recovered_env.update({
        "NYA02_MEMORY_DB": str(recovered_evidence),
        "NYA02_MEMORY_CONTROLS_DB": str(recovered_controls),
        "NYA02_F154_RECOVERY_DIR": str(recovery_dir),
        "NYA02_SUPPLY_ARTIFACT_DB": str(recovered_supply_db),
        "NYA02_SUPPLY_PROJECTION_ROOT": str(recovered_supply_proj),
        "LIFE_SUPPLY_SOCKET": str(recovered_socket),
        "LIFE_SUPPLY_DATA_ROOT": str(recovered_data),
        "LIFE_SUPPLY_OPERATOR_UIDS": str(os.geteuid()),
        "LIFE_SUPPLY_SERVICE_UIDS": str(os.geteuid()),
        "LIFE_SUPPLY_WRITER_INSTANCE": "f154-demo-recovered",
        "LIFE_SUPPLY_REQUIRE_NON_ROOT": "OFF",
        # Writes are enabled so the only reason a recovered write is refused is the gate itself.
        "LIFE_SUPPLY_SWITCH_ARTIFACT_EXTERNAL_ACTION": "ON",
    })
    blocked_process = start_supply_service(recovered_env)
    try:
        blocked_port = LifeArtifactPort(str(recovered_socket), OWNER)
        blocked_read = False
        try:
            blocked_port.read_version(method_id, method_version)
        except ArtifactOwnerRefused as exc:
            blocked_read = str(exc.status) == "BLOCKED" and "RECOVERY" in str(exc.reason)
        check("恢复未激活：正式 launcher 起的 Supply 对同一副本拒读正文", blocked_read)
        blocked_answer = blocked_port.create_artifact(
            workspace_id=workspace_id, title="隔离演示：恢复前写入", content="隔离演示：恢复前写入",
            grant_id=artifact_grant, operation_key="demo:recovery:write")
        check("恢复未激活：新写也被具名拒绝（不是 UNKNOWN 效果）",
              blocked_answer.effect_state == EffectState.REFUSED
              and blocked_answer.status == "BLOCKED"
              and "RECOVERY" in blocked_answer.reason,
              "state=%s status=%s reason=%s" % (blocked_answer.effect_state,
                                                blocked_answer.status, blocked_answer.reason))
    finally:
        stop_supply_service(blocked_process)
    blocked_service = memory_service_from_env(recovered_env)
    check("恢复未激活：正式 Memory 服务具名 BLOCKED",
          blocked_service.status()["status"] in ("BLOCKED", "UNAVAILABLE")
          and "RECOVERY" in json.dumps(blocked_service.status(), ensure_ascii=False),
          "service=%s" % json.dumps(blocked_service.status())[:200])
    check("恢复未激活：备份字节仍含正文（如实暴露，不冒已擦）",
          sentinel_in_file(recovered_evidence, SENTINEL_M0))

    # -- 恢复入口：controls 副本必须真的被读取并绑定 -----------------------------------------
    # 这一半说的是「哪些 M0 正文已被忘记」。按文件名接受、从不打开它，等于把屏障交给一个
    # 未读过的文件：缺文件、坏库、别的 owner 的账本或活实例自己的账本都会被当成有效屏障。
    from forget_request import RecoveryGate  # noqa: E402  (only this leg needs the gate types)

    def _recover_with(controls_arg):
        argv = ["recover", "--recovery-dir", str(recovery_dir),
                "--recovered-evidence", str(recovered_evidence),
                "--recovered-supply", str(recovered_supply_db),
                "--recovered-projection", str(recovered_supply_proj)]
        if controls_arg is not None:
            argv += ["--recovered-controls", str(controls_arg)]
        return run_cli(instance, *argv)

    marker_path = recovery_dir / RECOVERY_STATE_FILE
    # 激活绑定的是屏障的 controls 那一半。旧 CLI 允许省略它，只凭 evidence 写出 OPEN marker，
    # 正式 runtime 也照收；现在这个入口必须具名拒绝，且不得留下 marker。
    omitted_rc, omitted = _recover_with(None)
    check("恢复入口：省略 controls 的正式入口被具名拒绝（不再只凭 evidence 激活）",
          omitted_rc == 2 and omitted.get("reason") == "RECOVERED_CONTROLS_REQUIRED"
          and not marker_path.exists(),
          "rc=%d reason=%s marker=%s" % (omitted_rc, omitted.get("reason"),
                                          marker_path.exists()))
    foreign_controls = recovery_dir / "controls.foreign.db"
    shutil.copy2(controls_before, foreign_controls)
    intruder = sqlite3.connect(foreign_controls)
    try:
        intruder.execute(
            "INSERT INTO memory_controls VALUES(?,?,?,?,?,?,?,?)",
            ("demo:intruder", "intruder", CONVERSATION, "DELETE", "evt:foreign",
             None, "fp", "2026-01-01T00:00:00+00:00"))
        intruder.commit()
    finally:
        intruder.close()
    text_controls = recovery_dir / "controls.not-a-ledger.db"
    text_controls.write_bytes(b"this is not a sqlite ledger")
    controls_cases = (("缺失文件", recovery_dir / "controls.absent.db", "RECOVERED_CONTROLS_MISSING"),
             ("坏库", text_controls, "RECOVERED_CONTROLS_UNREADABLE"),
             ("错 owner", foreign_controls, "RECOVERED_CONTROLS_OWNER_MISMATCH"),
             ("错副本（副本目录之外）", controls_before, "RECOVERED_CONTROLS_OUTSIDE_COPY"),
             ("活实例自身", controls, "RECOVERED_CONTROLS_OUTSIDE_COPY"))
    for label, controls_arg, expected in controls_cases:
        refused_rc, refused = _recover_with(controls_arg)
        check("恢复入口：%s 的 controls 在任何擦除/marker 之前被具名拒绝" % label,
              refused_rc != 0 and refused.get("ok") is False
              and str(refused.get("reason")) in (expected, "RECOVERED_CONTROLS_LIVE_PATH")
              and not marker_path.exists(),
              "rc=%d reason=%s marker=%s" % (refused_rc, refused.get("reason"),
                                             marker_path.exists()))
    link_controls = recovery_dir / "controls.linked.db"
    try:
        os.symlink(recovered_controls, link_controls)
    except OSError:
        print("SKIP 恢复入口：符号链接 controls（本文件系统不支持创建符号链接）")
    else:
        refused_rc, refused = _recover_with(link_controls)
        check("恢复入口：符号链接的 controls 在 marker 之前被具名拒绝",
              refused_rc != 0
              and str(refused.get("reason")) == "RECOVERED_CONTROLS_SYMLINK"
              and not marker_path.exists(),
              "rc=%d reason=%s" % (refused_rc, refused.get("reason")))

    sweep_rc, swept = run_cli(
        instance, "recover",
        "--recovery-dir", str(recovery_dir),
        "--recovered-evidence", str(recovered_evidence),
        "--recovered-controls", str(recovered_controls),
        "--recovered-supply", str(recovered_supply_db),
        "--recovered-projection", str(recovered_supply_proj))
    check("恢复入口（正式 CLI）：同一被扫副本完成并激活",
          sweep_rc == 0 and swept.get("status") == "RECOVERED_OPEN"
          and swept.get("derived", {}).get("artifacts"),
          "rc=%d status=%s" % (sweep_rc, swept.get("status")))
    check("恢复入口：同一副本 M0 正文被重施清理",
          not sentinel_in_file(recovered_evidence, SENTINEL_M0))
    check("恢复入口：同一副本派生版本正文被重施清理",
          not sentinel_in_file(recovered_supply_db, SENTINEL_PRACTICE))
    opened_process = start_supply_service(recovered_env)
    try:
        opened_port = LifeArtifactPort(str(recovered_socket), OWNER)
        check("恢复激活后：正式 launcher 起的 Supply 读被删版本正文为空",
              opened_port.read_version(method_id, method_version).get("content") == "")
        check("恢复激活后：同一副本未删无关作品可读",
              opened_port.read_version(unrelated_id, unrelated_version).get("content")
              == SENTINEL_UNRELATED)
        marker = json.loads((recovery_dir / RECOVERY_STATE_FILE).read_text(encoding="utf-8"))
        check("恢复激活：marker 绑定本副本与其删除权威版本",
              marker.get("status") == "OPEN"
              and str(marker.get("recovered_evidence")) == str(recovered_evidence)
              and int(marker.get("authority_seq", -1))
              == int(projection.seq))
        check("恢复激活：marker 绑定真正读过的 controls 副本与其摘要",
              Path(str(marker.get("recovered_controls") or "")).resolve()
              == recovered_controls.resolve()
              and len(str(marker.get("recovered_controls_digest") or "")) == 64
              and str(marker.get("recovered_controls_identity") or "") in ("VERIFIED", "EMPTY"),
              "identity=%s" % marker.get("recovered_controls_identity"))
        check("恢复激活：marker 同时绑定被扫的 Supply 与投影目录",
              Path(str(marker.get("recovered_supply") or "")).resolve()
              == recovered_supply_db.resolve()
              and Path(str(marker.get("recovered_projection") or "")).resolve()
              == recovered_supply_proj.resolve())
    finally:
        stop_supply_service(opened_process)
    # The marker names one controls copy.  A service that is pointed at *another* copy must not
    # ride on it, otherwise a swapped ledger would open the same swept body with different
    # forget decisions.  Both halves are driven through their production entry points.
    swapped_controls = recovery_dir.parent / "swapped-controls.db"
    shutil.copy2(recovered_controls, swapped_controls)
    swapped_env = dict(recovered_env)
    swapped_env["NYA02_MEMORY_CONTROLS_DB"] = str(swapped_controls)
    swapped_service = memory_service_from_env(swapped_env)
    check("恢复激活后：正式 Memory 服务拒绝与 marker 不符的 controls 副本",
          swapped_service.status()["status"] in ("BLOCKED", "UNAVAILABLE")
          and "RECOVERY_COPY_MISMATCH" in json.dumps(swapped_service.status(),
                                                     ensure_ascii=False),
          "service=%s" % json.dumps(swapped_service.status())[:200])
    swapped_supply_dir = recovery_dir.parent / "swapped-supply"
    swapped_supply_dir.mkdir(parents=True, exist_ok=True)
    swapped_supply_db = swapped_supply_dir / "life_supply.sqlite"
    shutil.copy2(recovered_supply_db, swapped_supply_db)
    swapped_gate = RecoveryGate(recovery_dir / RECOVERY_STATE_FILE, owner=OWNER,
                                conversation=CONVERSATION, authority_db=authority,
                                minimum_seq=int(projection.seq))
    swapped_store = ManagedArtifactStore(swapped_supply_db, read_only=True,
                                         projection_root=recovered_supply_proj,
                                         recovery_gate=swapped_gate)
    swapped_refused = False
    try:
        swapped_store.read_head(unrelated_id)
    except Exception as exc:
        swapped_refused = str(getattr(exc, "code", "") or "") == "RECOVERY_COPY_MISMATCH"
    check("恢复激活后：Supply owner 拒绝与 marker 不符的数据库副本", swapped_refused)
    recovered_service = memory_service_from_env(recovered_env)
    reingest_after_restore = recovered_service.port.append_outcome(build_outcome_event(
        subject=OWNER, source_ref="life:post-restore:reingest",
        text="隔离演示：恢复后重投同源。",
        occurred_at=datetime.now(timezone.utc).isoformat(),
        refs=[("artifact", reading_id)]))
    # The pre-deletion copy carries an older self-history epoch: an untouched older
    # self-narration row is *epoch-suppressed*, not deleted, so the positive control is a
    # genuinely new, undeleted event written after activation -- not a rewritten epoch policy.
    post_restore = build_outcome_event(
        subject=OWNER, source_ref="life:post-restore:positive",
        text="隔离演示：恢复之后写下的正常经历。",
        occurred_at=datetime.now(timezone.utc).isoformat())
    written_after_restore = recovered_service.port.append_outcome(post_restore)
    check("恢复后开放读取：被忘内容不可回读，新写正控可读，同源重投再拒绝",
          recovered_service.port.read(forgotten_id) is None
          and written_after_restore["status"] in ("inserted", "appended", "APPLIED", "OK")
          and recovered_service.port.read(post_restore.event_id) is not None
          and reingest_after_restore["status"] == "refused",
          "service=%s write=%s forgotten=%s new=%s reingest=%s"
          % (recovered_service.status().get("status"), written_after_restore.get("status"),
             recovered_service.port.read(forgotten_id),
             recovered_service.port.read(post_restore.event_id) is not None,
             reingest_after_restore.get("status")))
    retained_events = MemoryDeletionAuthority(authority, owner=OWNER,
                                             conversation=CONVERSATION).projection().blocked_events
    check("恢复后：更早但未删的自述不在保留删除集合里（既有 epoch 抑制是另一层）",
          other_id not in retained_events and forgotten_id in retained_events)

    # -- 非空 controls：真正带治理行的副本也要被读取、绑定，并在激活后继续生效 -----------------
    # 上一条正控用的是删除前的空 controls 副本（identity=EMPTY），只能证明“没有控制行”。
    # 这里用正规 MemoryControlLedger API 写一条真实 DELETE 控制（不是 SQL 伪造），再走同一个
    # 正式 CLI：要求真的读到它、把路径与摘要写进 marker，并且激活后这条控制仍然生效。
    governed_dir = base / "restore-governed"
    governed_data = governed_dir / "supply"
    governed_data.mkdir(parents=True, exist_ok=True)
    governed_evidence = governed_dir / "evidence.db"
    governed_controls = governed_dir / "controls.db"
    governed_supply_db = governed_data / "life_supply.sqlite"
    governed_proj = governed_data / "projection"
    copy_db(evidence_before, governed_evidence)
    copy_db(supply_before, governed_supply_db)
    governed_ledger = MemoryControlLedger(governed_controls, governed_evidence, owner=OWNER,
                                          conversation=CONVERSATION, authority="")
    governed_ledger.initialize()
    controlled_rc = governed_ledger.apply(
        operation_id="demo:controls:governed", kind="DELETE", target=other_id, actor=OWNER)
    check("非空 controls：正规 API 在恢复副本写入一条治理控制行（非 SQL 伪造）",
          controlled_rc == "APPLIED")
    # 这条无关经历写在控制行**之后**：既有 self-history epoch 会抑制更早的自述，不能拿一条
    # 更早的行冒充“无关内容仍可读”。
    governed_kept = build_outcome_event(
        subject=OWNER, source_ref="practice:demo:governed-kept",
        text="隔离演示：控制行之后写下的无关经历。",
        occurred_at=datetime.now(timezone.utc).isoformat())
    EvidenceStore(governed_evidence).append(governed_kept)
    governed_marker = governed_dir / RECOVERY_STATE_FILE
    governed_rc, governed = run_cli(
        instance, "recover", "--recovery-dir", str(governed_dir),
        "--recovered-evidence", str(governed_evidence),
        "--recovered-controls", str(governed_controls),
        "--recovered-supply", str(governed_supply_db),
        "--recovered-projection", str(governed_proj))
    governed_binding = dict(governed.get("recovered_controls") or {})
    check("非空 controls：正式 CLI 真的读到并绑定 VERIFIED 副本（不是只回显文件名）",
          governed_rc == 0 and governed.get("status") == "RECOVERED_OPEN"
          and governed_binding.get("identity") == "VERIFIED"
          and int(governed_binding.get("rows") or 0) == 1,
          "rc=%d identity=%s rows=%s" % (governed_rc, governed_binding.get("identity"),
                                          governed_binding.get("rows")))
    governed_state = json.loads(governed_marker.read_text(encoding="utf-8"))
    check("非空 controls：marker 绑定这份副本的路径/摘要与 Supply 副本（不是空副本）",
          governed_state.get("status") == "OPEN"
          and str(governed_state.get("recovered_controls_identity")) == "VERIFIED"
          and Path(str(governed_state.get("recovered_controls") or "")).resolve()
          == governed_controls.resolve()
          and len(str(governed_state.get("recovered_controls_digest") or "")) == 64
          and Path(str(governed_state.get("recovered_supply") or "")).resolve()
          == governed_supply_db.resolve()
          and Path(str(governed_state.get("recovered_projection") or "")).resolve()
          == governed_proj.resolve())
    check("非空 controls：同一副本的派生正文也被正式 Supply 装配重施清理",
          not sentinel_in_file(governed_supply_db, SENTINEL_PRACTICE)
          and not sentinel_in_file(governed_supply_db, SENTINEL_SHARE))
    governed_env = dict(environment)
    governed_env.update({
        "NYA02_MEMORY_DB": str(governed_evidence),
        "NYA02_MEMORY_CONTROLS_DB": str(governed_controls),
        "NYA02_F154_RECOVERY_DIR": str(governed_dir),
    })
    governed_service = memory_service_from_env(governed_env)
    governed_new = build_outcome_event(
        subject=OWNER, source_ref="life:governed:post-restore",
        text="隔离演示：非空 controls 恢复之后写下的正常经历。",
        occurred_at=datetime.now(timezone.utc).isoformat())
    governed_write = governed_service.port.append_outcome(governed_new)
    check("非空 controls 激活：服务 READY，原控制效果仍遵守，无关内容可读，新写可用",
          governed_service.status().get("status") == "READY"
          and governed_service.port.read(other_id) is None
          and governed_service.port.read(governed_kept.event_id) is not None
          and governed_write.get("status") in ("inserted", "appended", "APPLIED", "OK")
          and governed_service.port.read(governed_new.event_id) is not None,
          "service=%s controlled=%s kept=%s write=%s"
          % (governed_service.status().get("status"), governed_service.port.read(other_id),
             governed_service.port.read(governed_kept.event_id) is not None,
             governed_write.get("status")))
    # 正常的治理写只改变被绑定文件的摘要，不会变成“另一份副本”：重新打开必须是 READY，
    # 不能因为 digest 变了就报虚假的 RECOVERY_COPY_MISMATCH。许可的 authority 前进仍按既有
    # freshness 规则要求再扫（那条规则不改，也不在这里制造一次真实删除）。
    post_control_rc = MemoryControlLedger(
        governed_controls, governed_evidence, owner=OWNER, conversation=CONVERSATION,
        authority="").apply(operation_id="demo:controls:after-activation", kind="DELETE",
                            target=governed_new.event_id, actor=OWNER)
    governed_reopened = memory_service_from_env(governed_env)
    check("非空 controls 恢复后：正常治理写不触发虚假 copy mismatch，且新控制生效",
          post_control_rc == "APPLIED" and governed_reopened.status().get("status") == "READY"
          and governed_reopened.port.read(governed_new.event_id) is None,
          "apply=%s service=%s" % (post_control_rc,
                                   governed_reopened.status().get("status")))

    # -- permission / stale-generation refusals ----------------------------------------------
    wrong_owner_refused = False
    try:
        MemoryControlLedger(controls, evidence, owner=OWNER, conversation=CONVERSATION,
                            authority=str(authority)).erase(
            operation_id="demo:intruder", event_ids=[other_id], actor="intruder", grant=grant)
    except PermissionError:
        wrong_owner_refused = True
    check("权限：非 owner 的物理擦除被拒", wrong_owner_refused)
    no_grant_refused = False
    try:
        MemoryControlLedger(controls, evidence, owner=OWNER, conversation=CONVERSATION,
                            authority=str(authority)).erase(
            operation_id="demo:no-grant", event_ids=[other_id], actor=OWNER,
            barrier_seq=int(projection.seq))
    except TypeError:
        no_grant_refused = True
    except PermissionError:
        no_grant_refused = True
    check("权限：不认正整数当凭证（旧 barrier_seq 形参已不在合同里）", no_grant_refused)
    stale = MemoryDeletionAuthority(authority_before, owner=OWNER, conversation=CONVERSATION,
                                    minimum_seq=int(projection.seq)).projection()
    check("旧代际：低于 floor 的权威副本被拒（不是空记忆）",
          not stale.healthy and stale.reason == "MEMORY_AUTHORITY_OLDER_THAN_TRUSTED")
    stale_env = dict(environment)
    stale_env["NYA02_MEMORY_AUTHORITY_DB"] = str(authority_before)
    stale_env["NYA02_MEMORY_AUTHORITY_MIN_SEQ"] = str(int(projection.seq))
    stale_service = memory_service_from_env(stale_env)
    stale_blocked = False
    try:
        stale_service.port.search(query="")
    except MemoryReadUnavailable as exc:
        stale_blocked = str(exc.code).startswith("MEMORY_AUTHORITY_")
    check("旧代际：产品读取具名阻断", stale_blocked)

    # -- one owner failure keeps the barrier; retry recovers; no duplicate generation --------
    failure_runtime = SupplyRuntime(supply_root)
    try:
        failure_reading = failure_runtime.port().create_artifact(
            workspace_id=workspace_id, title="隔离演示失败材料",
            content="隔离演示：一处 owner 失败的材料。", grant_id=artifact_grant,
            operation_key="demo:failure:v1")
    finally:
        failure_runtime.stop()
    if not failure_reading.applied:
        raise AssertionError("failure material refused: %s" % failure_reading.status)
    failure_reading_id = str(failure_reading.artifact_id)
    failure_version = str(failure_reading.version_id or "")
    failure_event = append_event(
        EvidenceStore(evidence), "隔离演示：一处 owner 清理失败。",
        "reading:artifact:demo:failure",
        refs=[("artifact", failure_reading_id), ("artifactversion", failure_version)])
    broken_store = ForgetCaseStore(
        cases, evidence_db=evidence, controls_db=controls, authority_db=authority,
        owner=OWNER, conversation=CONVERSATION,
        supply_store=ManagedArtifactStore(supply_db, projection_root=projection_root,
                                          read_only=True))
    pending = broken_store.request(operation_key="op-owner-failure",
                                   event_ids=[failure_event])
    check("一处 owner 清理失败：屏障已落、case 不 COMPLETE",
          pending["status"] == "OWNER_PENDING"
          and int(pending.get("barrier_seq") or 0) >= 1)
    check("一处 owner 清理失败：M0 正文已清（已生效的清理不回滚）",
          not payload_present(evidence, failure_event))

    seq_before_retry = MemoryDeletionAuthority(authority, owner=OWNER,
                                               conversation=CONVERSATION).projection().seq
    good_supply = ManagedArtifactStore(
        supply_db, projection_root=projection_root,
        fences_root=supply_root / "recovery" / "fences", writer_instance="f154-demo-retry")
    good_store = ForgetCaseStore(
        cases, evidence_db=evidence, controls_db=controls, authority_db=authority,
        owner=OWNER, conversation=CONVERSATION,
        supply_store=good_supply)
    retried = good_store.retry(pending["case_id"])
    seq_after_retry = MemoryDeletionAuthority(authority, owner=OWNER,
                                              conversation=CONVERSATION).projection().seq
    check("retry：失败 owner 补齐后可达 COMPLETE（受控当前存储无正文）",
          retried["status"] == "COMPLETE")
    check("retry 幂等：同一 case 不新增删除 generation",
          seq_after_retry == seq_before_retry)
    retried_again = good_store.retry(pending["case_id"])
    check("retry 再次执行：仍 COMPLETE，无重复效果",
          retried_again["status"] == "COMPLETE"
          and MemoryDeletionAuthority(authority, owner=OWNER,
                                      conversation=CONVERSATION).projection().seq
          == seq_before_retry)
    good_supply.close()

    # -- named negative paths retained from the earlier contract ------------------------------
    seq_before_missing = MemoryDeletionAuthority(authority, owner=OWNER,
                                                 conversation=CONVERSATION).projection().seq
    missing_rc, missing = run_cli(instance, "request", "--operation-key", "op-missing",
                                  "--event", "evt:does-not-exist")
    seq_after_missing = MemoryDeletionAuthority(authority, owner=OWNER,
                                                conversation=CONVERSATION).projection().seq
    check("找不到目标：HELD，不报已忘记，权威 seq 不增",
          missing_rc == 0 and missing.get("status") == "HELD"
          and missing.get("reason") == "FORGET_TARGET_NOT_FOUND"
          and seq_after_missing == seq_before_missing)

    third_id = append_event(EvidenceStore(evidence), "隔离演示：第三次经历。", "practice:demo:3")
    uncovered_rc, uncovered = run_cli(instance, "request", "--operation-key", "op-uncovered",
                                      "--event", third_id, "--uncovered", "embedding_index")
    check("未覆盖派生面：WAITING_RETENTION，不冒 COMPLETE",
          uncovered_rc == 0 and uncovered.get("status") == "WAITING_RETENTION"
          and uncovered.get("reason") == "FORGET_DERIVED_SURFACES_NOT_COVERED")

    hidden = retained / "authority.hidden.db"
    shutil.move(str(authority), str(hidden))
    try:
        missing_service = memory_service_from_env(environment)
        read_blocked = False
        try:
            missing_service.port.search(query="")
        except MemoryReadUnavailable as exc:
            read_blocked = str(exc.code).startswith("MEMORY_AUTHORITY_")
        blocked_rc, blocked_request = run_cli(instance, "request", "--operation-key",
                                              "op-while-missing", "--event", other_id)
        check("权威缺失：读具名阻断，新请求不报接纳完成",
              missing_service.status()["status"] == "BLOCKED" and read_blocked
              and blocked_rc == 3 and blocked_request.get("status") == "BLOCKED")
    finally:
        shutil.move(str(hidden), str(authority))

    # -- explicit report: what exists, what was erased, what does not exist ------------------
    present_surfaces = {
        "m0_evidence": True,
        "supply_artifact_versions": True,
        "supply_projection_files": True,
        "practice_documents": "stored in artifact versions (same owner)",
        "sharing_documents": "stored in artifact versions (same owner)",
        "recovery_activation_gate": True,
        "recovery_controls_bound": True,
    }
    not_present = ["embedding_index", "episode_index", "fts_index",
                   "continuity_cache_copy", "reflection_cache_copy"]
    case_state = good_store.status(hold["case_id"])
    print("DERIVED_PRESENT %s" % json.dumps(present_surfaces, ensure_ascii=False, sort_keys=True))
    print("DERIVED_NOT_PRESENT %s" % json.dumps(not_present, sort_keys=True))
    print("ERASED m0_erased_rows=%d artifact_versions_erased=%d"
          % (int(sqlite3.connect(evidence).execute(
              "SELECT count(*) FROM evidence_events WHERE payload_erased=1").fetchone()[0]),
             supply.content_erased_count()))
    print("LOCAL_ERASURE_COMPLETE true")
    print("COMPACTION %s" % json.dumps({
        "evidence": "secure_delete + wal_checkpoint(TRUNCATE) + VACUUM",
        "supply": "secure_delete + wal_checkpoint(TRUNCATE) + VACUUM"}))
    print("RETENTION case_state=%s pre-deletion copies retained at %s"
          % (case_state.get("status"), base))
    print("FIXTURE_ROOT %s" % base)
    if FAILURES:
        print("FAILURES: %d" % len(FAILURES))
        return 1
    print("ALL_EXPECTATIONS_MET")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
