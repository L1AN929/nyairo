#!/usr/bin/env python3
"""Tests for 0.2 Deep-Engine Seal: canonical SQLite mirror, S07 live affect, lease gate & budget telemetry."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for _p in (REPO_ROOT, REPO_ROOT / "components" / "life" / "scripts", REPO_ROOT / "components" / "phone"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from components.life.scripts import s07_affect_owner as s07
from components.life.scripts import v02_crash_and_partial_reconciler as reconciler
from components.life.scripts import v02_representation_lease_forget as rep_lease
from components.life.scripts import v02_v03_daily_loop_bridge as loop_bridge
from components.phone import v02_runtime_migration_view as rm_view


def _snapshot_tree(root: Path) -> set[str]:
    out: set[str] = set()
    for p in root.rglob("*"):
        out.add(str(p.relative_to(root)))
    return out


class TestV02DeepEngineSeal(unittest.TestCase):
    def test_zero_write_get_surfaces_empty_deep_engine_telemetry(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            before = _snapshot_tree(root)
            code, body = rm_view.read_v02_runtime_migration_view(
                root, has_subject_capability=False, now_iso="2026-11-19T18:00:00+08:00"
            )
            after = _snapshot_tree(root)
            self.assertEqual(code, 200)
            self.assertEqual(before, after)
            self.assertEqual(body["llm_calls"], 0)
            self.assertFalse(body["canonical_ledger_status"]["present"])
            self.assertFalse(body["s07_affect_summary"]["present"])
            self.assertFalse(body["representation_lease_health"]["present"])
            self.assertFalse(body["budget_quota_status"]["present"])

    def test_canonical_sqlite_live_mirror_zero_wal_leak_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            r1 = reconciler.record_live_cycle_settlement(
                root,
                cycle_id="cyc_001",
                action_kind="OUTING_VISIT",
                outcome_status="SETTLED",
                target_ref="round:cyc_001",
                summary="散步去了河堤",
                now_iso="2026-11-19T18:05:00+08:00",
            )
            self.assertTrue(r1["ok"])
            self.assertEqual(r1["llm_calls"], 0)
            r2 = reconciler.record_live_cycle_settlement(
                root,
                cycle_id="cyc_001",
                action_kind="OUTING_VISIT",
                outcome_status="SETTLED",
                target_ref="round:cyc_001",
                summary="散步去了河堤",
                now_iso="2026-11-19T18:05:00+08:00",
            )
            self.assertTrue(r2["ok"])
            # Zero -wal / -shm aux files after write!
            aux = [p.name for p in root.rglob("*") if p.name.endswith(("-wal", "-shm", "-journal"))]
            self.assertEqual(aux, [])

            before = _snapshot_tree(root)
            summary = reconciler.read_canonical_ledger_summary_zero_write(root)
            after = _snapshot_tree(root)
            self.assertEqual(before, after)
            self.assertTrue(summary["present"])
            self.assertGreaterEqual(summary["journal_events"], 1)
            aux_after = [p.name for p in root.rglob("*") if p.name.endswith(("-wal", "-shm", "-journal"))]
            self.assertEqual(aux_after, [])

    def test_s07_live_affect_pulse_and_zero_write_summary(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            res = s07.record_live_affect_pulse(
                root,
                source_ref="round:r_s07_1",
                kind=s07.AFFECT_KIND_AFFECT_PULSE,
                intensity=42,
                note="晚风很舒服",
                ttl_hours=6,
                operation_key="op_s07_test_1",
                now_iso="2026-11-19T18:10:00+08:00",
            )
            self.assertEqual(res["llm_calls"], 0)
            before = _snapshot_tree(root)
            summ = s07.read_affect_summary_zero_write(root, now_iso="2026-11-19T18:30:00+08:00")
            after = _snapshot_tree(root)
            self.assertEqual(before, after)
            self.assertTrue(summ["present"])
            self.assertEqual(summ["entry_count"], 1)
            self.assertEqual(summ["active_count"], 1)
            self.assertEqual(summ["latest_kind"], s07.AFFECT_KIND_AFFECT_PULSE)
            self.assertEqual(summ["latest_note"], "晚风很舒服")

    def test_representation_lease_enforcement_blocks_revoked_and_expired_skill_book(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # Partner cannot issue a lease (403)
            code_deny, body_deny = rm_view.mutate_v02_runtime_migration_view(
                root,
                {"action": "issue_representation_lease", "scope": "skill_book", "ttl_minutes": 60},
                actor_side="partner",
                now_iso="2026-11-19T18:00:00+08:00",
            )
            self.assertEqual(code_deny, 403)
            self.assertEqual(body_deny["reason_code"], "FORBIDDEN_ACTOR_SIDE")

            # Subject issues a 60-minute lease for skill_book
            code_ok, body_ok = rm_view.mutate_v02_runtime_migration_view(
                root,
                {"action": "issue_representation_lease", "scope": "skill_book", "ttl_minutes": 60},
                actor_side="subject",
                now_iso="2026-11-19T18:00:00+08:00",
            )
            self.assertEqual(code_ok, 200)
            self.assertEqual(body_ok["representation_lease_health"]["active_count"], 1)

            # Partner revokes skill_book lease -> guarded skill action must fail with 403!
            code_rev, body_rev = rm_view.mutate_v02_runtime_migration_view(
                root,
                {"action": "revoke_representation_lease", "scope": "skill_book", "reason_cn": "暂停技能外露"},
                actor_side="partner",
                now_iso="2026-11-19T18:05:00+08:00",
            )
            self.assertEqual(code_rev, 200)
            self.assertIn("skill_book", body_rev["representation_lease_health"]["blocked_scopes"])

            code_blk, body_blk = rm_view.mutate_v02_runtime_migration_view(
                root,
                {"action": "request_skill_migration_recheck", "method_id": "m_tea", "note_cn": "检查"},
                actor_side="partner",
                now_iso="2026-11-19T18:06:00+08:00",
            )
            self.assertEqual(code_blk, 403)
            self.assertEqual(body_blk["reason_code"], "REPRESENTATION_LEASE_REVOKED")

            # Subject re-issues lease with 30m TTL, then after 2h it is expired -> 403
            code_re, _ = rm_view.mutate_v02_runtime_migration_view(
                root,
                {"action": "issue_representation_lease", "scope": "skill_book", "ttl_minutes": 30},
                actor_side="subject",
                now_iso="2026-11-19T18:10:00+08:00",
            )
            self.assertEqual(code_re, 200)
            gate_exp = rep_lease.enforce_active_representation_lease(
                root, scope="skill_book", now_iso="2026-11-19T20:00:00+08:00"
            )
            self.assertFalse(gate_exp["allowed"])
            self.assertEqual(gate_exp["reason"], "REPRESENTATION_LEASE_EXPIRED")

    def test_budget_quota_zero_write_telemetry_and_daily_loop_autoproducers(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "state").mkdir(parents=True, exist_ok=True)
            (root / "state" / "life_request_budget.json").write_text(
                json.dumps({"day_key": "2026-11-19", "used": 3, "daily_cap": 12}, ensure_ascii=False),
                encoding="utf-8",
            )
            # Trigger real daily loop settlement -> should mirror canonical SQLite + S07 affect!
            settled = loop_bridge.settle_v02_v03_round_consequences(
                root,
                {
                    "effect_state": "APPLIED",
                    "receipt_ref": "rcpt:outing:seal_1",
                    "action_kind": "OUTING_VISIT",
                    "action_family": "outing_visit",
                    "place_id": "river_pier",
                    "topic_cn": "傍晚散步",
                    "summary_cn": "沿着海堤走了十分钟",
                    "operation_key": "op_seal_round_1",
                    "shared": True,
                },
                now_iso="2026-11-19T18:15:00+08:00",
            )
            self.assertTrue(settled["ok"])
            self.assertTrue(settled.get("settled"))

            before = _snapshot_tree(root)
            code, view = rm_view.read_v02_runtime_migration_view(
                root, has_subject_capability=True, now_iso="2026-11-19T18:20:00+08:00"
            )
            after = _snapshot_tree(root)
            self.assertEqual(code, 200)
            self.assertEqual(before, after)
            self.assertTrue(view["canonical_ledger_status"]["present"])
            self.assertTrue(view["s07_affect_summary"]["present"])
            self.assertTrue(view["budget_quota_status"]["present"])
            self.assertEqual(view["budget_quota_status"]["used"], 3)
            self.assertEqual(view["budget_quota_status"]["remaining"], 9)
            aux = [p.name for p in root.rglob("*") if p.name.endswith(("-wal", "-shm", "-journal"))]
            self.assertEqual(aux, [])


if __name__ == "__main__":
    unittest.main()
