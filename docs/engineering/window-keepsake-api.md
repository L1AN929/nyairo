# 窗边景致 / 冰箱贴与自选锁屏 / 分日慢写的信 · 后端接口

给 `nyairo-desktop` 消费的最小契约。三个读口是纯投影，一个写口只保存**显式选择引用**：
不复制图片/正文到新库，不新建 ACL、token、定时器或模型调用。事实仍由既有
World / Supply / `PhoneAlbum` / `LifeSharingService` / `PrivateProjectService` 拥有。

所有请求仍是本机回环，并受既有 `Host` / `Origin` / `Fetch-Site` 守护。私人视角使用本次
`serve` 的本地 capability（`X-Phone-Capability: <token>` 或
`Authorization: Bearer <token>`，与私人日记、双人书桌同一凭据）。

## 1. `GET /api/phone/home/window`

窗边景致：当前房间真实声明的植物对象、室内灯光、她自己决定过的窗向/窗景/院子座位、
当天 home 的 ambient 模板声景。纯读，零写。

```json
{
  "ok": true, "status": "applied",
  "data": {
    "schema": "phone.home.window.v1", "read_only": true,
    "at_home": true,
    "location": {"available": true, "place_id": "home", "area_id": "bedroom",
                 "home": true, "reason_code": null},
    "plants": [
      {"object_id": "home_plant04", "name": "盆栽", "kind": "plant",
       "area_id": "bedroom", "placement_area_source": "world_package_scene",
       "growth": {"value": null, "reason_code": "NO_PLANT_GROWTH_AUTHORITY"},
       "source": "world_package_scene"}
    ],
    "plants_status": {"status": "available", "reason_code": null,
                      "count": 1, "source": "world_package_scene"},
    "light": {"status": "unknown", "reason_code": "NO_DECLARED_LIGHT_HERE",
              "power": null, "band": null, "declared_source": null,
              "fixture_ids": [], "source": "world_room_environment",
              "derived_from_clock": false},
    "window": {
      "available": true, "status": "available", "reason_code": null,
      "window": {
        "orientation": {"status": "SET_BY_SELF", "value": "south", "revision": 1},
        "view": {"status": "SET_BY_SELF", "value": "能看到港口方向的海", "revision": 2}
      },
      "yard_seating": {"status": "SET_BY_SELF", "value": "一张旧木长椅"},
      "undecided_fields": [],
      "source": "world_home_space.home_personal_space.json"
    },
    "soundscape": {
      "available": true, "count": 1,
      "events": [{"event_id": "…", "event_type": "home_window_sound",
                  "location_id": "home", "summary": "窗外雨声变大",
                  "valid_from": "…", "valid_until": "…"}],
      "reason_code": null, "source": "world_ambient_events",
      "template_projection": true, "is_weather": false,
      "local_date": "2026-11-14", "local_time": "2026-11-14T12:00:00+09:00",
      "clock_source": "injected_world_clock"
    },
    "weather": {"value": null, "status": "unavailable",
                "reason_code": "NO_WEATHER_AUTHORITY", "source": "none",
                "is_weather_authority": false},
    "private_text_included": false, "local_only": true
  }
}
```

边界：

- **植物**只来自世界包在当前区域声明的 `kind=plant` 摆位；`object_id` / `area_id` 是
  世界包自己的身份与区域。没有生长权威时 `growth.value` 恒为 `null` 并具名
  `NO_PLANT_GROWTH_AUTHORITY`——不从标题、日期或对话推「长高了/开花了」。
- **灯光**是 `world_room_environment` 的声明灯 + 已记录开关，不按钟点推「白天」。
  没有声明的灯就是 `NO_DECLARED_LIGHT_HERE`，不是「关着」。
- **窗向/窗景/院子座位**来自 `world_home_space` 她自己已决定的 detail；未决定就是
  `status=UNSET` + `UNDECIDED_BY_SELF`，不补默认值。feature 关闭时
  `window.available=false` + `FEATURE_DISABLED`。
- **声景**是当天 World-local 的 ambient **模板**投影，`template_projection=true`、
  `is_weather=false`。不在家时 `NOT_AT_HOME`；feature 关闭时 `FEATURE_DISABLED`；
  World 本地时钟读不到时 `WORLD_LOCAL_CLOCK_UNAVAILABLE`。
