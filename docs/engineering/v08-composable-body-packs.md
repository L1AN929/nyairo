# v0.8 Composable Optional Body Packs (v0.4 Ch.51)

## Problem

The body layer needs two *optional* physiological processes — a menstrual
cycle and nocturnal emission — without turning them into mandatory daily
check-ins, personality traits, or adult-intimacy side effects. Existing body
surfaces assume a single continuous state and some character packs bind
pronouns/names directly into copy, which makes optional packs either coercive
or identity-fragile.

## Decision

Add a deterministic, zero-LLM engine (`components/world/scripts/v08_composable_body.py`)
and an HTTP view (`components/phone/v08_composable_body_view.py`) mounted at
`/api/phone/v08/composable-body` (GET + POST).

- **Default OFF and decoupled.** Both `menstrual_cycle` and `nocturnal_emission`
  start `enabled = False`. Every projection and the persisted state carry
  `pronoun_decoupled: True` and `independent_of_adult_intimacy: True`. Pronouns
  are resolved dynamically from `phone/character_pack_state.json` with a
  neutral `"TA"` fallback; no `她`/`千代` literal exists in the engine.
- **Menstrual pack.** Closed clock model: enabling cleanly requires a fresh
  `anchor_iso` (never back-fills past cycles or messages), plus
  `cycle_length_days` (21–40), `active_days` (2–8), `discomfort_profile`, and an
  explicit `pre_symptom_enabled` (default false). The projection derives
  `days_since_anchor`, `cycle_index`, `day_in_cycle`, active/pre windows,
  `needs_supply_change`, and a plain neutral `current_feeling_cn` only inside
  the active window. Only `use_cycle_supply` clears the supply flag; reading or
  talking never consumes supplies.
- **Nocturnal pack.** `probability_permille` (0–500). Binding a real sleep
  activity samples exactly once from `sha256(instance_salt|sleep_activity_id)`
  into a hidden ledger; re-binding never re-rolls. The hidden scheduled moment
  and the unrealised sample are omitted from every read until the subject
  settles a wake at or after it. An early wake cancels cleanly; a settled wake
  records at most one event and raises `pending_garment_care`, resolved by
  `resolve_garment_care` with a factual receipt. No periodic schedule, no
  absence penalty, no generated dream content.
- **Disable vs delete.** `enabled=False` stops future sensations/events and
  clears derived feeling/supply flags but preserves historical receipts
  (`history_deleted: False`). Deleting history is a separate, explicitly
  confirmed `clear_private_body_history`.
- **Voluntary sharing.** `share_body_care_hint_once` stores one expiring hint.
  The partner projection exposes only an unexpired `shared_care_hint` and the
  most recent 12 ordinary care rows; it never carries packs, enabled flags,
  feelings, supply state, sleep activities, or garment care.
- **Boundaries.** Subject actions return `403` on the partner door; identity and
  score keys (`uid`, `user_id`, `owner_uid`, `actor_side`,
  `has_subject_capability`, `subject_view`, `capability`, `intimacy_score`,
  `affection_score`, `favorability`) are refused with
  `CLIENT_IDENTITY_REFUSED`. Reads are 100% zero-write; state lives at
  `<root>/body/v08_composable_body_pack.json` under
  `body.v08.composable_pack.v1`.

## Alternatives considered

- **One always-on body simulation with flags.** Strongest case: a single state
  machine avoids duplicated projections. Rejected because it makes the packs
  mandatory and confuses "not happening" with "disabled", violating the
  default-OFF requirement.
- **Probability sampled per night on a schedule.** Strongest case: no client
  binding needed. Rejected because it invents events without a real sleep
  activity, adds an absence penalty, and breaks deterministic replay.
- **Expose the sampled outcome immediately.** Strongest case: simpler state
  reads. Rejected because it leaks future unrealised events; the ledger must
  stay hidden until the wake actually reaches the scheduled moment.
- **Bind pronoun/name into generated copy.** Strongest case: warmer prose.
  Rejected because it couples optional physiology to a fixed identity and
  violates pronoun decoupling.

## Consequences

The packs are inert until explicitly configured, replay-safe, and auditable
without any model calls. Historical receipts survive disable, so a later
re-enable does not pretend the past never happened. Partner surfaces stay
minimal by construction. The trade-off is a persisted random instance salt and
a hidden ledger: sampling is deterministic per instance/activity but not
externally predictable, and history deletion also drops replay receipts so a
stale `operation_key` cannot resurrect deleted rows.
