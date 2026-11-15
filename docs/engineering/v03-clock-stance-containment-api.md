# 0.3 Clock / Evaporation & Stance / Nested World Containment Engineering Contract

## 1. Scope & Frozen Boundaries

This document specifies the deterministic, zero-LLM, zero-RNG substrate contracts for:

1. **Three-Clock Offline Projection & Evaporable Ecology** (`components/life/scripts/v03_clock_evaporation.py`)
2. **Owner-Level Stance Lineage & Nested World Containment Ledger** (`components/life/scripts/life_attention_stance_containment.py`, ADR-047 & ADR-048)
3. **Phone Projection & Desk/Table View Bridge** (`components/phone/stance_containment_view.py`)

### Invariants & Frozen Anchors
- **Zero LLM / Zero RNG**: All reads, projections, slow-variable curves, TTL evaporations, stance lineage updates, and container state transitions execute in `O(N)` deterministic Python (`llm_calls == 0`, zero `random`/`secrets` imports).
- **100% Zero-Write Pure Reads**: `read_v03_clock_view`, `read_attention_stance_containment`, and `read_stance_containment` never create directories (`life/`, `phone/`) or touch disk when state files are absent.
- **Open-Source Character Neutral**: Dynamic resolution from `character.json` / `instance.json`; defaults to `"TA"`.
- **Untouched Frozen Anchors**:
  - `components/life/scripts/autonomous_life_assembly.py` (`76b4c7cbcdc29154851fedcd2b962f93549092c35f8feeedec29bfc93169fa5a`)
  - `components/life/scripts/life_sharing_service.py` (`0becf7df4d9b34881817ff6f1defdcc15929eacbd6183d4c8a38c5a048d46038`)

---

## 2. Three-Clock Projection & Evaporable Ecology (`v03_clock_evaporation.py`)

- **View Schema**: `phone.v03.clock.v1`
- **State Schema**: `life.v03.clock_evaporation.v1` (`<day_root>/phone/v03_clock_state.json`)
- **HTTP Route**: `GET / POST /api/phone/v03/clock`
- **Formula**: `World(t1) = F(World(t0), t1 - t0, rulepack_id)` (`rulepack_id = "nyairo.v03.deterministic_slow_vars.v1"`)

### 2.1 Three Clocks & Iron Laws
- **`wall_clock`**: Real ISO-8601 timestamp (`now_iso`, `local_date`).
- **`world_clock`**: Monotonic physical slow-variable projection (`409 WORLD_CLOCK_MONOTONIC_VIOLATION` on backward timestamps):
  - `damp_outerwear`: dries after `21600s` (6 hours, `damp -> dry`, `settled_dry = True`).
  - `desk_drink`: `< 900s` `"warm"`, `900s..1800s` `"tepid"`, `>= 1800s` (30 minutes) `"room_temp"` (`is_cooled = True`).
  - `missing_note`: fades naturally after `259200s` (72 hours) with `no_absence_debt = True`.
- **`cognitive_clock`**: Causal subject ticks (`cognitive_backfill_forbidden == True`, `fabricated_downtime_episodes == 0`). Offline gaps never fabricate cognitive episodes.
- **Discrete `UNKNOWN` Action Law**: Pending discrete actions (`effect_state == "UNKNOWN"`) are never guessed into `APPLIED` or `FAILED` by clock advancement (`guessed_by_clock == False`); only `resolve_unknown_action` with an explicit `receipt_ref` resolves them.

### 2.2 Accountability Partitions & Meaningless Idle
- **Accountability Tiers**: `("COMMITTED_FACT", "SUBJECT_IMPRESSION", "EVAPORABLE")`.
- **Silent Evaporation**: Expired `EVAPORABLE` items disappear silently on read and are purged on `advance_projection` with `tombstone_count == 0` and `audit_backlog_count == 0`.
- **Accountable State Guard**: Writing wallet/money, object placement/containment, or intimacy/mutual consent domains or payload keys (`amount_jpy`, `parent_container_id`, `consent_id`, etc.) into `record_evaporable` fails closed with `409 EVAPORABLE_FORBIDDEN_FOR_ACCOUNTABLE_STATE`.
- **Meaningless Idle**: `start_meaningless_idle` / `end_meaningless_idle` (`daydream_window`, `aimless_page_flip`, `halfway_abandon_tidy`, `listen_rain_blankly`) enforce `growth_score_delta == 0`, `productivity_penalty == 0`, `justification_required == False`, and `forced_summary_generated == False`.
- **`sync_v02_slices`**: Deterministically projects committed world facts (`world_state.json`, `stance_containment.json`, `delivered_shares.json`) into 0.2 phone slices (`outing_bench_bookstore.json`, `outing_routes_market.json`, `reunion_anchor.json`, `longterm_room_habits.json`).

