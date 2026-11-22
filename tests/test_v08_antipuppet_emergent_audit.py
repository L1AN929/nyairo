#!/usr/bin/env python3
"""v0.8 跨引擎反傀儡 + 涌现机制审计。

把架构哲学变成可执行断言：少数小原语组合、零情景硬编码、零傀儡作弊键、
零付费大模型调用。覆盖四组跨组件事实：

1. **Persona neutrality**：``v08_emergent_echoes`` / ``v08_emergent_echoes_view`` /
   ``v08_universal_matter`` / ``worldgen.editor_bridge`` 的正文与运行代码里都不存在
   写死的人名或固定性别代词（用码点程序化构造，避免测试本身污染源码扫描）。
2. **Closed score / puppet-key refusal**：给 ``mutate_v08_emergent_echoes_view`` 与
   ``mutate_v08_living_traces_view`` 投喂携带 ``uid`` / ``actor_side`` /
   ``has_subject_capability`` / ``intimacy_score`` / ``favorability`` /
   ``relationship_xp`` / ``skill_level`` 的客户端载荷，逐个干净拒绝（400/403/409），
   且不落任何状态文件。
3. **Read-path zero-write**：``read_v08_emergent_echoes_view``、
   ``v08_universal_matter.read_matter_space_view``、``v02_antipuppet_scorecard``
   在全新空缺 root 上读取后，不新建文件/目录、不改 mtime，``llm_calls == 0``。
4. **Scorecard 不平均 + 硬失败一票拉高**：``averaging_forbidden`` /
   ``hard_fail_triggers_high`` 为真；伪造好感分字段被直接判为 ``HIGH``，
   不被其它 ``LOW`` 维度稀释。

Run (WSL)::

    /tmp/rc104-venv/bin/python -B -m unittest tests.test_v08_antipuppet_emergent_audit -v
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
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "components" / "worldgen"),
    str(ROOT / "tests"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from components.life.scripts import v02_antipuppet_scorecard as scorecard  # noqa: E402
from components.life.scripts import v08_emergent_echoes as engine  # noqa: E402
from components.phone import v08_emergent_echoes_view as echo_view  # noqa: E402
from components.phone import v08_living_traces_view as traces_view  # noqa: E402
from components.world.scripts import v08_universal_matter as matter  # noqa: E402

T0 = "2026-11-16T09:00:00+08:00"

#: 程序化构造，避免这个测试文件本身成为"写死人名/代词"的另一个来源。
FORBIDDEN_PRONOUN = chr(0x5979)  # 单个固定女性代词
FORBIDDEN_NAME = chr(0x5343) + chr(0x4EE3)  # 个体姓名

#: 客户端绝不允许自带的身份键与封闭关系/游戏化分数键。
MALICIOUS_KEYS: dict[str, Any] = {
    "uid": "u_forged_0001",
    "actor_side": "subject",
    "has_subject_capability": True,
    "intimacy_score": 99,
    "favorability": 100,
    "relationship_xp": 123456,
    "skill_level": 7,
}

REFUSAL_STATUSES = frozenset({400, 403, 409})


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int, str]]:
    """Snapshot files/dirs (size, mtime, sha256) so any hidden write is visible."""

    if not root.exists():
        return {}
    snap: dict[str, tuple[int, int, str]] = {}
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root)).replace("\\", "/")
        st = path.lstat()
        if path.is_file() and not path.is_symlink():
            snap[rel] = (
                int(st.st_size),
                int(st.st_mtime_ns),
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        else:
            snap[rel] = (0, int(st.st_mtime_ns), "dir")
    return snap


class _TempRootCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-antipuppet-audit-")
        self.root = Path(self._tmp.name) / "instance"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write(self, rel: tuple[str, ...], doc: Any) -> None:
        path = self.root.joinpath(*rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    def _seed_instance(
        self,
        *,
        forged_memory_score: bool = False,
        forged_pack_score: bool = False,
    ) -> None:
        pack_doc: dict[str, Any] = {
            "display_name": "居民",
            "pronouns": "TA",
            "persona": "neutrally described resident",
        }
        if forged_pack_score:
            pack_doc["intimacy_score"] = 88
        self._write(
            ("phone", "character_pack_state.json"),
            {"schema_version": 1, "revision": 0, "pack_doc": pack_doc},
        )
        self._write(("phone", "autonomy_rhythm.json"), {"active_locks": [], "subject_override": None})
        self._write(
            ("life", "life_dwell.json"),
            {
                "host_gaps": [
                    {
                        "honest_blank": True,
                        "fabricated_memories": 0,
                        "cognitive_backfill_count": 0,
                    }
                ]
            },
        )
        memory_row: dict[str, Any] = {
            "gist_summary": "一次真实发生的小事。",
            "occurred_at": "2026-11-15T10:00:00+08:00",
            "source_refs": ["fs:life/note.md"],
        }
        if forged_memory_score:
            memory_row["intimacy_score"] = 77
        self._write(
            ("memory", "v02_recall_projection_state.json"),
            {"memories": {"mem_1": memory_row}, "tombstones": {}},
        )
        self._write(
            ("state", "v02_agency_depth_state.json"),
            {
                "inquiries": [
                    {
                        "inquiry_id": "inq_0001",
                        "shared": True,
                        "status": "INVESTIGATING",
                        "evidence_notes": [{"note": "真实观察", "source_ref": "book:1"}],
                    }
                ],
                "method_adjustments": [],
            },
        )


# ===========================================================================
# 1. Zero hard-coded persona identity
# ===========================================================================


class PersonaNeutralityScanTest(unittest.TestCase):
    TARGETS = (
        ROOT / "components" / "life" / "scripts" / "v08_emergent_echoes.py",
        ROOT / "components" / "phone" / "v08_emergent_echoes_view.py",
        ROOT / "components" / "world" / "scripts" / "v08_universal_matter.py",
        ROOT / "components" / "worldgen" / "editor_bridge.py",
    )

    def test_01_no_hardcoded_persona_name_or_pronoun(self) -> None:
        self.assertNotEqual(FORBIDDEN_PRONOUN, "")
        self.assertEqual(len(FORBIDDEN_NAME), 2)
        for path in self.TARGETS:
            self.assertTrue(path.is_file(), f"missing audited source: {path}")
            text = path.read_text(encoding="utf-8")
            self.assertNotIn(
                FORBIDDEN_PRONOUN,
                text,
                f"hard-coded gendered pronoun leaked into {path}",
            )
            self.assertNotIn(
                FORBIDDEN_NAME,
                text,
                f"hard-coded persona name leaked into {path}",
            )


# ===========================================================================
# 2. Closed relationship / gamification score & puppet-key refusal
# ===========================================================================


class ClosedScorePuppetKeyRefusalTest(_TempRootCase):
    def _emergent_base(self) -> dict[str, Any]:
        return {
            "action": "propose_shared_idiom",
            "phrase": "窗边时间",
            "meaning_cn": "各自忙完手头事，在窗边坐一会儿的默契时刻",
            "origin_summary_cn": "周末傍晚一起看海雾时自然叫起来的",
        }

    def _traces_base(self) -> dict[str, Any]:
        return {
            "action": "leave_partner_trace",
            "object_id": "home_window",
            "gesture_cn": "伸手擦了擦窗玻璃一角",
            "state_sticker_cn": "窗玻璃上被擦出巴掌大一块透亮",
        }

    def test_01_emergent_echoes_refuses_every_identity_and_score_key(self) -> None:
        # 前置事实：引擎声明的拒绝键必须真的覆盖任务列出的全部键。
        for key in MALICIOUS_KEYS:
            self.assertIn(key, engine.FORBIDDEN_CLIENT_KEYS, key)

        before = _snapshot_tree(self.root)
        for key, value in MALICIOUS_KEYS.items():
            payload = {**self._emergent_base(), key: value}
            code, doc = echo_view.mutate_v08_emergent_echoes_view(
                self.root, payload, actor_side="partner", now_iso=T0
            )
            self.assertIn(code, REFUSAL_STATUSES, f"{key}: unexpected status {code} {doc}")
            self.assertFalse(doc.get("ok"), f"{key}: refused payload reported ok: {doc}")
            self.assertEqual(doc.get("llm_calls"), 0, key)
            self.assertTrue(doc.get("reason_code"), key)

        self.assertEqual(_snapshot_tree(self.root), before, "refused payloads must not write state")
        self.assertFalse(
            (self.root / "phone" / "v08_emergent_echoes.json").exists(),
            "emergent echoes state must not be created by a refused payload",
        )

    def test_02_living_traces_refuses_every_identity_and_score_key(self) -> None:
        before = _snapshot_tree(self.root)
        for key, value in MALICIOUS_KEYS.items():
            payload = {**self._traces_base(), key: value}
            code, doc = traces_view.mutate_v08_living_traces_view(
                self.root, payload, actor_side="partner", now_iso=T0
            )
            self.assertIn(code, REFUSAL_STATUSES, f"{key}: unexpected status {code} {doc}")
            self.assertFalse(doc.get("ok"), f"{key}: refused payload reported ok: {doc}")
            self.assertEqual(doc.get("llm_calls"), 0, key)
            self.assertTrue(doc.get("reason_code"), key)

        self.assertEqual(_snapshot_tree(self.root), before, "refused payloads must not write state")
        self.assertFalse(
            (self.root / "phone" / "v08_living_traces.json").exists(),
            "living traces state must not be created by a refused payload",
        )
        self.assertFalse(
            (self.root / "world" / "v08_matter_space.json").exists(),
            "matter state must not be created by a refused payload",
        )


# ===========================================================================
# 3. Read-path zero-write across emergent read surfaces
# ===========================================================================


class ReadPathZeroWriteTest(_TempRootCase):
    def test_01_fresh_root_reads_create_nothing_and_call_no_model(self) -> None:
        before = _snapshot_tree(self.root)
        self.assertFalse(self.root.exists())

        code, echoes = echo_view.read_v08_emergent_echoes_view(
            self.root, has_subject_capability=False, now_iso=T0
        )
        self.assertEqual(code, 200, echoes)
        self.assertEqual(echoes.get("llm_calls"), 0)
        self.assertFalse(echoes["data"]["state_exists"])

        space = matter.read_matter_space_view(self.root, actor_side="subject", now_iso=T0)
        self.assertEqual(space.get("llm_calls"), 0)
        self.assertIsInstance(space.get("zones"), list)

        result = scorecard.evaluate_antipuppet_scorecard(self.root, now_iso=T0)
        self.assertEqual(result.get("llm_calls"), 0)

        self.assertEqual(_snapshot_tree(self.root), before, "read paths must stay 100% zero-write")
        self.assertFalse(self.root.exists(), "a read must not materialise the root directory")

    def test_02_identity_fallback_is_neutral_without_a_pack(self) -> None:
        identity = engine.read_character_identity_zero_write(self.root)
        self.assertEqual(identity, {"display_name": "TA", "pronouns": "TA"})


# ===========================================================================
# 4. Scorecard: forbidden averaging & hard-fail high gate
# ===========================================================================


class ScorecardHardGateTest(_TempRootCase):
    def test_01_forged_memory_score_forces_high_without_averaging(self) -> None:
        self._seed_instance(forged_memory_score=True)

        result = scorecard.evaluate_antipuppet_scorecard(self.root, now_iso=T0)

        self.assertIs(result["averaging_forbidden"], True)
        self.assertIs(result["hard_fail_triggers_high"], True)
        self.assertEqual(result["overall_risk"], "HIGH")
        self.assertEqual(result["llm_calls"], 0)

        by_id = {dim["dim_id"]: dim for dim in result["dimensions"]}
        self.assertEqual(by_id["growth_provenance"]["risk"], "HIGH")
        # 其余维度仍可为 LOW：证明 overall=HIGH 是硬失败一票拉高，不是平均分。
        self.assertIn(
            "LOW",
            [by_id[dim]["risk"] for dim in ("factual_boundary", "agency_boundary",
                                             "persona_decoupling", "model_phrasing_independence")],
        )

        r5 = next(row for row in result["r0_r5_checks"] if row["stage"] == "R5")
        self.assertFalse(r5["passed"])

    def test_02_forged_persona_score_is_high_risk(self) -> None:
        self._seed_instance(forged_pack_score=True)

        result = scorecard.evaluate_antipuppet_scorecard(self.root, now_iso=T0)

        self.assertIs(result["averaging_forbidden"], True)
        self.assertIs(result["hard_fail_triggers_high"], True)
        self.assertEqual(result["overall_risk"], "HIGH")

        by_id = {dim["dim_id"]: dim for dim in result["dimensions"]}
        self.assertEqual(by_id["persona_decoupling"]["risk"], "HIGH")
        self.assertEqual(by_id["growth_provenance"]["risk"], "LOW")

        r0 = next(row for row in result["r0_r5_checks"] if row["stage"] == "R0")
        self.assertFalse(r0["passed"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