- **天气**：本项目没有气象权威，因此始终 `value=null` +
  `NO_WEATHER_AUTHORITY`；绝不把模板声景、季节或钟点说成真实天气。
- 不做浇水/种植写口，不产生打卡债。

## 2. `GET /api/phone/home/keepsakes`

她自己显式选过的锁屏图片与冰箱贴。默认（无凭据）是伴侣视角；本次 serve 的
capability 有效时是自己视角。纯读：每次重新向 owner 核实。

```json
{
  "ok": true, "status": "applied",
  "data": {
    "schema": "phone.home.keepsakes.v1", "read_only": false,
    "view": "partner_default",
    "state": {"revision": 3, "updated_at": "2026-11-11T12:00:00+08:00",
              "reason_code": null},
    "lockscreen": {"status": "available", "has_lockscreen": true,
                   "reason_code": null,
                   "updated_at": "2026-11-11T12:00:00+08:00"},
    "fridge_pins": [
      {"share_id": "artifact:…", "status": "available", "reason_code": null,
       "pinned_at": "2026-11-11T12:00:00+08:00",
       "delivered_at": "2026-11-11T11:50:00+08:00",
       "state": "LOCAL_DELIVERED", "text_withheld": false,
       "text": "冰箱上那张：今天海风很凉。", "text_chars": 14}
    ],
    "fridge_pin_count": 1,
    "private_text_included": true, "local_only": true
  }
}
```

自己视角（`view=subject`）额外返回：

- `lockscreen.album_artifact_id`、`lockscreen.caption`（≤200 字）、
  `lockscreen.content_type`、`lockscreen.image_surface`（指回原有
  `/api/phone/album/<id>/image`）。图片字节、相册路径、SHA-256、媒体文件名**不返回**。
- 冰箱贴正文就是**已投递分享自己的正文**（≤240 字摘要），不是来源作品全文。

默认/伴侣视角的隐私：

- 锁屏只给 `has_lockscreen` / `status` / `updated_at` / 具名原因；**不返回**
  `album_artifact_id`、caption、content_type。
- 冰箱贴只允许真实 `LOCAL_DELIVERED`：GET 会重新读分享 owner，核
  `state`、本地投递回执（`local_only=true`、未外发、`audience=local:owner`、
  `channel=local_inbox`）与 source 当前可读性。源被治理/不可读、分享不存在、
  非已投递时：该行 `status=withheld` + 具名 `reason_code`，正文不返回，对象本身不删。
- 错误/过期凭据返回 403（`CAPABILITY_INVALID` / `CAPABILITY_UNAVAILABLE`），不降级。
  `X-Phone-Actor` 声明不是身份。

## 3. `POST /api/phone/home/keepsakes`

唯一写口：只写实例自己的 `phone/window_keepsakes.json`（引用 + 选择时间 + revision，
原子替换）。先 `X-CSRF-Token`，再本次 serve 的本地 capability；缺任何一项都零写。

```json
{"action": "select_lockscreen", "album_artifact_id": "artifact:…"}
{"action": "clear_lockscreen"}
{"action": "pin_share", "share_id": "artifact:…"}
{"action": "unpin_share", "share_id": "artifact:…"}
```

- `select_lockscreen`：ref 必须在她的真实相册里且媒体现存。服务端先走 owner 的
  `read_album_image`，再走 `read_album_image_bytes` 校验大小/SHA-256；字节读完即丢。
  普通作品、非相册 manifest、已归档/被治理图片一律具名拒绝（如
  `NOT_AN_ALBUM_IMAGE`、`ALBUM_IMAGE_WITHHELD`），零写。
- `pin_share`：必须是真实 `LOCAL_DELIVERED` 且本地回执/源当前可读；未投递、
  不存在、源被治理一律具名拒绝（`PINNED_SHARE_NOT_DELIVERED` 等），零写。
- `clear_lockscreen` / `unpin_share` 是逻辑撤销，不删文件、不删 owner 数据。
- **同意图幂等**：相同的 select/pin/unpin 重复，返回
  `status=already_applied`、`changed=false`，revision 与 updated_at 都不变；
  不换 key 重放 UNKNOWN，也不新造通用操作账本。
