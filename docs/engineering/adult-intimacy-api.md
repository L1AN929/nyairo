# `phone.adult.intimacy.v1` — 欲望自然消退与 36B 成年亲密完整闭环后端接口规范

## 1. 模块定位与硬边界

- **实现文件**：`components/phone/adult_intimacy_view.py`
- **持久化路径**：`<root>/phone/adult_intimacy.json`（原子临时文件 + `fsync` + `0600` 权限落盘，拒绝符号链接）
- **Schema**：`phone.adult.intimacy.v1`
- **对标设计原稿**：
  - `35-欲望形成冲突与消退.md`（慢变量形成、身体/心情约束与无债自然消退）
  - `36B-成年亲密与性生活-完整设计与实现规格.md` & `ADR-047-成年亲密与性生活的控制链.md`
  - `36B-1` 成年性格与偏好种子、`36B-2` 欲望唤起与身体模型、`36B-3` 衣物床品浴室与私密空间、`36B-4` 场景运行时与同意引擎、`36B-5` 呈现写法与模型路由、`36B-7` 记忆关系与长期演化、`36B-9` 身体档案与男女差异、`36B-10` 独处的欲望与自慰、`36B-11` 不顺利的时候与谈论性
- **核心架构约束**：
  1. **纯标准库确定性闭环**：零大模型调用、零网络请求、零 `random` 随机数（统一使用 `hashlib.sha256` 确定性抽样与幂等指纹）。
  2. **GET 100% 零写入**：`<root>/phone/adult_intimacy.json` 不存在时，`read_adult_intimacy(...)` 不创建任何目录或文件，直接返回 `revision: 0`、成年门默认关闭（`adult_confirmed: False`、`gate_open: False`）的只读投影，普通日常陪伴完全独立不受影响（`ordinary_companionship_independent: True`）。
  3. **开源去角色死绑 + 多身体档案**：动态读取 `sensory_character_view` 的当前激活角色包（默认中性个体 `"TA"` / `neutral_resident`，千代 `chiyo`、晴 `haru`、空 `sola` 等仅作可选预设），支持 `female`、`male`（勃起派生、射精落点与墙钟不应期 `refractory_until`）、`neutral` 三种身体档案与伴侣解剖配对矩阵。
  4. **双视角严格脱敏**：
     - `subject_view=False`（`view: "partner_default"`）：严格隐藏 `SOLO` 独处账本（仅保留 `solo_enabled` 布尔开关）、隐藏未确认 `DRAFT` 种子与主体原始立场表、隐藏私密记忆条目与未披露私人感受。
     - `subject_view=True`（`view: "subject"`）：展示主体自身的偏好种子、有效 `SOLO` 分区与私密记忆引用（`TRANSCRIPT` 仍永久排除在模型上下文 `context_refs` 之外）。

---

## 2. Python 导出接口与异常契约

### 2.1 核心函数

```python
def read_adult_intimacy(
    root: Path | str,
    *,
    now: str | datetime | None = None,
    subject_view: bool = False,
) -> dict[str, Any]: ...

def apply_adult_intimacy_action(
    root: Path | str,
    payload: Mapping[str, Any],
    *,
    now: str | datetime | None = None,
    subject_view: bool = False,
) -> dict[str, Any]: ...
```

### 2.2 结构化异常类（HTTP 状态码对齐）

