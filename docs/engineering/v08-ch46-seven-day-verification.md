# v0.8 Wave 4 · 第 46 篇 §五 8 类故障 & §六 第 1–7 天连续生活验证

- 验证对象：`v04_neighborhood_living` / `v04_neighborhood_view` / `v02_v03_daily_loop_bridge`
  / 真实 `AutonomousLifeCycle` + `AutonomousLifeResident`
- 测试文件：`tests/test_v08_ch46_seven_day_verification.py`
- 运行（WSL，POSIX 夹具）：
  ```bash
  /tmp/rc104-venv/bin/python -B -m unittest \
    tests.test_v08_ch46_seven_day_verification \
    tests.test_v08_street_notice_booklet_loop \
    tests.test_v08_courtyard_drying_loop \
    tests.test_v04_neighborhood_living
  ```
- 结果：`Ran 62 tests … OK`（本文件 9 条：8 个故障 + 1 条七天连续）。

## 一、成本与边界

| 项 | 实测 |
| --- | --- |
| 产品大模型调用 | **0**（认知全部是脚本化 0-LLM 替身；每条结算 `llm_calls == 0`） |
| 随机数 | 0（引擎纯确定性） |
| 真实 owner 写入 | v0.4 状态文件 + 桥接后果账本，均由正式入口写入 |
| 依赖 | WSL / POSIX 文件锁与 AF_UNIX（真实 World/Supply 服务夹具） |

**未覆盖的边界（如实记录）**

1. 未跑真实大模型 24 小时在线长跑；七天是同一进程内的多轮次，不是真实墙钟七天。
2. 桌面端 UI 未联动更新；验证到 HTTP 业务函数与真实 owner 状态，不到桌面渲染。
3. **同线索 operation_key 去重边界**：Resident 自带的 v0.4 key 是
   `resident_v04_<raw_source_ref>`。同一线索（如 `v04_courtyard_hang:wet_cloth_main`）在不同天
   的第二次物理动作会命中 Day 1 的回执并成为 `already_applied`（世界不再变化）。本次第 2 天
   的重新挂出改走**独立 operation_key 的正式 `settle_*_round_consequences`**（仍是
   bridge → phone view → v0.4 owner），测试与本文如实区分两条路。
4. **支撑位没有物理容量语义**：`CLOTH_SUPPORTS` 只有 `place_id / shelter / label`，没有
   容量字段，`porch_hook` 不是“容量 1 的槽位”。故障 3 因此不伪造 `SUPPORT_AT_CAPACITY`：
   两件不同 item_id 的布巾可以同挂一处且互不覆盖；真正被 `409` 明确拒绝的是同一
   `operation_key` 携带不同意图的重放（`OPERATION_KEY_CONFLICT`）。
5. 引擎既有行为：行程在中段因封路暂停时 `current_place_id` 为 `None`，投影的
   `carried_effective_location` 会回退到 `origin_place_id`（本例 `home`）。布巾自身仍在
   `hand_bag` 且 `carried_item_ids` 保留，本测试只断言真实携带事实，不改引擎既有投影。
6. 耐受盆栽允许 hydration 归零进入 `wilting_recoverable`，`never_dies_offline == True`；
   第 4–7 天未再浇水，因此收尾时仍是 wilting，属既定的“离线不死”语义。

## 二、§五 8 类故障验收（`TestCh46EightFaultScenarios`）

