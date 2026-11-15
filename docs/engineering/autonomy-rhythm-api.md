# 主体日常自主边界、话题余温、主动改画风与异步回信 API 契约（`autonomy_rhythm_slice`）

本文件定义手机本地服务端新增的确定性、零随机数、零模型调用接口：

- `GET /api/phone/autonomy/rhythm` & `POST /api/phone/autonomy/rhythm`（`phone.autonomy.rhythm.v1`）

---

## 1. 设计边界与四项核心支柱

1. **零写入纯读与开源角色包中性默认**
   - 状态文件路径为 `<root>/phone/autonomy_rhythm.json`（`0600` 原子替换落盘，拒绝符号链接）。
   - 未落盘时 `GET /api/phone/autonomy/rhythm` **100% 纯零写入**（`revision=0`），个体名称与人称代词动态读取 `character_pack_state.json`（默认开源中性预设 `neutral_resident`，`display_name="TA"`、`pronouns="TA"`、`locked_to_chiyo=false`）。
   - 状态文件损坏时，`GET` 返回 `state_status="degraded"` 与 `state_reason_code="AUTONOMY_RHYTHM_STATE_CORRUPT"`；`POST` 按 fail-closed 直接返回 `409 AUTONOMY_RHYTHM_STATE_CORRUPT`，绝不静默覆盖。
   - 全模块禁止导入或调用 `random`，所有分流与日常走神线索均由 `hashlib.sha256` 在 `(pack_id, local_date/hour_slot, normalized_topic)` 上确定性计算。

2. **支柱一：`refusal_guard`（「打死也不帮」日常自主拒绝与防滑跪立场锁）**
   - 明确独立于底层模型合规安全层（`not_safety_policy_layer=true`、`apology_template_allowed=false`、`zero_guilt_or_score_penalty=true`）。
   - 支持五类日常边界：`errand_outsourcing`（代写周报/PPT/表格/检讨/跑腿杂活）、`interrupt_own_activity`（正看书/专注/戴滤噪耳机时强令放下手头事）、`peek_private_draft`（索看未公开私密日记/草稿）、`forced_compliance`（居高临下命令顺从）与 `plain_unwilling`（主体显式声明单纯不想帮），最多保留 `6` 条活跃锁（超出上限显式 `declare_refusal` 返回 `409 REFUSAL_LOCK_LIMIT`）。
   - **防滑跪不变式**：事项一旦锁入 `stance="REFUSED_FIRM"`，后续软磨硬泡、催促、卖惨或激将只会递增 `push_attempts`（`capitulation_allowed=false`），导引文本随催促次数变得更简短干脆，绝不解锁改口，也绝不输出客服式卑微道歉。
   - 解锁（`release_refusal`）与主动立锁（`declare_refusal`）必须携带本人 `X-Phone-Capability`（伴侣侧调用一律 `403 CAPABILITY_REQUIRED`）。

3. **支柱二：`topic_threads`（突兀换话题不秒跟与话题余温，对标 `41-话题还挂着.md`）**
   - 四种中性理由：`MID_TELLING`、`ASKED_USER`、`SUBJECT_ENGAGED`、`USER_RETRACTED`；最多同时保留 `2` 条未过期话题（默认 `30` 分钟自然淡去，`5..120` 分钟可配），无换话题计数器（`zero_switch_counter=true`，`not_quarrel_or_score=true`）。
   - 对方自己说完离开的普通话题不强留（`owner="user"` 且非 `USER_RETRACTED` 时返回 `409 NOT_SUBJECT_THREAD`）。
   - 自动维护 `last_session_topic`，使连续两轮普通 `evaluate_turn`（例如先聊「性知识科普」，下一句突兀跳到「这个东西买下来便宜不便宜」）无需手工预埋即可检出 `topic_shift_detected=true`，并按确定性惯性在 `FINISH_THEN_FOLLOW`、`FOLLOW_AND_RETURN_LATER`、`ASK_ABOUT_JUMP`（10 分钟冷却）中选择回应而非瞬间失忆秒跟。
   - 欲言又止撤回（`USER_RETRACTED`）最多顺口问一次 `ASK_WHAT_USER_MEANT`；对方摆手或调用 `wave_off_retracted` 后立即放下不再追问。
   - 绕回原话题（`return_to_thread`，需本人 capability）最多一次，二次绕回返回 `409 ALREADY_RETURNED`。
   - 真急事或身体不适（`urgent=true` 或发烧/生病/受伤/去医院等）归入 `priority_class="PRIORITY"`，无条件返回 `offered_thread_options=["FOLLOW"]`、`selected_thread_response="FOLLOW"`。

