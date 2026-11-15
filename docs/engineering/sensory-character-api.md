# 开源角色包与多通道感官场 API 契约（`sensory_character_slice`）

本文件定义手机本地服务端新增的两组确定性、零模型调用接口：

- `GET /api/phone/character/pack` & `POST /api/phone/character/pack`（`phone.character.pack.v1`）
- `GET /api/phone/body/sensory` & `POST /api/phone/body/sensory`（`phone.body.sensory_field.v1`）

---

## 1. 设计边界与硬约束

1. **开源中性默认个体（不绑定单一预设角色）**
   - 当 `<root>/phone/character_pack_state.json` 尚未落盘时，`GET /api/phone/character/pack` **严格零写入**，直接投影内建中性预设 `"neutral_resident"`（`display_name="TA"`，`pronouns="TA"`，`source="default_neutral_preset"`，`revision=0`，`locked_to_chiyo=false`）。
   - 提供四套示例预设：`neutral_resident`（默认）、`chiyo`、`haru`、`sola`，并支持导入任意符合 `proposed.character_pack.v1` 合同的自定义角色包。

2. **`38-角色包合同` 防火墙**
   - `kind` 必须为 `"proposed.character_pack.v1"`（否则 `400 PACK_KIND_UNKNOWN`）；`fictional` 必须严格为布尔真 `true`（否则 `400 PACK_NOT_FICTIONAL`）。
   - 递归禁字段防火墙：对全部嵌套 `dict` / `list` 扫描 `DEPLOY_ONLY | USER_ONLY`（如 `adult_confirmed`、`attestation`、`preferred_tier`、`user_genitals`、`limits` 等），命中即返回 `400 PACK_FIELD_NOT_ALLOWED:<path>`，绝不静默丢弃。
   - 成年门禁预览 `adult_gate_preview`：`declared_age < 18`（`DECLARED_MINOR`）、`relation_to_user in ("blood_relative", "guardian")`（`FORBIDDEN_RELATION`）、`obligations` 含 `minor_education`（`MINOR_INSTITUTION`）时 `verdict="FAIL"`；其余合法成年角色包一律返回 `verdict="REQUIRES_DEPLOY_ATTESTATION"`（`pack_never_opens_gate=true`）。`relation_to_user="partner"` 且称呼含 `"哥哥"` 合法允许。
   - `sexual_seed`：含 `POWER_EXCHANGE` 一律拒绝（`400 PACK_FIELD_INVALID:sexual_seed.stances`）；无论包内声明什么 `seed_status`，有效状态始终强制为 `"DRAFT"`（`pack_never_confirms_seed=true`）。
   - 规范化 `pack_ref`：`[pack_id, version, sha256_hex[:16]]`，基于排序紧凑 JSON 计算，任意键顺序重排结果一致。

3. **`51-可组合身体` 与视图隐私**
   - 切换角色包、修改名字或人称代词**绝不**自动开启任何生理体验。
   - `optional_processes`（允许值：`menstruation_lite`、`nocturnal_emission_lite`）默认 `[]`，仅能通过携带本人 `X-Phone-Capability` 的 `set_optional_physiology` 修改（无凭据返回 `403`），且仅在 `view == "her"` 下的 `body_summary.optional_processes` 暴露。
   - `partner_default` 视图**绝不**返回原始 `sexual_seed`（`stances` / `orientation` / `solo_inclination`）、`optional_processes` 或配饰 `private_note`。
   - 日常配饰 `everyday_accessories`（`0..2` 件）：仅 `kind == "headphones"` 允许 `mode == "ambient_filter"`，其余配饰使用该模式返回 `400 ACCESSORY_MODE_INVALID`。

