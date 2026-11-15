# 双人书桌只读面（desk presence）· 后端接口

给 `nyairo-desktop` 消费的最小契约。它只把既有 owner 的事实收成两读一写，不新建
store、ACL、token、定时器或模型调用。

## 1. `GET /api/phone/desk/presence`

四态木牌 + 桌角当前作品 / 读物 + 通信可用性。纯读，零写；多次 GET 返回同一份事实。

```json
{
  "ok": true, "status": "applied",
  "data": {
    "schema": "phone.desk.presence.v1", "read_only": true,
    "door_sign": "FOCUSED_WORK",
    "door_status": "ok",
    "door_reason_code": null,
    "evidence": {
      "location": {"available": true, "home": true, "place_id": "home",
                   "area_id": "bedroom", "name": "家", "reason_code": null},
      "body": {"status": "available", "recovery_state": "inactive",
               "recovery_mode": null},
      "activity": {"available": true, "foreground_ref": "actv_1",
                   "foreground_status": "ACTIVE", "foreground_kind": "WRITING",
                   "interruptibility": "SAFE_BOUNDARY", "reason_code": null},
      "resting_basis": null, "ordinary_rest_is_not_sleep": true
    },
    "desk": {
      "current_work": {"available": true, "artifact_id": "artifact:…",
                       "title": "窗边手稿", "head_version_id": "version-…",
                       "version_number": 1, "private_text_included": false},
      "bound_work": {"artifact_id": "artifact:…", "available": true,
                     "is_current": false, "source": "instance.work_artifact_id",
                     "note": "启动绑定；不是当前前台作品"},
      "reading": {"available": true, "artifact_id": "artifact:…",
                  "title": "窗边的读物", "page": 2, "pages": 6,
                  "unclear_count": 1, "note_count": 0, "bookmark_page": 2,
                  "bookmark_current": true, "private_text_included": false}
    },
    "interruptibility": {"value": "SAFE_BOUNDARY", "reason_code": null,
                         "source": "canonical_life_state.foreground.interruptibility"},
    "communication_availability": {"value": "OPEN", "reason_code": null,
                         "source": "activity_interruption.InterruptionPolicy"},
    "partner_presence": {"value": null, "status": "NOT_RECORDED",
                         "reason_code": "NO_PARTNER_EVIDENCE"},
    "private_text_included": false, "local_only": true
  }
}
```

四态依据（只有证据齐时才给 `door_sign`）：

| 条件 | door_sign |
| --- | --- |
| World 位置可信、`location.home=false` | `OUT_IN_TOWN` |
| 在家、owner 恢复会话 `active` 且 `mode=sleep_like` | `RESTING` |
| 在家、canonical 前台 Activity `status=ACTIVE` | `FOCUSED_WORK` |
| 在家、恢复会话可读且不在睡、前台无 ACTIVE | `AT_HOME_OPEN` |

普通 `mode=rest` 是醒着的休息，**不**单独算 `RESTING`（`resting_basis` 会写明
`OWNER_RECOVERY_ACTIVE_REST_NOT_SLEEP`）。`PAUSED` / `WAITING` / `BLOCKED` 不冒充专注。
位置读不到、canonical 读不到、Body 恢复事实读不到时：`door_sign=null`，并给
`door_status=unknown|unavailable` + `door_reason_code`；**绝不默认在家开放**。

`current_work` 的来源是 **canonical 前台 Activity 自己的 target 绑定**，不是实例启动时的
`work_artifact_id`：先取 `origin_ref`/`checkpoint_ref`/`target_ref` 里唯一一个 artifact
（版本引用 `artifactversion:` 不算对象），没有单值绑定时才接受唯一一个 artifact
`subject_refs`；多个对象视为不可证，不回退首项。再用 owner 的 `read_project` 读它的
head/版本。前台不是可续写文本作品、没有可证 target、前台在读材料，或前台绑定的是短笺
草稿时：`current_work.available=false` + 具名 `reason_code`（如
`FOREGROUND_TARGET_NOT_A_WORK`、`FOREGROUND_TARGET_AMBIGUOUS`、`NO_FOREGROUND_ACTIVITY`、
`FOREGROUND_NOT_ACTIVE`）。启动绑定若存在，只在 `bound_work` 返回并标
`is_current=false`，绝不冒充桌上的当前作品。

