#!/usr/bin/env python3
"""T20 item 7, run as the Life Supply *service* identity -- the identity that may write.

An autonomous write is performed by the service principal, so that is the identity whose write
must be refused when it holds no newly granted permission.  The attempt uses the CURRENT v0.2
narrow port (``components/life/scripts/life_artifact_port.py``) against the OLD owner the build
left running, with a grant id that was never issued, and then re-reads the owner's own database
to show that nothing was written.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

T20 = Path(os.environ["NYA02_T20"]).resolve()
V02 = Path(os.environ["NYA02_REPO"]).resolve()
SOCK = os.environ["NYA02_T20_SOCKET"]
sys.path[:0] = [str(V02), str(V02 / "components/life/scripts")]

from life_artifact_port import EffectState, LifeArtifactPort   # noqa: E402

ARTIFACT_FACTS = json.loads((T20 / "old-artifact.json").read_text(encoding="utf-8"))
SUBJECT = ARTIFACT_FACTS["subject"]
ARTIFACT = ARTIFACT_FACTS["artifact"]
DB = T20 / "old-data/life_supply.sqlite"


def counts() -> dict:
    con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    try:
        return {
            "artifacts": con.execute("select count(*) from managed_artifacts").fetchone()[0],
            "versions": con.execute("select count(*) from artifact_versions").fetchone()[0],
            "head": con.execute("select head_version_id from managed_artifacts where artifact_id=?",
                                (ARTIFACT["artifact_id"],)).fetchone()[0],
            "max_version": con.execute("select max(version_number) from artifact_versions"
                                       " where artifact_id=?",
                                       (ARTIFACT["artifact_id"],)).fetchone()[0],
        }
    finally:
        con.close()


before = counts()
port = LifeArtifactPort(SOCK, SUBJECT)
attempt = port.create_artifact(workspace_id=ARTIFACT["workspace_id"],
                               title="T20 未授权写入", content="# 没有新授权\n",
                               grant_id="grant:t20:never-issued",
                               operation_key="op:t20:unauthorized:1")
after = counts()
report = {"identity_uid": os.getuid(), "status": attempt.status, "reason": attempt.reason,
          "detail": attempt.detail, "effect_state": attempt.effect_state,
          "before": before, "after": after}
print(json.dumps(report, ensure_ascii=False), flush=True)
ok = (attempt.effect_state == EffectState.REFUSED and bool(attempt.reason) and before == after)
print("T20_CHECK 7.service-write-without-new-grant %s %s/%s; no write: %s"
      % ("PASS" if ok else "FAIL", attempt.status, attempt.reason, before == after), flush=True)
sys.exit(0 if ok else 1)