4. **五通道感官场与文字声景卡**
   - `GET /api/phone/body/sensory` **100% 纯零写入**，只读 `PhoneWorld.snapshot()`、`world_ambient_events`（当前地点与邻接可达地点）、`somatic_comfort.json`、`character_pack_state.json` 与 `sensory_cards.json`。
   - 五通道：
     - `auditory`：结合窗户开合（`open` -> `none`/`clear`、`ajar` -> `partial_window`/`softened`、`closed` -> `closed_window_not_soundproof`/`muffled`）与耳机 `ambient_filter`（`headphones_ambient_filter`）；邻接地点活跃声源投影到 `adjacent_unseen_sounds`（`visual_access=false`，`identity_certainty="sound_only_unverified"`，`clarity="distant_or_doorway"`）；`closed_door_or_window_is_absolute_soundproof=false`。
     - `visual`：结合 `room_light`、权威世界本地小时、`local_zone`、`window_state` 与光环境事件，输出 `lighting_band` 与 `reading_suitability`（如白昼 `11..16` 时 `home` + `window_side` + `open` 且无雨为 `window_glare` / `glare_near_window`；夜间未开灯为 `unlit_indoor` / `too_dim`；点亮 `warm_floor_lamp` / `bedside_lamp` 为 `warm_reading_light` / `dim_lamp_pool`）；`visible_is_not_reachable=true`。
     - `olfactory`：受 `scent_note_enabled` 控制（关闭时 `status="disabled_by_preference"`，`cues=[]`）；仅由真实条件触发 `tea_steam`（热茶在 `warm`/`tepid` 阶段，30 分钟冷却为 `room_temp` 后自动消失）、`rain_breeze`、`indoor_damp_cloth`、`ventilating_draft`；`creates_food_inventory=false`。
     - `tactile_thermal`：透传 `somatic_comfort_view` 四模态（`thermal` / `moisture` / `posture` / `tactile`）的 `{quality, salience, region}`。
     - `sensory_load`：综合身体信号、角色噪声敏感度、光线偏好与关窗潮衣闷气，给出 `load_band` 与具体物理微调建议 `suggested_adjustments`；`maps_to_emotion_or_pathology=false`。
   - 文字声景卡 `sound_cards`（最多 12 张，`medium="text_sound_card"`，`has_audio_recording=false`）：写操作必须携带本人 capability（否则 `403`）；支持 `operation_key` 幂等重放（重复提交返回 `status="already_applied"`）；`partner_default` 仅返回 `shared == true` 的卡片，**不暴露任何未公开张数或总张数**。
   - 伴侣感官小贴士 `partner_sensory_notes`（最多 3 条，`1..140` 字）：仅需 CSRF（若显式携带错误 capability 仍按 fail-closed 返回 `403`）。

---

## 2. 路由与请求格式

### 2.1 `GET /api/phone/character/pack`
- 查询参数：不接受任何 query（携带则返回 `400 UNKNOWN_QUERY`）。
- 请求头：无 capability 返回 `view="partner_default"`；有效 `X-Phone-Capability` 返回 `view="her"`；错误 capability 返回 `403`。

### 2.2 `POST /api/phone/character/pack`
所有写请求均需 `X-CSRF-Token` 与 `Content-Type: application/json`，按 `action` 严格校验字段白名单：
- `{"action": "select_preset", "preset_id": "neutral_resident" | "chiyo" | "haru" | "sola"}`（未知预设返回 `400 PRESET_UNKNOWN`）
- `{"action": "apply_custom_pack", "pack": {...}}`
- `{"action": "update_identity", "display_name"?, "pronouns"?, "subject_calls_user"?, "user_calls_subject"?, "sensory_preferences"?, "everyday_accessories"?}`（除 `action` 外至少提供一项，否则 `400 IDENTITY_UPDATE_EMPTY`）
- `{"action": "set_optional_physiology", "optional_processes": [...]}`（**必须携带本人 `X-Phone-Capability`**，否则 `403`）

### 2.3 `GET /api/phone/body/sensory`
- 查询参数：不接受任何 query（携带则返回 `400 UNKNOWN_QUERY`）。
- 纯读零写入，返回五路 `channels`、`sound_cards` 与 `partner_sensory_notes`。

### 2.4 `POST /api/phone/body/sensory`
- 本人声景卡动作（**必须携带 CSRF + `X-Phone-Capability`**，否则 `403`）：
  - `{"action": "capture_sound_card", "title"?, "impression_note"?, "shared"?, "operation_key"?}`（World 不可读时 `409 WORLD_UNAVAILABLE`；满 12 张时 `409 SOUND_CARD_LIMIT`；无声源时记录 `["quiet_ambient_room"]` 且 `quiet_fallback=true`）
  - `{"action": "set_sound_card_share", "card_id": "...", "shared": true | false}`（不存在返回 `404 SOUND_CARD_NOT_FOUND`）
  - `{"action": "remove_sound_card", "card_id": "..."}`（不存在返回 `404 SOUND_CARD_NOT_FOUND`）
- 伴侣感官小贴士动作（需 CSRF）：
  - `{"action": "leave_sensory_note", "text": "..."}`（满 3 条返回 `409 PARTNER_SENSORY_NOTE_LIMIT`）
  - `{"action": "remove_sensory_note", "note_id": "..."}`（不存在返回 `404 SENSORY_NOTE_NOT_FOUND`）
  - `{"action": "clear_sensory_notes"}`

---

## 3. 独立预览 Fixture

```bash
PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/sensory_character_fixture.py --port 3861
```

启动后输出单行 JSON（含 `port`、`root`、`capability`、`csrf`、`endpoints`），收到 `SIGINT` / `SIGTERM` 后干净关闭全部底层服务。
