# 创作托盘 / 小物履历 / 三样喜欢之物 · 后端接口

给 `nyairo-desktop` 消费的最小契约。三个读口是纯投影，两个写口只保存**显式引用/短卡**：
不复制图片、资料正文、作品正文或订单，不新建 ACL、token、定时器或模型调用，也不产生
任何打卡义务。图片/正文/交易仍由 `PhoneAlbum` / `ResearchWorkspaceService` /
`PrivateProjectService` / `LifeSharingService` / World 产权 economy 与 Body substrate 拥有。

所有请求仍是本机回环，并受既有 `Host` / `Origin` / `Fetch-Site` 守护。私人视角使用本次
`serve` 的本地 capability（`X-Phone-Capability: <token>` 或 `Authorization: Bearer <token>`，
与私人日记、双人书桌、冰箱贴同一凭据）。

## 1. `GET /api/phone/home/workbench`

创作托盘。默认（无凭据）是伴侣视角，只报告是否有内容与三类件数；本次 serve 的 capability
有效时是自己视角，返回引用、放入时基准版本、当前 head、可读状态与私人备注。纯读：每次
重新向真实 owner 核实，缺文件返回空且零写。

```json
{
  "ok": true, "status": "applied",
  "data": {
    "schema": "phone.home.workbench.v1", "read_only": true, "view": "subject",
    "state": {"revision": 1, "updated_at": "2026-11-11T12:00:00+08:00", "reason_code": null},
    "has_materials": true,
    "counts": {"images": 1, "materials": 1, "works": 1},
    "status": "available", "updated_at": "2026-11-11T12:00:00+08:00",
    "no_obligation": true, "local_only": true,
    "note": "今天想做的三样",
    "images": [
      {"album_artifact_id": "artifact:…", "status": "available", "reason_code": null,
       "caption": "窗边的光", "content_type": "image/png",
       "added_at": "2026-11-11T12:00:00+08:00"}
    ],
    "materials": [
      {"artifact_id": "artifact:…", "status": "CURRENT", "reason_code": null,
       "baseline_source_revision": "artifact-version:…",
       "current_source_revision": "artifact-version:…",
       "title": "海风与旧码头", "added_at": "2026-11-11T12:00:00+08:00"}
    ],
    "work": {
      "artifact_id": "artifact:…", "status": "available", "reason_code": null,
      "baseline_version_id": "artifact-version:…", "baseline_version_number": 2,
      "current_head_version_id": "artifact-version:…", "current_version_number": 2,
      "head_advanced": false, "title": "窗边手稿",
      "added_at": "2026-11-11T12:00:00+08:00"
    }
  }
}
```

边界：

- 图片只允许 `PhoneAlbum` 真实认证且媒体现存/大小与 SHA-256 可核的 PNG/JPEG；普通作品、
  已归档/被治理图片在写入口具名拒绝。
- 资料只经 `ResearchWorkspaceService` 的正式 materials 读口；换版后 `status=STALE`，
  `baseline_source_revision` 与 `current_source_revision` 分开，不偷偷改基准。
- 作品只允许私人作品；相册清单、分享信封、资料、研究、实践回顾/做法一律不是托盘作品。
- `head_advanced` 只按版本历史/序号证明后继：同一版本 `false`；基准版本在真实历史里且
  head 序号更大 `true`；缺历史、基准不在历史或序号不更大 `unknown`。字符串不相等**不是**证据。
- 默认视角**不返回**图片/资料/作品引用、标题、备注、正文、版本指纹；只给
  `has_materials` / `counts` / `updated_at` / 安全状态 / `no_obligation=true`。
- source 被治理/删除/不可读时该行 `status=withheld|UNAVAILABLE` + 具名 `reason_code`，
  不缓存正文绕治理。错误凭据 403，`X-Phone-Actor` 不是身份。

## 2. `POST /api/phone/home/workbench`

