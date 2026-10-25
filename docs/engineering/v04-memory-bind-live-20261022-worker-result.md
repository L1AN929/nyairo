# 同实例 Memory 显式绑定 · 施工结果（2026-10-22）

工单：0 产品调用的同实例绑定，承接 `v04-memory-binding-prerequisites-20261022-parent-result.md`。
目标实例 `/root/nyairo-day-20261022`；只走既有 `scripts/phone.py enable-instance-memory`，
未改业务实现、未启动服务、未跑生活段、未调用产品模型。

**结论：绑定 READY，幂等成立，只读后验全部通过。** 这只证明接线与读健康：
resident 尚未重启加载新绑定，召回是否影响选择、G1、EP-09、跨备份恢复 FAIL 均未被本工单改变。

## 交付物

- 一次性验收脚本：`.agents/worker-runs/20261022-memory-bind-live-evidence.py`
  （`capture` 采集证据 / `--verify-only` 纯读验收；两者都只读实例，唯一写盘是 `--out` 证据）
- 证据（`.agents/worker-runs/20261022-memory-bind-live-*`）：
  `before.json`、`after.json`、`enable.json`、`enable-idempotent.json`、`status-{before,after}.json`、
  `preflight-{before,after}.json`、`memory-reads.json`、`verify.json` 及对应 `.log/.err/.exit`
- 本文件

## A. 前置安全核对（全部通过，0 变更）

| 命令 | 真实退出码 | 结果 |
| --- | --- | --- |
| `python -B scripts/day_capability_probe.py status --root /root/nyairo-day-20261022` | 0 | READY，`run:one-day`，`running_recorded=false`，lease FREE，Memory 状态字段固定 `UNBOUND`（status 不初始化 Memory） |
| `python -B scripts/phone.py life-preflight --instance /root/nyairo-day-20261022` | 0 | READY，`blocked=[]`，`run_ready=true`；checks：lease FREE、memory UNBOUND、supply/nested/permissions/switch/model READY，privacy/world UNKNOWN（与绑定无关） |
| 证据脚本 `capture --phase before` | 0 | `self_read_only=true`；目标实例活进程 0；serve marker 三个 pid 全不存在、端口 54795 未监听 |

现场路径（以 nested `instance.json` 实际记录为准，不按 audit 推断）：
`install=/root/nyairo-day-20261022/one-day/life`，`state=<install>/state`，
`memory=<install>/memory`（绑定前不存在）。

owner 依据 resident 既有优先级核实：`NYA02_MEMORY_SUBJECT` 缺失 → `NYA02_SUBJECT=chiyo` →
owner=`chiyo`；无已有显式 Memory 绑定，不存在冲突。Memory owner 未与手机 workspace subject 做强绑定。

预算与账本基线：`CHIYO_AUTONOMOUS_RUN_ID=run:one-day`；配置
`MAX_TOTAL_REQUESTS_PER_UTC_DAY=96`、`..._PER_RUN_WINDOW=96`；
`life_request_budget.json` 的 `active_day=2026-10-22`、`days=12`、`windows={run:one-day:12}`。
材料/Activity 基线记录在 `before.json`（artifact/workspace/world id、supply socket、status 摘要，
只存白名单字段与长度/摘要，不存正文）。整树 87 个文件记录 mode/size/mtime。

## B. 显式绑定

```bash
python -B scripts/phone.py enable-instance-memory \
  --instance /root/nyairo-day-20261022 --owner chiyo --operation-key memory-bind-20261022
```

真实退出码 **0**。回执：`ok=true`、`status=READY`、`created=true`、`reused=false`、
`processes_started=0`、`service_started=false`、`restart_required=true`、`owner=chiyo`、
`written=[memory/evidence.db, memory/controls.db, one-day/instance.json]`；
`health.status=READY`、`recall_ready=true`、`governance_healthy=true`。

备份：`one-day/instance.json-backup-20261022T152640672841Z`，与绑定前配置**内存逐值比较**：
environment 只新增 `NYA02_MEMORY_DB` / `NYA02_MEMORY_CONTROLS_DB` / `NYA02_MEMORY_SUBJECT`，
无删除、无改值；顶层非 environment 字段零差异。instance.json mode 保持 `644`；
`memory/evidence.db`、`memory/controls.db` 为 `600`，新建 `memory/` 目录为 `700`，无 `-wal/-shm/-journal`。

