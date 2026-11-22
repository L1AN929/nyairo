# Convergence Ticket Discipline (2026-11-16)

This document defines the mandatory convergence-ticket standard for cleanup and hardening waves in `D:\nyairo-rc1-body-backend` and `D:\个人\nyairo-desktop`.

## 1. Work Separation
Every unit of work is classified as either:
- **Round A — Behavior-Preserving Convergence**: merge duplicated adapters, shared transport/envelope logic, and split-brain state projections while preserving existing HTTP paths, status semantics (fixing only proven copy-paste status drift without changing valid contract semantics), JSON payloads, RBAC whitelists, and TTL timestamps.
- **Round B/C — Boundary Fixes & Capability Extensions**: explicit locking (`state_tx_guard.py`), submission-ordered retry window & reconciliation, true persisted receipt verification, cognitive epistemic masking, causal relational micro-actions, and Daily Loop soft landing.

Behavior-preserving convergence and boundary/semantic changes MUST NOT be mixed in the same worker ticket.

## 2. Mandatory Convergence Ticket Fields
Every convergence ticket and worker brief must declare:
1. **Duplicated implementations replaced** (exact functions / handlers / projections being unified).
2. **Canonical authority / single entry point** (one shared helper or authoritative state owner).
3. **Migrated callers** (every call site switched to the canonical entry point).
4. **Deleted or deactivated legacy code** (confirm zero remaining writers, shadow schedulers, or bypass readers).
5. **Exclusive write set & hotspot ownership** (`components/phone/service.py`, `components/phone/server.py`, and shared transaction guards are each owned by **one exclusive worker per round** — never split across parallel workers writing the same file).
6. **Domain differences preserved** (where two routes differ intentionally — such as per-route capability checks, RBAC actor whitelists, read-only vs mutating paths — keep differences explicit rather than forcing a monolithic "God endpoint").

## 3. Convergence Registry

### Ticket A1: Phone Slice Adapter & HTTP Handler Convergence (`roundA1`, Completed)
- **Exclusive write set**: `components/phone/service.py`, `components/phone/server.py`, `tests/test_v08_slice_adapter_convergence.py`
- **Duplicated implementations replaced**:
  - `PhoneService` 13 homologous `v02`/`v03`/`v04`/`v08` GET/POST slice wrapper pairs (`v03_clock`, `v03_ecology`, `v03_embodied_social`, `v04_neighborhood`, `v04_builder_trylive`, `v04_elastic_play`, `v02_agency_depth`, `v02_crossmodal`, `v02_runtime_migration`, `v08_living_traces`, `v08_composable_body`, `v03_encounters`, `v08_emergent_echoes`).
  - `PhoneService.body_health()` bespoke `runtime.json` parsing & fatigue calculation.
  - `PhoneHandler` duplicate JSON body readers (`_read_v03_json_body`, `_read_v02_crossmodal_json_body`) and 16 `_get_v0*` + 18 `_post_v0*` handlers.
- **Canonical entry points**:
  - `PhoneService._read_slice_view(...)` and `PhoneService._mutate_slice_view(...)`
  - `PhoneService.body_health()` delegating through `body_health_projection.body_health_view` + `_read_runtime_state` + `_somatic_fatigue`
  - `PhoneHandler._read_slice_json_body(...)`, `_serve_slice_get(...)`, `_serve_slice_post(...)` (with legacy JSON readers downgraded to thin aliases; `_post_adult_intimacy` preserved explicitly for role semantics)
- **Preserved domain differences**: per-route `allow_identity_fields`, `max_bytes`, `require_non_empty`, `desk`, `deliver` (`result`/`v03_result`/`tuple`), per-slice RBAC (`subject_view` vs `"partner"`), and HTTP URLs.
- **Copy-paste defect repaired**: `v03_clock`, `v03_ecology`, and `v03_embodied_social` error tuples (`403`/`409`/`reason_code`) are no longer flattened into `400`.