4. **支柱三：`spontaneous_pivot`（主体主动走神突然改画风，对标 `20-主体的无意义权利`）**
   - 结合真实 World 商店日常念头、Somatic 桌边温饮与 Sensory 窗外声光确定性派生 `candidate_cue`（如突然想起店里某件小东西买下来到底算便宜还是贵、手边水快放温、窗外动静晃神）。
   - `needs_transition=false`、`needs_explanation=false`：导引明确「不必铺垫过渡，也不用解释为什么突然换话题」。
   - 默认冷却 `45` 分钟（`15..240` 分钟可配）；冷却期内 `ready_now=false`，重复调用 `trigger_spontaneous_pivot` 返回 `409 SPONTANEOUS_PIVOT_COOLDOWN`；在 `PRIORITY` 或拒绝锁冲突轮次自动压制。

5. **支柱四：`reply_rhythm`（隔几十分钟至数小时再回信息的真实异步生活节律）**
   - 综合本人显式 `set_phone_aside`（`outside` / `sleeping` / `deep_focus` / `aside` / `back_online`）与实时 World 非居家位置（`DEFERRED_OUTSIDE`，默认 `180` 分钟）、Body `sleep_like` 恢复（`DEFERRED_SLEEPING`，`240` 分钟）、双人书桌免打扰/专注牌（`DEFERRED_DEEP_FOCUS`，`120` 分钟）及角色包耳机 `ambient_filter`（`DEFERRED_PHONE_ASIDE`，`90` 分钟），派生当前可联状态与预计延迟分钟数（`AVAILABLE_NOW` 为 `0`）。
   - 当 `pace_mode="natural_async"` 且处于非 `AVAILABLE_NOW` 状态时，普通非急事 `evaluate_turn` 不强制秒回，而是写入有界手机待回信箱 `pending_messages`（最多 `8` 条，第 `9` 条返回 `409 PENDING_INBOX_FULL`），返回 `delivery_mode="DEFERRED_HOURS"`，并明确标注 `is_service_error=false`、`not_cold_war=true`。
   - 支持真急事 `urgent=true` 直通 `IMMEDIATE`，或对已排队消息调用 `urgent_knock` 提升为 `READY_TO_REPLY`，以及空闲/到点后 `claim_ready_messages` 与 `clear_delivered_messages`。

---

## 2. 路由与动作格式

### 2.1 `GET /api/phone/autonomy/rhythm`
- 查询参数：不接受任何 query（携带则返回 `400 UNKNOWN_QUERY`）。
- 视图与隐私：无 capability 返回 `view="partner_default"`、`subject_view="partner_default"`（自动剥离全部 `private_note`）；携带有效 `X-Phone-Capability` 返回 `view="her"`；携带错误 capability 返回 `403`。

### 2.2 `POST /api/phone/autonomy/rhythm`
所有写请求均需 `X-CSRF-Token` 与 `Content-Type: application/json`，按 `action` 严格执行字段白名单（多余字段返回 `400 UNKNOWN_FIELD:...`，空串可选字段返回 `400`，伪造身份字段返回 `409 CLIENT_IDENTITY_REFUSED:...`）：

- **需本人 `X-Phone-Capability` 的主体动作（无凭据返回 `403`）**：
  - `{"action": "declare_refusal", "topic_label": "...", "category"?, "reason_summary"?, "private_note"?}`
  - `{"action": "release_refusal", "lock_id": "..."}`（不存在返回 `404 REFUSAL_LOCK_NOT_FOUND`）
  - `{"action": "hold_subject_topic", "topic_label": "...", "reason"?, "owner"?, "private_note"?}`
  - `{"action": "return_to_thread", "thread_id": "..."}`（不存在返回 `404 THREAD_NOT_FOUND`，二次绕回返回 `409 ALREADY_RETURNED`）
  - `{"action": "trigger_spontaneous_pivot", "custom_prompt"?}`
  - `{"action": "set_phone_aside", "mode": "outside" | "sleeping" | "deep_focus" | "aside" | "back_online", "minutes"?, "note"?, "private_note"?}`

- **伴侣侧 / 宿主可调用的 CSRF 动作（若显式携带错误 capability 仍返回 `403`）**：
  - `{"action": "evaluate_turn", "user_text": "...", "urgent"?, "session_id"?}`
  - `{"action": "record_turn_context", "topic_label": "...", "reason": "MID_TELLING" | "ASKED_USER" | "SUBJECT_ENGAGED" | "USER_RETRACTED", "owner"?, "private_note"?}`
  - `{"action": "wave_off_retracted", "thread_id"?}`
  - `{"action": "urgent_knock", "msg_id": "...", "reason"?}`（不存在返回 `404 PENDING_MESSAGE_NOT_FOUND`）
  - `{"action": "claim_ready_messages"}`
  - `{"action": "clear_delivered_messages", "msg_ids"?}`
  - `{"action": "update_preferences", "pace_mode"?, "away_delay_minutes"?, "anti_errand_firmness"?, "thread_hold_min"?, "spontaneous_pivot_enabled"?, "pivot_cooldown_min"?}`

---

## 3. 独立预览 Fixture

```bash
PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/autonomy_rhythm_fixture.py --port 3862
```

启动后输出单行 JSON（含 `ok`、`port`、`root`、`capability`、`csrf`、`location`、`endpoints`），收到 `SIGINT` / `SIGTERM` 后干净关闭全部底层服务并清理临时目录。