唯一写口：只写实例自己的 `phone/workbench_tray.json`（引用 + 放入时基准 + revision，
原子替换）。先 `X-CSRF-Token`，再本次 serve 的本地 capability；缺任何一项都零写。

```json
{"action": "set_tray",
 "album_artifact_ids": ["artifact:…", "artifact:…"],
 "material_ids": ["artifact:…", "artifact:…"],
 "work_artifact_id": "artifact:…",
 "note": "不超过 120 字"}
{"action": "clear_tray"}
```

- `set_tray` 是整体替换：最多 2 张相册图、2 份正式资料、1 件私人作品，备注 ≤120 字。
  服务端在放入时向真实 owner 读取基准：资料取当前 source revision，作品取当前
  head version id + 序号。客户端不能传版本/正文/标题；携带即按名字拒绝（409）。
- 混合任何无效引用（伪图/伪资料/伪作品/超数/重复）→ 整单拒绝、零写。
- 同一意图重放（引用与备注都相同）返回 `status=already_applied`、`changed=false`，
  revision 与 updated_at 不变；**不会**因为作品后来更新而偷偷重记基准。显式移出再放
  才是新的选择。
- `clear_tray` 是逻辑撤销，不删文件、不删 owner 数据。
- 每个 action 只接受自己那份字段，按字段**是否存在**判断：`clear_tray` 携带
  `album_artifact_ids`/`material_ids`/`work_artifact_id`/`note` 时，即使值为 `[]`/`null`/`""`
  也返回 `UNKNOWN_FIELD:<字段>` 并零写，不按值真假静默吞掉。

## 3. `GET /api/phone/home/curios`

小物履历。纯读真实产权 economy 与 Body substrate，按稳定 `item_instance_id` 关联实际
placement/手持/在途。不 tick、不 materialize、不 ensure、不迁移，也不回退到主体当前区域。

```json
{
  "ok": true, "status": "applied",
  "data": {
    "schema": "phone.home.curios.v1", "read_only": true,
    "items": [
      {"item_instance_id": "item:…", "name": "虚拟小点心",
       "catalog_item_id": "virtual_snack_wafer", "ownership": "OWNED",
       "acquired": {"status": "available", "reason_code": null,
                    "acquired_at": "2026-11-11T12:00:00Z", "acquisition_kind": "PURCHASE",
                    "price": 240, "currency": "JPY",
                    "source": {"transaction_id": "…", "purchase_intent_id": "…",
                               "source_kind": "local_owner_phone_purchase",
                               "delivery_kind": "phone_virtual_store_home_delivery",
                               "delivery_id": null}},
       "delivery": {"status": "unknown", "reason_code": "NO_DELIVERY_RECORD"},
       "position": {"status": "available", "kind": "room", "carried": false,
                    "location_id": "home", "area_id": "bedroom"},
       "remaining_uses": {"value": null,
                          "reason_code": "NO_REMAINING_USES_AUTHORITY"},
       "no_fabricated_wear": true, "no_attachment_score": true}
    ],
    "count": 1, "pending_delivery_count": 0, "unknown_position_count": 0,
    "source": "world_property_economy+world_body_substrate",
    "no_fabricated_wear": true, "no_attachment_score": true, "local_only": true
  }
}
```

边界：

- 只列产权 economy 里真实购买的实例；场景声明的静态物件不是她的私产，不列。
- 名称/取得时间/价格/交易号/购买意图全部来自 economy 自己的账本；同一 sku 或同名不被
  当成同一实例，唯一键是 `item_instance_id`。
- `delivery` 与 `position` 分开：购买成功不等于已送达，送达也不等于已摆好。
- 没有 placement 时 `position.status` 是 `pending_delivery` 或 `unknown` + 具名原因，
  绝不回退成主体当前区域；substrate 读不到时是 `SUBSTRATE_UNAVAILABLE`。
- 本项目没有「剩余次数」权威，`remaining_uses.value` 恒为 null +
  `NO_REMAINING_USES_AUTHORITY`；不造磨损/依恋分。