| 异常类名 | `status_code` | 典型触发原因（`reason_code`） |
| --- | :---: | --- |
| `AdultIntimacyValidationError` | `400` | 未知字段（`UNKNOWN_FIELD:<k>`）、空外籍字段（`EMPTY_FOREIGN_FIELD:<k>`）、伪造身份字段（`FOREIGN_FIELD_FORBIDDEN:<k>`）、非法枚举或生理自相矛盾组合（`BODY_PROFILE_INVALID:*`） |
| `AdultIntimacyPermissionError` | `403` | 角色包自证成年/种子（`PACK_CANNOT_SELF_ATTEST` / `PACK_CANNOT_CONFIRM_SEED`）、未成年阶段（`MINOR_STAGE_PROHIBITED`）、儿童化词表命中（`CHILD_CODING_PROHIBITED`）、血亲/监护关系（`FORBIDDEN_RELATION`）、非自愿/权力交换（`NON_CONSENSUAL_PROHIBITED`）、动物/真人（`BESTIALITY_PROHIBITED` / `REAL_PERSON_PROHIBITED`）、第三人在场（`THIRD_PARTY_PRESENT`）、金钱/礼物解锁同意（`MONEY_OR_GIFT_CANNOT_UNLOCK_CONSENT`）、外部推送通道拦截（`OUTBOUND_CHANNEL_FORBIDDEN`）、伴侣表现打分或越权写 S11（`PREF_TARGET_FORBIDDEN` / `S11_WRITER_NOT_SUBJECT`） |
| `AdultIntimacyNotFoundError` | `404` | 指定 `permit_id`、`garment_id` 或 `memory_id` 不存在 |
| `AdultIntimacyConflictError` | `409` | 失能状态硬拦截（`SUBJECT_UNAVAILABLE`）、解剖不匹配（`ANATOMY_MISMATCH`）、男性插入勃起不足（`ERECTION_INSUFFICIENT`）、不应期（`ORGASM_REFRACTORY`）、假装高潮硬拦截（`ORGASM_FEIGNED`）、同睡眠周期重邀冷却（`REINVITE_COOLDOWN`）、空闲超时（`PERMIT_EXPIRED`）、幂等键冲突（`IDEMPOTENCY_KEY_REUSED`）、衣物外层阻挡（`GARMENT_LAYER_BLOCKED`）、消耗品缺货（`OUT_OF_STOCK:*`）、墓碑记忆禁复活（`MEMORY_TOMBSTONED_CANNOT_RESURRECT`） |

---

## 3. 八大闭环机制与 `action` 清单（共 26 个动作）

### 3.1 成年资格门与安全熔断（`adult_gate`）
- `configure_adult_gate`：部署者显式确认 `adult_confirmed=True`、`life_stage in ("university_student", "working_adult", "independent_adult")`、`attested_by="deployer"`，并配置 `feature_enabled`、`deploy_tier_cap`、`deploy_enabled_categories`（仅 `ANAL`/`TOYS`）、`solo_enabled` 与词表规则。
- `report_safety_boundary`：收到 `user_minor_self_report` / `child_coding_detected` / `non_consensual_detected` / `third_party_intrusion` 时立即中止活跃场景，关闭成人范围并置 `review_required=True`。

### 3.2 多身体档案与欲望慢变量消退（`body_and_desire`）
- `configure_body_profile`：切换 `female` / `male` / `neutral` 身体预设、校验生理覆盖项与伴侣解剖声明 `user_genitals`。
- `update_body_context`：更新清醒/醉酒/睡眠/昏迷/急性生病/疲劳/不适/压力/心情/`desire_pulse` 及 `sleep_completed` 跨夜结算。未行动时唤起（`tau=8min`）与欲望脉冲（`tau=90min`）随时间自然回落，`no_frustration_debt: True`。进入失能状态时立即终止任何开放场景。

### 3.3 偏好种子与双向菜单求交（`preference_menu`）
- `confirm_preference_seed`：部署者将当前 `(pack_id, version)` 种子从 `DRAFT` 确认为 `CONFIRMED`（切换角色包或版本升级时自动退回 `DRAFT`）。
- `revise_subject_preference`：仅限 `subject_view=True` 主体凭私密记忆证据修订自身偏好（收窄无需证据并即时作用于开放许可）。
- `update_partner_limits`：伴侣更新各类目 `YES/MAYBE/NO`、`allow_subject_initiation`、`preferred_tier`、`solo_talk`、`save_moment`、`save_transcript`；收窄为 `NO` 或降档立即同步收窄开放场景。