`reading` 来自现有阅读工作台的 `material` 只读投影（真实 reader 页码/总页/疑问数/停点），
本面不保存位置、不修改阅读进度。两者都不返回正文。`partner_presence` 没有真实在场依据时
恒为 `NOT_RECORDED`；用户在线、刚 GET、共同书桌名称都不算在场。

## 2. `GET /api/phone/desk/notes`

留存短笺（`DRAFT` / `DEFERRED` / `DROPPED`）与「下一业务本地日」到期的 OPEN 便签。
纯读，零写。

默认（无凭据，或只有 `X-Phone-Actor` 声明——声明不是身份）返回 `view=partner_default`：
短笺只有 `share_id/state/head_version_id/source_artifact_id/text_chars/body_withheld/
visibility/allowed_actions`，**没有正文、没有标题**；明日便签只有 `item_ref/due_at/
status/revision/title_withheld=true`。附 `partner_preview_available=false` 与
`partner_preview_reason_code=NO_EXPLICIT_PREVIEW_GRANT`：目前没有「允许伴侣读未发正文」的
现成授权机制，所以不宣称支持。

本次 serve 的本地 capability 有效（`X-Phone-Capability: <token>` 或
`Authorization: Bearer <token>`，与私人日记同一凭据）时返回 `view=subject`：加入真实
`text`（≤2000 字）、`title`、`defer_condition` / `dropped_reason`，以及明日条目的
`title`。**错误/过期凭据返回 403**，不降级成默认视角，也不返回任何正文。凭据只与本次
serve 的内存值常量时间比较；服务端不打印、不写入响应。

`tomorrow.local_day` 来自实例业务时钟（`fastday_clock.json`）的下一天；`due_at` 是
FutureItems 的 UTC ISO，筛选时换算到时钟自己的偏移再比日期，**不是 now+24h**。业务时钟
缺失时退回系统时钟并在 `tomorrow.clock` 具名。

已 `LOCAL_DELIVERED` 的分享不在这个聚合里：它只通过原有 `/api/phone/shares` 与本地
inbox 的既有引用呈现，GET 绝不会偷偷 `send_local`。

## 3. `POST /api/phone/desk/tomorrow`

明日便签的唯一写口。先 `X-CSRF-Token`，再本次 serve 的本地 capability；缺任何一项都
零写。它只转发到现有 `life_future_items.FutureItems`，没有第二 store，也不替她自动
改期/完成/发送。

```json
{"action": "propose", "title": "明天把便签写完",
 "due_at": "2026-11-13T08:30:00+08:00"}
```

- `propose`：`title` 必填；`due_at` 与 `due_in_minutes` **二选一**。同意图重放返回同一
  `item_ref`、`created=false`，不产生第二条。
- `cancel`：`item_ref` 必填，可选 `reason`。
- `reschedule`：`item_ref` + `due_at` / `due_in_minutes` 二选一，可选 `reason`。

成功返回 `{"ok":true,"data":{"action":…,"item_ref":…,"item":{…},"read_back":true}}`；
写后重新读回同一个对象。失败（未知字段、到期时间歧义、条目不存在/已非 OPEN）返回现有
错误信封，零新版本。`FutureItems` 的原子替换保证不会留下半写状态。

短笺写入不新增路由：继续用既有 `POST /api/phone/shares`（显式 `share_text` 或
`excerpt_chars`）和 `POST /api/phone/shares/<id>/actions`（`defer` / `drop` /
`send_local`，带 `expected_head` 与 `operation_key`）。`DROPPED` 终态不复活。

## 4. 边界与未支持

- 不改 `/api/phone/state`、`/today`、`/shares` 的既有形状；不扩 Life/World 契约。
- 不提供伴侣公开短笺的并行 `visibility=public`、新 ACL 或新凭据；客户端自填
  `visibility` 不会绕过授权。
- 页面文本、页注正文、作品正文、来源正文都不进入本面；标题沿既有 `/projects` 与
  `/reading/material` 的暴露政策。
- 本面 GET 不启动服务、不调模型、不修改阅读位置、不 mark-read、不推进 future、不触发
  owner.tick。World/Body owner 自己的后台 tick 若在进程里运行，仍可能落盘；若要证明手机
  侧零写，应在测试里暂停并排空该 tick，而不是删除 Body 快照。