实例允许变化仅三处：上述备份、两个 DB、`one-day/instance.json` 内容（+3 环境名）。

## C. 幂等

同参数再执行一次，真实退出码 **0**：`ok=true`、`status=READY`、`created=false`、`reused=true`、
`backup=null`、`written=[]`、`processes_started=0`。整树快照（mode/size/mtime）与首次绑定后一致；
第二次调用没有新增备份、没有重写配置。

## D. 只读后验

- `status` 与 `life-preflight` 再次实跑均 exit 0：status READY、lease FREE、无运行记录；
  preflight `blocked=[]`、`memory.status=READY`、`recall_ready=true`、owner=`chiyo`，
  `NYA02_MEMORY_DB/_CONTROLS_DB/_SUBJECT` 三键 `bound=true, source=nested_life`。
- Memory service 四次只读（经实例真实绑定解析 `memory_service_from_env`，未构造 store、未 initialize）：
  `status=READY`（`recall_ready=true`，governance healthy）、`list count=0`、
  `context count=0`、`read_experience("memory-bind-live-missing-id")=NOT_FOUND`
  （`MEMORY_EXPERIENCE_NOT_RECALLABLE`）；三处均 `has_text=false`，证据里没有正文。
  空库只表示这个新绑定内没有可召回记录，不是“她没有过去”。
- 账本仍是 12：`run:one-day=12`、UTC day=12，配置 96/96 未变；同 run、同 Activity、
  同材料引用、同旧 START，无新模型调用。
- 无目标实例活进程，serve marker 无存活 pid，lease FREE，未启动任何服务。
- before→after 整树差异只有允许集合：`one-day/instance.json`（内容）、
  `one-day/instance.json-backup-*`、`memory/evidence.db`、`memory/controls.db`；
  其余文件 mode/size/mtime 全部不变。两次 capture 自校验 `self_read_only=true`。

## E. 父级独立验收命令

```bash
cd /mnt/d/nyairo-rc1-body-backend && set -a && source /root/.nyairo-model.env && set +a && \
PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B \
  .agents/worker-runs/20261022-memory-bind-live-evidence.py --verify-only \
  --root /root/nyairo-day-20261022 \
  --out .agents/worker-runs/20261022-memory-bind-live-verify-parent.json
```

纯读；ALL 33 项 checks 为 true 时 exit 0，否则非 0。施工时实跑：`PASS`、33/33、exit **0**。
父级如需单独复核两条只读命令（同样需继承模型环境，否则 preflight 的 model 行会因未配置而 BLOCKED）：

```bash
PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/day_capability_probe.py status --root /root/nyairo-day-20261022
PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/phone.py life-preflight --instance /root/nyairo-day-20261022
```

`verify-only` 检查项覆盖：两份 enable 回执（首次 created/READY、二次 reused/零写）、owner 解析、
DB 位于 install `memory/` 且分离、备份存在且仅三 Memory 键差异、顶层字段不变、
run/96/账本 12 不变、无活进程、serve marker 不活、lease FREE、status/preflight 除 Memory 行外不变、
Memory 四读形状、绑定前后树差异仅允许集合、capture 与 verify 自身只读、非 Memory 文件零变化。

## 未验证与边界

- resident 未启动/未重启，`restart_required=true` 如设计；**新绑定尚未被任何 resident 加载**。
- 未验证记忆召回是否进入候选、是否影响选择、同实例续接；绑定 READY 不覆盖这些。
- 未跑生活段、24h、全量套件、浏览器、旧绿测试；无真实产品模型调用。
- 跨备份恢复防复活仍是历史 `V_C_RESTORE=FAIL`，本工单不修复也不遮蔽；`understanding` 仍 UNKNOWN，G1 未闭合。
- 共同请求预算沿用历史口径：实例账本 12，加此前旧预检 1，共 13/96、余 83；本工单新增 0。
- 未提交、未推送、未部署；`.agents/worker-runs/` 证据按既有忽略规则不入库。
