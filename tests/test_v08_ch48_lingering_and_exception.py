#!/usr/bin/env python3
"""v0.4 第 48 章 §五/§六 + 第 52 章 §三：有界不续期的挂心线索与急事偏好例外。

Coverage:
* ``note_lingering_thread`` / ``resolve_lingering_thread`` 的 TTL 语义；
* 读取（projection / matcher / HTTP view / summary）绝不延长 ``expires_at``、绝不写盘；
* 到期后 ``active_now == False``、不再被情景匹配注入，但记录仍在（历史不被删除）；
* 伴侣视角剥除 ``visibility != "shared"`` 的线索，零私密泄露；主体动作对伴侣 403；
* 来讯命中急事标记且存在生效的 ``scoped_preferences`` / 活跃方向 / 活跃线索时，
  ``preference_exception_cue`` 发出让位提示，且保存的偏好原样保留；平静来讯不发提示；
* 两个模块零硬编码姓名与性别代词、``llm_calls == 0``。

Run (WSL):
``/tmp/rc104-venv/bin/python -B -m unittest tests.test_v08_ch48_lingering_and_exception -v``
"""

from __future__ import annotations

import hashlib
import json
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
    str(ROOT / "tests"),
    str(ROOT / "scripts"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from components.life.scripts import v08_emergent_echoes as engine  # noqa: E402
from components.phone import v08_emergent_echoes_view as view  # noqa: E402

T0 = "2026-11-16T09:00:00Z"
T_PLUS_30M = "2026-11-16T09:30:00Z"
T_PLUS_125M = "2026-11-16T11:05:00Z"

SHE = "她"


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


def _thread_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "action": "note_lingering_thread",
        "summary_cn": "桌上那份还没收尾的稿子，说好明天一起看",
        "scope_kind": "work",
        "scope_ref": "work:floorplan_draft",
        "ttl_minutes": 120,
        "visibility": "private",
    }
    payload.update(overrides)
    return payload


def _write_elastic_play(root: Path) -> Path:
    path = root / "phone" / "v04_elastic_play.json"
    doc = {
        "schema": "nyairo.v04.elastic_play_state.v1",
        "revision": 3,
        "updated_at": T0,
        "day_direction": {
            "direction_id": "dd-1",
            "direction": "今天想先把书架顶层理出来",
            "status": "in_progress",
            "updated_at": T0,
        },
        "scoped_preferences": [
            {
                "pref_id": "pref-partner_explanation",
                "scope": "partner_explanation",
                "instruction": "讲事情的时候先收个尾，别说到一半去忙别的",
                "preference": "讲事情的时候先收个尾，别说到一半去忙别的",
                "taught_by": "partner",
                "status": "active",
                "applies_to_private_writing": False,
                "updated_at": T0,
            }
        ],
    }
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


