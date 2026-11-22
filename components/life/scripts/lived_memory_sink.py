#!/usr/bin/env python3
"""Stage 4 Canonical Lived-Memory Sink (Blueprint §4.2A).

Single write-only, idempotent, zero-LLM fan-out entry point that settles lived
episodes from v03/v04/v05/daily-loop engines into the four canonical memory
stores while enforcing the pre-write governance gates:

1. Unified Forget Barrier (`load_unified_forget_barrier_readonly` +
   `is_entity_forget_blocked`): refuses with `FORGET_BARRIER_BLOCKED` and
   ZERO writes if fail-closed or if `episode_key` / `memory_id` / `source_refs`
   hit any hold, tombstone, V-C seal, or native authority block.
2. Active Representation Lease (`enforce_active_representation_lease`,
   `scope="episodic_memory"`): refuses with `REPRESENTATION_LEASE_BLOCKED` and
   ZERO writes if revoked or expired.
3. Four-store coordinated idempotent settlement (touching SQLite databases
   ONLY when already initialized or inside a managed instance):
   - Native M0 `evidence.db` via `LifeExperiencePort.append_outcome`
     (+ optional bounded single-pass M1 `EpisodeWorker.process_once()` with
     `ConservativeBoundaryJudge` when sibling `episode.db` already exists);
   - S07 Affect (`s07_affect_owner.record_live_affect_pulse`) when
     `emotional_salience >= 0.42` or `affect_kind` is set;
   - `v02_recall_projection.record_episodic_trace` keyed by deterministic
     `memory_id = f"mem_{sha256(episode_key)[:12]}"`;
   - `v02/canonical_life.sqlite3` via
     `v02_crash_and_partial_reconciler.record_live_cycle_settlement`.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

_LIFE_SCRIPTS = str(Path(__file__).resolve().parent)
_REPO_ROOT = Path(__file__).resolve().parents[3]
_WORLD_SCRIPTS = str(_REPO_ROOT / "components" / "world" / "scripts")
_NATIVE_ROOT = str(_REPO_ROOT / "components" / "native")
_NATIVE_SRC = str(_REPO_ROOT / "components" / "native" / "src")
for _p in (_LIFE_SCRIPTS, _WORLD_SCRIPTS, str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import instance_paths as _instance_paths  # noqa: E402
import s07_affect_owner as _s07_affect  # noqa: E402
import v02_crash_and_partial_reconciler as _reconciler  # noqa: E402
import v02_recall_projection as _recall_proj  # noqa: E402
import v02_representation_lease_forget as _rlf  # noqa: E402

SCHEMA_LIVED_MEMORY_SINK = "nyairo.lived_memory_sink.v1"

VALID_LIVED_DOMAINS: tuple[str, ...] = (
    "v03_illness",
    "v04_outing",
    "v05_town",
    "v05_craft",
    "v05_dream_desire",
    "daily_round",
    "v09_stance",
    "v09_commitment",
)

DOMAIN_TO_CATEGORY_CN: dict[str, str] = {
    "v03_illness": "体感与照料",
    "v04_outing": "街区出行",
    "v05_town": "小镇见闻",
    "v05_craft": "居家手作",
    "v05_dream_desire": "梦境与心愿",
    "daily_round": "日常经历",
    "v09_stance": "主见与选择",
    "v09_commitment": "街坊约定",
}

S07_SALIENCE_THRESHOLD: float = 0.42
DEFAULT_M0_OWNER: str = "chiyo"


@dataclass(frozen=True)
class LivedEpisodeEvent:
    """Canonical payload for a single lived-life episode settled into memory."""

    episode_key: str
    domain: str
    topic: str
    exact_detail: str
    gist_summary: str
    occurred_at: str
    emotional_salience: float = 0.45
    affect_kind: Optional[str] = None
    affect_intensity: int = 36
    shared: bool = True
    source_refs: tuple[tuple[str, str], ...] = ()


def compute_episode_memory_id(episode_key: str) -> str:
    """Return deterministic recall-projection memory_id for ``episode_key``."""
    clean_key = str(episode_key or "").strip()
    digest = hashlib.sha256(clean_key.encode("utf-8")).hexdigest()[:12]
    return f"mem_{digest}"


def _strip_emojis_and_ctrl(text: Any, *, max_len: int = 400) -> str:
    raw = str(text or "")
    chars: list[str] = []
    for ch in raw:
        cat = unicodedata.category(ch)
        if cat == "So":
            continue
        if cat.startswith("C") and ch not in ("\n", "\t"):
            continue
        chars.append(ch)
    cleaned = " ".join("".join(chars).split()).strip()
    if max_len > 0 and len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip()
    return cleaned


def _normalize_iso_timestamp(raw: Optional[str], *, fallback_iso: Optional[str] = None) -> str:
    for candidate in (raw, fallback_iso):
        if isinstance(candidate, str) and candidate.strip():
            text = candidate.strip()
            try:
                dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.isoformat()
            except ValueError:
                continue
    return datetime.now(timezone.utc).isoformat()


def _normalize_source_refs(raw: Any) -> tuple[tuple[str, str], ...]:
    if not raw:
        return ()
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    if isinstance(raw, Mapping):
        items: Sequence[Any] = list(raw.items())
    elif isinstance(raw, (list, tuple, set)):
        items = list(raw)
    else:
        return ()
    for item in items:
        kind = ""
        ident = ""
        if isinstance(item, (tuple, list)) and len(item) >= 2:
            kind = _strip_emojis_and_ctrl(item[0], max_len=48)
            ident = _strip_emojis_and_ctrl(item[1], max_len=140)
        elif isinstance(item, Mapping):
            kind = _strip_emojis_and_ctrl(item.get("kind") or item.get("type") or "", max_len=48)
            ident = _strip_emojis_and_ctrl(item.get("id") or item.get("ref") or "", max_len=140)
        elif isinstance(item, str) and item.strip():
            s = _strip_emojis_and_ctrl(item, max_len=160)
            if ":" in s:
                k, v = s.split(":", 1)
                kind = k.strip()[:48]
                ident = v.strip()[:140]
            else:
                kind = "ref"
                ident = s[:140]
        if kind and ident:
            pair = (kind, ident)
            if pair not in seen:
                seen.add(pair)
                out.append(pair)
        if len(out) >= 16:
            break
    return tuple(out)


def _clamp_salience(val: Any, default: float = 0.45) -> float:
    try:
        if isinstance(val, bool):
            return default
        f = float(val)
    except (TypeError, ValueError):
        return default
    if f != f:  # NaN guard
        return default
    return round(max(0.0, min(1.0, f)), 4)


def _clamp_intensity(val: Any, default: int = 36) -> int:
    try:
        if isinstance(val, bool):
            return default
        n = int(round(float(val)))
    except (TypeError, ValueError):
        return default
    return max(1, min(int(_s07_affect.INTENSITY_SCALE_HUNDREDTHS), n))


def _coerce_event(
    event: LivedEpisodeEvent | Mapping[str, Any],
    *,
    fallback_iso: str,
) -> LivedEpisodeEvent:
    if isinstance(event, LivedEpisodeEvent):
        raw_key = event.episode_key
        raw_domain = event.domain
        raw_topic = event.topic
        raw_exact = event.exact_detail
        raw_gist = event.gist_summary
        raw_occurred = event.occurred_at
        raw_salience = event.emotional_salience
        raw_affect_kind = event.affect_kind
        raw_intensity = event.affect_intensity
        raw_shared = event.shared
        raw_refs = event.source_refs
    elif isinstance(event, Mapping):
        raw_key = event.get("episode_key")
        raw_domain = event.get("domain") or "daily_round"
        raw_topic = event.get("topic") or ""
        raw_exact = event.get("exact_detail") or ""
        raw_gist = event.get("gist_summary") or ""
        raw_occurred = event.get("occurred_at") or fallback_iso
        raw_salience = event.get("emotional_salience", 0.45)
        raw_affect_kind = event.get("affect_kind")
        raw_intensity = event.get("affect_intensity", 36)
        raw_shared = event.get("shared", True)
        raw_refs = event.get("source_refs") or ()
    else:
        raise TypeError("event must be LivedEpisodeEvent or Mapping")

    episode_key = _strip_emojis_and_ctrl(raw_key, max_len=160)
    domain = _strip_emojis_and_ctrl(raw_domain, max_len=48).lower() or "daily_round"
    topic = _strip_emojis_and_ctrl(raw_topic, max_len=120) or domain
    exact_detail = _strip_emojis_and_ctrl(raw_exact, max_len=600) or topic
    gist_summary = _strip_emojis_and_ctrl(raw_gist, max_len=240) or exact_detail[:180]
    occurred_at = _normalize_iso_timestamp(
        str(raw_occurred) if raw_occurred is not None else None,
        fallback_iso=fallback_iso,
    )
    affect_kind: Optional[str] = None
    if isinstance(raw_affect_kind, str) and raw_affect_kind.strip():
        affect_kind = _strip_emojis_and_ctrl(raw_affect_kind, max_len=48).lower() or None

    return LivedEpisodeEvent(
        episode_key=episode_key,
        domain=domain,
        topic=topic,
        exact_detail=exact_detail,
        gist_summary=gist_summary,
        occurred_at=occurred_at,
        emotional_salience=_clamp_salience(raw_salience, 0.45),
        affect_kind=affect_kind,
        affect_intensity=_clamp_intensity(raw_intensity, 36),
        shared=bool(raw_shared),
        source_refs=_normalize_source_refs(raw_refs),
    )


def _is_event_forget_blocked(
    event: LivedEpisodeEvent,
    memory_id: str,
    barrier: Mapping[str, Any],
) -> bool:
    if barrier.get("fail_closed"):
        return True
    blocked_ids = barrier.get("blocked_ids") or ()
    candidates: list[str] = [
        event.episode_key,
        memory_id,
        f"life_outcome:{event.episode_key}",
        f"lived_ep:{event.episode_key}",
        f"{event.domain}:{event.episode_key}",
    ]
    probe_entity: dict[str, Any] = {
        "episode_key": event.episode_key,
        "memory_id": memory_id,
        "id": event.episode_key,
        "source_id": event.episode_key,
        "source_refs": [event.episode_key, memory_id],
    }
    for kind, ident in event.source_refs:
        candidates.append(ident)
        candidates.append(f"{kind}:{ident}")
        probe_entity["source_refs"].append(ident)
        probe_entity["source_refs"].append(f"{kind}:{ident}")
        if kind not in probe_entity:
            probe_entity[kind] = ident

    for cand in candidates:
        if cand in blocked_ids:
            return True
        if _rlf.is_entity_forget_blocked(cand, barrier):
            return True
    if _rlf.is_entity_forget_blocked(probe_entity, barrier):
        return True
    return False


def _read_json_file_readonly(path: Path) -> Optional[dict[str, Any]]:
    try:
        if not path.is_file() or path.is_symlink():
            return None
        parsed = json.loads(path.read_text(encoding="utf-8"))
        return dict(parsed) if isinstance(parsed, Mapping) else None
    except (OSError, ValueError, TypeError):
        return None


def _resolve_m0_owner_and_min_seq(paths: _instance_paths.InstancePaths) -> tuple[str, Optional[int]]:
    owner = DEFAULT_M0_OWNER
    min_seq: Optional[int] = None
    nested = _read_json_file_readonly(paths.day_root / "one-day" / "instance.json")
    env_map = nested.get("environment") if isinstance(nested, Mapping) else None
    if isinstance(env_map, Mapping):
        for k in ("NYA02_MEMORY_SUBJECT", "NYA02_SUBJECT"):
            val = str(env_map.get(k) or "").strip()
            if val:
                owner = val
                break
        for k in ("NYA02_MEMORY_AUTHORITY_MIN_SEQ", "CHIYO_LIFE_MEMORY_AUTHORITY_MIN_SEQ"):
            raw_seq = str(env_map.get(k) or "").strip()
            if raw_seq:
                try:
                    min_seq = int(raw_seq)
                except ValueError:
                    min_seq = None
                break
    return owner, min_seq


def _resolve_existing_m1_episode_db(paths: _instance_paths.InstancePaths) -> Optional[Path]:
    candidates: list[Path] = []
    attr_m1 = getattr(paths, "memory_episode_db", None)
    if isinstance(attr_m1, (str, Path)) and str(attr_m1).strip():
        candidates.append(Path(attr_m1))
    candidates.extend(
        [
            paths.memory_evidence_db.parent / "episode.db",
            paths.memory_dir / "episode.db",
            paths.day_root / "memory" / "episode.db",
        ]
    )
    seen: set[str] = set()
    for cand in candidates:
        key = str(cand)
        if key in seen:
            continue
        seen.add(key)
        try:
            if cand.is_file() and not cand.is_symlink():
                return cand
        except OSError:
            continue
    return None


def _lookup_existing_m0_event_id_readonly(evidence_db: Path, source_origin: str, primary_ref: str) -> Optional[str]:
    conn: Optional[sqlite3.Connection] = None
    try:
        resolved = evidence_db.resolve()
        query = "?mode=ro" if Path(f"{resolved}-wal").exists() else "?mode=ro&immutable=1"
        conn = sqlite3.connect(resolved.as_uri() + query, uri=True, timeout=2.0)
        row = conn.execute(
            "SELECT event_id FROM evidence_events WHERE source_origin=? AND primary_source_ref_id=?",
            (source_origin, primary_ref),
        ).fetchone()
        return str(row[0]) if row is not None and row[0] else None
    except sqlite3.Error:
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass


def _settle_native_m0_and_m1(
    paths: _instance_paths.InstancePaths,
    event: LivedEpisodeEvent,
    memory_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    evidence_db = paths.memory_evidence_db
    if os.name != "posix":
        return (
            {"active": False, "recorded": False, "deduped": False, "skipped": True, "reason": "PLATFORM_NON_POSIX"},
            {"active": False, "recorded": False, "deduped": False, "skipped": True, "reason": "PLATFORM_NON_POSIX"},
        )
    try:
        ev_exists = evidence_db.is_file() and not evidence_db.is_symlink()
    except OSError:
        ev_exists = False
    if not ev_exists:
        return (
            {"active": False, "recorded": False, "deduped": False, "skipped": True, "reason": "EVIDENCE_DB_NOT_INITIALIZED"},
            {"active": False, "recorded": False, "deduped": False, "skipped": True, "reason": "M1_EPISODE_DB_NOT_INITIALIZED"},
        )

    for _p in (_NATIVE_SRC, _NATIVE_ROOT):
        if _p not in sys.path:
            sys.path.insert(0, _p)

    from app.evidence import LIFE_ORIGIN  # noqa: E402
    from components.native.memory_runtime_v1.life_experience_port import (  # noqa: E402
        LifeExperiencePort,
        build_outcome_event,
    )

    owner, min_seq = _resolve_m0_owner_and_min_seq(paths)
    controls_db = (
        paths.memory_controls_db
        if paths.memory_controls_db.is_file() and not paths.memory_controls_db.is_symlink()
        else None
    )
    authority_db: str | Path = (
        paths.memory_authority_db
        if (
            paths.memory_authority_db is not None
            and paths.memory_authority_db.is_file()
            and not paths.memory_authority_db.is_symlink()
        )
        else ""
    )

    m0_text = f"[{event.domain}] {event.topic}: {event.exact_detail}"[:1000]
    extra_refs: list[tuple[str, str]] = [
        ("domain", event.domain),
        ("memory_id", memory_id),
    ]
    extra_refs.extend(event.source_refs)

    ev_obj = build_outcome_event(
        subject=owner,
        source_ref=event.episode_key,
        text=m0_text,
        occurred_at=event.occurred_at,
        refs=extra_refs,
    )
    port = LifeExperiencePort(
        evidence_db,
        controls_db=controls_db,
        authority_db=authority_db,
        authority_min_seq=min_seq,
        subject=owner,
    )

    try:
        append_res = port.append_outcome(ev_obj)
    except ValueError as exc:
        if "idempotency key conflicts" in str(exc).lower():
            existing_id = _lookup_existing_m0_event_id_readonly(evidence_db, LIFE_ORIGIN, event.episode_key)
            append_res = {"status": "duplicate", "event_id": existing_id or ev_obj.event_id}
        else:
            raise

    status = str(append_res.get("status") or "")
    m0_receipt: dict[str, Any] = {
        "active": True,
        "recorded": status == "inserted",
        "deduped": status == "duplicate",
        "status": status,
        "event_id": append_res.get("event_id"),
        "path": str(evidence_db),
    }
    if status == "refused":
        m0_receipt["blocked"] = True
        m0_receipt["reason"] = append_res.get("reason") or "M0_REFUSED"

    # Optional bounded single-pass M1 EpisodeWorker indexing when sibling episode.db already exists
    m1_db = _resolve_existing_m1_episode_db(paths)
    if m1_db is None:
        m1_receipt: dict[str, Any] = {
            "active": False,
            "recorded": False,
            "deduped": False,
            "skipped": True,
            "reason": "M1_EPISODE_DB_NOT_INITIALIZED",
        }
        return m0_receipt, m1_receipt

    from app.m1 import (  # noqa: E402
        ConservativeBoundaryJudge,
        EpisodeStore,
        EpisodeWorker,
        M0EvidenceReader,
    )

    reader = M0EvidenceReader(evidence_db)
    store = EpisodeStore(m1_db)
    worker = EpisodeWorker(reader, store, ConservativeBoundaryJudge())
    m1_res = worker.process_once()
    inserted_n = int(m1_res.get("inserted", 0) or 0)
    duplicate_n = int(m1_res.get("duplicate", 0) or 0)
    failed_n = int(m1_res.get("failed", 0) or 0)
    m1_receipt = {
        "active": True,
        "recorded": inserted_n > 0,
        "deduped": inserted_n == 0 and failed_n == 0,
        "inserted": inserted_n,
        "duplicate": duplicate_n,
        "failed": failed_n,
        "path": str(m1_db),
    }
    return m0_receipt, m1_receipt


def _s07_op_already_present_readonly(root: Path, op_key: str, source_ref: str) -> Optional[str]:
    store_file = _s07_affect.affect_store_path(root / "life")
    doc = _read_json_file_readonly(store_file)
    if not isinstance(doc, Mapping):
        return None
    ops = doc.get("operations")
    if isinstance(ops, Mapping) and op_key in ops:
        row = ops.get(op_key)
        return str(row.get("record_id") or "") if isinstance(row, Mapping) else ""
    ring = doc.get("committed_op_ring")
    if isinstance(ring, list):
        for item in ring:
            if isinstance(item, Mapping) and str(item.get("operation_key") or "") == op_key:
                return str(item.get("record_id") or "")
    records = doc.get("records")
    if isinstance(records, list):
        for rec in records:
            if not isinstance(rec, Mapping):
                continue
            srefs = rec.get("source_refs")
            if isinstance(srefs, list) and source_ref in srefs:
                return str(rec.get("record_id") or "")
    return None


def _settle_s07_affect(
    root: Path,
    event: LivedEpisodeEvent,
    effective_now_iso: str,
) -> dict[str, Any]:
    if event.emotional_salience < S07_SALIENCE_THRESHOLD and not event.affect_kind:
        return {
            "active": False,
            "recorded": False,
            "deduped": False,
            "skipped": True,
            "reason": "BELOW_SALIENT_THRESHOLD",
        }

    op_key = f"lived_ep:{event.episode_key}"[:120]
    source_ref = f"lived:{event.domain}:{event.episode_key}"[:120]

    existing_rec_id = _s07_op_already_present_readonly(root, op_key, source_ref)
    if existing_rec_id is not None:
        return {
            "active": True,
            "recorded": False,
            "deduped": True,
            "operation_key": op_key,
            "record_id": existing_rec_id or None,
        }

    if event.affect_kind in _s07_affect.AFFECT_RECORD_KINDS:
        clean_kind = str(event.affect_kind)
    elif event.affect_kind and "shift" in event.affect_kind:
        clean_kind = _s07_affect.AFFECT_KIND_MOOD_SHIFT
    elif event.affect_kind and "drive" in event.affect_kind:
        clean_kind = _s07_affect.AFFECT_KIND_DRIVE
    else:
        clean_kind = _s07_affect.AFFECT_KIND_AFFECT_PULSE

    note_text = f"{event.topic}: {event.gist_summary}"[:180]
    try:
        res = _s07_affect.record_live_affect_pulse(
            root,
            source_ref=source_ref,
            kind=clean_kind,
            intensity=event.affect_intensity,
            note=note_text,
            ttl_hours=6,
            operation_key=op_key,
            now_iso=effective_now_iso,
        )
    except _s07_affect.S07AffectOwnerError as exc:
        if "OPERATION_KEY_CONFLICT" in str(exc):
            return {
                "active": True,
                "recorded": False,
                "deduped": True,
                "operation_key": op_key,
            }
        raise

    is_replay = bool(res.get("idempotent_replay") or res.get("already_applied"))
    return {
        "active": True,
        "recorded": bool(res.get("ok")) and not is_replay,
        "deduped": is_replay,
        "operation_key": op_key,
        "record_id": res.get("record_id"),
        "kind": clean_kind,
        "store_path": res.get("store_path"),
    }


def _settle_v02_recall_projection(
    root: Path,
    event: LivedEpisodeEvent,
    memory_id: str,
    effective_now_iso: str,
) -> dict[str, Any]:
    rp_state = _read_json_file_readonly(root / "memory" / "v02_recall_projection_state.json")
    if isinstance(rp_state, Mapping):
        tombstones = rp_state.get("tombstones")
        if isinstance(tombstones, Mapping) and memory_id in tombstones:
            return {
                "active": True,
                "recorded": False,
                "deduped": False,
                "blocked": True,
                "reason": "MEMORY_TOMBSTONED",
                "memory_id": memory_id,
            }
        memories = rp_state.get("memories")
        if isinstance(memories, Mapping) and memory_id in memories:
            return {
                "active": True,
                "recorded": False,
                "deduped": True,
                "memory_id": memory_id,
            }

    category_cn = DOMAIN_TO_CATEGORY_CN.get(event.domain, "日常经历")
    res = _recall_proj.record_episodic_trace(
        root,
        memory_id=memory_id,
        topic=event.topic,
        exact_detail=event.exact_detail,
        gist_summary=event.gist_summary,
        category=category_cn,
        occurred_at=event.occurred_at,
        emotional_salience=event.emotional_salience,
        shared=bool(event.shared),
        visibility="shared" if event.shared else "private",
        episode_key=event.episode_key,
        source_refs=event.source_refs,
        now_iso=effective_now_iso,
        has_subject_capability=True,
    )
    if not res.get("ok"):
        return {
            "active": True,
            "recorded": False,
            "deduped": False,
            "blocked": True,
            "reason": str(res.get("error_code") or res.get("reason") or "RECALL_TRACE_REFUSED"),
            "memory_id": memory_id,
        }
    return {
        "active": True,
        "recorded": True,
        "deduped": False,
        "memory_id": memory_id,
        "visibility": "shared" if event.shared else "private",
    }


def _settle_v02_canonical_ledger(
    paths: _instance_paths.InstancePaths,
    event: LivedEpisodeEvent,
    memory_id: str,
    effective_now_iso: str,
) -> dict[str, Any]:
    root = paths.day_root
    canonical_db = _reconciler.canonical_ledger_path(root)
    try:
        db_exists = canonical_db.is_file() and not canonical_db.is_symlink()
    except OSError:
        db_exists = False

    if not db_exists:
        return {
            "active": False,
            "recorded": False,
            "deduped": False,
            "skipped": True,
            "reason": "V02_CANONICAL_NOT_INITIALIZED",
        }

    cycle_id = f"lived_ep:{event.episode_key}"[:160]
    res = _reconciler.record_live_cycle_settlement(
        root,
        cycle_id=cycle_id,
        action_kind=f"LIVED_{event.domain.upper()}",
        outcome_status="SETTLED",
        target_ref=f"{event.domain}:{memory_id}"[:240],
        summary=f"{event.topic}: {event.gist_summary}"[:360],
        now_iso=effective_now_iso,
    )
    return {
        "active": True,
        "recorded": bool(res.get("recorded")),
        "deduped": bool(res.get("idempotent_replay")),
        "cycle_id": res.get("cycle_id"),
        "event_id": res.get("event_id"),
        "ledger_path": res.get("ledger_path"),
    }


def record_lived_episode_to_canonical_memory(
    day_root: Path | str,
    event: LivedEpisodeEvent | Mapping[str, Any],
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Settle a lived episode into canonical memory stores with governance & idempotency.

    Performs ZERO disk writes if blocked by Forget Barrier or Representation Lease.
    Never creates stray SQLite `.db`/`.sqlite3` files on uninitialized roots.
    Always runs with ``llm_calls == 0``.
    """
    paths = _instance_paths.resolve_instance_paths(day_root)
    root = paths.day_root
    effective_now_iso = _normalize_iso_timestamp(now_iso)
    ev = _coerce_event(event, fallback_iso=effective_now_iso)

    if not ev.episode_key:
        return {
            "ok": False,
            "recorded": False,
            "deduped": False,
            "blocked": False,
            "reason": "EPISODE_KEY_REQUIRED",
            "memory_id": "",
            "episode_key": "",
            "stores": {},
            "llm_calls": 0,
        }

    memory_id = compute_episode_memory_id(ev.episode_key)

    # Pre-Write Governance Gate 1: Unified Forget Barrier (strictly zero-write)
    barrier = _rlf.load_unified_forget_barrier_readonly(root)
    if _is_event_forget_blocked(ev, memory_id, barrier):
        return {
            "ok": False,
            "recorded": False,
            "deduped": False,
            "blocked": True,
            "reason": "FORGET_BARRIER_BLOCKED",
            "memory_id": memory_id,
            "episode_key": ev.episode_key,
            "stores": {},
            "llm_calls": 0,
        }

    # Pre-Write Governance Gate 2: Active Representation Lease (strictly zero-write)
    lease_check = _rlf.enforce_active_representation_lease(
        root,
        scope="episodic_memory",
        now_iso=effective_now_iso,
    )
    if not lease_check.get("allowed", True):
        return {
            "ok": False,
            "recorded": False,
            "deduped": False,
            "blocked": True,
            "reason": "REPRESENTATION_LEASE_BLOCKED",
            "lease_reason": lease_check.get("reason"),
            "lease_id": lease_check.get("lease_id"),
            "memory_id": memory_id,
            "episode_key": ev.episode_key,
            "stores": {},
            "llm_calls": 0,
        }

    # Four-Store Coordinated Idempotent Settlement
    m0_res, m1_res = _settle_native_m0_and_m1(paths, ev, memory_id)
    s07_res = _settle_s07_affect(root, ev, effective_now_iso)
    recall_res = _settle_v02_recall_projection(root, ev, memory_id, effective_now_iso)
    canonical_res = _settle_v02_canonical_ledger(paths, ev, memory_id, effective_now_iso)

    stores: dict[str, Any] = {
        "evidence_db": m0_res,
        "m1_episode": m1_res,
        "s07_affect": s07_res,
        "v02_recall": recall_res,
        "v02_canonical": canonical_res,
    }

    any_recorded = any(bool(item.get("recorded")) for item in stores.values())
    active_items = [item for item in stores.values() if item.get("active")]
    all_deduped = bool(active_items) and (not any_recorded) and all(
        bool(item.get("deduped")) for item in active_items
    )

    return {
        "ok": True,
        "recorded": any_recorded,
        "deduped": all_deduped,
        "blocked": False,
        "memory_id": memory_id,
        "episode_key": ev.episode_key,
        "domain": ev.domain,
        "stores": stores,
        "llm_calls": 0,
    }


def emit_lived_episode_safe(
    day_root: Path | str,
    event: LivedEpisodeEvent | Mapping[str, Any],
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Bounded, non-blocking helper for upstream engines' write/settle paths."""
    try:
        return record_lived_episode_to_canonical_memory(day_root, event, now_iso=now_iso)
    except Exception as exc:
        return {
            "ok": False,
            "recorded": False,
            "deduped": False,
            "blocked": False,
            "reason": f"SINK_ERROR:{type(exc).__name__}",
            "stores": {},
            "llm_calls": 0,
        }


settle_lived_episode = record_lived_episode_to_canonical_memory

__all__ = [
    "DOMAIN_TO_CATEGORY_CN",
    "LivedEpisodeEvent",
    "S07_SALIENCE_THRESHOLD",
    "SCHEMA_LIVED_MEMORY_SINK",
    "VALID_LIVED_DOMAINS",
    "compute_episode_memory_id",
    "emit_lived_episode_safe",
    "record_lived_episode_to_canonical_memory",
    "settle_lived_episode",
]
