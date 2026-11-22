#!/usr/bin/env python3
"""v0.8 Ch.35 six-concern lifecycle + Ch.47 compact single-situation header.

Deterministic, 100% zero-LLM unit coverage for two already-landed surfaces:

Ch.35 (``components/life/scripts/everyday_desire_lifecycle.py``)
* observed-origin gating: ``object:`` / ``material:`` / ``place:`` must be
  genuinely observed, otherwise ``EverydayDesireError(409 CONCERN_ORIGIN_NOT_OBSERVED)``;
  plain unstructured refs stay backwards compatible unless
  ``require_observed_origin=True``;
* source-loss automatic subsidence: a retained concern whose structured origin
  vanished quietly moves to ``auto_subsided_concerns`` with
  ``reason == "origin_no_longer_present"`` and zero penalty / resentment / illness,
  and the read projection touches no disk byte;
* strict intimacy channel isolation: an active intimacy concern never renders on
  ``ordinary_work`` / ``public_or_shared`` channels or with the adult gate closed;
* RMW + ``op_seq`` order-based eviction: > ``MAX_IDEMPOTENCY_RECORDS`` distinct
  submissions evict the strictly oldest ``op_seq`` (never the alphabetically
  smallest key), evicted keys replay idempotently from ``committed_op_ring``,
  and a changed payload on an evicted key is a hard 409 conflict.

Ch.47 (``chiyo_bundle/lived_context_bridge.py``)
* the compact ``当前处境：`` single-situation line is silent on a calm default
  root and renders for a live journey, an active project and an active concern;
* ``measure_context_budget_breakdown`` is zero-write, reports ``calm_silent`` and
  keeps ``autonomy_line`` / ``interactive_line`` ahead of low-salience flavor
  hints (``anchor_line``) under crowded-context pressure.

Run (WSL):
``PYTHONPATH=components/life/scripts:components/world/scripts:components/phone:. \\
python3 -m unittest tests.test_v08_ch35_concerns_and_compact_situation -v``
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    ROOT,
    ROOT / "components" / "life" / "scripts",
    ROOT / "components" / "world" / "scripts",
    ROOT / "components" / "phone",
):
    if str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))

from chiyo_bundle import lived_context_bridge as lcb  # noqa: E402
from components.life.scripts import everyday_desire_lifecycle as edl  # noqa: E402
from components.life.scripts import state_tx_guard as guard  # noqa: E402

NOW = "2026-11-16T09:00:00+08:00"


def _write_json(path: Path, doc: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int, str]]:
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


def _stat_tuple(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return int(stat.st_size), int(stat.st_mtime_ns)


class _LifecycleBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-ch35-")
        self.root = Path(self._tmp.name) / "day"
        self.root.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # --- fixtures -----------------------------------------------------------
    def _seed_matter(self, objects: dict[str, dict[str, Any]]) -> None:
        _write_json(self.root / "world" / "v08_matter_space.json", {"matter_objects": objects})

    def _seed_materials(self, rows: list[dict[str, Any]]) -> None:
        _write_json(self.root / "phone" / "v03_encounters.json", {"shared_materials": rows})

    def _seed_places(self, places: dict[str, dict[str, Any]]) -> None:
        _write_json(self.root / "world" / "v04_neighborhood_state.json", {"places": places})

    def _emerge(self, **overrides: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "action": "emerge_concern",
            "domain": "exploration",
            "origin_kind": "immediate_opportunity",
            "origin_refs": ["今天窗边很亮"],
            "expression": "想停下来多看一会儿",
            "persistence": "turn_only",
        }
        payload.update(overrides)
        return edl.apply_everyday_desire_action(self.root, payload, now_iso=NOW)

    def _assert_rejected(self, **overrides: Any) -> None:
        with self.assertRaises(edl.EverydayDesireError) as ctx:
            self._emerge(**overrides)
        self.assertEqual(ctx.exception.reason_code, "CONCERN_ORIGIN_NOT_OBSERVED")
        self.assertEqual(ctx.exception.http_status, 409)


class ObservedOriginGatingTest(_LifecycleBase):
    """Ch.35 §一/§五: structured origins must be genuinely observed."""

    def test_01_object_origin_gating(self) -> None:
        self._seed_matter({
            "cup_01": {"object_id": "cup_01", "label_cn": "白瓷杯", "item_kind": "vessel", "parent_id": "home_desk"},
            "hidden_cup": {"object_id": "hidden_cup", "label_cn": "藏起来的杯", "item_kind": "vessel",
                           "parent_id": "home_desk", "_subject_hidden": True},
            "orphan": {"object_id": "orphan", "label_cn": "无主物", "item_kind": "thing",
                       "parent_id": edl.SUBJECT_UNKNOWN_PARENT},
        })

        self.assertEqual(
            edl.verify_concern_origin_observed_readonly(self.root, "object:cup_01"),
            (True, "object_observed"),
        )
        for bad in ("object:missing", "object:hidden_cup", "object:orphan"):
            ok, reason = edl.verify_concern_origin_observed_readonly(self.root, bad)
            self.assertFalse(ok, bad)
            self.assertEqual(reason, "object_not_observed_or_missing", bad)

        applied = self._emerge(origin_refs=["object:cup_01"])
        self.assertEqual(applied["status"], "applied")

        self._assert_rejected(origin_refs=["object:missing"])
        self._assert_rejected(origin_refs=["object:hidden_cup"])

    def test_02_material_origin_gating(self) -> None:
        self._seed_materials([
            {"material_id": "pad_opened", "title_cn": "翻开的册子", "status": "OPENED_PARTIAL"},
            {"material_id": "pad_unopened", "title_cn": "还没拆的册子", "status": "RECEIVED_UNOPENED"},
            {"material_id": "pad_withdrawn", "title_cn": "撤回的册子", "status": "WITHDRAWN"},
            {"material_id": "pad_discarded", "title_cn": "丢掉的册子", "status": "DISCARDED"},
        ])

        self.assertEqual(
            edl.verify_concern_origin_observed_readonly(self.root, "material:pad_opened"),
            (True, "material_observed"),
        )
        for bad in ("material:pad_unopened", "material:pad_withdrawn", "material:pad_discarded",
                    "material:missing"):
            ok, reason = edl.verify_concern_origin_observed_readonly(self.root, bad)
            self.assertFalse(ok, bad)
            self.assertEqual(reason, "material_withdrawn_or_unopened", bad)

        applied = self._emerge(domain="creation", origin_kind="sustained_interest",
                               origin_refs=["material:pad_opened"], expression="想把这段抄进小册")
        self.assertEqual(applied["status"], "applied")

        self._assert_rejected(domain="creation", origin_refs=["material:pad_unopened"])
        self._assert_rejected(domain="creation", origin_refs=["material:pad_withdrawn"])

    def test_03_place_origin_gating(self) -> None:
        self._seed_places({
            "riverside_bench": {"place_id": "riverside_bench", "knowledge_status": "visited"},
            "forest_trail": {"place_id": "forest_trail", "knowledge_status": "unknown"},
        })

        self.assertEqual(
            edl.verify_concern_origin_observed_readonly(self.root, "place:riverside_bench"),
            (True, "place_visited"),
        )
        for bad in ("place:forest_trail", "place:missing"):
            ok, reason = edl.verify_concern_origin_observed_readonly(self.root, bad)
            self.assertFalse(ok, bad)
            self.assertEqual(reason, "place_not_visited", bad)

        applied = self._emerge(origin_refs=["place:riverside_bench"])
        self.assertEqual(applied["status"], "applied")

        self._assert_rejected(origin_refs=["place:forest_trail"])
        self._assert_rejected(origin_refs=["place:missing"])

    def test_04_plain_refs_stay_backwards_compatible(self) -> None:
        # split_structured_origin_ref only recognises the three grounded prefixes.
        self.assertEqual(edl.split_structured_origin_ref("object:cup_01"), ("object", "cup_01"))
        self.assertEqual(edl.split_structured_origin_ref("material:pad_1"), ("material", "pad_1"))
        self.assertEqual(edl.split_structured_origin_ref("place:bench"), ("place", "bench"))
        self.assertIsNone(edl.split_structured_origin_ref("object:   "))
        self.assertIsNone(edl.split_structured_origin_ref("今天窗边很亮"))
        self.assertIsNone(edl.split_structured_origin_ref(""))
        self.assertEqual(
            edl.verify_concern_origin_observed_readonly(self.root, "今天窗边很亮"),
            (True, "unstructured_ref_allowed"),
        )

        # A plain origin emerges cleanly by default...
        plain = self._emerge(origin_refs=["今天窗边很亮"])
        self.assertEqual(plain["status"], "applied")
        # ...but is refused when the caller demands full grounding.
        self._assert_rejected(origin_refs=["今天窗边很亮"], require_observed_origin=True)


class SourceLossSubsidenceTest(_LifecycleBase):
    """Ch.35 §五: a source-lost retained concern subsides with zero penalty and zero write."""

    def _assert_zero_write_of_state(self, before_stat: tuple[int, int], before_tree: dict) -> None:
        state_path = edl.everyday_desire_state_path(self.root)
        self.assertEqual(_stat_tuple(state_path), before_stat, "projection must not rewrite the state file")
        self.assertEqual(_snapshot_tree(self.root), before_tree, "projection must be 100% zero-write")

    def test_01_material_loss_subsides_quietly(self) -> None:
        self._seed_materials([{"material_id": "mat_live", "title_cn": "翻开过的册子", "status": "OPENED_PARTIAL"}])
        applied = self._emerge(
            domain="creation",
            origin_kind="sustained_interest",
            origin_refs=["material:mat_live"],
            expression="想把这段抄进小册",
            persistence="retained",
        )
        concern_ref = applied["action_receipt"]["concern"]["concern_ref"]
        self.assertIn(concern_ref, [row["concern_ref"] for row in applied["retained_concerns"]])

        # Source physically vanishes: the shared material is withdrawn by the partner.
        self._seed_materials([{"material_id": "mat_live", "title_cn": "翻开过的册子", "status": "WITHDRAWN"}])

        state_path = edl.everyday_desire_state_path(self.root)
        before_stat = _stat_tuple(state_path)
        before_tree = _snapshot_tree(self.root)

        proj = edl.project_everyday_desires(self.root, now_iso=NOW)

        self._assert_zero_write_of_state(before_stat, before_tree)
        self.assertEqual(proj["retained_concerns"], [])
        self.assertEqual(len(proj["auto_subsided_concerns"]), 1)
        row = proj["auto_subsided_concerns"][0]
        self.assertEqual(row["concern_ref"], concern_ref)
        self.assertEqual(row["reason"], "origin_no_longer_present")
        self.assertEqual(row["frustration_debt"], 0)
        self.assertEqual(row["penalty"], 0)
        self.assertEqual(row["resentment_delta"], 0)
        self.assertEqual(row["illness_delta"], 0)

        # The on-disk concern is untouched: subsidence is a read-time projection only.
        stored, err = edl.read_everyday_desire_state(self.root)
        self.assertIsNone(err)
        self.assertEqual(len(stored["retained_concerns"]), 1)
        self.assertEqual(stored["retained_concerns"][0]["status"], "ACTIVE")

    def test_02_object_consumed_subsides_quietly(self) -> None:
        self._seed_matter({
            "cup_02": {"object_id": "cup_02", "label_cn": "白瓷杯", "item_kind": "vessel", "parent_id": "home_desk"},
        })
        applied = self._emerge(
            domain="comfort",
            origin_refs=["object:cup_02"],
            expression="想用这只杯子喝口水",
            persistence="retained",
        )
        concern_ref = applied["action_receipt"]["concern"]["concern_ref"]

        # Object is consumed/discarded -> no longer physically present.
        self._seed_matter({
            "cup_02": {"object_id": "cup_02", "label_cn": "白瓷杯", "item_kind": "vessel",
                       "parent_id": "home_desk", "consumed": True},
        })

        state_path = edl.everyday_desire_state_path(self.root)
        before_stat = _stat_tuple(state_path)
        before_tree = _snapshot_tree(self.root)
        proj = edl.project_everyday_desires(self.root, now_iso=NOW)

        self._assert_zero_write_of_state(before_stat, before_tree)
        self.assertEqual(proj["retained_concerns"], [])
        self.assertEqual(len(proj["auto_subsided_concerns"]), 1)
        row = proj["auto_subsided_concerns"][0]
        self.assertEqual(row["concern_ref"], concern_ref)
        self.assertEqual(row["reason"], "origin_no_longer_present")
        self.assertEqual(row["origin_reason"], "object_not_observed_or_missing")
        self.assertEqual(row["penalty"], 0)
        self.assertEqual(row["resentment_delta"], 0)
        self.assertEqual(row["illness_delta"], 0)

    def test_03_plain_origin_never_auto_subsides(self) -> None:
        applied = self._emerge(
            domain="creation",
            origin_refs=["手边这张稿纸"],
            expression="想把这一页写完",
            persistence="retained",
        )
        concern_ref = applied["action_receipt"]["concern"]["concern_ref"]
        proj = edl.project_everyday_desires(self.root, now_iso=NOW)
        self.assertEqual(proj["auto_subsided_concerns"], [])
        self.assertIn(concern_ref, [row["concern_ref"] for row in proj["retained_concerns"]])


class IntimacyChannelIsolationTest(_LifecycleBase):
    """Ch.35: an active intimacy concern never leaks onto ordinary/public channels."""

    def setUp(self) -> None:
        super().setUp()
        self.applied = edl.apply_everyday_desire_action(
            self.root,
            {
                "action": "emerge_concern",
                "domain": "intimacy",
                "origin_kind": "relational_wish",
                "origin_refs": ["留给你的一句话"],
                "expression": "想靠得更近一点",
                "persistence": "retained",
            },
            now_iso=NOW,
            channel_visibility="private_intimate",
            adult_gate_open=True,
        )
        self.concern_ref = self.applied["action_receipt"]["concern"]["concern_ref"]

    def _project(self, channel: str, gate: bool) -> dict[str, Any]:
        return edl.project_everyday_desires(
            self.root, now_iso=NOW, channel_visibility=channel, adult_gate_open=gate
        )

    def test_01_visible_only_on_private_intimate_with_gate_open(self) -> None:
        visible = self._project("private_intimate", True)
        self.assertIn(self.concern_ref, [row["concern_ref"] for row in visible["retained_concerns"]])

        for channel, gate in (
            ("ordinary_work", True),
            ("public_or_shared", True),
            ("private_intimate", False),
            ("subject_private", False),
        ):
            proj = self._project(channel, gate)
            blob = json.dumps(proj, ensure_ascii=False)
            self.assertNotIn(self.concern_ref, blob, f"{channel}/{gate}")
            self.assertNotIn("想靠得更近一点", blob, f"{channel}/{gate}")
            self.assertEqual(
                [row["domain"] for row in proj["retained_concerns"] if row["domain"] == "intimacy"],
                [],
                f"{channel}/{gate}",
            )

    def test_02_turn_only_intimacy_also_isolated(self) -> None:
        edl.apply_everyday_desire_action(
            self.root,
            {
                "action": "emerge_concern",
                "domain": "intimacy",
                "origin_kind": "relational_wish",
                "origin_refs": ["安静的傍晚"],
                "expression": "只想靠近一会儿",
                "persistence": "turn_only",
            },
            now_iso=NOW,
            channel_visibility="private_intimate",
            adult_gate_open=True,
        )
        self.assertNotIn("只想靠近一会儿", json.dumps(self._project("ordinary_work", True), ensure_ascii=False))

    def test_03_arbitration_filters_intimacy_on_wrong_channel(self) -> None:
        concern = {
            "concern_ref": self.concern_ref,
            "domain": "intimacy",
            "origin_refs": ["留给你的一句话"],
            "expression": "想靠得更近一点",
            "persistence": "retained",
            "status": "ACTIVE",
        }
        closed = edl.arbitrate_six_concerns([concern], channel_visibility="private_intimate", adult_gate_open=False)
        self.assertEqual(closed["chosen_primary"], None)
        self.assertEqual(closed["filtered_concerns"][0]["reason_code"], "ADULT_GATE_CLOSED")

        ordinary = edl.arbitrate_six_concerns([concern], channel_visibility="ordinary_work", adult_gate_open=True)
        self.assertEqual(ordinary["chosen_primary"], None)
        self.assertEqual(
            ordinary["filtered_concerns"][0]["reason_code"], "SENSITIVE_CONCERN_WITHHELD_ON_CHANNEL"
        )

        private = edl.arbitrate_six_concerns(
            [concern], channel_visibility="private_intimate", adult_gate_open=True
        )
        self.assertEqual(private["chosen_primary"]["domain"], "intimacy")


class IdempotencyEvictionOrderTest(_LifecycleBase):
    """RMW + submission-order (``op_seq``) eviction, never alphabetical key order."""

    # 70 distinct keys: the newest submission (``op-01``) is alphabetically smallest,
    # so any alphabetical retention would keep ``op-70`` and drop ``op-01`` -- the reverse
    # of what submission order requires.
    def _submit_series(self, count: int = 70) -> list[str]:
        keys: list[str] = []
        for idx in range(count):
            key = f"op-{count - idx:02d}"
            result = edl.apply_everyday_desire_action(
                self.root,
                {"action": "advance_turn", "operation_key": key},
                now_iso=NOW,
            )
            self.assertEqual(result["status"], "applied", key)
            keys.append(key)
        return keys

    def test_01_oldest_op_seq_evicted_not_alphabetical(self) -> None:
        keys = self._submit_series(70)
        self.assertEqual(len({*keys}), 70)
        self.assertEqual(edl.MAX_IDEMPOTENCY_RECORDS, 64)

        state, err = edl.read_everyday_desire_state(self.root)
        self.assertIsNone(err)
        idem = state["idempotency"]
        self.assertEqual(len(idem), 64)

        evicted = [key for key in keys if key not in idem]
        self.assertEqual(evicted, [f"op-{n:02d}" for n in range(70, 64, -1)])
        self.assertIn("op-01", idem, "newest submission must survive even though it sorts first")
        self.assertNotIn("op-70", idem, "oldest submission must be evicted even though it sorts last")

        # Exactly the oldest six receipts are summarised into the committed ring.
        ring_keys = {row.get("operation_key") for row in state["committed_op_ring"]}
        self.assertTrue(set(evicted).issubset(ring_keys))

        seqs = sorted(int(row["op_seq"]) for row in idem.values() if row.get("op_seq") is not None)
        self.assertEqual(seqs, list(range(7, 71)))

    def test_02_replay_and_conflict_semantics(self) -> None:
        keys = self._submit_series(70)
        oldest, newest = keys[0], keys[-1]
        self.assertEqual((oldest, newest), ("op-70", "op-01"))
        rev_before = edl.read_everyday_desire_state(self.root)[0]["revision"]

        # Evicted key replays from the committed ring, not as a fresh action.
        replay_old = edl.apply_everyday_desire_action(
            self.root, {"action": "advance_turn", "operation_key": oldest}, now_iso=NOW
        )
        self.assertEqual(replay_old["status"], "already_applied")
        self.assertTrue(replay_old["replayed"])
        self.assertEqual(replay_old["reconciled_from"], "committed_op_ring")

        # Retained key replays straight from the idempotency map.
        replay_new = edl.apply_everyday_desire_action(
            self.root, {"action": "advance_turn", "operation_key": newest}, now_iso=NOW
        )
        self.assertEqual(replay_new["status"], "already_applied")
        self.assertEqual(replay_new["reconciled_from"], "idempotency")

        # Replays never mutate state.
        self.assertEqual(edl.read_everyday_desire_state(self.root)[0]["revision"], rev_before)

        # A changed payload under an evicted key is a hard conflict, never a silent fresh action.
        with self.assertRaises(edl.EverydayDesireError) as ctx:
            edl.apply_everyday_desire_action(
                self.root,
                {"action": "advance_turn", "operation_key": oldest, "new_scene_id": "elsewhere"},
                now_iso=NOW,
            )
        self.assertEqual(ctx.exception.reason_code, "IDEMPOTENCY_KEY_CONFLICT")
        self.assertEqual(ctx.exception.http_status, 409)

    def test_03_retry_window_expired_classification(self) -> None:
        # The lifecycle never passes ``expected_revision``; the guard's long-range
        # reconciliation still classifies a too-old retry explicitly rather than as a new op.
        state = {"revision": 10, "evicted_up_to_revision": 5, "idempotency": {}, "committed_op_ring": []}
        outcome, detail = guard.reconcile_evicted_or_active_op(
            None, state, "op-gone", dict_key="idempotency", expected_revision=3, payload_hash="h"
        )
        self.assertEqual(outcome, "expired")
        self.assertEqual(detail["reason_code"], "RETRY_WINDOW_EXPIRED")

        still_inside = guard.reconcile_evicted_or_active_op(
            None, state, "op-gone", dict_key="idempotency", expected_revision=8, payload_hash="h"
        )
        self.assertEqual(still_inside[0], "miss")

    def test_04_concurrent_same_key_applies_once(self) -> None:
        payload = {"action": "advance_turn", "operation_key": "concurrent-key"}

        def _submit() -> str:
            return edl.apply_everyday_desire_action(self.root, dict(payload), now_iso=NOW)["status"]

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            statuses = list(pool.map(lambda _i: _submit(), range(8)))

        self.assertEqual(statuses.count("applied"), 1, statuses)
        self.assertEqual(statuses.count("already_applied"), 7, statuses)

        state, err = edl.read_everyday_desire_state(self.root)
        self.assertIsNone(err)
        self.assertEqual(state["revision"], 1, "the write must land exactly once")
        self.assertEqual(state["turn_index"], 1)
        self.assertEqual(len(state["idempotency"]), 1)


class CompactSituationHeaderTest(unittest.TestCase):
    """Ch.47 §三/§七: one compact single-situation header + zero-write budget telemetry."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-ch47-")
        self.root = Path(self._tmp.name) / "day"
        self.root.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _cfg(self, *, private: bool = True) -> dict[str, Any]:
        return {
            "day_root": str(self.root),
            "phone": {"enabled": True, "private_artifacts": private},
        }

    def _seed_concern(self, *, domain: str = "exploration", expression: str = "想把窗台的薄荷记下来") -> None:
        edl.apply_everyday_desire_action(
            self.root,
            {
                "action": "emerge_concern",
                "domain": domain,
                "origin_kind": "immediate_opportunity",
                "origin_refs": ["窗台那盆薄荷"],
                "expression": expression,
                "persistence": "retained",
            },
            now_iso=NOW,
        )

    def test_01_calm_default_is_silent(self) -> None:
        self.assertEqual(lcb._format_compact_current_situation(), "")
        self.assertEqual(
            lcb._format_compact_current_situation(v04_nb={}, v04_ep={}, autonomy_doc={}, tension_doc={}),
            "",
        )

    def test_02_journey_variants_render_one_line(self) -> None:
        moving = lcb._format_compact_current_situation(
            v04_nb={"journey": {"status": "in_transit", "target_place_id": "旧书店"}}
        )
        self.assertTrue(moving.startswith("当前处境："), moving)
        self.assertIn("正在往旧书店去", moving)
        self.assertLessEqual(len(moving), 160)

        paused = lcb._format_compact_current_situation(
            v04_nb={"outing_continuity": {"paused_at_node": "河堤岔口"}}
        )
        self.assertIn("正停在河堤岔口", paused)

        turning = lcb._format_compact_current_situation(
            v04_nb={"journey": {"is_turning_back": True}}
        )
        self.assertIn("折返", turning)

    def test_03_active_project_and_privacy(self) -> None:
        ep = {
            "personal_projects": [
                {"status": "active", "purpose_cn": "河边长椅小册", "next_step_cn": "补一页昨天傍晚的光"}
            ]
        }
        line = lcb._format_compact_current_situation(v04_ep=ep, include_private=True)
        self.assertIn("手头在做「河边长椅小册」", line)
        self.assertLessEqual(len(line), 160)

        # A private project never surfaces without private artifacts.
        self.assertEqual(lcb._format_compact_current_situation(v04_ep=ep, include_private=False), "")

        # A resting/shelved project never nags a compact line.
        resting = {"personal_projects": [{"status": "resting_not_feeling_it", "purpose_cn": "暂时不想画的地图边角"}]}
        self.assertEqual(
            lcb._format_compact_current_situation(v04_ep=resting, include_private=True),
            "",
        )

    def test_04_active_concern_renders_and_intimacy_is_isolated(self) -> None:
        self._seed_concern()
        line = lcb._format_compact_current_situation(day_root=self.root, now_iso=NOW, include_private=True)
        self.assertIn("当前处境：", line)
        self.assertIn("心里还惦记着", line)
        self.assertIn("薄荷", line)
        self.assertLessEqual(len(line), 160)

        # An intimacy concern is written but must never surface on this ordinary line.
        self._seed_concern(domain="intimacy", expression="想靠得更近一点")
        after = lcb._format_compact_current_situation(day_root=self.root, now_iso=NOW, include_private=True)
        self.assertNotIn("想靠得更近一点", after)

    def test_05_measure_calm_root_is_zero_write_and_silent(self) -> None:
        calm_root = Path(self._tmp.name) / "never-created"
        diag = lcb.measure_context_budget_breakdown(calm_root)
        self.assertEqual(diag["rendered_text"], "")
        self.assertEqual(diag["total_chars"], 0)
        self.assertTrue(diag["calm_silent"])
        self.assertTrue(diag["within_budget"])
        self.assertEqual(diag["kept_section_names"], [])
        self.assertFalse(calm_root.exists(), "a calm read must not create the root")

    def test_06_measure_active_concern_compact_line_within_budget(self) -> None:
        self._seed_concern()
        before = _snapshot_tree(self.root)
        diag = lcb.measure_context_budget_breakdown(self.root, self._cfg())
        self.assertEqual(_snapshot_tree(self.root), before, "budget telemetry must be zero-write")

        self.assertIn("当前处境：", diag["rendered_text"])
        self.assertIn("compact_situation_line", diag["per_section_chars"])
        self.assertLessEqual(diag["per_section_chars"]["compact_situation_line"], 160)
        self.assertLessEqual(diag["total_chars"], 1800)
        self.assertTrue(diag["within_budget"])
        self.assertFalse(diag["calm_silent"])

    def test_07_crowded_pressure_keeps_commitment_lines_over_flavor(self) -> None:
        _write_json(
            self.root / "phone" / "emotional_tension.json",
            {
                "tension_summary": {"subject_is_busy": True},
                "schedule": {"availability_summary": "正在赶一份很长的校对稿"},
            },
        )
        _write_json(
            self.root / "phone" / "v04_elastic_play.json",
            {
                "schema": "v04.elastic_play.v1",
                "revision": 3,
                "temporary_game": {
                    "status": "active",
                    "game_kind": "十分钟纸牌",
                    "prop_label": "旧纸牌",
                    "turns": [
                        {"actor_side": "subject", "turn_text": "我先出一张红桃"},
                        {"actor_side": "partner", "turn_text": "那我跟一张黑桃"},
                    ],
                },
                "personal_projects": [],
            },
        )
        _write_json(
            self.root / "phone" / "wardrobe.json",
            {"selection": {"homewear": {"style": "米色针织衫"}, "hairband_style": "细发带"}},
        )

        heavy_hint = "背景" * 450  # truncated to 850 chars, eating the shared budget
        diag = lcb.measure_context_budget_breakdown(
            self.root, self._cfg(), ephemeral_hint=heavy_hint
        )
        rendered = diag["rendered_text"]
        self.assertLessEqual(diag["total_chars"], 1800)
        self.assertTrue(diag["within_budget"])
        self.assertIn("日程状态", rendered)
        self.assertIn("临时小游戏进行中", rendered)

        kept = diag["kept_section_names"]
        self.assertIn("autonomy_line", kept)
        self.assertIn("interactive_line", kept)
        self.assertLess(kept.index("autonomy_line"), kept.index("interactive_line"))

        if "anchor_line" in kept:
            self.assertLess(kept.index("interactive_line"), kept.index("anchor_line"))
        else:
            self.assertIn("anchor_line", diag["evicted_section_names"])

    def test_08_ranking_puts_flavor_strictly_last(self) -> None:
        sections = [
            ("autonomy_line", "- 自主边界" + "x" * 40),
            ("interactive_line", "- 互动" + "a" * 40),
            ("v03_line", "- v03" + "d" * 40),
            ("desk_line", "- 书桌" + "j" * 40),
            ("sensory_line", "- 体感" + "k" * 40),
            ("anchor_line", "- 锚点" + "l" * 40),
        ]
        # Cold cues boost sensory/v03, but flavor still sorts after every commitment line.
        ranked = lcb._rank_middle_sections_by_situation(
            sections, clean_incoming="今天好冷，有点发烧，穿少了，好累"
        )
        names = [name for name, _text in ranked]
        self.assertEqual(names[0], "autonomy_line")
        flavor = {"desk_line", "sensory_line", "anchor_line"}
        last_commitment = max(idx for idx, name in enumerate(names) if name not in flavor)
        first_flavor = min(idx for idx, name in enumerate(names) if name in flavor)
        self.assertLess(last_commitment, first_flavor, names)

        # Empty incoming restores the exact historical order, autonomy pinned first.
        historical = lcb._rank_middle_sections_by_situation(sections, clean_incoming="")
        self.assertEqual([name for name, _ in historical], [name for name, _ in sections])


if __name__ == "__main__":
    unittest.main()
