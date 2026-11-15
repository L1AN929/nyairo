# 情绪脾气与日常张力接口契约（`phone.emotional.tension.v1`）

## 1. 它是什么 / 它不是什么

### 它是什么
`components/phone/emotional_tension_view.py` 与 `/api/phone/emotion/tension`（`GET` / `POST`）落地 `v3.0` 第 39、40、42、43、44 篇五大日常自主与情绪张力机制：

1. **她自己的日程与该做的事（`schedule`，SC01–SC19）**：固定事项（`FIXED`）与每周确定性外部邀约（`OFFERED`）。伴侣默认视角仅暴露 `partner_status`（`FREE` / `OUT`）与忙闲摘要，绝不泄露私有备忘；去不去、接不接仅由主体决定，跳过不记亏欠不扣分，接单转截止日计入 48h 压力。
2. **吵架、摩擦与行为修复（`quarrel`，QR01–QR26）**：小摩擦随时间自然淡去；破裂（`OPEN` / `IN_REPAIR` / `RESOLVED` / `LET_GO`）仅主体可开启，修复**只认实际改正行为**（`record_behavior_change`），口头道歉、送礼、转账或求和好亲密（`attempt_apology_or_gift_repair`）诚实返回 `APOLOGY_OR_GIFT_NOT_REPAIR` 且绝不解开破裂；未回消息/晚上线不算事故；要空间必须说出口（禁止冷暴力默不作声），每起事件最多一次且睡醒自动解除；未解关系域破裂或空间期自动阻断主体发起亲密。
3. **想念留话（`missing_partner`，MH01–MH18）**：主体留日常小事（最多 3 件、72 小时自然淡去、来源失效即放下、私密内容禁入）；伴侣刚到场且不忙时最多浮现 1 件，伴侣主动问近况才展开其余；**绝不统计离开时长或累加思念权重**。
4. **没来由的坏心情（`low_mood`，LM01–LM18）**：按 `(instance_seed, iso_year, iso_week)` 的 `SHA-256` 纯确定性派生，每周至多一段、白天 2~6 小时、当天自行散去；照常回话且每段最多说一次「有点闷，没什么事，不是你的原因，过一会儿就好」；伴侣哄/送礼/道歉绝不提前结束；闷时不主动发起亲密；过后不留历史痕迹。
5. **开口请帮小忙（`asking_partner`，AH01–AH18）**：封闭四类（`look_once` / `help_briefly` / `sit_awhile` / `help_pick`），必须绑定真实物件引用 `target_object_ref`，严禁涉钱、涉性、替主体做决定；一次最多挂 1 件、2 小时未回自然淡去（不算拒绝）；伴侣婉拒无需理由、零扣分、同类睡醒前不再问；帮了也不记人情债。

### 它不是什么
- **零外部大模型调用、零 `random` 随机数、零后台线程**。
- **`GET` 100% 零写入**：`<root>/phone/emotional_tension.json` 不存在时直接返回确定性默认投影（`revision = 0`），绝不落盘建文件。
- **开源角色中性**：动态读取 `sensory_character_view` 当前角色包的名称与人称代词（默认 `"TA"`），不绑死特定角色名。

## 2. 路由与鉴权边界

- `GET /api/phone/emotion/tension`
  - 仅接受无查询参数纯读；带有效 `X-Phone-Capability` 返回 `view="subject"`，不带返回 `view="partner"`（自动剥离私有日程备忘、未浮现留话与低落内部窗口），带错凭据直接 `403`。
- `POST /api/phone/emotion/tension`
  - 必须携带有效 `X-CSRF-Token`；所有 `SUBJECT_ONLY_ACTIONS`（如开启破裂、说需要空间、记日常留话、开口请帮小忙、日程拍板等）必须携带有效主体 `capability`，否则返回 `403`。
  - 伴侣动作（如 `partner_arrive`、`partner_ask_whats_new`、`attempt_apology_or_gift_repair`、`comfort_low_mood`、`respond_favor`）仅需 `CSRF`，但绝不允许伴侣自证行为修复（`PARTNER_CANNOT_SELF_CERTIFY_REPAIR` -> `403`）。
  - 未知字段、空外键或外域积分字段一律返回 `400`；版本冲突、重复事件计数、施压筹码或越界话题一律返回 `409`。