class V08Ch48LingeringTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-ch48-")
        self.root = Path(self._tmp.name)
        (self.root / "phone").mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _mutate(self, payload: dict[str, Any], *, actor_side: str = "subject",
                now_iso: str = T0) -> dict[str, Any]:
        projected, _status = engine.apply_emergent_echoes_mutation(
            self.root, payload, actor_side=actor_side, now_iso=now_iso
        )
        return projected

    def _project(self, *, subject: bool = True, now_iso: str = T0) -> dict[str, Any]:
        return engine.read_emergent_echoes_zero_write(
            self.root, has_subject_capability=subject, now_iso=now_iso
        )

    # -- TTL 语义 ------------------------------------------------------------
    def test_01_ttl_never_renews_and_expiry_keeps_history(self) -> None:
        row = self._mutate(_thread_payload())["lingering_threads"][0]
        self.assertTrue(str(row["thread_id"]).startswith("lt_"))
        self.assertEqual(row["status"], "active")
        self.assertTrue(row["read_never_renews_ttl"])
        self.assertEqual(row["ttl_minutes"], 120)
        self.assertIsNone(row["resolved_at"])
        self.assertIsNone(row["resolution"])
        expires_at = row["expires_at"]
        self.assertEqual(expires_at, "2026-11-16T11:00:00Z")
        self.assertTrue(row["active_now"])

        for _ in range(5):
            projected = self._project(now_iso=T_PLUS_30M)
            self.assertEqual(projected["lingering_threads"][0]["expires_at"], expires_at)
            self.assertTrue(projected["lingering_threads"][0]["active_now"])
            engine.project_emergent_echoes_zero_write(
                self.root, has_subject_capability=True, now_iso=T_PLUS_30M
            )
            matched = engine.match_situational_echoes_zero_write(
                self.root, incoming_text="稿子", include_private=True, now_iso=T_PLUS_30M
            )
            self.assertEqual(len(matched["matched_lingering_threads"]), 1)
            self.assertEqual(matched["matched_lingering_threads"][0]["expires_at"], expires_at)

        expired = self._project(now_iso=T_PLUS_125M)["lingering_threads"][0]
        self.assertFalse(expired["active_now"])
        self.assertEqual(expired["status"], "active", "expiry is not resolution")
        self.assertEqual(expired["expires_at"], expires_at)

        after = engine.match_situational_echoes_zero_write(
            self.root, incoming_text="稿子", include_private=True, now_iso=T_PLUS_125M
        )
        self.assertEqual(after["matched_lingering_threads"], [])
        # The record still exists -- expiry never deletes history.
        self.assertEqual(len(self._project(now_iso=T_PLUS_125M)["lingering_threads"]), 1)

    def test_02_resolve_clears_active_before_ttl(self) -> None:
        thread = self._mutate(_thread_payload())["lingering_threads"][0]
        resolved = self._mutate({
            "action": "resolve_lingering_thread",
            "thread_id": thread["thread_id"],
            "resolution": "talked_through",
        }, now_iso=T_PLUS_30M)["lingering_threads"][0]
        self.assertEqual(resolved["status"], "resolved")
        self.assertEqual(resolved["resolution"], "talked_through")
        self.assertEqual(resolved["resolved_at"], T_PLUS_30M)
        self.assertFalse(resolved["active_now"])
        self.assertEqual(resolved["expires_at"], thread["expires_at"])

        matched = engine.match_situational_echoes_zero_write(
            self.root, incoming_text="稿子", include_private=True, now_iso=T_PLUS_30M
        )
        self.assertEqual(matched["matched_lingering_threads"], [])

        with self.assertRaises(engine.V08NotFoundError) as ctx:
            self._mutate({
                "action": "resolve_lingering_thread",
                "thread_id": "lt_missing",
                "resolution": "basis_changed",
            })
        self.assertEqual(ctx.exception.status_code, 404)
        with self.assertRaises(engine.V08ValidationError):
            self._mutate({
                "action": "resolve_lingering_thread",
                "thread_id": thread["thread_id"],
                "resolution": "forgotten_forever",
            })

    def test_03_ttl_validation(self) -> None:
        with self.assertRaises(engine.V08ValidationError):
            self._mutate(_thread_payload(ttl_minutes=14))
        with self.assertRaises(engine.V08ValidationError):
            self._mutate(_thread_payload(ttl_minutes=721))
        with self.assertRaises(engine.V08ValidationError):
            self._mutate(_thread_payload(ttl_minutes=True))
        with self.assertRaises(engine.V08ValidationError):
            self._mutate(_thread_payload(scope_kind="mood"))
        with self.assertRaises(engine.V08ValidationError):
            self._mutate(_thread_payload(summary_cn="短"))

        defaulted = self._mutate(_thread_payload(ttl_minutes=None))["lingering_threads"][0]
        self.assertEqual(defaulted["ttl_minutes"], 120)

    # -- 伴侣视角与权限 -------------------------------------------------------
    def test_04_partner_view_strips_private_and_blocks_actions(self) -> None:
        self._mutate(_thread_payload(visibility="private"))
        shared = self._mutate(_thread_payload(
            summary_cn="约好周末一起去看的那场展",
            scope_kind="object",
            scope_ref="object:exhibit_ticket",
            visibility="shared",
        ))["lingering_threads"][-1]

        partner = self._project(subject=False)
        self.assertEqual(
            [row["thread_id"] for row in partner["lingering_threads"]],
            [shared["thread_id"]],
        )
        self.assertNotIn('"private"', json.dumps(partner["lingering_threads"], ensure_ascii=False))

        code, body = view.mutate_v08_emergent_echoes_view(
            self.root, _thread_payload(), actor_side="partner", now_iso=T0
        )
        self.assertEqual(code, 403)
        self.assertEqual(body["reason_code"], "FORBIDDEN_ACTOR_SIDE")

        code, body = view.mutate_v08_emergent_echoes_view(
            self.root,
            {"action": "resolve_lingering_thread",
             "thread_id": shared["thread_id"],
             "resolution": "activity_finished"},
            actor_side="partner",
            now_iso=T0,
        )
        self.assertEqual(code, 403)
        self.assertEqual(body["reason_code"], "FORBIDDEN_ACTOR_SIDE")

        code, body = view.read_v08_emergent_echoes_view(
            self.root, has_subject_capability=False, now_iso=T0
        )
        self.assertEqual(code, 200)
        self.assertEqual(len(body["data"]["lingering_threads"]), 1)

    # -- 急事例外 ------------------------------------------------------------
    def test_05_urgency_exception_cue_keeps_saved_preference(self) -> None:
        elastic_path = _write_elastic_play(self.root)
        before_bytes = elastic_path.read_bytes()

        urgent = engine.match_situational_echoes_zero_write(
            self.root, incoming_text="我有急事，现在就要商量", now_iso=T0
        )
        self.assertEqual(urgent["preference_exception_cue"], engine.PREFERENCE_EXCEPTION_NOTE)
        self.assertTrue(urgent["scoped_preference_active"])
        self.assertEqual(urgent["llm_calls"], 0)

        calm = engine.match_situational_echoes_zero_write(
            self.root, incoming_text="今天有点安静，随便聊聊", now_iso=T0
        )
        self.assertEqual(calm["preference_exception_cue"], "")

        # The saved preference is an informational cue, never deleted by the exception.
        self.assertEqual(elastic_path.read_bytes(), before_bytes)
        doc = json.loads(elastic_path.read_text(encoding="utf-8"))
        self.assertEqual(len(doc["scoped_preferences"]), 1)
        self.assertEqual(doc["scoped_preferences"][0]["status"], "active")

    def test_06_urgency_needs_live_context(self) -> None:
        # No saved preference, no active direction, no active thread: nothing yields.
        cue = engine.match_situational_echoes_zero_write(
            self.root, incoming_text="十万火急", now_iso=T0
        )
        self.assertEqual(cue["preference_exception_cue"], "")
        self.assertFalse(cue["scoped_preference_active"])

        # An active lingering thread is enough context for the exception.
        self._mutate(_thread_payload(visibility="shared"))
        with_thread = engine.match_situational_echoes_zero_write(
            self.root, incoming_text="赶紧看一下这个", now_iso=T0
        )
        self.assertEqual(with_thread["preference_exception_cue"], engine.PREFERENCE_EXCEPTION_NOTE)

    # -- 零写与中立 ----------------------------------------------------------
    def test_07_reads_are_zero_write(self) -> None:
        self._mutate(_thread_payload(visibility="shared"))
        _write_elastic_play(self.root)
        before = _snapshot(self.root)

        for _ in range(3):
            engine.read_emergent_echoes_zero_write(
                self.root, has_subject_capability=True, now_iso=T_PLUS_30M
            )
            engine.project_emergent_echoes_zero_write(
                self.root, has_subject_capability=False, now_iso=T_PLUS_30M
            )
            engine.match_situational_echoes_zero_write(
                self.root, incoming_text="急事 稿子", include_private=True, now_iso=T_PLUS_30M
            )
            view.read_v08_emergent_echoes_view(
                self.root, has_subject_capability=True, now_iso=T_PLUS_30M
            )
            view.summarize_emergent_echoes_for_context(self.root, include_private=True)

        self.assertEqual(before, _snapshot(self.root), "reads must be byte- and mtime-identical")

    def test_08_modules_have_no_hardcoded_identity(self) -> None:
        modules = (
            ROOT / "components" / "life" / "scripts" / "v08_emergent_echoes.py",
            ROOT / "components" / "phone" / "v08_emergent_echoes_view.py",
        )
        for path in modules:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("千代", text, f"{path.name} must not hard-code a name")
            self.assertNotIn(SHE, text, f"{path.name} must not hard-code a pronoun")
            self.assertNotIn("他", text, f"{path.name} must not hard-code a pronoun")


if __name__ == "__main__":
    unittest.main()