- 不带出 Supply 私人正文、标题或相册；只沿 World 既有边界给产权与位置事实。

## 4. `GET /api/phone/home/trio-exchange`

三样喜欢之物。`her_picks` 只引用真实 `LOCAL_DELIVERED` 分享并每次现读正文；
`partner_picks` 只是伴侣写的短卡。缺文件返回空，`no_obligation=true`。

```json
{
  "ok": true, "status": "applied",
  "data": {
    "schema": "phone.home.trio_exchange.v1", "read_only": true,
    "state": {"revision": 1, "updated_at": "2026-11-11T12:00:00+08:00", "reason_code": null},
    "her": {"count": 1, "picks": [
      {"share_id": "artifact:…", "status": "available", "reason_code": null,
       "state": "LOCAL_DELIVERED", "delivered_at": "2026-11-11T11:50:00+08:00",
       "text_withheld": false, "text": "写好了一点，先放在这里。", "text_chars": 12,
       "source_text_included": false}
    ]},
    "partner": {"count": 1, "picks": [
      {"title": "海边的石头", "description": "灰蓝色，被浪磨圆",
       "partner_contributed": true, "added_at": "2026-11-11T12:00:00+08:00"}
    ]},
    "partner_contributed": true, "private_text_included": true,
    "no_obligation": true, "local_only": true
  }
}
```

边界：

- `her` 行每次重新读分享 owner：必须是 `LOCAL_DELIVERED`、有本地投递回执
  （`local:owner` / `local_inbox`、未外发）且 source 当前可读。未投递/伪引用/来源被治理
  时 `status=withheld` + 具名原因，正文不返回、不缓存。
- 默认只给**已投递分享自己的有界正文**，不给来源作品全文/标题、不给来源引用。
- `partner` 卡片 `partner_contributed=true`，只存在匣子里；绝不调用 Memory / Belief /
  经历 / 认知 API，也不表示她看过、相信或记住。

## 5. `POST /api/phone/home/trio-exchange`

只写实例自己的 `phone/trio_exchange.json`。`set_her_picks` 需 CSRF + capability；
`set_partner_picks` 只需 CSRF，但请求若**显式携带** cap 则仍按既有规则验证，错凭据 403，
不会悄悄降级。同源/Host/Origin 护栏不变。

```json
{"action": "set_her_picks", "her_picks": ["artifact:…", "artifact:…", "artifact:…"]}
{"action": "set_partner_picks",
 "partner_picks": [{"title": "≤40 字", "description": "≤120 字"}]}
```

- 两个数组都最多 3 项；空数组是清空。批内任一无效（未投递分享、伪引用、标题/描述超长、
  卡片夹带额外字段）→ 整批拒绝、零写，不静默截断。
- `set_partner_picks` 不接受 `her_picks`，`set_her_picks` 不接受 `partner_picks`：按字段
  **是否存在**判断，夹带即 `UNKNOWN_FIELD`，值为 `[]`/`null`/`""` 也一样拒绝并零写，
  不悄悄忽略。
- 同一意图重放返回 `status=already_applied`，revision 与 updated_at 不变。

## 6. 预览夹具

`scripts/workbench_curio_fixture.py --port 3855` 用正常构造启动：真实 Supply owner、
`one_day_life.prepare`、`WorldGateway`、worldgen 真实室内家具、真实相册导入、正式
`import_material`、产权 economy 正式 `initialize_genesis` + `PhoneShopping.purchase`
真实购买并在 `home/bedroom` 物化、正式 `share_prepare` + `issue_grant` + `send_local`。
零模型、零 secrets；启动输出只给 root、端口、capability 文件路径与引用 id，不打印 token。

桌面端取得凭据的既有方式：读 scratch root 下 `run/phone-capability.json`（0600），把
`token` 放进本会话的 `X-Phone-Capability` 头或 `Authorization: Bearer`。不存在测试后门
路由，也不会把真 capability 变成默认公开 JS。
