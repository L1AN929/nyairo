#!/usr/bin/env python3
"""Round B1 boundary tests: serialized RMW, long-horizon retries, true receipts.

Coverage:
* concurrent stale ``expected_revision`` on a locked writer: exactly one success,
  the other side gets a named 409 ``REVISION_CONFLICT`` (no lost update);
* long-horizon eviction retry: a key pushed out of the active ledger replays from
  the bounded commit ring with ``already_applied`` and zero duplicated effect;
  an unknown key older than the eviction window is ``RETRY_WINDOW_EXPIRED``;
  the same key with a different payload is ``OPERATION_KEY_CONFLICT``;
* ``PhoneService.slice_operation`` is a pure read: it never mints a key, never
  creates the journal directory/file and reports what the journal really holds;
* true completion receipts: ``record_completed`` / ``mark_completed_with_ref``
  refuse fake, failed, wrong-actor and domain-mismatched refs, accept a receipt
  that exists in a persisted store, and ``mark_started`` never claims execution;
  legacy completed rows stay readable and honestly unverified;
* shared idioms cannot be self-confirmed; divergent notices keep both sides.

Run (WSL):
``/tmp/rc104-venv/bin/python -B -m unittest tests.test_v08_tx_retry_and_true_receipts -v``
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "tests"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from components.life.scripts import state_tx_guard as guard  # noqa: E402
from components.life.scripts import v04_elastic_play_learning as elastic  # noqa: E402
from components.life.scripts import v08_emergent_echoes as echoes  # noqa: E402
from components.phone.service import PhoneService  # noqa: E402
from components.world.scripts import v04_neighborhood_living as neighborhood  # noqa: E402
from components.world.scripts import v08_open_affordance as open_affordance  # noqa: E402

T0 = "2026-11-16T09:00:00+08:00"
T1 = "2026-11-16T12:00:00+08:00"


def _snapshot(root: Path) -> dict[str, tuple[int, int, str]]:
    snap: dict[str, tuple[int, int, str]] = {}
    if not root.exists():
        return snap
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root)).replace("\\", "/")
        stat = path.lstat()
        if path.is_file() and not path.is_symlink():
            snap[rel] = (
                int(stat.st_size),
                int(stat.st_mtime_ns),
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        else:
            snap[rel] = (0, int(stat.st_mtime_ns), "dir")
    return snap


def _seed_matter_receipt(
    root: Path,
    operation_key: str,
    *,
    status: str = "applied",
    actor_side: str = "subject",
    object_id: str = "window_frame",
    action: str = "sand_and_wax",
) -> str:
    world = root / "world"
    world.mkdir(parents=True, exist_ok=True)
    existing: dict[str, Any] = {}
    path = world / "v08_matter_space.json"
    if path.is_file():
        existing = json.loads(path.read_text(encoding="utf-8"))
    operations = existing.get("operations") if isinstance(existing.get("operations"), dict) else {}
    operations[operation_key] = {
        "fingerprint": "fp_" + operation_key,
        "actor_side": actor_side,
        "receipt": {
            "status": status,
            "action": action,
            "object_id": object_id,
            "at": T1,
        },
    }
    existing["operations"] = operations
    path.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")
    return operation_key


def _idiom_payload(phrase: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "action": "propose_shared_idiom",
        "phrase": phrase,
        "meaning_cn": "只属于两个人的说法",
        "origin_summary_cn": "一次傍晚散步时随口说的",
    }
    payload.update(overrides)
    return payload


class ConcurrentRevisionTest(unittest.TestCase):
    def test_locked_neighborhood_writer_serializes_stale_revision(self) -> None:
        with tempfile.TemporaryDirectory(prefix="b1-nb-") as tmp:
            root = Path(tmp)
            barrier = threading.Barrier(2)
            outcomes: list[tuple[str, str]] = []
            lock = threading.Lock()

            def attempt(weather: str, key: str) -> None:
                barrier.wait(timeout=10)
                try:
                    status, _result, _view = neighborhood.apply_v04_neighborhood_command(
                        root,
                        {
                            "action": "set_local_weather",
                            "weather": weather,
                            "operation_key": key,
                            "expected_revision": 0,
                        },
                        actor_side="subject",
                        now_iso=T0,
                    )
                    verdict = ("ok", status)
                except neighborhood.V04NeighborhoodError as exc:
                    verdict = ("err", exc.reason_code)
                with lock:
                    outcomes.append(verdict)

            threads = [
                threading.Thread(target=attempt, args=("clear", "op_clear")),
                threading.Thread(target=attempt, args=("rain", "op_rain")),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)
                self.assertFalse(thread.is_alive())

            self.assertEqual(sum(1 for kind, _ in outcomes if kind == "ok"), 1, outcomes)
            errors = [code for kind, code in outcomes if kind == "err"]
            self.assertEqual(errors, ["REVISION_CONFLICT"], outcomes)


class LongHorizonRetryTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="b1-retry-")
        self.root = Path(self._tmp.name)
        (self.root / "phone").mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _apply(self, payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
        return echoes.apply_emergent_echoes_mutation(
            self.root, payload, actor_side="subject", now_iso=T0
        )

    def _evict_active_ledger(self) -> dict[str, Any]:
        path = self.root / "phone" / "v08_emergent_echoes.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        for index in range(300):
            guard.stamp_receipt(
                state,
                f"op_bulk_{index}",
                {"status": "applied", "ok": True},
                revision=100 + index,
                dict_key="operation_ledger",
                payload_hash=f"bulk-hash-{index}",
                committed_at=f"2026-11-16T00:00:{index % 60:02d}Z",
            )
            guard.trim_receipts_by_op_seq(
                state, dict_key="operation_ledger", max_keep=256
            )
        guard.atomic_write_json(path, state)
        return state

    def test_evicted_key_replays_without_second_effect(self) -> None:
        payload = _idiom_payload("盐霜线", operation_key="op_idiom_keep")
        projected, status = self._apply(payload)
        self.assertEqual(status, "APPLIED")
        revision_before = projected["revision"]
        idioms_before = len(projected["shared_idioms"])
        self.assertEqual(idioms_before, 1)

        self._evict_active_ledger()

        retry_payload = dict(payload)
        retry_payload["expected_revision"] = revision_before
        replayed, status2 = self._apply(retry_payload)
        self.assertEqual(status2, "DUPLICATE")
        self.assertEqual(replayed["revision"], revision_before)
        self.assertEqual(len(replayed["shared_idioms"]), idioms_before)

        # Same key, different payload -> explicit conflict, never a silent replay.
        changed = _idiom_payload("另一个说法", operation_key="op_idiom_keep")
        with self.assertRaises(echoes.V08ConflictError) as ctx:
            self._apply(changed)
        self.assertEqual(ctx.exception.reason_code, "OPERATION_KEY_CONFLICT")

        # Unknown key from before the eviction window -> explicit expired retry.
        ancient = _idiom_payload("很旧的说法", operation_key="op_ancient")
        ancient["expected_revision"] = 0
        with self.assertRaises(echoes.V08ConflictError) as ctx2:
            self._apply(ancient)
        self.assertEqual(ctx2.exception.reason_code, "RETRY_WINDOW_EXPIRED")

    def test_open_affordance_replay_helpers_use_the_same_window(self) -> None:
        state: dict[str, Any] = {
            "revision": 9,
            "operations": {},
            "committed_op_ring": [],
            "evicted_up_to_revision": 5,
        }
        open_affordance._store_operation(
            state,
            operation_key="op_live",
            fingerprint="fp_live",
            receipt={"status": "applied", "action": "apply_living_trace"},
        )
        replay = open_affordance._check_replay(
            state,
            operation_key="op_live",
            fingerprint="fp_live",
            root=None,
            body={},
        )
        assert replay is not None
        self.assertTrue(replay["already_applied"])
        self.assertEqual(replay["action"], "apply_living_trace")
        self.assertGreaterEqual(state["operations"]["op_live"]["op_seq"], 1)

        with self.assertRaises(open_affordance.V08OpenAffordanceError) as ctx:
            open_affordance._check_replay(
                state,
                operation_key="op_live",
                fingerprint="fp_other",
                root=None,
                body={},
            )
        self.assertEqual(ctx.exception.reason_code, "OPERATION_KEY_CONFLICT")

        with self.assertRaises(open_affordance.V08OpenAffordanceError) as ctx2:
            open_affordance._check_replay(
                state,
                operation_key="op_gone",
                fingerprint="fp_gone",
                root=None,
                body={"expected_revision": 4},
            )
        self.assertEqual(ctx2.exception.reason_code, "RETRY_WINDOW_EXPIRED")


class SliceOperationLookupTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="b1-slice-")
        self.root = Path(self._tmp.name) / "instance"
        self.shim = types.SimpleNamespace(root=self.root)
        self.shim._journal_path = types.MethodType(PhoneService._journal_path, self.shim)
        self.shim.record_operation = types.MethodType(PhoneService.record_operation, self.shim)
        self.shim._clock = lambda: "2026-11-16T09:00:00+08:00"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_lookup_is_zero_write_and_never_mints_a_key(self) -> None:
        before = _snapshot(Path(self._tmp.name))
        out = PhoneService.slice_operation(self.shim, "op_never_seen")
        after = _snapshot(Path(self._tmp.name))
        self.assertEqual(before, after, "slice_operation must not create files or dirs")
        self.assertTrue(out["ok"])
        self.assertFalse(out["found"])
        self.assertEqual(out["operation_key"], "op_never_seen")
        self.assertIsNone(out["latest"])
        self.assertEqual(out["records"], [])
        self.assertFalse((self.root / "phone" / "operations.jsonl").exists())

    def test_journal_rows_are_attributed_and_readable(self) -> None:
        payload = {"action": "probe", "operation_key": "op_slice_1"}
        PhoneService._journal_slice_operation(
            self.shim,
            payload,
            {"ok": True, "status": "applied"},
            mutator=lambda *a, **k: (200, {}),
            subject_view=True,
        )
        journal = self.root / "phone" / "operations.jsonl"
        self.assertTrue(journal.is_file())
        rows = [
            json.loads(line)
            for line in journal.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(rows[0]["actor"], "subject")

        out = PhoneService.slice_operation(self.shim, "op_slice_1")
        self.assertTrue(out["found"])
        self.assertEqual(out["latest"]["actor"], "subject")
        self.assertEqual(out["latest"]["action"], "probe")

        # A replayed slice result is not journaled a second time.
        PhoneService._journal_slice_operation(
            self.shim,
            payload,
            {"ok": True, "status": "already_applied", "already_applied": True},
            mutator=lambda *a, **k: (200, {}),
            subject_view=True,
        )
        again = PhoneService.slice_operation(self.shim, "op_slice_1")
        self.assertEqual(len(again["records"]), 1)


class TrueCompletionReceiptTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="b1-receipt-")
        self.root = Path(self._tmp.name)
        (self.root / "phone").mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_verifier_zero_write_and_five_checks(self) -> None:
        receipt_key = _seed_matter_receipt(self.root, "op_craft_window")
        before = _snapshot(self.root)
        verified = elastic.verify_completion_receipt_readonly(
            self.root,
            receipt_key,
            actor_side="subject",
            linked_object_ids=["window_frame"],
            target_hint="打磨窗框",
        )
        after = _snapshot(self.root)
        self.assertEqual(before, after, "verification must be a pure read")
        self.assertTrue(verified["verified"])
        self.assertEqual(verified["verified_source"], "world/v08_matter_space.json")
        self.assertEqual(verified["verified_action"], "sand_and_wax")

        with self.assertRaises(elastic.V04ConflictError) as ctx:
            elastic.verify_completion_receipt_readonly(
                self.root, "note:does-not-exist", actor_side="subject"
            )
        self.assertEqual(ctx.exception.reason_code, "INVALID_COMPLETION_RECEIPT")

        _seed_matter_receipt(self.root, "op_craft_failed", status="failed")
        with self.assertRaises(elastic.V04ConflictError) as ctx_failed:
            elastic.verify_completion_receipt_readonly(
                self.root, "op_craft_failed", actor_side="subject"
            )
        self.assertEqual(ctx_failed.exception.reason_code, "RECEIPT_NOT_COMPLETED")

        _seed_matter_receipt(self.root, "op_partner_craft", actor_side="partner")
        with self.assertRaises(elastic.V04ConflictError) as ctx_actor:
            elastic.verify_completion_receipt_readonly(
                self.root, "op_partner_craft", actor_side="subject"
            )
        self.assertEqual(ctx_actor.exception.reason_code, "RECEIPT_ACTOR_MISMATCH")

        with self.assertRaises(elastic.V04ConflictError) as ctx_target:
            elastic.verify_completion_receipt_readonly(
                self.root,
                "op_craft_window",
                actor_side="subject",
                linked_object_ids=["cup_of_water"],
                target_hint="喝一杯水",
            )
        self.assertEqual(ctx_target.exception.reason_code, "COMPLETION_TARGET_MISMATCH")

    def test_record_completed_refuses_fake_and_accepts_persisted_receipt(self) -> None:
        projected, status = elastic.apply_elastic_play_mutation(
            self.root,
            {"action": "set_day_direction", "direction": "把书签按季节夹好"},
            actor_side="subject",
            now_iso=T0,
        )
        self.assertEqual(status, "APPLIED")
        self.assertFalse(projected["day_direction"]["pre_scheduled"])

        with self.assertRaises(elastic.V04ConflictError) as ctx:
            elastic.apply_elastic_play_mutation(
                self.root,
                {
                    "action": "set_day_direction",
                    "mode": "record_completed",
                    "completed_summary": "假装已经夹好了",
                    "receipt_ref": "note:bookmark-20261113",
                },
                actor_side="subject",
                now_iso=T1,
            )
        self.assertEqual(ctx.exception.reason_code, "INVALID_COMPLETION_RECEIPT")

        receipt_key = _seed_matter_receipt(
            self.root,
            "op_bookmark_real",
            object_id="bookmark_20261113",
            action="craft_card",
        )
        done, status2 = elastic.apply_elastic_play_mutation(
            self.root,
            {
                "action": "set_day_direction",
                "mode": "record_completed",
                "completed_summary": "把书签按季节夹好了",
                "receipt_ref": receipt_key,
            },
            actor_side="subject",
            now_iso=T1,
        )
        self.assertEqual(status2, "APPLIED")
        items = done["day_direction"]["completed_items"]
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0]["verified"])
        self.assertEqual(items[0]["verification_status"], "verified")

    def test_mark_started_never_claims_execution_and_legacy_reads_unverified(self) -> None:
        projected, _ = echoes.apply_emergent_echoes_mutation(
            self.root,
            {
                "action": "sketch_day_direction",
                "horizon_note_cn": "今天想把窗台木框打磨到能上蜡",
                "next_decision_boundary_cn": "打磨完再决定要不要换木料",
                "visibility": "private",
            },
            actor_side="subject",
            now_iso=T0,
        )
        direction_id = projected["day_directions"][0]["direction_id"]

        started, _ = echoes.apply_emergent_echoes_mutation(
            self.root,
            {
                "action": "advance_or_interrupt_direction",
                "direction_id": direction_id,
                "transition": "mark_started",
            },
            actor_side="subject",
            now_iso=T1,
        )
        row = started["day_directions"][0]
        self.assertEqual(row["status"], "in_progress")
        self.assertFalse(row["executed_in_world"])

        path = self.root / "phone" / "v08_emergent_echoes.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["day_directions"].append(
            {
                "direction_id": "dd_legacy_completed",
                "horizon_note_cn": "一条很久以前的方向",
                "next_decision_boundary_cn": "没有边界的旧记录",
                "bound_ref": None,
                "visibility": "private",
                "status": "completed",
                "executed_in_world": True,
                "evidence_ref": "legacy:note",
                "breakpoint_note_cn": "",
                "failure_judgment": None,
                "zero_guilt": True,
                "created_at": T0,
                "updated_at": T0,
            }
        )
        path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

        legacy_view = echoes.read_emergent_echoes_zero_write(
            self.root, has_subject_capability=True, now_iso=T1
        )
        legacy = next(
            item
            for item in legacy_view["day_directions"]
            if item["direction_id"] == "dd_legacy_completed"
        )
        self.assertEqual(legacy["evidence_ref"], "legacy:note")
        self.assertFalse(legacy["verified"])
        self.assertEqual(legacy["verification_status"], "legacy_unverified")

    def test_mark_completed_rejects_failed_and_wrong_actor_refs(self) -> None:
        projected, _ = echoes.apply_emergent_echoes_mutation(
            self.root,
            {
                "action": "sketch_day_direction",
                "horizon_note_cn": "把窗框擦干净再上蜡",
                "next_decision_boundary_cn": "蜡干了再看",
                "visibility": "private",
            },
            actor_side="subject",
            now_iso=T0,
        )
        direction_id = projected["day_directions"][0]["direction_id"]

        _seed_matter_receipt(self.root, "op_failed_act", status="failed")
        with self.assertRaises(echoes.V08ConflictError) as ctx:
            echoes.apply_emergent_echoes_mutation(
                self.root,
                {
                    "action": "advance_or_interrupt_direction",
                    "direction_id": direction_id,
                    "transition": "mark_completed_with_ref",
                    "evidence_ref": "op_failed_act",
                },
                actor_side="subject",
                now_iso=T1,
            )
        self.assertEqual(ctx.exception.reason_code, "RECEIPT_NOT_COMPLETED")

        _seed_matter_receipt(self.root, "op_partner_act", actor_side="partner")
        with self.assertRaises(echoes.V08ConflictError) as ctx2:
            echoes.apply_emergent_echoes_mutation(
                self.root,
                {
                    "action": "advance_or_interrupt_direction",
                    "direction_id": direction_id,
                    "transition": "mark_completed_with_ref",
                    "evidence_ref": "op_partner_act",
                },
                actor_side="subject",
                now_iso=T1,
            )
        self.assertEqual(ctx2.exception.reason_code, "RECEIPT_ACTOR_MISMATCH")

        good_key = _seed_matter_receipt(self.root, "op_real_act")
        completed, _ = echoes.apply_emergent_echoes_mutation(
            self.root,
            {
                "action": "advance_or_interrupt_direction",
                "direction_id": direction_id,
                "transition": "mark_completed_with_ref",
                "evidence_ref": good_key,
                "direction_hint": "把窗框擦干净再上蜡",
            },
            actor_side="subject",
            now_iso=T1,
        )
        done = completed["day_directions"][0]
        self.assertEqual(done["status"], "completed")
        self.assertTrue(done["executed_in_world"])
        self.assertTrue(done["verified"])


class SharedIdiomAndDivergenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="b1-idiom-")
        self.root = Path(self._tmp.name)
        (self.root / "phone").mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_self_confirm_is_refused_and_peer_confirm_succeeds(self) -> None:
        projected, _ = echoes.apply_emergent_echoes_mutation(
            self.root,
            _idiom_payload("半勺糖"),
            actor_side="subject",
            now_iso=T0,
        )
        idiom = projected["shared_idioms"][0]
        self.assertEqual(idiom["proposed_by"], "subject")

        with self.assertRaises(echoes.V08ConflictError) as ctx:
            echoes.apply_emergent_echoes_mutation(
                self.root,
                {"action": "confirm_shared_idiom", "idiom_id": idiom["idiom_id"]},
                actor_side="subject",
                now_iso=T1,
            )
        self.assertEqual(ctx.exception.reason_code, "SELF_CONFIRM_FORBIDDEN")

        confirmed, _ = echoes.apply_emergent_echoes_mutation(
            self.root,
            {"action": "confirm_shared_idiom", "idiom_id": idiom["idiom_id"]},
            actor_side="partner",
            now_iso=T1,
        )
        row = confirmed["shared_idioms"][0]
        self.assertEqual(row["status"], "confirmed")
        self.assertEqual(row["confirmed_by"], "partner")

    def test_divergent_notices_keep_both_sides(self) -> None:
        echoes.apply_emergent_echoes_mutation(
            self.root,
            {
                "action": "subject_add_divergent_notice",
                "target_ref": "shop:old_bookstore",
                "target_title_cn": "巷口旧书店",
                "notice_cn": "我注意到门口那盆薄荷快枯了",
            },
            actor_side="subject",
            now_iso=T0,
        )
        echoes.apply_emergent_echoes_mutation(
            self.root,
            {
                "action": "partner_add_divergent_notice",
                "target_ref": "shop:old_bookstore",
                "target_title_cn": "巷口旧书店",
                "notice_cn": "我注意的是柜台后面那本旧海图册",
            },
            actor_side="partner",
            now_iso=T0,
        )
        updated, _ = echoes.apply_emergent_echoes_mutation(
            self.root,
            {
                "action": "subject_add_divergent_notice",
                "target_ref": "shop:old_bookstore",
                "target_title_cn": "巷口旧书店",
                "notice_cn": "今天再看，薄荷已经被人浇过水了",
            },
            actor_side="subject",
            now_iso=T1,
        )
        notice = updated["divergent_notices"][0]
        self.assertIn("旧海图册", notice["partner_notice_cn"])
        self.assertIn("浇过水", notice["subject_notice_cn"])
        self.assertEqual(set(notice["entries_by_side"]), {"subject", "partner"})
        self.assertIn("旧海图册", notice["entries_by_side"]["partner"]["notice_cn"])


if __name__ == "__main__":
    unittest.main()
