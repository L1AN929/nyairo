# Agent Note: 一条命令开始她的日常 —— 用户入口只做薄封装

Status: implemented

## Problem

已有 `prepare_demo`、`fast_day.py`、`live_day.py`、`usable_day.py`、`phone.py`、`phone.life`
等开发入口，但"让一个新人把她的日常跑起来"仍要求自己拼：先建实例、再绑 Memory、再起
Supply/World、再起手机面、然后另开命令跑 resident；失败原因也散落在英文 reason code 和
日志里。`usable_day.py` 的 4 次共享上限是**旧实例**的受控窗口，不适合一个全新实例；直接改它
会改变既有规则。

## Decision

新增用户入口 `scripts/nyairo_day.py` + 一页说明 `docs/DAY_START.zh-CN.md`，全部复用既有机制：

1. **`init` / `start`（目录不存在时）** 调 `phone.demo.prepare_demo(with_world=True)`，不预置
   合成阅读材料和占位作品（`reading_content=None`、`seed_work_artifact=False`）；随后调既有
   `phone.life.enable_instance_memory`。删除底账没有默认值，这个入口把默认位置算在实例目录之外
   的同级目录（`<root>.parent/<name>-memory-authority/authority.db`），保留 V-C 的边界：
   同机换目录不等于抵抗整机回滚。实例建好后写 `run/nyairo-day.instance.json` 归管标记；
   没有这个标记的已有实例一律拒绝（不接管、不迁移、不替它新建 Memory）。
2. **`start` 默认 0 产品**：只调 `demo.serve_instance`（Supply/World + loopback HTTP），
   打印“服务就绪，尚未开始自主活动”，不启动 resident、不发模型请求、不改 World 写门。
3. **`run-day` 是唯一会花额度的命令**：必须显式 `--live-model`，且必须给
   `--duration-seconds` 与 `--max-new-requests`；复用 `phone.live_day.run_segment`，预算沿用
   实例自己的持久账本，不重置、不换 run、不隐藏重跑。
4. **`status` / `phone` / `import` / `stop`**：`status` 用中文汇总既有 `living_view`、
   today、arrangements、projects 与持久账本；`phone` 读 AI 手机的 today/arrangements/作品
   读口；`import` 用真实 Supply owner 建件并回读后写三处阅读绑定（不强迫她读）；`stop` 只停
   本入口自己记录并核对过 argv 的 pid。失败 reason code 翻成人话，同时保留原因代码与日志路径。
5. **窄接线**：`server.create_server` / `demo.serve_instance` 增加 `reuse_address`（默认
   `False`，旧调用方行为不变），只有本入口传 `True`，使“停止后立刻用同一端口重新打开”不被
   TIME_WAIT 卡住；`SO_REUSEADDR` 不会让第二个活着的 listener 抢同一端口。

## Alternatives considered

- **直接扩展 `usable_day.py`** —— 否决：它的 4 次共享窗口上限是旧实例的受控规则，任务明确
  不改；新实例应该走自己的持久账本。
- **另建网页/桌面应用** —— 否决：任务是“一条命令 + 一页说明”，页面不是必需品，且会引入新的
  运行平台与前端契约。
- **`start` 默认跑 resident** —— 否决：会在用户没看见的情况下花模型额度；默认启动必须 0 请求，
  并把“她还没开始生活”说清楚。
- **让 `today`/`arrangements` 在全新实例上返回空成功** —— 否决：那会把“活动权威文件还没产生”
  伪装成“她今天什么都没做”，违反既有 today 的语义；保持具名 `CANONICAL_STATE_ABSENT`。
- **为 Memory 底账再设一个实例内默认值** —— 否决：会跟实例自己的备份/恢复一起回滚，等于取消
  V-C 刚交付的域外边界。

## Consequences

- 新实例只需 `start` 一条命令即可看到手机、Supply/World 与 Memory；默认不花额度。
- 真正的一天仍需显式 `run-day`，模型未配置时按既有 `NO_MODEL_PROVIDER` 拒绝且 0 请求。
- 刚建好还没跑过一天的实例，`today`/`arrangements` 会具名 503（`CANONICAL_STATE_ABSENT`），
  `projects` 与阅读材料读口正常；这不是失败，也不会被写成“今天没事”。
- 验证：`tests/test_nyairo_day_entry.py` 7 例；WSL 新根实跑 `start`（自动 init）、`status`、
  `phone`、`import`（owner 回读）、`stop`→同根 `start`（材料/run/账本不变），详见
  `.agents/worker-runs/20261026-simple-launch-result.md`。未跑真实模型日，未提交/部署。
- 2026-10-27 增补：`init` / 首次 `start` 可显式选 `--world-package` 与 `--lived-needs`，
  `start` 另有按进程树的 `--room-living` / `--world-actions`；创建选择固定在实例 marker，
  已有 root 换包/改初值会返回具名原因要求新目录。见
  [日常入口街区与身体](2026-10-27-living-entry-neighborhood-and-body.md)。
