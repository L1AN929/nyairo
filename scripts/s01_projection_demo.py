#!/usr/bin/env python3
"""S01 F-011/F-012/F-014 demo: managed Markdown, external edit, explicit import, health.

What is real
------------
* the real ``LifeSupplyService`` owner on its own Unix socket over a unique isolated root;
* the real ``LifeArtifactPort`` (the same client the Life runtime uses) for every artifact read
  and write, including the new observe/import/rebuild operations;
* real managed Markdown files under ``<root>/data/projection`` and real SQLite CAS/versions.

What is *not* claimed
---------------------
No product model is called (0 calls, 0 network).  The "corrupt payload" step injects a controlled
reader fault to show that payload health and file health are independent; it is labelled as an
injection and never touches a real artifact.  This is not G1, not a 24h run, and not evidence that
a model naturally edits files.

Run (WSL/POSIX)::

    PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/s01_projection_demo.py
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import life_artifact_port as lap  # noqa: E402

SUBJECT = "demo:s01-projection"
WORKSPACE_CAP = "PROVISION_PERSONAL_WORKSPACE"
WORKSPACE_SCOPE = "workspace:personal"
WORKSPACE_AUTHORITY = "life-supply.workspace.v1"
ARTIFACT_CAP = "COMMIT_MANAGED_ARTIFACT"
ARTIFACT_SCOPE = "artifact:personal"
ARTIFACT_AUTHORITY = "life-supply.artifact.v1"
V1 = "# 夜航手记\n\n第一版：她在灯下写下今天看见的河。\n"
E2 = V1 + "\n（外部补记：有人把窗台上的雨声也写了进来。）\n"
E3 = E2 + "（第二次外部编辑，基于导入后的版本。）\n"
V3 = E2 + "\n她后来自己又添了一句河的尽头。\n"
FAILURES: list[str] = []


def say(text: str) -> None:
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


def short(value: Any, limit: int = 18) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "…"
    return value


class Owner:
    """The real Life Supply owner on one socket over an isolated data root."""

    def __init__(self, data_root: Path, socket_path: Path) -> None:
        from lifesupply.service.life_supply import LifeSupplyService, LifeSupplySocketServer
        from lifesupply.service.switches import FeatureSwitches

        socket_path.parent.mkdir(parents=True, exist_ok=True)
        if socket_path.exists() or socket_path.is_symlink():
            raise RuntimeError("REFUSING_TO_REUSE_SOCKET_PATH:%s" % socket_path)
        uid = os.geteuid()
        self.uid = uid
        self.socket_path = socket_path
        self.data_root = data_root
        self.service = LifeSupplyService(
            data_root, operator_uids={uid}, service_uids={uid},
            switches=FeatureSwitches.all_on(), writer_instance="s01-projection-demo")
        if self.service.state.state != "READY":
            raise RuntimeError("owner not READY: %r" % (self.service.startup_error,))
        self.server = LifeSupplySocketServer(str(socket_path), self.service)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def call(self, request: dict) -> dict:
        return self.service.dispatch(request, peer_uid=self.uid)

    def port(self) -> "lap.LifeArtifactPort":
        return lap.LifeArtifactPort(str(self.socket_path), SUBJECT, timeout=15.0)

    def projection_path(self, artifact_id: str) -> Path:
        binding = self.service.artifact.read_projection_binding(artifact_id) or {}
        rel = str(binding.get("relative_path") or "")
        return self.data_root / "projection" / rel

    def stop(self) -> None:
        # Ordered stop: HTTP/socket shutdown, connection close, drain, then release fences.
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=10)
        self.service.close()
        # Socket files are deliberately left on disk (repo rule: sockets are only added).


def issue(owner: Owner, capability: str, scope: str, target: str, authority: str,
          use_mode: str, operation_id: str) -> str:
    answer = owner.call({"op": "issue_grant", "grant": {
        "subject": SUBJECT, "capability": capability, "scope": scope, "target": target,
        "authority_scope": authority, "use_mode": use_mode, "operation_id": operation_id}})
    if answer.get("status") != "ALLOW":
        raise SystemExit("grant refused: %r" % (answer,))
    return str(answer["grant_id"])


def provision(owner: Owner) -> tuple[str, str]:
    workspace_grant = issue(owner, WORKSPACE_CAP, WORKSPACE_SCOPE,
                            "workspace:%s" % SUBJECT, WORKSPACE_AUTHORITY,
                            "ONE_SHOT", "s01:workspace")
    answer = owner.call({"op": "provision_workspace", "grant_id": workspace_grant,
                         "operation_key": "s01:workspace",
                         "workspace": {"subject_id": SUBJECT}})
    if answer.get("status") != "OK":
        raise SystemExit("workspace refused: %r" % (answer,))
    workspace_id = str(answer["workspace"]["workspace_id"])
    artifact_grant = issue(owner, ARTIFACT_CAP, ARTIFACT_SCOPE,
                           "workspace:%s" % workspace_id, ARTIFACT_AUTHORITY,
                           "REUSABLE", "s01:artifact-grant")
    return workspace_id, artifact_grant


def brief_view(view: dict) -> dict:
    rep = view.get("representation_health") or {}
    head = view.get("head") or {}
    candidate = view.get("candidate") or {}
    return {
        "lifecycle": (view.get("artifact") or {}).get("lifecycle"),
        "head_version": head.get("version_id"),
        "content_health": (view.get("content_health") or {}).get("state"),
        "representation_health": rep.get("state"),
        "representation_reason": rep.get("reason_code"),
        "candidate": candidate.get("candidate_id"),
        "candidate_base": candidate.get("base_projection_version"),
        "candidate_author": candidate.get("authority"),
    }


def main() -> int:
    base = Path(os.environ.get("TMPDIR") or "/tmp") / "nyairo-s01-projection"
    root = base / ("demo-%d-%d" % (os.getpid(), time.time_ns()))
    root.mkdir(parents=True, exist_ok=False)
    data_root = root / "data"
    say("S01 projection demo root: %s" % root)

    owner = Owner(data_root, root / "owner-1.sock")
    try:
        workspace_id, grant = provision(owner)
        port = owner.port()
        created = port.create_artifact(
            workspace_id=workspace_id, title="夜航手记", content=V1,
            grant_id=grant, operation_key="s01:create")
        if created.effect_state != lap.EffectState.APPLIED:
            raise SystemExit("create failed: %r" % (created.raw,))
        artifact_id = str(created.artifact_id)
        v1 = str(created.version_id)
        path = owner.projection_path(artifact_id)
        say("STEP 1 canonical commit %s; projection file written separately: %s"
            % (short(v1), path.is_file()))

        view = port.observe_projection(artifact_id)
        check("create: payload AVAILABLE and projection CURRENT",
              view["content_health"]["state"] == "AVAILABLE"
              and view["representation_health"]["state"] == "CURRENT", brief_view(view))
        check("create: observation carries no projection body",
              E2 not in json.dumps(view, ensure_ascii=False))
        say("  status: " + json.dumps(brief_view(view), ensure_ascii=False))

        # -- 2. missing projection is not a missing payload ------------------------------
        path.unlink()
        view = port.observe_projection(artifact_id)
        canonical = port.read_artifact(artifact_id)
        check("missing projection reports MISSING, not UNBOUND",
              view["representation_health"]["state"] == "MISSING", brief_view(view))
        check("canonical payload still readable while projection is missing",
              canonical["content"] == V1
              and view["content_health"]["state"] == "AVAILABLE")
        rebuilt = port.rebuild_projection(artifact_id=artifact_id)
        check("explicit rebuild repairs a MISSING projection",
              rebuilt.effect_state == lap.EffectState.APPLIED
              and (rebuilt.raw.get("representation_health") or {}).get("state") == "CURRENT",
              rebuilt.raw)

        # -- 3. external edit: observation + candidate, head unchanged --------------------
        path.write_text(E2, encoding="utf-8")
        view = port.observe_projection(artifact_id)
        candidate = view["candidate"] or {}
        check("external edit reports DIVERGED with an external-UNKNOWN candidate",
              view["representation_health"]["state"] == "DIVERGED"
              and candidate.get("authority") == "EXTERNAL_UNKNOWN"
              and candidate.get("base_projection_version") == v1, brief_view(view))
        check("external edit does not move the canonical head",
              (view["head"] or {}).get("version_id") == v1)
        say("  observe: " + json.dumps(brief_view(view), ensure_ascii=False))

        denied = port.import_projection(
            artifact_id=artifact_id, candidate_id=candidate["candidate_id"],
            base_version_id=v1, confirm=False, grant_id=grant,
            operation_key="s01:import-no-confirm")
        check("import without explicit confirmation is refused",
              denied.effect_state == lap.EffectState.REFUSED
              and denied.reason == "EXPLICIT_CONFIRMATION_REQUIRED", denied.raw)

        # -- 4. explicit import through the owner CAS -----------------------------------
        imported = port.import_projection(
            artifact_id=artifact_id, candidate_id=candidate["candidate_id"],
            base_version_id=v1, confirm=True, grant_id=grant, operation_key="s01:import-1")
        v2 = str(imported.version_id)
        provenance = imported.raw.get("provenance") or {}
        check("explicit import mints one new version with EXPLICIT_IMPORT provenance",
              imported.effect_state == lap.EffectState.APPLIED
              and imported.raw.get("source_kind") == "EXPLICIT_IMPORT"
              and provenance.get("external_contributor") == "UNKNOWN"
              and imported.raw.get("representation_health", {}).get("state") == "CURRENT",
              imported.raw)
        old = port.read_version(artifact_id, v1)
        new = port.read_version(artifact_id, v2)
        check("old version stays readable without import provenance; imported version is labelled",
              old.get("provenance") is None
              and (new.get("provenance") or {}).get("source_kind") == "EXPLICIT_IMPORT")
        replay = port.import_projection(
            artifact_id=artifact_id, candidate_id=candidate["candidate_id"],
            base_version_id=v1, confirm=True, grant_id=grant, operation_key="s01:import-1")
        history = port.list_versions(artifact_id, limit=20)
        check("same-key retry returns the same version, not a second one",
              replay.raw.get("replayed") is True
              and str(replay.version_id) == v2 and history["total_versions"] == 2,
              {"replay": replay.raw.get("replayed"), "versions": history["total_versions"]})

        # -- 5. a second writer advances the head; old candidate conflicts -----------------
        path.write_text(E3, encoding="utf-8")
        view = port.observe_projection(artifact_id)
        stale_candidate = (view["candidate"] or {}).get("candidate_id")
        check("second external edit is based on the imported version",
              (view["candidate"] or {}).get("base_projection_version") == v2, brief_view(view))
        second = port.commit_version(
            artifact_id=artifact_id, expected_head=v2, content=V3,
            grant_id=grant, operation_key="s01:second-writer")
        v3 = str(second.version_id)
        refused = port.import_projection(
            artifact_id=artifact_id, candidate_id=stale_candidate, base_version_id=v2,
            confirm=True, grant_id=grant, operation_key="s01:import-stale")
        head = port.read_head(artifact_id) or {}
        check("importing an old base after a real head move conflicts and does not overwrite",
              second.effect_state == lap.EffectState.APPLIED
              and refused.effect_state == lap.EffectState.CONFLICT
              and refused.reason == "HEAD_MOVED"
              and str(head.get("version_id")) == v3
              and head.get("content") == V3, refused.raw)

        # -- 6. file changes again after observation: the old candidate is stale ----------
        path.write_text(E3 + "（又一处未确认的外部改动。）\n", encoding="utf-8")
        stale = port.import_projection(
            artifact_id=artifact_id, candidate_id=stale_candidate, base_version_id=v2,
            confirm=True, grant_id=grant, operation_key="s01:import-stale-2")
        check("an unconfirmed later file edit can never be imported through an old candidate",
              stale.effect_state == lap.EffectState.REFUSED
              and stale.reason in {"CANDIDATE_STALE", "HEAD_MOVED"}, stale.raw)

        # -- 7. bounded file safety -------------------------------------------------------
        path.unlink()
        outside = root / "outside.txt"
        outside.write_text("outside must never be read\n", encoding="utf-8")
        path.symlink_to(outside)
        view = port.observe_projection(artifact_id)
        check("a symlinked projection is refused instead of followed",
              view["representation_health"]["state"] == "UNBOUND"
              and view["representation_health"]["reason_code"] == "SYMLINK_REFUSED",
              brief_view(view))
        check("the symlink target's text is not in the observation",
              "outside must never be read" not in json.dumps(view, ensure_ascii=False))
        path.unlink()
        path.write_bytes(b"x" * (256 * 1024 + 1))
        view = port.observe_projection(artifact_id)
        check("an oversized projection is DIVERGED/TOO_LARGE with no candidate",
              view["representation_health"]["state"] == "DIVERGED"
              and view["representation_health"]["reason_code"] == "PROJECTION_TOO_LARGE"
              and view["candidate"] is None, brief_view(view))
        path.write_bytes(b"\xff\xfe\x00bad-utf8")
        view = port.observe_projection(artifact_id)
        check("a non-UTF8 projection is DIVERGED/NOT_UTF8",
              view["representation_health"]["reason_code"] == "PROJECTION_NOT_UTF8",
              brief_view(view))
        rebuilt = port.rebuild_projection(artifact_id=artifact_id)
        check("rebuild refuses to overwrite DIVERGED external evidence",
              rebuilt.effect_state == lap.EffectState.REFUSED
              and rebuilt.reason == "PROJECTION_DIVERGED", rebuilt.raw)

        # -- 8. payload health and file health are independent (controlled injection) -----
        path.write_text(E3 + "（最后一次外部编辑，留给重启后观察。）\n", encoding="utf-8")
        before_restart = port.observe_projection(artifact_id)
        store = owner.service.artifact
        real_head = store.read_head(artifact_id)
        corrupt_head = dict(real_head)
        corrupt_head["content"] = "injected corrupt payload (demo only)"
        injected = store.observe_projection(artifact_id, _head_override=corrupt_head)
        check("injected corrupt payload reports CORRUPT while the file is still nameable",
              injected["content_health"]["state"] == "CORRUPT"
              and injected["content_health"]["reason_code"] == "STORED_HASH_MISMATCH"
              and injected["representation_health"]["state"] == "DIVERGED"
              and injected["candidate"] is None, brief_view(injected))
        say("  injected payload fault is demo-only; the real owner rows are unchanged")

        # -- 9. same-root ordered restart ------------------------------------------------
        version_before = (before_restart["head"] or {}).get("version_id")
        candidate_before = (before_restart["candidate"] or {}).get("candidate_id")
        binding_before = (before_restart["representation_health"]["binding"] or {}).get(
            "relative_path")
        owner.stop()

        restarted = Owner(data_root, root / "owner-2.sock")
        try:
            port2 = restarted.port()
            after = port2.observe_projection(artifact_id)
            check("restart keeps the canonical head",
                  (after["head"] or {}).get("version_id") == version_before == v3)
            check("restart keeps the same managed binding path",
                  (after["representation_health"]["binding"] or {}).get("relative_path")
                  == binding_before)
            check("restart recomputes the same external-edit candidate",
                  (after["candidate"] or {}).get("candidate_id") == candidate_before,
                  {"before": candidate_before,
                   "after": (after["candidate"] or {}).get("candidate_id")})
            imported_version = port2.read_version(artifact_id, v2)
            check("import provenance survives the restart",
                  (imported_version.get("provenance") or {}).get("source_kind")
                  == "EXPLICIT_IMPORT")
            history = port2.list_versions(artifact_id, limit=20)
            check("version history after restart still holds the separately committed versions",
                  history["total_versions"] == 3, {"total": history["total_versions"]})
        finally:
            restarted.stop()

        say("SUMMARY versions_created=3 imports=1 (v2) external_edits_observed=3 "
            "product_model_calls=0")
    finally:
        try:
            owner.stop()
        except Exception:
            pass

    if FAILURES:
        say("S01_PROJECTION_DEMO_FAILED: " + ", ".join(FAILURES))
        return 1
    say("S01_PROJECTION_DEMO_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
