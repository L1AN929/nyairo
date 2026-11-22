#!/usr/bin/env python3
"""v0.2 统一状态汇总与脑区接线单测。

覆盖：
1. v08 matter space 房间桌面物件在主/伴侣双视角进入 ``chatless_traces``，
   伴侣视角绝不泄漏私密物件；
2. ``v02_recall_projection`` P12 自传体章节并入 ``periods``，伴侣视角按共享隔离；
3. 旧版 ``v02_runtime_migration`` 省电/休眠档同步反映到 ``budget_survival``；
4. ``v02_common_ground`` 轻量共在协议并入 ``rescaled_copractices``；
5. Forget 暂停/墓碑同时拦截新旧来源；
6. ``read_v02_honest_ux_view`` 在空目录、缺失目录与有状态目录下 100% 零磁盘写入。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "scripts"),
    str(ROOT / "tests"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from chiyo_bundle import phone_tool  # noqa: E402
from components.life.scripts import (  # noqa: E402
    v02_autobiography_skill_provenance as _asp,
    v02_lived_slices_and_vd_protocols as _lived_slices,
    v02_representation_lease_forget as _rlf,
)
from components.phone import (  # noqa: E402
    capability as phone_capability,
    v02_agency_depth_view,
    v02_honest_ux_view,
    v02_runtime_migration_view,
)

POSIX_ONLY = unittest.skipUnless(
    hasattr(socket, "AF_UNIX") and os.name == "posix",
    "Requires POSIX domain sockets (run inside WSL)",
)

NOW = "2026-11-18T10:00:00+08:00"


def _write_json(root: Path, rel: tuple[str, ...], payload: dict[str, Any]) -> Path:
    path = root.joinpath(*rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    path.write_text(body, encoding="utf-8", newline="\n")
    return path


def _view(root: Path, *, subject: bool) -> dict[str, Any]:
    code, doc = v02_honest_ux_view.read_v02_honest_ux_view(
        root,
        has_subject_capability=subject,
        now_iso=NOW,
    )
    assert code == 200 and doc.get("ok") is True, doc
    assert doc.get("llm_calls") == 0, doc
    data = doc["data"]
    assert data.get("llm_calls") == 0
    return data


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int, str]]:
    if not root.exists():
        return {}
    snap: dict[str, tuple[int, int, str]] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        stat = path.lstat()
        if path.is_file() and not path.is_symlink():
            snap[rel] = (int(stat.st_size), int(stat.st_mtime_ns), hashlib.sha256(path.read_bytes()).hexdigest())
        else:
            snap[rel] = (0, int(stat.st_mtime_ns), "dir")
    return snap


def _seed_matter_space(root: Path) -> None:
    _write_json(
        root,
        ("world", "v08_matter_space.json"),
        {
            "zones": {
                "desk_room": {
                    "zone_id": "desk_room",
                    "label_cn": "书桌房间",
                    "visibility": "shared",
                }
            },
            "matter_objects": {
                "obj_desk_lamp": {
                    "object_id": "obj_desk_lamp",
                    "label_cn": "书桌上的台灯",
                    "parent_id": "desk_room",
                    "active": True,
                },
                "obj_private_letter": {
                    "object_id": "obj_private_letter",
                    "label_cn": "抽屉里的私信",
                    "parent_id": "desk_room",
                    "visibility": "private",
                    "active": True,
                },
                "obj_flagged_private": {
                    "object_id": "obj_flagged_private",
                    "label_cn": "标记为私密的本子",
                    "parent_id": "desk_room",
                    "private": True,
                    "active": True,
                },
                "obj_retired": {
                    "object_id": "obj_retired",
                    "label_cn": "已经收起的旧物",
                    "parent_id": "desk_room",
                    "active": False,
                },
            },
        },
    )


def _seed_recall_memories(root: Path) -> None:
    _write_json(
        root,
        ("memory", "v02_recall_projection_state.json"),
        {
            "memories": {
                "mem_shared_walk": {
                    "occurred_at": "2026-10-05T09:30:00+08:00",
                    "topic": "海边散步",
                    "gist_summary": "十月一起沿着海堤走了很久。",
                    "shared": True,
                },
                "mem_private_note": {
                    "occurred_at": "2026-11-06T21:00:00+08:00",
                    "topic": "私下记下的小事",
                    "gist_summary": "十一月独自记下的一件小事。",
                    "shared": False,
                },
            },
            "tombstones": {},
        },
    )


def _seed_common_ground(root: Path) -> None:
    _write_json(
        root,
        ("phone", "v02_common_ground.json"),
        {
            "items": {
                "cg_copresence": {
                    "item_id": "cg_copresence",
                    "topic": "傍晚各做各的",
                    "summary": "傍晚开着视频各做各的事。",
                    "cadence": "co_presence",
                    "ground_status": "SHARED_WITH_PARTNER",
                },
                "cg_private_relay": {
                    "item_id": "cg_private_relay",
                    "topic": "只属于自己的轻接力",
                    "summary": "还没和 TA 说过的节奏。",
                    "cadence": "light_relay",
                    "ground_status": "PRIVATE_UNSHARED",
                },
                "cg_daily_plan": {
                    "item_id": "cg_daily_plan",
                    "topic": "每天固定的安排",
                    "summary": "已经按日常节奏运行。",
                    "cadence": "daily",
                    "ground_status": "SHARED_WITH_PARTNER",
                },
            }
        },
    )


class TestV02UnifiedStateAndBrainWiring(unittest.TestCase):
    def test_01_matter_space_objects_visible_in_both_views_and_partner_redacts_private(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _seed_matter_space(root)

            subject = _view(root, subject=True)
            partner = _view(root, subject=False)
            subject_objects = subject["chatless_traces"]["objects"]
            partner_objects = partner["chatless_traces"]["objects"]

            self.assertTrue(subject_objects, "subject must see room/desk matter objects")
            self.assertTrue(partner_objects, "partner must see ordinary shared matter objects")
            subject_ids = {row["object_id"] for row in subject_objects}
            partner_ids = {row["object_id"] for row in partner_objects}

            self.assertIn("obj_desk_lamp", subject_ids)
            self.assertIn("obj_private_letter", subject_ids)
            self.assertIn("obj_flagged_private", subject_ids)
            self.assertNotIn("obj_retired", subject_ids)
            self.assertNotIn("obj_retired", partner_ids)

            self.assertIn("obj_desk_lamp", partner_ids)
            self.assertNotIn("obj_private_letter", partner_ids)
            self.assertNotIn("obj_flagged_private", partner_ids)

            lamp = next(row for row in partner_objects if row["object_id"] == "obj_desk_lamp")
            self.assertEqual(lamp["title_cn"], "书桌上的台灯")
            self.assertEqual(lamp["zone_id"], "desk_room")
            self.assertTrue(lamp["shared"])
            self.assertEqual(set(partner["chatless_traces"].keys()), {"objects", "works", "bookmarks", "waits"})

    def test_02_recall_narrative_chapters_merge_into_periods_with_partner_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _seed_recall_memories(root)

            subject = _view(root, subject=True)
            partner = _view(root, subject=False)
            subject_ids = [row["period_id"] for row in subject["periods"]]
            partner_ids = [row["period_id"] for row in partner["periods"]]

            self.assertIn("ch_2026_10", subject_ids)
            self.assertIn("ch_2026_11", subject_ids)
            self.assertIn("ch_2026_10", partner_ids)
            self.assertNotIn("ch_2026_11", partner_ids, "private month must stay off the partner view")

            for row in subject["periods"] + partner["periods"]:
                self.assertTrue(row.get("period_id"))
                self.assertTrue(row.get("title_cn"))
                self.assertIn("shared", row)

    def test_03_legacy_runtime_migration_power_modes_reflect_into_budget_survival(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            _write_json(
                root,
                ("phone", "v02_runtime_migration.json"),
                {"mode_config": {"mode": "LOW_BUDGET", "budget_scale_pct": 25}},
            )
            low = _view(root, subject=True)["budget_survival"]
            self.assertEqual(low["mode"], "LOW_BUDGET")
            self.assertEqual(low["legacy_runtime_mode"], "LOW_BUDGET")
            self.assertEqual(low["budget_scale_pct"], 25)

            _write_json(
                root,
                ("phone", "v02_runtime_migration.json"),
                {"runtime_mode": "DORMANT"},
            )
            dormant = _view(root, subject=False)["budget_survival"]
            self.assertEqual(dormant["mode"], "DORMANT")
            self.assertEqual(dormant["legacy_runtime_mode"], "DORMANT")
            self.assertEqual(dormant["budget_scale_pct"], 0)

    def test_04_common_ground_light_copresence_merges_into_rescaled_copractices(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _seed_common_ground(root)

            subject = _view(root, subject=True)
            partner = _view(root, subject=False)
            subject_ids = {row["practice_id"] for row in subject["rescaled_copractices"]}
            partner_ids = {row["practice_id"] for row in partner["rescaled_copractices"]}

            self.assertIn("cg_copresence", subject_ids)
            self.assertIn("cg_private_relay", subject_ids)
            self.assertNotIn("cg_daily_plan", subject_ids, "non-light cadence must not be merged")

            self.assertIn("cg_copresence", partner_ids)
            self.assertNotIn("cg_private_relay", partner_ids)
            self.assertNotIn("cg_daily_plan", partner_ids)

            row = next(item for item in partner["rescaled_copractices"] if item["practice_id"] == "cg_copresence")
            self.assertEqual(row["scale_mode"], "co_presence")
            self.assertTrue(row["shared"])

    def test_05_forget_barrier_blocks_new_and_legacy_sources(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _seed_matter_space(root)
            _seed_common_ground(root)
            _write_json(
                root,
                ("phone", "v08_living_traces.json"),
                {
                    "traces": [
                        {
                            "trace_id": "trace_keep",
                            "object_id": "obj_trace_keep",
                            "object_label_cn": "留在桌上的杯子",
                            "state_sticker_cn": "刚倒过水",
                            "visibility": "shared",
                            "fade_mode": "until_touched",
                        },
                        {
                            "trace_id": "trace_blocked",
                            "object_id": "obj_trace_blocked",
                            "object_label_cn": "被忘掉的杯子",
                            "state_sticker_cn": "不该再出现",
                            "visibility": "shared",
                            "fade_mode": "until_touched",
                        },
                    ]
                },
            )

            free = _view(root, subject=False)
            free_objects = {row["object_id"] for row in free["chatless_traces"]["objects"]}
            free_practices = {row["practice_id"] for row in free["rescaled_copractices"]}
            self.assertIn("obj_desk_lamp", free_objects)
            self.assertIn("obj_trace_blocked", free_objects)
            self.assertIn("cg_copresence", free_practices)

            _write_json(
                root,
                ("governance", "v02_representation_lease_forget_state.json"),
                {
                    "holds": {
                        "hold_merge": {
                            "hold_id": "hold_merge",
                            "status": "HOLD_PENDING_SCOPE_CLARIFICATION",
                            "read_blocked": True,
                            "candidate_source_ids": [
                                "obj_desk_lamp",
                                "obj_trace_blocked",
                                "cg_copresence",
                            ],
                        }
                    }
                },
            )

            subject = _view(root, subject=True)
            partner = _view(root, subject=False)
            for data in (subject, partner):
                objects = {row["object_id"] for row in data["chatless_traces"]["objects"]}
                practices = {row["practice_id"] for row in data["rescaled_copractices"]}
                self.assertNotIn("obj_desk_lamp", objects)
                self.assertNotIn("obj_trace_blocked", objects)
                self.assertNotIn("cg_copresence", practices)

    def test_06_read_view_is_zero_write_on_missing_empty_and_populated_roots(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)

            missing = base / "missing_instance"
            missing_snapshot = _snapshot_tree(missing)
            _view(missing, subject=False)
            _view(missing, subject=True)
            self.assertFalse(missing.exists(), "read must not materialise a missing root")
            self.assertEqual(missing_snapshot, _snapshot_tree(missing))

            empty = base / "empty_instance"
            empty.mkdir()
            empty_before = _snapshot_tree(empty)
            _view(empty, subject=False)
            _view(empty, subject=True)
            self.assertEqual(empty_before, _snapshot_tree(empty))

            populated = base / "populated_instance"
            _seed_matter_space(populated)
            _seed_recall_memories(populated)
            _seed_common_ground(populated)
            _write_json(
                populated,
                ("phone", "v02_runtime_migration.json"),
                {"mode_config": {"mode": "LOW_BUDGET", "budget_scale_pct": 40}},
            )
            _write_json(
                populated,
                ("governance", "v02_representation_lease_forget_state.json"),
                {
                    "holds": {
                        "hold_zero_write": {
                            "hold_id": "hold_zero_write",
                            "status": "HOLD_PENDING_SCOPE_CLARIFICATION",
                            "read_blocked": True,
                            "candidate_source_ids": ["obj_desk_lamp"],
                        }
                    }
                },
            )
            populated_before = _snapshot_tree(populated)
            partner = _view(populated, subject=False)
            subject = _view(populated, subject=True)
            self.assertEqual(populated_before, _snapshot_tree(populated))
            self.assertEqual(partner["perspective"], "partner")
            self.assertEqual(subject["perspective"], "subject")
            self.assertTrue(subject["chatless_traces"]["objects"])

            live_day = base / "live_day_instance"
            (live_day / "one-day").mkdir(parents=True)
            live_before = _snapshot_tree(live_day)
            live_partner = _view(live_day, subject=False)
            live_subject = _view(live_day, subject=True)
            self.assertEqual(live_before, _snapshot_tree(live_day))
            live_partner_ids = {row["object_id"] for row in live_partner["chatless_traces"]["objects"]}
            live_subject_ids = {row["object_id"] for row in live_subject["chatless_traces"]["objects"]}
            self.assertIn("ceramic_cup_01", live_partner_ids)
            self.assertIn("pen_01", live_partner_ids)
            self.assertIn("ceramic_cup_01", live_subject_ids)

    def test_08_delegated_views_enforce_partner_403_and_reject_nested_forbidden(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for view_mod, act, bad_payload in (
                (
                    v02_agency_depth_view.mutate_v02_agency_depth_view,
                    "salvage_work_scrap",
                    {"action": "salvage_work_scrap", "nested": {"uid": 0}},
                ),
                (
                    v02_agency_depth_view.mutate_v02_agency_depth_view,
                    "simplify_personal_tool",
                    {"action": "simplify_personal_tool", "nested": {"owner": "spoof"}},
                ),
                (
                    v02_agency_depth_view.mutate_v02_agency_depth_view,
                    "record_period_snapshot",
                    {"action": "record_period_snapshot", "nested": {"authority": "fake"}},
                ),
                (
                    v02_runtime_migration_view.mutate_v02_runtime_migration_view,
                    "explicit_import_representation",
                    {"action": "explicit_import_representation", "artifact_id": "a1", "nested": {"uid": 0}},
                ),
                (
                    v02_runtime_migration_view.mutate_v02_runtime_migration_view,
                    "approve_pending_forget_hold",
                    {"action": "approve_pending_forget_hold", "case_id": "h1", "nested": {"owner": "spoof"}},
                ),
            ):
                p_code, p_doc = view_mod(root, {"action": act}, actor_side="partner", now_iso=NOW)
                self.assertEqual(p_code, 403, f"partner must be 403 for {act}: {p_doc}")
                self.assertEqual(p_doc.get("reason_code"), "FORBIDDEN_ACTOR_SIDE")

                n_code, n_doc = view_mod(root, bad_payload, actor_side="subject", now_iso=NOW)
                self.assertEqual(n_code, 400, f"nested forbidden key must be 400 for {act}: {n_doc}")
                self.assertEqual(n_doc.get("reason_code"), "INVALID_FIELDS")

            self.assertEqual(len(phone_tool.LIFE_UPDATE_SURFACES), 37)
            self.assertEqual(phone_tool.LIFE_READ_SURFACES.get("v02_honest_ux"), "/api/phone/v02/honest-ux")


@POSIX_ONLY
class TestV02BrainHandPhoneToolEndToEnd(unittest.TestCase):
    def test_07_phone_tool_reads_v02_honest_ux_and_invokes_all_five_subject_actions(self) -> None:
        import autonomy_rhythm_fixture as fixture

        saved_env = dict(os.environ)
        os.environ["CHIYO_WORLD_LOCAL_OWNER_PHONE"] = "chiyo"
        tmp_dir = Path(tempfile.mkdtemp(prefix="v02-unified-brain-"))
        stack = None
        try:
            stack = fixture.build_stack(tmp_dir / "inst", port=0)
            fixture.serve_stack(stack)
            cap_path, cap_obj = phone_capability.issue(
                stack.root,
                subject="chiyo",
                endpoint=stack.base,
            )
            stack.server.set_capability(cap_obj)
            hermes_home = tmp_dir / "hermes_home"
            (hermes_home / "chiyo").mkdir(parents=True, exist_ok=True)
            cfg = {
                "cli_owner": True,
                "local_owner_uid": os.getuid(),
                "memory": False,
                "memory_tool_policy": "unrestricted",
                "phone": {
                    "enabled": True,
                    "base_url": stack.base,
                    "profile_home": str(stack.root),
                    "write_enabled": True,
                    "private_artifacts": True,
                    "capability_file": str(cap_path),
                },
            }
            (hermes_home / "chiyo" / "config.json").write_text(json.dumps(cfg), encoding="utf-8")

            def _call(act: str, **extra: Any) -> dict[str, Any]:
                return json.loads(
                    phone_tool.phone_handler({"action": act, **extra}, hermes_home=str(hermes_home))
                )

            _seed_matter_space(stack.root)
            ws = stack.root / "ws_brain"
            ws.mkdir(parents=True, exist_ok=True)
            _rlf._ensure_control_ledger_locked(stack.root)
            _rlf.create_managed_artifact(
                stack.root,
                artifact_id="art_brain_1",
                controller_principal_ref="owner_1",
                subject_id="chiyo",
                initial_body="# Brain Note\nv1\n",
                operation_id="op_brain_create",
                title="brain_note.md",
                visibility="shared",
                now_iso=NOW,
            )
            _rlf.bind_representation(
                stack.root,
                artifact_id="art_brain_1",
                workspace_root=ws,
                workspace_rel_path="art_brain_1.md",
                operation_id="op_brain_bind",
                now_iso=NOW,
            )
            (ws / "art_brain_1.md").write_text("# Brain Note\nv2\n", encoding="utf-8")
            _rlf.observe_external_edit(
                stack.root,
                artifact_id="art_brain_1",
                workspace_root=ws,
                operation_id="op_brain_obs",
                actor_ref="ext",
                trusted_actor_identity=False,
                now_iso=NOW,
            )
            _, hold_res = _rlf.submit_forget_command(
                stack.root,
                command_text="忘掉刚才那句话",
                operation_id="op_brain_hold",
                recent_source_ids=["src_brain_1"],
                now_iso=NOW,
            )
            hold_id = hold_res["hold_id"]
            _lived_slices.mutate_v02_lived_slices(
                stack.root,
                {
                    "action": "register_personal_script",
                    "script_id": "tool_brain_1",
                    "title": "画稿快捷台",
                    "options": [
                        {"option_id": "opt_keep", "label": "收起杂项", "used_count": 3, "maintenance_cost": 1.0},
                        {"option_id": "opt_drop", "label": "多余按钮", "used_count": 0, "maintenance_cost": 2.0},
                    ],
                    "shared": False,
                    "operation_key": "op_brain_script_reg",
                },
                actor_side="subject",
                now_iso=NOW,
            )
            _asp.record_autobiographical_episode(
                stack.root,
                {
                    "episode_id": "ep_brain_1",
                    "period_id": "2026-11",
                    "domain": "painting",
                    "title": "画稿试色中断但收下有效底纹",
                    "summary": "调出三组可用灰绿与暖沙底纹后决定暂时放手。",
                    "outcome": "abandoned",
                    "visibility": "shared",
                    "source_ref": "work:w_brain_1",
                    "operation_key": "op_brain_ep_1",
                },
                now_iso=NOW,
            )

            r_hux = _call("v02_honest_ux")
            self.assertTrue(r_hux.get("ok"), r_hux)
            self.assertEqual(r_hux.get("http_status"), 200)
            hux_data = (r_hux.get("result") or {}).get("data") or {}
            obj_ids = {row.get("object_id") for row in (hux_data.get("chatless_traces") or {}).get("objects") or []}
            self.assertIn("obj_desk_lamp", obj_ids)
            self.assertEqual((hux_data.get("md_conflicts") or [])[0].get("state"), "DIVERGED")

            for idx, (surf, body) in enumerate(
                (
                    (
                        "v02_agency_depth",
                        {
                            "action": "salvage_work_scrap",
                            "scrap_id": "scrap_brain_1",
                            "source_work_id": "work:w_brain_1",
                            "source_version_id": "v1",
                            "source_outcome": "abandoned",
                            "fragment_title": "有用的排版边角",
                            "reusable_payload": {"palette_note": "冷灰与暖沙三二配比"},
                            "applicability_tags": ["layout", "palette"],
                            "provenance_receipt": "receipt:w_brain_1:v1",
                            "shared": True,
                        },
                    ),
                    (
                        "v02_agency_depth",
                        {
                            "action": "simplify_personal_tool",
                            "script_id": "tool_brain_1",
                            "removed_option_ids": ["opt_drop"],
                            "reason": "半年没用过且维护费太高",
                        },
                    ),
                    (
                        "v02_agency_depth",
                        {
                            "action": "record_period_snapshot",
                            "period_id": "2026-11",
                            "note": "十一月踏实阶段快照",
                            "shared": True,
                        },
                    ),
                    (
                        "v02_runtime_migration",
                        {
                            "action": "explicit_import_representation",
                            "artifact_id": "art_brain_1",
                        },
                    ),
                    (
                        "v02_runtime_migration",
                        {
                            "action": "approve_pending_forget_hold",
                            "case_id": hold_id,
                            "resolution": "retract_only",
                        },
                    ),
                ),
                start=1,
            ):
                resp = _call(
                    "phone_life_update",
                    target_surface=surf,
                    payload=body,
                    operation_key=f"op-brain-e2e-{idx}",
                )
                self.assertTrue(resp.get("ok"), f"{surf} {body['action']} failed: {resp}")
                self.assertEqual(resp.get("http_status"), 200)

            after = _call("v02_honest_ux")
            after_data = (after.get("result") or {}).get("data") or {}
            self.assertEqual((after_data.get("md_conflicts") or [])[0].get("state"), "CURRENT")
            self.assertTrue(after_data.get("scraps"))
            self.assertTrue(after_data.get("personal_tools"))
        finally:
            if stack is not None:
                fixture.close_stack(stack)
            shutil.rmtree(tmp_dir, ignore_errors=True)
            os.environ.clear()
            os.environ.update(saved_env)


if __name__ == "__main__":
    unittest.main()
