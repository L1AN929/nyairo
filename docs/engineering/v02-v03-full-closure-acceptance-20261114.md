# CHIYO v0.2 + v0.3 + 36B Full Closure & Errata Acceptance (2026-11-14)

## 1. Scope Closed

1. **v0.2 (`D:\CHIYO-总体设计-v2.0-final`) Residual & Chapter 19 Closure**:
   - **4-Level Recall Projection (`components/life/scripts/v02_recall_projection.py`, `GET/POST /api/phone/v02/recall-projection`)**:
     - `CLARITY_EXACT`, `CLARITY_GIST`, `CLARITY_RANGE`, `CLARITY_UNAVAILABLE`;
     - Natural decay vs explicit `mark_active_fade` vs governance `tombstone_memory` (never resurrects).
   - **Common Ground & Epistemic Boundary (`components/life/scripts/v02_common_ground.py`, `GET/POST /api/phone/v02/common-ground`)**:
     - `PRIVATE_UNSHARED`, `SHARED_WITH_PARTNER`, `PENDING_CLARIFICATION`;
     - Communication receipt progression (`SENT` -> `DELIVERED` -> `READ` -> `ACKNOWLEDGED` -> `UNDERSTOOD` -> `AGREED`);
     - Unseen partner object move (`EVENT_UNSEEN_MOVE`) -> honest miss at expected spot -> nearby search or partner clarification (zero omniscient state peeking).
   - **Chapter 19 P0 Hardening**:
     - `DORMANT_CONSERVATION` vs `LOW_BUDGET_CONSERVATION` runtime separation;
     - `agency_cognition_worker.py` `MAX_COGNITION_QUEUE_DEPTH = 32` bounded backpressure;
     - `life_co_presence.py` `MAX_INVITATION_SECONDS = 7200` auto-expiry;
     - Crossmodal palette live album source re-verification on read;
     - `wardrobe_pocket_view.py` multi-delivery `revisitable_works` projection.

2. **v0.3 (`D:\CHIYO-总体设计-v3.0`, Ch. 01–44 + `36A` + `36B-1..11` + `ERR-01..13` & `GAP-01..12`)**:
   - **36B Adult Intimacy & X01–X30 Fault Matrix (`components/phone/adult_intimacy_view.py`, `components/phone/validator_lexicon.v1.json`)**:
     - External lexicon validator (`validator_lexicon.v1.json`), route drift hard downgrade, paired remote session hard block (`GAP-07`), open emotional rupture gate (`SUBJECT_EMOTIONALLY_UNAVAILABLE`), local U1/U2/U3 explicit controls, `solo` zero-leak isolation, and `85/85` fault-matrix cases (`tests/test_adult_intimacy_fault_matrix.py`).
   - **Ch. 38–44 Character Pack, Sensory, Emotional Tension & Autonomy Errata (`sensory_character_view.py`, `emotional_tension_view.py`, `autonomy_rhythm_view.py`)**:
     - Clean `.pack.json` `export_pack` (100% zero-write), `import_pack_json`, and deployer `attest_character_pack` bound to `pack_sha256`;
     - `thought_of_you` partner thought notes, non-capitulating `REFUSED_FIRM` stance lock, `hold_subject_topic` & `clear_session_threads` (`tests/test_v03_ch38_44_errata_parity.py`, `82/82` cases).
   - **Economy Split Wallet, Dynamic Illness Recovery & Outbound Provenance (`v03_economy_illness_bridge.py`, `v03_ecology_view.py`)**:
     - `earned_wallet` vs `stipend_wallet` split + `withdraw_stipend` (`subject` never sees unwithdrawn stipend balance);
     - Dynamic illness recovery (`ERR-05`), outbound `SENT_PENDING_ACK != READ` (`ERR-04`), external fact provenance (`ERR-07`), diagnostic export redaction (`GAP-03`), and zero-write in-transit slow letters (`tests/test_v03_economy_illness_reality_bridge.py` `32/32` + `tests/test_v03_ecology_gap_errata.py` `7/7`).
   - **Embodied Kitchen Two-Hand Interlock, Dwell & Everyday Desire Lifecycle (`v03_embodied_npc_ecology.py`, `life_dwell.py`, `everyday_desire_lifecycle.py`)**:
     - Two-hand slot occupancy interlock, `start_dwell` bounded blank windows (`growth_score_delta == 0`, `forced_memory_write == False`), and 6-domain `emerge_concern` turn-only vs retained lifecycle (`tests/test_v03_embodied_cognitive_spatial.py` `9/9`).
   - **Hermes Tooling, Lived Context Projection & Native Desktop UI (`phone_tool.py`, `lived_context_bridge.py`, `D:\个人\nyairo-desktop`)**:
     - `33/33` `phone_life_update` subject surfaces + zero-write `build_lived_turn_context` (`<= 1800` chars);
     - Native Kazumi desktop category filter bar (`#herCategoryFilterBar`), autonomy quick bar (`#herAutonomyQuickBar`), `.pack.json` import/export, U1/U2/U3 + GAP-07 controls, split-wallet stipend controls, and zero external `:3831` web bounce-outs.

## 2. Hard Invariants Preserved

- Frozen & sealed SHA-256 anchors byte-identical:
  - `components/life/scripts/autonomous_life_assembly.py`: `76b4c7cbcdc29154851fedcd2b962f93549092c35f8feeedec29bfc93169fa5a`
  - `components/life/scripts/life_sharing_service.py`: `0becf7df4d9b34881817ff6f1defdcc15929eacbd6183d4c8a38c5a048d46038`
  - `components/native/native_recall/readonly_recall_adapter.py`: `6667ff66fe8105f28f436609f0cd8d744b185c0580e7f1642aafef0b873af78a`
- Zero paid external model calls (`llm_calls == 0`, quota preserved at `573/1000` used, `427` remaining).
- Design directories (`D:\CHIYO-总体设计-v2.0-final`, `D:\CHIYO-总体设计-v3.0`, `D:\CHIYO-总体设计-v0.4`) 100% untouched.
