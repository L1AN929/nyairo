# 同实例 Memory 显式绑定 · 父级独立验收（2026-10-22）

结论：**实际绑定完成，父级只读验收通过。** `/root/nyairo-day-20261022` 的 Memory 由未绑定变为 READY。没有运行 resident，不能宣称新绑定已被生活进程加载，更不代表记忆影响选择或 G1 通过。

## 实际操作与写面

用户明确“继续 nyairo”，依 rev95 下一步完成一个含前置核对、绑定、幂等、验收与报告的工单。
唯一 worker 通过 `scripts/dispatch_worker.py` 直接后台执行，任务 `.agents/worker-runs/20261022-memory-bind-live-task.md`，200轮/1200秒/无回退；后台 blp7fbc3l，exit0，模型 workbuddy-wg/cn:deepseek-v4.1-flash，日志 `20261022-232209-memory-bind-live.log`。

worker 使用既有 `phone.py enable-instance-memory`，owner 按现场 `NYA02_SUBJECT=chiyo` 确定，不与手机 workspace subject 强制相等。首次回执 READY/created=true/restart_required=true，二次 READY/reused=true/written=[]，均shell0。

实例实际允许变化：one-day/instance.json 只新增三个 NYA02_MEMORY 名字、一份入口备份、install/memory 下 evidence.db 与独立 controls.db。备份名 `instance.json-backup-20261022T152640672841Z`。原配置644，DB600，目录700；未手工改权限，无sidecar。除此之外实例文件 mode/size/mtime 未变。

仓库交付只有本单证据/一次性验收脚本、worker报告及父级管理文档；未改业务源码。既有整体 dirty 保留，父级 diff --stat 是累计工作树范围，不能当作本工单逐字节零越界证明；暂存区为空，未提交推送部署。

## 父级独立实跑

WSL `/mnt/d/nyairo-rc1-body-backend`，只 source `/root/.nyairo-model.env` 到进程，不输出密钥；Python `/tmp/rc104-venv/bin/python -B`、PYTHONDONTWRITEBYTECODE=1。

1. `.agents/worker-runs/20261022-memory-bind-live-evidence.py --verify-only --root /root/nyairo-day-20261022 --out .agents/worker-runs/20261022-memory-bind-live-verify-parent.json`：真实shell0，2026-10-22T15:30:11Z，PASS，33/33 checks=true，failures=[]。
2. `scripts/day_capability_probe.py status --root /root/nyairo-day-20261022`：真实shell0，READY、run:one-day、running_recorded=false、lease FREE、model_called=false。安全输出及退出码保存 status-parent.json/.exit。
3. `scripts/phone.py life-preflight --instance /root/nyairo-day-20261022`：真实shell0，READY、blocked=[]；memory READY、owner chiyo、recall_ready=true、participation BOUND_READY_READONLY_VERIFIED。保存 preflight-parent.json/.exit。

父级没有重跑 enable 或幂等调用，没有启动服务，没有跑会自起Supply的材料verify，没有跑旧绿套件/全量/浏览器。

33项包括：首次/幂等回执、备份存在/仅三环境名差异、其他配置不变、实际路径在实例install内且两个DB分离、run和配置96/96不变、账本仍12、无活进程/租约FREE、非Memory文件不变、当前读前后快照不变。
Memory service实际四读：status READY，list/context count=0，无正文；missing-id NOT_FOUND。空库只表示新绑定内暂没有可召回记录，不代表没有过去。

## 报告差异与验证上限

- `day_capability_probe status` 的 Memory 摘要仍固定 UNBOUND。它与现场 life-preflight/service READY 不一致，不能用此固定摘要否定实际绑定，也不能称所有状态面已修好；本工单不改摘要代码。
- worker开头说“独立验收”只代表其自测；父级结论以以上独立运行结果为准。
- 一次性证据脚本给部分活动状态使用 SHA256 截短摘要，超出“不为证明完整性算SHA”的任务约定；没有新增产品校验或改业务路径。记录此偏差，不把该摘要当因果或完整性证明。配置备份的全字段比较与文件mode/size/mtime检查仍有各自检查项。
- 文件树快照覆盖文件mode/size/mtime，不是所有文件内容的逐字节证明；二次调用零变化依worker前后快照与回执，父级不重放写操作。

## 停点

本工单结束。实例12次 + 历史旧预检1次 = 共同13/96，余83，本工单新增产品调用0；配置96/96未改变、同run，无活resident/服务、lease FREE。

下一步按 NOW 第六节进入 G1 逐场景，先做中断/接续的有界行为工单；将新绑定的实际runtime加载证据纳入同一场景，不再单拆前置工单。不重放旧START、不prepare/reset/新run、不灌合成经历，不以延长时长凑验收。需改契约/删数据/超额度则停止。

未验证：resident加载/真实记忆参与、历史影响选择、G1五场景闭合、跨备份恢复安全、24h。V_C_RESTORE=FAIL、understanding UNKNOWN保留，EP-09整体未完成。
