# 衣柜 / 小列表 / 作品旧版并排 API（wardrobe-pocket slice）

Status: implemented（2026-11-11，0.2 补齐）

三个读面、两个写面、两份实例本地 JSON。边界按设计 15 章 2.1 / 2.2 / 2.3 / 2.8 / 4.6
与 13 章 3：

1. 衣柜只记录**她显式的搭配风格与换季选择**，不是 World 产权、位置或穿戴状态；
2. 小列表是**有界的个人卡片**（每类 ≤12）与最多 3 张伴侣短建议，不是数据库、权限或偏好推断；
3. 作品旧版并排只投影 `PrivateProjectService` 的**两个真实历史版本**，不建新 store；
4. 不调用阅读、模型、记忆写入；不创建 FutureItem、义务或后台 timer。

## 通用契约

- 所有端点都过现有 `_guard_common`（Host / Origin / Sec-Fetch-Site）。
- 三个 GET 默认允许**无凭据**的安全只读视图；一旦显式携带 `Authorization: Bearer <token>`
  或 `X-Phone-Capability`，就必须按本次 serve 的本地 capability 做既有常量时间校验：
  错误 / 不可用一律 403（`CAPABILITY_INVALID` / `CAPABILITY_UNAVAILABLE`），**绝不降级**。
- 所有 POST 先过 `_guard_mutation`（CSRF）。衣柜与个人列表再要本次 serve 的本地 capability；
  伴侣建议替换只需 CSRF，但显式携带的 capability 仍必须验证。
- `X-Phone-Actor` 只是声明，不是身份；没有 capability 的本人写仍然 403。
- 字段白名单按**动作存在性**校验：该动作不接受的字段，即使值是 `[]` / `null` / `""`
  也按存在拒绝（400 `UNKNOWN_FIELD:<name>`）。必需文本为空/缺失为 400。
- GET 不建目录、不建文件、不写 owner；写只落在实例自己的两份选择 JSON。

本地状态：

- `<root>/phone/wardrobe.json`，`schema_version=1`
- `<root>/phone/pocket_lists.json`，`schema_version=1`

缺失 = 空选择；损坏 = fail closed（只读返回 `WARDROBE_STATE_INVALID` /
`POCKET_STATE_INVALID`，绝不写回）；符号链接拒读。原子写、0600。

## GET /api/phone/home/wardrobe

纯读聚合。返回她显式的风格、发带/发夹、搭配呼应备注、blanket/cushion 换季选择，
并对任何被点名的已购实例向真实 `world_property_economy` 现读核对。

默认（伴侣/无凭据）视图**不含** `pairing_note`、不含实例 id；本人 capability 才给全量。
两个视角都明确 `selection_only=true`、`not_ownership_claim=true`、
`does_not_change_world_wear_state=true`、`does_not_change_somatic_garment_state=true`。

```json
{
  "ok": true, "status": "applied",
  "data": {
    "schema": "phone.home.wardrobe.v1",
    "view": "subject|partner_default",
    "state": {"revision": 1, "updated_at": "...", "reason_code": null},
    "selection_only": true, "not_ownership_claim": true,
    "outfit": {
      "homewear": {"style": "宽松针织"|null,
                   "item": null | {"status": "verified|rejected|unavailable",
                                   "reason_code": null|"...", "catalog_item_id": "..."}}
    },
    "accessories": {"hairband": {...}, "hairclip": {...}},
    "season_textiles": {"blanket": {"season": "winter"|null},
                        "cushion": {"season": "summer"|null}},
    "pairing_note": "...|null",
    "related_owned_items": [],
    "related_owned_items_status": {"status": "none", "reason_code":
                                   "NO_OWNED_RELEVANT_ITEMS", "count": 0},
    "no_ownership_inferred": true, "no_preference_inferred": true,
    "private_text_included": false, "local_only": true
  }
}
```

- 当前 World economy 目录**没有声明任何衣柜/饰品类别**，因此
  `related_owned_items` 恒为 `[]` + `NO_OWNED_RELEVANT_ITEMS`；点心、家具等已购实例
  不会被当作发带。不为了演示造商品、不按名字编产权。
- 若绑定了实例 id 但 economy 不可读：状态 `unavailable` + 具名原因，不显示“已拥有”。

## POST /api/phone/home/wardrobe

CSRF + 本人 capability。一个动作一次：

- `set`：字段为下列任意子集，至少一个；`[]` / `null` / 空串按存在拒绝。
  - `homewear_style` / `outdoor_style` / `hairband_style` / `hairclip_style`：≤120 字符，非空；
  - `homewear_item_instance_id` / `outdoor_item_instance_id` /
    `hairband_item_instance_id` / `hairclip_item_instance_id`：必须是真实 economy 里
    `disposition=OWNED`、`owner=本人`、**类别适用于该槽位**、当前可读的实例；否则按
    `ITEM_NOT_FOUND` / `ITEM_NOT_OWNED_BY_SUBJECT` / `ITEM_NOT_OWNED` /
    `ITEM_CATEGORY_NOT_APPLICABLE` 拒绝，零写；
  - `pairing_note`：≤120 字符，非空；
  - `blanket_season` / `cushion_season`：`summer` | `spring_autumn` | `winter`。