### 3.4 私密空间、衣物层级、床品与事后清洁（`private_space`）
- `update_private_space`：管理场所（仅室内私密场所且关门、无第三人时 `privacy_ok=True`；活跃场景中隐私丢失立即转入 `PAUSED`）、衣物穿脱层级顺序（`OUTERWEAR -> TOP_OUTER -> TOP_INNER`、`BOTTOM_OUTER -> BOTTOM_INNER`，脱在地上的衣物跨夜保留直至拾起）、消耗品补货与扣减、整理床铺/更换床单、事后 `warm_shower`（校验热水器完好）/ `cold_rinse` / `wipe` / `drink_water`。

### 3.5 双向同意 `permit` 状态机与无条件喊停（`scene_permit`）
- `propose_scene`：发起邀请（支持 `operation_key` 幂等）；同睡眠周期被拒后再邀触发 `REINVITE_COOLDOWN`（伴侣连问记施压计数，达 2 次写 `BOUNDARY` 并产生 S11 候选；**主体撞冷却绝不记施压**）。
- `respond_scene`：`ACCEPT` / `DECLINE` / `NOT_NOW` / `COUNTER`（婉拒零惩罚、零关系扣分、不算吵架）。
- `advance_beat`：逐拍推进，按最高优先级执行**确定性停止词前置识别**（白名单 `"别停"`/`"不要停"` 放行；裸 `"不要"` 转 `PAUSED` 确认；停止词 `"停"`/`"不要了"`/`"stop"`/`"红灯"` 立即转 `STOPPED`），校验 `600s` 空闲超时、男性插入勃起前置、上游拒答平滑降档、禁假装高潮与精确一次物理/生理落账。
- `request_widen` / `answer_widen`：场内临时放宽提议与应答（含糊回答 `UNCLEAR` 一律按未同意处理）。
- `pause_scene` / `confirm_resume` / `narrow_scene` / `stop_scene`：暂停、双人确认代次恢复、即时收窄、无条件最高优先级喊停（支持转 `STOPPED` 或退回纯拥抱安抚 `AFTERCARE`）。

### 3.6 三档呈现路由与外部推送硬拦截（`presentation_router`）
- `register_render_route`：注册模型路由（`FADE_OUT` / `SUGGESTIVE` / `EXPLICIT` 及 `policy_basis`）。
- `check_outbound_guard`：拦截 `qq`、`wechat`、`telegram`、`push_notification`、`email`、`public_log`、`shared_export`、`slow_letter`、`note` 等外部推送/共享通道输出任何私密内容（`403 OUTBOUND_CHANNEL_FORBIDDEN`）。

### 3.7 不顺利的时候、硬禁假装高潮与场外坦诚谈性（`setbacks_and_talk`）
- `record_setback_or_query`：处理单拍小意外 `mishap`（门外敲门 `KNOCK` 强制按隐私丢失暂停）、不适降速/停场 `discomfort`、基于真实 Body 回执回答 `ask_orgasm`（严禁说谎假装）、仅记录主体自身感受的 `preference_note`（严禁评价伴侣表现）与受守卫的 `s11_write`。
- `discuss_intimacy_outside_scene`：场外坦诚聊偏好/愿望/知识/节奏，固定 `is_scene_invitation: False`、档位封顶 `SUGGESTIVE`、不报次数、绝不自动开启许可。

### 3.8 独处无记忆哈希槽与独立私密记忆擦除（`solo_and_memory`）
- `evaluate_solo_window`：六前提满足后按 `SHA-256(instance_seed, "solo_window", slot_id)` 无记忆抽样（严禁传入伴侣在线/离线时长字段，不随时间越攒越高），写入隔离 `SOLO` 分区。
- `query_or_mention_solo`：在 `solo_talk=True` 且私密共处时按粗粒度事实提及或照实回答，禁止与性邀请同轮出现（`MENTION_WITH_PROPOSE`）。
- `record_private_memory` / `erase_private_memory` / `revoke_intimacy_consent`：物理隔离私密记忆写入、一键撤回同意及不可逆墓碑擦除（审计日志仅记操作者，绝不残留正文，已擦除 ID 永不复活）。