---

## 3. Owner Stance Lineage & Nested World Containment (`life_attention_stance_containment.py` & `stance_containment_view.py`)

- **Owner Ledger Schema**: `life.v03.attention_stance_containment.v1` (`<day_root>/life/attention_stance_containment.json`)
- **Phone View Schema**: `phone.v03.stance_containment.v1` (`<day_root>/phone/stance_containment.json`)
- **HTTP Route**: `GET / POST /api/phone/stance-containment`

### 3.1 Reading Material Anchor & Stance Revision Lineage (ADR-047)
- **Supply / Workbench Probing**: `discover_reading_materials(day_root)` and `probe_reading_material_revision(day_root, scope_ref)` inspect `<day_root>/supply/` and `<day_root>/phone/reading_workbench.json` (zero-write). When a material is mounted, omitted `source_revision` auto-binds to the mounted revision, while a mismatched `source_revision` raises `409 STANCE_SOURCE_REVISION_MISMATCH`. Unmounted scopes accept explicit `(scope_ref, source_revision, page_or_anchor, facet)`.
- **Closed Facet Enum**: `("appearance", "content", "method_fit")`.
- **Actions**:
  - `propose_stance`: Creates root stance (`status = "ACTIVE"`, `shared_with_partner = False` by default).
  - `fix_stance_typo`: CAS via `expected_version` (`409 STANCE_VERSION_CONFLICT`), edits `text` in-place under the same `stance_id` without altering lineage.
  - `revise_stance`: Creates a new `stance_id` linked via `replaces_stance_id` / `superseded_by`; marks prior stance `SUPERSEDED` without overwriting historical text.
  - `mark_source_updated`: Advances `current_source_revision` and marks prior active stances `SOURCE_REVISION_STALE` while retaining original text.
  - `revoke_stance_source`: Marks stances `SOURCE_WITHDRAWN`, purges `text`, `cached_excerpt`, and `private_note` on disk, records tombstones in `withdrawn_sources`, and prevents resurrection across `rebuild_stance_index` or `operation_key` replay (`409 STANCE_SOURCE_WITHDRAWN`).

### 3.2 Nested World Containers, Open/Lock Gate & Two-Hand Partial Completion (ADR-048)
- **Topology**: `room -> place/surface -> container (optional parent_container_id, depth <= 3) -> item`, plus `left_hand` / `right_hand` slots.
- **Container Open States**: `("open_top", "open", "closed", "locked")` (default `"open_top"`), mutated via `set_container_open_state` and configured via `register_container` / `nest_container`.
  - Any `"locked"` container or ancestor blocks item insertion/extraction with `409 CONTAINER_LOCKED`.
  - Any `"closed"` container or ancestor blocks item insertion/extraction with `409 CONTAINER_CLOSED`.
- **Non-Atomic Partial Completion (`pick_and_put_in_container`)**:
  - Step 1 picks up the reachable item into `hand_slot`.
  - If Step 2 fails due to `CONTAINER_LOCKED`, `CONTAINER_CLOSED`, `CONTAINER_FULL`, `CONTAINER_NOT_REACHABLE`, or `CONTAINER_CLASS_REJECTED`, the item remains persisted in `hand_slot` on disk (`status = "partial_completed"`, `partial_completion = True`, `partial_completed = True`, `rolled_back = False`).
- **Physics vs. Subject Spatial Impression**:
  - `subject_move_container` and `partner_move_container` move the container together with all nested `descendant_container_ids` and child items.
  - When `partner_move_container` runs without co-located subject witness (`subject_impression_updated == False`), `subject_last_seen_place` stays at the old location (`impression_stale == True`) until the subject executes `observe_current_room` (or arrives with `observe_on_arrival = True`).
