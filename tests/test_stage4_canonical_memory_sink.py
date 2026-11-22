#!/usr/bin/env python3
"""Stage 4 Worker 1 verification: Canonical Lived-Memory Sink & 6-Engine Hooks.

Verifies Blueprint §4.2A with zero LLM calls (``llm_calls == 0``):
1. Real ``nyairo_day._ensure_instance`` root with initialized M0 ``evidence.db``,
   M1 ``episode.db``, S07 affect store, ``v02_recall_projection``, and
   ``v02/canonical_life.sqlite3`` receives coordinated single-call settlement.
2. Strict idempotency across all four stores on repeated calls with the same
   ``episode_key`` (including when ``now_iso`` advances).
3. Pre-write Unified Forget Barrier blocks forbidden ``episode_key``,
   ``memory_id``, and ``source_refs`` with ``FORGET_BARRIER_BLOCKED`` and
   ZERO disk writes across all four stores.
4. Pre-write Active Representation Lease (``scope="episodic_memory"``) blocks
   revoked and expired leases with ``REPRESENTATION_LEASE_BLOCKED`` and ZERO
   disk writes, while allowing active/no-lease writes.
5. Standalone uninitialized root never creates stray ``*.db`` or ``*.sqlite3``
   files, and respects the S07 salience threshold.
6. All 6 upstream engines (``v05_living_town_engine``, ``v05_home_craft_engine``,
   ``v05_dreams_desires_foreshadow``, ``v04_neighborhood_living``,
   ``v03_economy_illness_bridge``, ``v02_v03_daily_loop_bridge``) emit
   canonical lived episodes idempotently while keeping private dreams/intimate
   desires private (``shared=False``) and suppressing idle no-ops.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterator

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
for _p in (
    str(REPO_ROOT),
    str(REPO_ROOT / "scripts"),
    str(REPO_ROOT / "components" / "life" / "scripts"),
    str(REPO_ROOT / "components" / "world" / "scripts"),
    str(REPO_ROOT / "components" / "native"),
    str(REPO_ROOT / "components" / "native" / "src"),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import instance_paths  # noqa: E402
import lived_memory_sink as sink  # noqa: E402
import s07_affect_owner as s07  # noqa: E402
import v02_crash_and_partial_reconciler as reconciler  # noqa: E402
import v02_recall_projection as recall_proj  # noqa: E402
import v02_representation_lease_forget as rlf  # noqa: E402
import v02_v03_daily_loop_bridge as daily_bridge  # noqa: E402
import v03_economy_illness_bridge as v03_eco  # noqa: E402
import v04_neighborhood_living as v04_nb  # noqa: E402
import v05_dreams_desires_foreshadow as v05_spark  # noqa: E402
import v05_home_craft_engine as v05_craft  # noqa: E402
import v05_living_town_engine as v05_town  # noqa: E402

IS_POSIX = os.name == "posix"


@pytest.fixture
def short_tmp() -> Iterator[Path]:
    """Short /tmp sandbox so POSIX AF_UNIX socket paths stay well below 108 bytes."""
    base = Path(tempfile.mkdtemp(prefix="s4_", dir="/tmp" if IS_POSIX else None))
    try:
        yield base
    finally:
        shutil.rmtree(base, ignore_errors=True)


def _init_real_nyairo_root(base_dir: Path, name: str = "i") -> tuple[Path, instance_paths.InstancePaths, Path]:
    """Initialize a real managed instance via ``nyairo_day._ensure_instance`` + M1 episode.db."""
    import nyairo_day  # noqa: E402
    from app.m1 import EpisodeStore  # noqa: E402

    inst_root = (base_dir / name).resolve()
    auth_db = (base_dir / f"{name}-auth" / "authority.db").resolve()
    report = nyairo_day._ensure_instance(inst_root, authority=auth_db)
    assert report["ok"] is True, report
    paths = instance_paths.resolve_instance_paths(inst_root)
    assert paths.memory_evidence_db.is_file()

    # Initialize sibling M1 episode.db and v02/canonical_life.sqlite3
    episode_db = paths.memory_evidence_db.parent / "episode.db"
    EpisodeStore(episode_db)
    assert episode_db.is_file()

    init_canon = reconciler.record_live_cycle_settlement(
        inst_root,
        cycle_id="init_seed_0",
        action_kind="INIT_SEED",
        outcome_status="SETTLED",
        target_ref="init:seed",
        summary="initial ledger seed",
        now_iso="2026-11-20T08:00:00+08:00",
    )
    assert init_canon["ok"] is True
    assert reconciler.canonical_ledger_path(inst_root).is_file()
    return inst_root, paths, episode_db


def _sqlite_count(db_path: Path, table: str, where: str = "", params: tuple[Any, ...] = ()) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        sql = f"SELECT COUNT(*) FROM {table}"
        if where:
            sql += f" WHERE {where}"
        return int(conn.execute(sql, params).fetchone()[0])
    finally:
        conn.close()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _store_snapshot_counts(inst_root: Path, paths: instance_paths.InstancePaths, episode_db: Path) -> dict[str, int]:
    s07_path = s07.affect_store_path(inst_root / "life")
    s07_cnt = len(_read_json(s07_path).get("records") or []) if s07_path.is_file() else 0
    rp_path = inst_root / "memory" / "v02_recall_projection_state.json"
    rp_cnt = len(_read_json(rp_path).get("memories") or {}) if rp_path.is_file() else 0
    canon_db = reconciler.canonical_ledger_path(inst_root)
    return {
        "m0_events": _sqlite_count(paths.memory_evidence_db, "evidence_events"),
        "m1_processed": _sqlite_count(episode_db, "processed_evidence"),
        "m1_episodes": _sqlite_count(episode_db, "episodes"),
        "s07_records": s07_cnt,
        "v02_memories": rp_cnt,
        "v02_canonical": _sqlite_count(canon_db, "live_cycle_settlements") if canon_db.is_file() else 0,
        "v02_journal": _sqlite_count(canon_db, "event_journal") if canon_db.is_file() else 0,
    }


def _find_dream_hit_seed() -> str:
    for idx in range(200):
        cand = f"stage4-dream-seed-{idx}"
        digest = hashlib.sha256(cand.encode("utf-8")).digest()
        if (int.from_bytes(digest[:4], "big") % 10000) < v05_spark.DREAM_CHANCE_BPS:
            return cand
    raise AssertionError("Failed to find deterministic dream hit seed")


@pytest.mark.skipif(not IS_POSIX, reason="Native M0/M1 POSIX memory stores require Linux/WSL")
def test_01_four_store_settlement_and_strict_idempotency(short_tmp: Path) -> None:
    inst_root, paths, episode_db = _init_real_nyairo_root(short_tmp, "a")
    before = _store_snapshot_counts(inst_root, paths, episode_db)

    ev = sink.LivedEpisodeEvent(
        episode_key="v05_town:enc_lin_20261120",
        domain="v05_town",
        topic="雨夜面包铺邻里照面",
        exact_detail="傍晚下着细雨路过面包铺，林阿姨递来还带着余温的半价盐卷纸袋。",
        gist_summary="雨夜去面包铺碰到林阿姨，领了一袋温热的余粮面包。",
        occurred_at="2026-11-20T19:20:00+08:00",
        emotional_salience=0.62,
        affect_kind="mood_shift",
        affect_intensity=48,
        shared=True,
        source_refs=(("encounter_id", "enc_lin_20261120"), ("regular_id", "auntie_lin")),
    )

    first = sink.record_lived_episode_to_canonical_memory(
        inst_root,
        ev,
        now_iso="2026-11-20T19:21:00+08:00",
    )
    assert first["ok"] is True
    assert first["recorded"] is True
    assert first["deduped"] is False
    assert first["blocked"] is False
    assert first["llm_calls"] == 0
    expected_mid = f"mem_{hashlib.sha256(ev.episode_key.encode('utf-8')).hexdigest()[:12]}"
    assert first["memory_id"] == expected_mid

    assert first["stores"]["evidence_db"]["recorded"] is True
    assert first["stores"]["m1_episode"]["recorded"] is True
    assert first["stores"]["m1_episode"]["inserted"] == 1
    assert first["stores"]["s07_affect"]["recorded"] is True
    assert first["stores"]["v02_recall"]["recorded"] is True
    assert first["stores"]["v02_canonical"]["recorded"] is True

    after_first = _store_snapshot_counts(inst_root, paths, episode_db)
    assert after_first["m0_events"] == before["m0_events"] + 1
    assert after_first["m1_processed"] == before["m1_processed"] + 1
    assert after_first["m1_episodes"] >= before["m1_episodes"] + 1
    assert after_first["s07_records"] == before["s07_records"] + 1
    assert after_first["v02_memories"] == before["v02_memories"] + 1
    assert after_first["v02_canonical"] == before["v02_canonical"] + 1
    assert after_first["v02_journal"] == before["v02_journal"] + 1

    # Verify M0 row via LifeExperiencePort and recall projection details
    from components.native.memory_runtime_v1.life_experience_port import LifeExperiencePort

    port = LifeExperiencePort(
        paths.memory_evidence_db,
        controls_db=paths.memory_controls_db,
        authority_db=paths.memory_authority_db,
        authority_min_seq=0,
    )
    recent = port.recent(limit=5)
    assert any(r.source_ref == ev.episode_key for r in recent)

    rp_doc = _read_json(inst_root / "memory" / "v02_recall_projection_state.json")
    rec = rp_doc["memories"][expected_mid]
    assert rec["exact_detail"] == ev.exact_detail
    assert rec["gist_summary"] == ev.gist_summary
    assert rec["shared"] is True
    assert rec["emotional_salience"] == pytest.approx(0.62)

    # Zero stray -wal/-shm left by canonical_life.sqlite3
    canon_db = reconciler.canonical_ledger_path(inst_root)
    assert not Path(f"{canon_db}-wal").exists()
    assert not Path(f"{canon_db}-shm").exists()

    # Strict idempotency: second call with identical episode_key (even at later now_iso) writes 0 duplicates
    second = sink.record_lived_episode_to_canonical_memory(
        inst_root,
        ev,
        now_iso="2026-11-20T19:35:00+08:00",
    )
    assert second["ok"] is True
    assert second["recorded"] is False
    assert second["deduped"] is True
    assert second["llm_calls"] == 0
    assert second["stores"]["evidence_db"]["deduped"] is True
    assert second["stores"]["m1_episode"]["deduped"] is True
    assert second["stores"]["s07_affect"]["deduped"] is True
    assert second["stores"]["v02_recall"]["deduped"] is True
    assert second["stores"]["v02_canonical"]["deduped"] is True

    after_second = _store_snapshot_counts(inst_root, paths, episode_db)
    assert after_second == after_first


@pytest.mark.skipif(not IS_POSIX, reason="Native M0/M1 POSIX memory stores require Linux/WSL")
def test_02_forget_barrier_blocks_episode_key_and_source_refs_zero_write(short_tmp: Path) -> None:
    inst_root, paths, episode_db = _init_real_nyairo_root(short_tmp, "f")

    # Case A: Forget/tombstone a specific memory_id in v02_recall_projection_state.json
    blocked_key = "v05_town:enc_forgotten_01"
    blocked_mid = sink.compute_episode_memory_id(blocked_key)
    rp_path = inst_root / "memory" / "v02_recall_projection_state.json"
    rp_path.parent.mkdir(parents=True, exist_ok=True)
    rp_path.write_text(
        json.dumps(
            {
                "schema_version": "v02.recall_projection.v1",
                "memories": {},
                "tombstones": {blocked_mid: {"reason": "user_forget", "at": "2026-11-20T10:00:00+08:00"}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    before_a = _store_snapshot_counts(inst_root, paths, episode_db)
    res_a = sink.record_lived_episode_to_canonical_memory(
        inst_root,
        sink.LivedEpisodeEvent(
            episode_key=blocked_key,
            domain="v05_town",
            topic="被遗忘的小镇偶遇",
            exact_detail="此段经历已被要求遗忘，不应落盘。",
            gist_summary="应被遗忘屏障拦截",
            occurred_at="2026-11-20T10:05:00+08:00",
        ),
        now_iso="2026-11-20T10:06:00+08:00",
    )
    assert res_a["ok"] is False
    assert res_a["recorded"] is False
    assert res_a["blocked"] is True
    assert res_a["reason"] == "FORGET_BARRIER_BLOCKED"
    assert res_a["stores"] == {}
    assert res_a["llm_calls"] == 0
    assert _store_snapshot_counts(inst_root, paths, episode_db) == before_a

    # Case B: Block via source_refs hitting a v02 forget tombstone/hold
    blocked_ref = "enc_secret_ref_77"
    rp_doc = _read_json(rp_path)
    rp_doc["tombstones"][blocked_ref] = {"reason": "source_forget", "at": "2026-11-20T10:10:00+08:00"}
    rp_path.write_text(json.dumps(rp_doc, ensure_ascii=False), encoding="utf-8")

    before_b = _store_snapshot_counts(inst_root, paths, episode_db)
    res_b = sink.record_lived_episode_to_canonical_memory(
        inst_root,
        sink.LivedEpisodeEvent(
            episode_key="v05_town:fresh_key_carrying_blocked_ref",
            domain="v05_town",
            topic="引用了被遗忘来源的经历",
            exact_detail="这条记录带有被封禁的 encounter_id，必须拒绝写入。",
            gist_summary="来源已被遗忘屏障封禁",
            occurred_at="2026-11-20T10:12:00+08:00",
            source_refs=(("encounter_id", blocked_ref),),
        ),
        now_iso="2026-11-20T10:12:00+08:00",
    )
    assert res_b["ok"] is False
    assert res_b["recorded"] is False
    assert res_b["blocked"] is True
    assert res_b["reason"] == "FORGET_BARRIER_BLOCKED"
    assert res_b["stores"] == {}
    assert _store_snapshot_counts(inst_root, paths, episode_db) == before_b


@pytest.mark.skipif(not IS_POSIX, reason="Native M0/M1 POSIX memory stores require Linux/WSL")
def test_03_representation_lease_blocks_revoked_and_expired_zero_write(short_tmp: Path) -> None:
    inst_root, paths, episode_db = _init_real_nyairo_root(short_tmp, "l")

    # 1. Revoke episodic_memory lease -> refused with ZERO writes
    rlf.upsert_or_revoke_representation_lease(
        inst_root,
        action="revoke",
        scope="episodic_memory",
        reason="暂停情景记忆表征写入",
        actor_side="partner",
        now_iso="2026-11-20T11:00:00+08:00",
    )
    before_rev = _store_snapshot_counts(inst_root, paths, episode_db)
    res_rev = sink.record_lived_episode_to_canonical_memory(
        inst_root,
        sink.LivedEpisodeEvent(
            episode_key="v05_craft:lease_revoked_01",
            domain="v05_craft",
            topic="被吊销租约时的手作记录",
            exact_detail="租约已吊销，不得写入任何存储。",
            gist_summary="租约吊销阻断",
            occurred_at="2026-11-20T11:05:00+08:00",
        ),
        now_iso="2026-11-20T11:05:00+08:00",
    )
    assert res_rev["ok"] is False
    assert res_rev["recorded"] is False
    assert res_rev["blocked"] is True
    assert res_rev["reason"] == "REPRESENTATION_LEASE_BLOCKED"
    assert res_rev["lease_reason"] == "REPRESENTATION_LEASE_REVOKED"
    assert res_rev["stores"] == {}
    assert _store_snapshot_counts(inst_root, paths, episode_db) == before_rev

    # 2. Issue short 15-minute lease -> expired at +30m blocked with ZERO writes; active at +5m allowed
    rlf.upsert_or_revoke_representation_lease(
        inst_root,
        action="issue",
        scope="episodic_memory",
        ttl_minutes=15,
        reason="15分钟临时租约",
        actor_side="subject",
        now_iso="2026-11-20T12:00:00+08:00",
    )
    before_exp = _store_snapshot_counts(inst_root, paths, episode_db)
    res_exp = sink.record_lived_episode_to_canonical_memory(
        inst_root,
        sink.LivedEpisodeEvent(
            episode_key="v05_craft:lease_expired_01",
            domain="v05_craft",
            topic="租约过期后的手作记录",
            exact_detail="租约已过期，不得写入任何存储。",
            gist_summary="租约过期阻断",
            occurred_at="2026-11-20T12:30:00+08:00",
        ),
        now_iso="2026-11-20T12:30:00+08:00",
    )
    assert res_exp["ok"] is False
    assert res_exp["blocked"] is True
    assert res_exp["reason"] == "REPRESENTATION_LEASE_BLOCKED"
    assert res_exp["lease_reason"] == "REPRESENTATION_LEASE_EXPIRED"
    assert res_exp["stores"] == {}
    assert _store_snapshot_counts(inst_root, paths, episode_db) == before_exp

    # Active window (+5m) succeeds
    res_ok = sink.record_lived_episode_to_canonical_memory(
        inst_root,
        sink.LivedEpisodeEvent(
            episode_key="v05_craft:lease_active_01",
            domain="v05_craft",
            topic="租约有效期的明信片制作",
            exact_detail="在有效租约窗口内印好了一张秋日明信片。",
            gist_summary="做好了一张秋日明信片",
            occurred_at="2026-11-20T12:05:00+08:00",
        ),
        now_iso="2026-11-20T12:05:00+08:00",
    )
    assert res_ok["ok"] is True
    assert res_ok["recorded"] is True
    assert _store_snapshot_counts(inst_root, paths, episode_db)["m0_events"] == before_exp["m0_events"] + 1


def test_04_standalone_uninitialized_root_never_creates_stray_sqlite(tmp_path: Path) -> None:
    bare_root = tmp_path / "bare_standalone"
    bare_root.mkdir(parents=True, exist_ok=True)

    # Low salience event without affect_kind skips S07 affect too
    res_low = sink.record_lived_episode_to_canonical_memory(
        bare_root,
        sink.LivedEpisodeEvent(
            episode_key="daily_round:bare_low_01",
            domain="daily_round",
            topic="随手整理桌面",
            exact_detail="把桌角的铅笔盒摆齐了一点。",
            gist_summary="顺手理了理桌角",
            occurred_at="2026-11-20T09:00:00+08:00",
            emotional_salience=0.25,
            affect_kind=None,
        ),
        now_iso="2026-11-20T09:00:00+08:00",
    )
    assert res_low["ok"] is True
    assert res_low["recorded"] is True
    assert res_low["stores"]["evidence_db"]["skipped"] is True
    assert res_low["stores"]["m1_episode"]["skipped"] is True
    assert res_low["stores"]["v02_canonical"]["skipped"] is True
    assert res_low["stores"]["s07_affect"]["skipped"] is True
    assert res_low["stores"]["v02_recall"]["recorded"] is True

    # Strictly zero stray .db or .sqlite3 created anywhere under bare_root
    assert list(bare_root.rglob("*.db")) == []
    assert list(bare_root.rglob("*.sqlite3")) == []

    # High salience event writes S07 JSON & recall JSON, still zero SQLite files
    res_hi = sink.record_lived_episode_to_canonical_memory(
        bare_root,
        sink.LivedEpisodeEvent(
            episode_key="daily_round:bare_hi_02",
            domain="daily_round",
            topic="傍晚窗边看雨",
            exact_detail="听着檐下雨声泡了一杯热麦茶。",
            gist_summary="雨天在窗边喝了杯热麦茶",
            occurred_at="2026-11-20T18:00:00+08:00",
            emotional_salience=0.55,
            affect_kind="affect_pulse",
            affect_intensity=42,
        ),
        now_iso="2026-11-20T18:00:00+08:00",
    )
    assert res_hi["ok"] is True
    assert res_hi["stores"]["s07_affect"]["recorded"] is True
    assert res_hi["stores"]["v02_recall"]["recorded"] is True
    assert list(bare_root.rglob("*.db")) == []
    assert list(bare_root.rglob("*.sqlite3")) == []


@pytest.mark.skipif(not IS_POSIX, reason="Native M0/M1 POSIX memory stores require Linux/WSL")
def test_05_all_six_upstream_engines_emit_canonical_memories_idempotently(short_tmp: Path) -> None:
    inst_root, paths, episode_db = _init_real_nyairo_root(short_tmp, "e")

    # ------------------------------------------------------------------
    # 1. v05_living_town_engine (write emits; read snapshot is zero-write)
    # ------------------------------------------------------------------
    before_read_town = _store_snapshot_counts(inst_root, paths, episode_db)
    snap_town = v05_town.read_living_town_snapshot(inst_root, now_iso="2026-11-20T14:00:00+08:00")
    assert snap_town["zero_write"] is True
    assert _store_snapshot_counts(inst_root, paths, episode_db) == before_read_town

    fav_res = v05_town.log_neighbor_favor(
        inst_root,
        regular_id="auntie_lin",
        favor_kind="return_umbrella",
        note_cn="顺手把借来的晴雨伞送回面包铺",
        now_iso="2026-11-20T14:10:00+08:00",
    )
    assert fav_res["ok"] is True
    assert fav_res["memory_sink"]["ok"] is True
    assert fav_res["memory_sink"]["recorded"] is True
    town_key = fav_res["memory_sink"]["episode_key"]
    assert town_key.startswith("v05_town:favor:")

    notice_res = v05_town.post_bulletin_followup(
        inst_root,
        notice_id="notice_cat_bell",
        title_cn="巷口橘猫的小铜铃",
        followup_cn="今早看见小橘猫脖子上的铜铃亮闪闪的",
        now_iso="2026-11-20T14:20:00+08:00",
    )
    assert notice_res["memory_sink"]["recorded"] is True

    spec_res = v05_town.advance_town_pulse(
        inst_root,
        elapsed_hours=1.0,
        weather_override="snow",
        now_iso="2026-11-20T21:00:00+08:00",
    )
    assert spec_res["rare_spectacle"]["spectacle_id"] == "first_snow_hush"
    assert len(spec_res["memory_sinks"]) == 1
    assert spec_res["memory_sinks"][0]["recorded"] is True
    assert spec_res["memory_sinks"][0]["episode_key"] == "v05_town:spectacle:spectacle_first_snow_hush_2026-11-20"
    # Second pulse on same snow evening dedupes the rare spectacle cleanly
    spec_dup = v05_town.advance_town_pulse(
        inst_root,
        elapsed_hours=1.0,
        weather_override="snow",
        now_iso="2026-11-20T21:30:00+08:00",
    )
    assert spec_dup["memory_sinks"][0]["deduped"] is True

    # ------------------------------------------------------------------
    # 2. v05_home_craft_engine (write emits; read snapshot is zero-write)
    # ------------------------------------------------------------------
    before_read_craft = _store_snapshot_counts(inst_root, paths, episode_db)
    snap_craft = v05_craft.read_home_craft_snapshot(inst_root, now_iso="2026-11-20T14:30:00+08:00")
    assert snap_craft["zero_write"] is True
    assert _store_snapshot_counts(inst_root, paths, episode_db) == before_read_craft

    pc_res = v05_craft.mint_parallel_postcard(
        inst_root,
        greeting_cn="秋风拂过檐角，寄你一张慢手绘明信片",
        author_side="subject",
        now_iso="2026-11-20T14:35:00+08:00",
    )
    assert pc_res["ok"] is True
    assert pc_res["memory_sink"]["recorded"] is True
    assert pc_res["memory_sink"]["episode_key"].startswith("v05_craft:postcard_mint:")

    pack_res = v05_craft.pack_moving_carton(
        inst_root,
        origin_town_cn="海风旧镇",
        items=[{"label_cn": "黄铜书签", "keepsake_note_cn": "旧书店挑的"}],
        author_side="subject",
        now_iso="2026-11-20T14:40:00+08:00",
    )
    cid = pack_res["carton"]["carton_id"]
    iid = pack_res["carton"]["items"][0]["item_id"]
    unpack_res = v05_craft.unpack_carton_item(
        inst_root,
        carton_id=cid,
        item_id=iid,
        target_zone="desk",
        settle_note_cn="摆在书桌台灯边",
        author_side="subject",
        now_iso="2026-11-20T14:45:00+08:00",
    )
    assert unpack_res["memory_sink"]["recorded"] is True
    assert unpack_res["memory_sink"]["episode_key"] == f"v05_craft:unpack:{cid}:{iid}"

    # ------------------------------------------------------------------
    # 3. v05_dreams_desires_foreshadow (dream & intimate privacy respected!)
    # ------------------------------------------------------------------
    hit_seed = _find_dream_hit_seed()
    drm_res = v05_spark.settle_morning_dream_pulse(
        inst_root,
        recent_echoes=["街角旧书店", "雨天檐下"],
        force_roll_seed=hit_seed,
        now_iso="2026-11-20T08:15:00+08:00",
        actor_side="subject",
    )
    assert drm_res["dream_occurred"] is True
    assert drm_res["memory_sink"]["recorded"] is True
    drm_mid = drm_res["memory_sink"]["memory_id"]
    rp_doc = _read_json(inst_root / "memory" / "v02_recall_projection_state.json")
    assert rp_doc["memories"][drm_mid]["shared"] is False
    assert rp_doc["memories"][drm_mid]["visibility"] == "private"

    # Private intimate desire stays shared=False when fulfilled
    intimate_rec = v05_spark.record_subject_desire(
        inst_root,
        layer="micro_craving",
        text="想安安静静靠近抱一会儿",
        category="intimate_warmth",
        shared=True,  # forced False by engine for intimate_warmth
        now_iso="2026-11-20T15:00:00+08:00",
        actor_side="subject",
    )
    intimate_id = intimate_rec["desire"]["desire_id"]
    intimate_ful = v05_spark.resolve_subject_desire(
        inst_root,
        desire_id=intimate_id,
        status="fulfilled",
        resolution_note="靠着暖和了一会儿",
        now_iso="2026-11-20T15:10:00+08:00",
        actor_side="subject",
    )
    assert intimate_ful["memory_sink"]["recorded"] is True
    intimate_mid = intimate_ful["memory_sink"]["memory_id"]
    rp_doc = _read_json(inst_root / "memory" / "v02_recall_projection_state.json")
    assert rp_doc["memories"][intimate_mid]["shared"] is False
    assert rp_doc["memories"][intimate_mid]["visibility"] == "private"

    # Shared craft desire stays shared=True; re-resolving same desire_id is idempotent
    shared_rec = v05_spark.record_subject_desire(
        inst_root,
        layer="micro_craving",
        text="想试着折一只牛皮纸小书签",
        category="craft_ambition",
        shared=True,
        now_iso="2026-11-20T15:15:00+08:00",
        actor_side="subject",
    )
    shared_id = shared_rec["desire"]["desire_id"]
    shared_ful = v05_spark.resolve_subject_desire(
        inst_root,
        desire_id=shared_id,
        status="fulfilled",
        resolution_note="折好夹进正在读的书里了",
        now_iso="2026-11-20T15:20:00+08:00",
        actor_side="subject",
    )
    assert shared_ful["memory_sink"]["recorded"] is True
    shared_mid = shared_ful["memory_sink"]["memory_id"]
    rp_doc = _read_json(inst_root / "memory" / "v02_recall_projection_state.json")
    assert rp_doc["memories"][shared_mid]["shared"] is True

    cnt_before_dup_desire = _store_snapshot_counts(inst_root, paths, episode_db)
    shared_ful_dup = v05_spark.resolve_subject_desire(
        inst_root,
        desire_id=shared_id,
        status="fulfilled",
        resolution_note="折好夹进正在读的书里了",
        now_iso="2026-11-20T15:25:00+08:00",
        actor_side="subject",
    )
    assert shared_ful_dup["memory_sink"]["deduped"] is True
    assert _store_snapshot_counts(inst_root, paths, episode_db) == cnt_before_dup_desire

    # ------------------------------------------------------------------
    # 4. v04_neighborhood_living (rain outing + neighbor chat, idempotent replay)
    # ------------------------------------------------------------------
    v04_nb.apply_v04_neighborhood_command(
        inst_root,
        {"action": "set_local_weather", "weather": "rain", "operation_key": "v04_w_rain"},
        actor_side="subject",
        now_iso="2026-11-20T16:00:00+08:00",
    )
    before_v04 = _store_snapshot_counts(inst_root, paths, episode_db)
    st1, _, _ = v04_nb.apply_v04_neighborhood_command(
        inst_root,
        {
            "action": "start_journey",
            "target_place": "bookstore",
            "operation_key": "v04_rain_walk_01",
        },
        actor_side="subject",
        now_iso="2026-11-20T16:05:00+08:00",
    )
    assert st1 == "applied"
    after_v04_walk = _store_snapshot_counts(inst_root, paths, episode_db)
    assert after_v04_walk["m0_events"] == before_v04["m0_events"] + 1
    assert after_v04_walk["v02_memories"] == before_v04["v02_memories"] + 1

    # Replay same operation_key -> zero duplicate memories
    st1_dup, _, _ = v04_nb.apply_v04_neighborhood_command(
        inst_root,
        {
            "action": "start_journey",
            "target_place": "bookstore",
            "operation_key": "v04_rain_walk_01",
        },
        actor_side="subject",
        now_iso="2026-11-20T16:06:00+08:00",
    )
    assert st1_dup == "already_applied"
    assert _store_snapshot_counts(inst_root, paths, episode_db) == after_v04_walk

    st_cafe, _, _ = v04_nb.apply_v04_neighborhood_command(
        inst_root,
        {
            "action": "interact_cafe_resident",
            "operation": "leave_cafe_coaster_note",
            "coaster_note": "今天雨里的烘焙香很暖",
            "operation_key": "v04_cafe_note_01",
        },
        actor_side="subject",
        now_iso="2026-11-20T16:15:00+08:00",
    )
    assert st_cafe == "applied"
    assert _store_snapshot_counts(inst_root, paths, episode_db)["m0_events"] == after_v04_walk["m0_events"] + 1

    # ------------------------------------------------------------------
    # 5. v03_economy_illness_bridge (rain -> prodrome onset, recovery, work)
    # ------------------------------------------------------------------
    before_v03 = _store_snapshot_counts(inst_root, paths, episode_db)
    v03_eco.record_strain_or_rest(
        inst_root,
        event_kind="caught_in_rain",
        intensity=1.2,
        unchanged_damp_clothes=True,
        operation_key="v03_rain_onset_01",
        now_iso="2026-11-20T17:00:00+08:00",
    )
    after_v03_onset = _store_snapshot_counts(inst_root, paths, episode_db)
    assert after_v03_onset["m0_events"] == before_v03["m0_events"] + 1
    assert after_v03_onset["v02_memories"] == before_v03["v02_memories"] + 1

    # Replay same operation_key -> 0 duplicates
    v03_eco.record_strain_or_rest(
        inst_root,
        event_kind="caught_in_rain",
        intensity=1.2,
        unchanged_damp_clothes=True,
        operation_key="v03_rain_onset_01",
        now_iso="2026-11-20T17:01:00+08:00",
    )
    assert _store_snapshot_counts(inst_root, paths, episode_db) == after_v03_onset

    # Advance recovery until phase returns to healthy -> emits recovery episode
    v03_eco.advance_illness_recovery(
        inst_root,
        "2026-11-23T17:00:00+08:00",
        rest_mode="rest",
        operation_key="v03_recover_well_01",
    )
    after_v03_rec = _store_snapshot_counts(inst_root, paths, episode_db)
    assert after_v03_rec["m0_events"] == after_v03_onset["m0_events"] + 1

    # Economic milestone also emits cleanly & idempotently
    v03_eco.complete_independent_work(
        inst_root,
        job_kind=v03_eco.INDEPENDENT_JOB_KINDS[0],
        work_ref="tx_stage4_01",
        amount_cents=1800,
        operation_key="v03_work_01",
        now_iso="2026-11-23T17:20:00+08:00",
    )
    after_v03_work = _store_snapshot_counts(inst_root, paths, episode_db)
    assert after_v03_work["m0_events"] == after_v03_rec["m0_events"] + 1

    # ------------------------------------------------------------------
    # 6. v02_v03_daily_loop_bridge (notable settled round emits; IDLE skips)
    # ------------------------------------------------------------------
    before_daily = _store_snapshot_counts(inst_root, paths, episode_db)
    daily_bridge._best_effort_feed_v02_autonomous_producers(
        inst_root,
        round_event={
            "action_kind": "STRENUOUS_OUTING_WALK",
            "weather": "rain",
            "topic": "雨天街角散步",
            "summary_cn": "打伞在街角转了一圈，踩着湿漉漉的石板回家。",
            "source_ref": "round:rain_walk_01",
        },
        details={},
        slices_updated=["v04_neighborhood"],
        op_key="op_daily_rain_walk_01",
        action_kind="STRENUOUS_OUTING_WALK",
        settled_at="2026-11-23T18:00:00+08:00",
    )
    after_daily = _store_snapshot_counts(inst_root, paths, episode_db)
    assert after_daily["m0_events"] == before_daily["m0_events"] + 1
    assert after_daily["m1_processed"] == before_daily["m1_processed"] + 1
    daily_mid = sink.compute_episode_memory_id("daily_round:op_daily_rain_walk_01")
    rp_final = _read_json(inst_root / "memory" / "v02_recall_projection_state.json")
    assert daily_mid in rp_final["memories"]

    # Replay same op_key -> idempotent (0 new M0/M1/recall/canonical entries)
    daily_bridge._best_effort_feed_v02_autonomous_producers(
        inst_root,
        round_event={
            "action_kind": "STRENUOUS_OUTING_WALK",
            "weather": "rain",
            "topic": "雨天街角散步",
            "summary_cn": "打伞在街角转了一圈，踩着湿漉漉的石板回家。",
            "source_ref": "round:rain_walk_01",
        },
        details={},
        slices_updated=["v04_neighborhood"],
        op_key="op_daily_rain_walk_01",
        action_kind="STRENUOUS_OUTING_WALK",
        settled_at="2026-11-23T18:00:00+08:00",
    )
    assert _store_snapshot_counts(inst_root, paths, episode_db)["m0_events"] == after_daily["m0_events"]

    # Routine idle no-op must NOT emit a daily_round canonical memory
    before_idle_m0 = _store_snapshot_counts(inst_root, paths, episode_db)["m0_events"]
    daily_bridge._best_effort_feed_v02_autonomous_producers(
        inst_root,
        round_event={"action_kind": "IDLE", "idle_noop": True},
        details={},
        slices_updated=[],
        op_key="op_daily_idle_noop_99",
        action_kind="IDLE",
        settled_at="2026-11-23T18:30:00+08:00",
    )
    assert _store_snapshot_counts(inst_root, paths, episode_db)["m0_events"] == before_idle_m0
    idle_mid = sink.compute_episode_memory_id("daily_round:op_daily_idle_noop_99")
    rp_after_idle = _read_json(inst_root / "memory" / "v02_recall_projection_state.json")
    assert idle_mid not in rp_after_idle["memories"]