| # | 场景 | 真实路径 | 实测事实 |
| --- | --- | --- | --- |
| 1 | 拿到巾后移动受阻 | `start_journey(carry_item_ids)` + `set_roadblock` + `advance_v04_neighborhood_processes` | `hand_bag` 保持；`carried_item_ids` 含布巾；`paused_at_node` + `ROADBLOCK_ENCOUNTERED` + `blocked_route_id=open_shortcut`；`paused_walked_seconds=60`；绝不回 `home_rack` |
| 2 | 挂上巾已提交但响应丢失 | 同 `operation_key="fault_hang_lost_resp"` 重放 | 第二次 `already_applied=True`，`result` 与首次逐字相同；`drying_items` 仍 1 条；状态文件字节与修订号不变 |
| 3 | 同挂钩“同时占用” | 第二件布巾同挂 `porch_hook`；同 key 不同意图重放 | 引擎无支撑位容量 → 两件共存且先挂者 75.0 不被覆盖；同 key 不同 payload → `409 OPERATION_KEY_CONFLICT`，先落账者不动 |
| 4 | 晾到一半下雨 | 晴天 1h → `rain` → 2h → `clear` 至干 | 分段精确值 `75→47.0`（露天晴天）×`→92.0`（露天雨）→干透；`sleeping_dry=True`，`segment_count≥3`，绝不整段按晴天 |
| 5 | 关服务后重新启动 | `load_state_readonly` 全新读取 + 只读投影 | `drying_items` / `journey` / `revision` 逐字一致；只读观察零字节变化；半干时不出现“已干可收/已收回”候选（对象重建见 §六 Day 5） |
| 6 | 用户频繁刷新地图 | 20 轮 `read_v04_neighborhood_view` + `collect_*_loop_candidates` | 目录树（路径/字节/mtime）完全不变；`revision` 不递增；所有返回 `llm_calls == 0` |
| 7 | 观察权限被收回 | `has_subject_capability` 由 True 切 False + 真实 HTTP 业务函数 | 私人卡片 `draft_text=None / draft_withheld=True / withheld=True`；未共享小册 `withheld=True, entries=[]`；无旧缓存越权 |
| 8 | 世界已提交、后果重复投递 | 同 `operation_key` 的 `ADVANCE_CLOCK` 事件连续投递两次 | 首次 `APPLIED`；第二次 `already_applied=True`；世界状态字节与 `revision` 不变；后果账本仅 1 条、账本 `revision` 不再递增 |

## 三、§六 第 1–7 天连续隔离实例（`TestCh46SevenDayContinuousLife`）

同一个临时实例根目录、同一个 v0.4 状态文件；Day 1–4 共用 Epoch A，Day 5 真的
`stop()` + `runtime.close()` 后从磁盘重建 Epoch B，Day 6–7 继续用 B。实测状态轨迹：

| 天 | 动作 | 实测观察 |
| --- | --- | --- |
| 1 | 晴天居家；`Resident.tick()` 选中 `v04_courtyard_hang:` 挂到 `yard_line`；推进 8h；再 tick 选中 `v04_courtyard_collect:` | rev2 布巾 `yard_line / 75.0 / wet`；rev3 `5.0 / sleeping_dry=True`；rev4 收回 `home_rack / 5.0`；`llm_calls=0` |
| 2 | 旧布巾弄湿重挂；转 `rain` 后 tick 选中 `v04_courtyard_cloth:` 抢收 | rev6 `yard_line / 80.0`；rev8 `porch_hook / 52.0`；`len(drying_items)==1`，无第二条布巾 |
| 3 | 只推进世界时间（不传伴侣输入），tick 选中 `v04_porch_plant:` | 常春藤 `70→62→46→45→22`（`paused`）在 ≤35 门限出现浇水；浇水后 `67.0 / leafy`；纸张 `8+0+0+0 == 8` |
| 4 | 前往 `street_corner`，途中 `pause`；小册 `pause_booklet`；NO_ACTION tick | `paused_at_node / USER_PAUSED / paused_walked_seconds=60`；小册 `paused`；NO_ACTION 轮两者都保持，不被强行继续 |
| 5 | `stop()`+`runtime.close()`，同一目录重建新 cycle/resident | 构造本身零字节改动；`revision`、各地 `knowledge_status`、纸张五项、行程暂停断点、`operation_receipts`、小册状态逐项一致；NO_ACTION 轮零字节改动，无补演虚构事件 |
| 6 | 伴侣 `set_roadblock(open_shortcut)` + `post_public_notice(related_route_id=open_shortcut, claim=blocked)` | 亲历前：subject 视角 `roadblocks` 无该条、`known_blocked_to_subject=False`、告示 `body=None / body_withheld_until_read=True`；走近后 `ROADBLOCK_ENCOUNTERED` 并折返；tick 选中 `v04_public_notice:` 后 `read_by_subject=True`，正文可见 |
| 7 | 解除小册暂停；tick 选中 `v04_place_booklet:`；再来一轮 NO_ACTION | 小册 `active`，`entries=["yard"]`，`shared_with_partner=False`；partner 视角 `withheld=True`；NO_ACTION 轮 v0.4 状态字节零变化 |

七天收尾账（实测）：

- v0.4 状态 `revision=25`，`operation_receipts=7`（真实 owner 回执）。
- `drying_items` 始终 1 条；书桌材料 `paper 8 + scrap 0 + cards 0 + box 0 == total 8`。
- 书店 3 本、共享架 2 件，全程零增减。
- 产品大模型调用 **0 次**；认知全部是脚本化 0-LLM 替身（安静轮次可能不发起决策）。