- `clear`：`fields` 必须是**非空**的字段名数组；只清点名的子字段。空的 `fields: []`
  是 400 `WARDROBE_CLEAR_FIELDS_REQUIRED`（不是“清空全部”的隐式语义）。重复字段 400。

同一 `set` / `clear` 重放返回 `already_applied`，`revision` 与 `updated_at` 不变。

## GET /api/phone/pocket/lists

纯读。默认视图只返回 `shared=true` 的个人卡片（仅 title + status，**不含** entry id、
理由、期待、取舍、备注、operation key 或私人总数）与全部伴侣建议（标
`partner_contributed=true`）。本人 capability 才返回全量卡片与真实计数。

`shared` 必须在写入时由本人显式设置，缺省 `false`；`shared=false` 后下一次 GET 立即扣留，
不缓存旧公开正文。

三类卡片与有限状态：

- `playlist`：`title` + `reason`（喜欢理由）；`status=active|resting`。
  `resting` 保留理由与喜欢，不删除。
- `favorites`：`title` + `expectation`（读前期待）+ `verdict`（读后简短取舍）；
  `status=saved_unread|reviewed`。`reviewed` 仅是本人显式自报回看，**不**声称已有阅读
  或理解证明；保存与 GET 不调用阅读/模型/记忆写。
- `want_to_try`：`title` + 可选 `note`；`status=active|paused|dropped|done`。
  改期/放弃无债，不建 FutureItem。

跨类别状态（例如给 `playlist` 写 `reviewed`）是 400 `POCKET_STATUS_NOT_FOR_LIST`；
未知状态同样 400，不吞值。

## POST /api/phone/pocket/lists

- `add`（CSRF + capability）：`{action,list,title,...,status?,shared?,operation_key}`。
  `operation_key` 必填；同一 key 在同一列表重放返回 `already_applied`，不涨 revision；
  同一 key 跨列表复用 409。每类最多 12 条，超出 409 `POCKET_LIST_FULL`。
- `update`（CSRF + capability）：`{action,list,entry_id,title?,...,status?,shared?}`。
  未改变任何值返回 `already_applied`。
- `remove`（CSRF + capability）：`{action,list,entry_id}`；不存在的 id 返回
  `already_applied`（删除是幂等的）。
- `set_partner_suggestions`（只需 CSRF；显式 cap 仍验证）：
  `{action,suggestions:[{title≤60,note?≤120},...]}`，最多 3 条，整体替换。
  `suggestions: []` **是合法显式清空**，不是未知字段。每张卡固定
  `partner_contributed=true`；不允许携带 `list` / `entry_id` / `shared` / `status` /
  `operation_key` 或任何个人列表字段（按存在 400），不会覆盖本人列表，也不会自动采纳。
  能力凭据不等于伴侣身份，更不等于能把建议写进私单。

## GET /api/phone/pocket/work-revisit

Query 精确为三个参数，每个一次，无默认“最新作品”猜测：

```
?artifact_id=<id>&left_version_id=<vid>&right_version_id=<vid>
```

未知参数 400 `UNKNOWN_QUERY`；重复参数 400 `QUERY_REPEATED:<key>`；缺失/空值 400
`WORK_REVISIT_QUERY_REQUIRED`；左右同一版本 400 `VERSIONS_NOT_DISTINCT`。

- 作品必须是她当前可读的 `project`（相册/资料/研究/实践文档等非作品按既有 owner 拒绝；
  分享信封按其 title marker 拒绝 `SHARE_ENVELOPE_NOT_A_PROJECT`）；
  两个版本必须真实属于该作品，否则 409 `VERSION_NOT_IN_ARTIFACT`。
- **本人 capability**：对两个真实历史版本各给有界摘要（≤600 字符），
  `excerpt_basis=owner_version_excerpt`；未投递的新版本也可以由本人读。
- **默认（伴侣）**：对**每个精确 source version** 单独要求存在真实 `LOCAL_DELIVERED`
  分享，且本地受众/频道/receipt 与来源当前可读都核过；摘要取**该版已投递分享正文**，
  `excerpt_basis=delivered_share_text`。没有该版投递就 `withheld` +
  `VERSION_NOT_DELIVERED`。**一封更新版本的分享，不能证明更早版本的正文已经公开。**
- 已归档（`ARCHIVED`）来源一律 `withheld` + `ARCHIVED_SOURCE`，不借历史读口绕开当前
  生命周期；归档不是删除，内容仍在 owner。
- 对比只给安全结论：本人视角按两个版本 `content_sha256` 判 `changed`，范围
  `full_version_content`；伴侣视角只按已投递分享正文判，范围
  `delivered_share_text_only`，`source_text_change_provable=false`。两端都不返回差异片段。
- 本端点不创建新分享、不授予许可、不扩大其他端点的隐私政策。

## 已知边界

- 衣柜不是全身物理或自动换装；不改既有 somatic 外衣穿挂状态，避免第二个同物件真相。
- 当前 economy 目录没有可适用的衣柜类别，所以“真实已购衣柜实例”正路径尚不存在；
  负控（假 id / 他人物 / 非 OWNED / 点心充发带）是已实现能力边界。
- `work-revisit` 的伴侣披露上界是**精确版本的真实本地投递**；既有 explicit disclosure
  grant 仍有效也不单独当作“已 LOCAL_DELIVERED”，本端点只认真实投递。