### Ticket A2: Room/Zone & Body Sensation Authority Alignment (`roundA2`, Completed)
- **Exclusive write set**: `components/world/scripts/v08_microzone_ecology.py`, `components/world/scripts/v08_composable_body.py`, `tests/test_v08_room_body_authority_alignment.py`
- **Duplicated/split-brain state replaced**:
  - `v08_microzone_ecology.py` hardcoded `window_state="closed"` and `posture_cn="正常坐着"` drifting from `phone/somatic_comfort.json` and World `pose` in `data/world/body_substrate.json`.
  - `v08_composable_body.py` unguarded sensation/hint strings lacking shared qualitative raw-free checks.
- **Canonical authorities**:
  - `phone/somatic_comfort.json` (`window.state`, `window.updated_at`, `local_position.zone`) + `data/world/body_substrate.json` (`pose`) synced zero-write on read (`microzone` local wins unless `window.updated_at` is strictly newer), without touching `created_at`/`updated_at`/TTL clocks.
  - `components/body/scripts/body_observation.py` (`_assert_raw_free`) gating `v08_composable_body` qualitative descriptors while preserving pack off-by-default/wake-only boundaries and `created_at`/`expires_at` decay clocks.

### Tickets B0/B1/B2 & C1/C2/C3: Boundary Hardening & Compositional Elasticity (Completed)
- **B0 (`roundB0`, Completed)**: Shared `components/life/scripts/state_tx_guard.py` (`state_file_lock` `RLock` + `flock`/`msvcrt` on `<state>.lock`, monotonic `op_seq` submission-order receipt trimming + bounded `committed_op_ring` / `RETRY_WINDOW_EXPIRED`).
- **B1 (`roundB1`, Completed)**: Exclusive `PhoneService` `SimpleNamespace` unbound method compatibility + `operations.jsonl` journaling + pure-read `slice_operation`; `state_tx_guard` locking & `op_seq` eviction across `v04_elastic_play_learning`, `v08_emergent_echoes`, `v04_neighborhood_living`, `v08_open_affordance`; 5-point `verify_completion_receipt_readonly`; `mark_started` preserves `executed_in_world=False`; `confirm_shared_idiom` blocks unilateral self-confirmation.
- **B2a/b/c (`roundB2a`/`roundB2b`/`roundB2c`, Completed)**: `state_tx_guard` + `op_seq` in `v08_universal_matter`, pre-miss Two-Worlds masking (`subject_last_seen_snapshot`, `subject_view_revision`, `OBJECT_NOT_AT_EXPECTED_SPOT` non-leaking refusal, zero-oracle `preview_placement_zero_write`) across `v08_universal_matter`, `v08_living_traces_view`, `stance_containment_view`; `lived_context_bridge` disk-fallback TTL & committed-clock `lingering_threads` anti-resurrection + `confirmed`-only consensus gate.
- **C1 (`roundC1`, Completed)**: Carrier-bound `placement_relation`, `posture_tag`, `anchor_page` with explicit follow/invalidation laws; live-projected `ARRANGE_OBJECTS`/`active_arrangements`; non-escalating `NOTE_ASSOCIATION`/`subject_association` in `v08_universal_matter` & `v08_living_traces_view`.
- **C2a/b (`roundC2a`/`roundC2b`, Completed)**: Quiet-tick puppet quota removed; deterministic 4-way soft landing + 4h cooldown + `notes/unsupported_intents.jsonl` (`v02_v03_daily_loop_bridge`, `phone_tool` 37 surfaces kept); unmapped bridge AD fallthrough blocked + refused foreground `ACTIVE` un-wedged (`autonomous_life_cycle`); false `process_mistake` regret stopped (`v02_agency_depth_engine`); single-instance runtime E2E verified (`llm_calls == 0`).
- **C3 (`roundC3`, Completed)**: Generic container/carrier recursive nesting (`parent_id`), Chinese posture/arrangement/association mapping, and privacy scrubbing in `D:\个人\nyairo-desktop\{v08_living_traces.go,web\index.html,v08_matter_stage_test.go}`.