- 请求体只接受 `action` 与对应引用。`caption` / `text` / `title` / `summary` /
  `path` / `url` / `media` / `sha256` / `artifact_id` 等一并按名字拒绝，不静默忽略。

成功：

```json
{"ok": true, "status": "applied",
 "data": {"action": "select_lockscreen", "changed": true, "revision": 1,
          "updated_at": "2026-11-11T12:00:00+08:00",
          "album_artifact_id": "artifact:…"}}
```

## 4. `GET /api/phone/home/letters`

**没有信件 store。** 这是既有「分享 → 精确 source version」与
`PrivateProjectService` 多版本时间线的只读投影：把跨 >=2 个「owner 时间戳本地日」的
累积作品/分享列出来。绝不按标题里有没有「信」猜身份，`letter_type` 恒为 `null`。

```json
{
  "ok": true, "status": "applied",
  "data": {
    "schema": "phone.home.letters.v1", "read_only": true,
    "view": "partner_default",
    "letters": [
      {"share_id": "artifact:…", "state": "LOCAL_DELIVERED", "delivered": true,
       "status": "delivered", "reason_code": null,
       "postmark": {
         "first_written_day": "2026-11-10", "last_written_day": "2026-11-11",
         "writing_days": 2, "elapsed_days_inclusive": 2,
         "delivered_at": "2026-11-11T11:50:00+08:00",
         "timezone_offset": "+08:00",
         "date_basis": "owner_timestamp_localized",
         "historical_business_day_provable": false
       },
       "versions_considered": 2, "versions_after_cutoff_excluded": 1,
       "bounded": {"history_complete": true, "history_reason_code": null,
                   "undated_versions": 0, "unbounded_versions": 0,
                   "date_completeness": "complete"},
       "text_withheld": false, "text": "写好了一点，先放在这里。",
       "text_chars": 12, "source_work_body_included": false,
       "projection_only": true, "letter_type": null}
    ],
    "count": 1, "truncated": false, "scanned_shares": 4,
    "date_basis": "owner_timestamp_localized",
    "historical_business_day_provable": false,
    "timezone_offset": "+08:00",
    "clock": {"source": "business_clock", "logical_at": "2026-11-11T12:00:00+08:00",
              "reason_code": null},
    "letter_type_is_not_a_store": true,
    "private_text_included": true, "local_only": true
  }
}
```

边界：

- **只统计到分享绑定的 `source_version_id` 为止**。投递后又提交的新版本不会算进这封信，
  `versions_after_cutoff_excluded` 会具名计数；不能用投递后的私密修改改写已投递内容。
- 日期取 `today_business_day` 的真实偏移，把 owner 各版本 `created_at`
  （epoch 秒或 ISO）转换到该本地日期。`date_basis=owner_timestamp_localized`；
  `historical_business_day_provable=false`——owner wall-clock 与压缩业务钟的逻辑日
  不是一回事，没有既有历史业务邮戳就不冒充。
- `writing_days` 是 distinct 本地日；`elapsed_days_inclusive=(end-start)+1`。两者分开，
  不混用。
- 缺时间戳 / 历史分页截断：`bounded.date_completeness=partial`、
  `bounded.history_complete=false` 并具名原因；不假完整总数。
- `DRAFT` / `DEFERRED`：默认只给安全元数据，正文与私人标题扣留；有效 capability
  才返回有界**分享正文**（≤1000 字）。
- `LOCAL_DELIVERED`：默认只返回**已投递分享自己的正文**，不拿来源作品全文替代。
- source 被治理/不可读时沿既有 withheld 规则失败关闭（`status=withheld` +
  `reason_code`），不缓存已公开文本绕治理。

## 5. 预览夹具

`scripts/window_keepsake_fixture.py --port 3854` 用正常构造启动：真实 Supply owner、
`one_day_life.prepare`、`WorldGateway`、worldgen 真实室内家具、真实相册导入、两个
owner-clock commit 与正式 `share_prepare` + `issue_grant` + `send_local`。零模型、零
secrets；启动输出只给 root、端口、capability 文件路径与引用 id，不打印 token。

桌面端取得凭据的既有方式：读 scratch root 下 `run/phone-capability.json`（0600），把
`token` 放进本会话的 `X-Phone-Capability` 头或 `Authorization: Bearer`。不存在测试后门
路由，也不会把真 capability 变成默认公开 JS。
