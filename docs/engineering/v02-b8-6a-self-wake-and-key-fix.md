# v0.2 B8-6A：自我唤醒根因修复与密钥暴露修复

本批起点 `5a610a6`，分支 `v0.2-autonomous-life`。只做两件事，不扩到 B8-7。

## 1. 根因：`note_own_write()` 存在，但活路径从不调用它

`ArtifactChangeFeed` 早就准备好了"这个 head 是我们写的"的接口：

```python
def note_own_write(self, artifact_id: str, head_version_id: str) -> None:
    """Record a head *we* wrote, so our own edit is not an event to ourselves."""
```

但**没有任何活路径调用它**（只有 B8-1 的单元测试直接调过）。于是 Feed 的游标只在 poll 时前进：

```
poll 记住 V1 → 她提交 V2 → 下一次 poll 看到 V2 ≠ V1 → 一个关于她自己作品的唤醒
```

B8-6 长测里 4 次自主写入正好对应 4 次 `ARTIFACT_CHANGED`（`owner_confirmed_versions 4` / `real_events 4`），
只被每日轮次预算截断（`ROUND_BUDGET_EXHAUSTED`）。这不是"防不住"，是**接线漏了一根**。

## 2. 修复（最小改动，复用既有真实来源标识）

* `autonomous_life_cycle.py`：新增可选回调 `own_write_recorder`；在 owner 确认版本的地方
  （`_record_artifact_progress`，拿到的是 owner 回执里的 `artifact_id` 与 `version_id`）调用它。
  回调失败只记录 `own_write_record_error`，不吞不抛。
* `autonomous_life_assembly.py`：先建 `ArtifactChangeFeed`，再把它接到 cycle 上：

```python
artifact_feed = events.ArtifactChangeFeed(port, root, subject_id=supply["subject_id"])
cycle.own_write_recorder = artifact_feed.note_own_write
```

**没有屏蔽整份作品的后续变化**：别人的版本与游标不同，仍然照常唤醒；LR-4 的合法恢复机会完全没动；
千代以后仍能通过正常恢复条件继续创作。

| 情况 | 行为 |
|---|---|
| 本次自主 Action 确认提交的版本 | 更新观察游标，**不**产生新的自主唤醒 |
| 其他合法写入者提交的新版本 | 仍产生事件（游标不同） |
| UNKNOWN 操作 | 仍按原 execution_id 与 owner 对账，不当成功也不当外部变化 |
| 重启后 | 游标已持久化，先前自己的提交不会被当成新事件 |

## 3. 验收证据

### 3.1 确定性（`components/check_life_b8_6a.py`，新套件 `life-b8-6a`，`B8_6A_CHECK: PASS`）

```
[1] her own commit is observed, and wakes nobody            ok
[2] another writer's version is exactly one event           ok / ok
[3] repeated polling adds nothing                           ok
[4] a restart does not re-wake for the same version         ok / ok
[5] the LR-4 resume-eligibility feed still wakes            ok / ok
[6] the loop no longer depends on the budget to stop        ok
[7] negative control: without the recorder the same sequence wakes   ok
[8] the live wiring exists (drift guard)                    ok / ok
```

第 [7] 项是**阴性对照**：同一序列去掉 recorder 后**必须**产生唤醒 —— 这正是"这是修复而不是巧合"的证据。

### 3.2 真机端到端（隔离 profile `chiyo-soak2`，全部三个 Feed 就绪）

| 观测 | 修复前（`chiyo-soak`） | 修复后（`chiyo-soak2`） |
|---|---|---|
| 自主写入次数（owner 确认） | 4 | 2（随后外部写入又触发 1 轮 = 3） |
| 由自己写入产生的 `real_events` | **4** | **0** |
| 外部合法写入者提交 V4 | —— | `real_events: 1`、`event_wakes: 1`（**恰好一次**） |
| 轮次结束后 | 靠预算耗尽才停 | 自己安静下来（`silence` 增长，无自驱级联） |
| 作品 | v1 → v5 | v1 → v3（真实正文） |

修复后计数：`model_calls 2`、`confirmed_versions 2`、`real_events 0`；外部写入后 `real_events 1` / `event_wakes 1`。

## 4. 密钥暴露修复

`run_model_e2e.sh` 与 `run_b8_causal.sh` 原先用 `runuser … env NYA02_MODEL_API_KEY=… python3`，
密钥落在**进程命令行**上，`ps` 对全机可见。改为与常驻进程一致的方案：

* launcher 把四个 `NYA02_MODEL_*` 写进 **0600 文件**（`$ROOT/model_env`，服务身份所有），只传路径
  `NYA02_MODEL_ENV_FILE`；
* `autonomous_life_assembly.load_model_env_file()` 由 harness 自己读取并填入 `os.environ`
  （已有值优先，不覆盖操作者显式导出的变量）；
* 调用方式与测试兼容性不变：`EXPERIMENT_B: PASS`（`rc=0`）在改后照常通过。

验证（不输出密钥本身）：

```
$ grep -n 'NYA02_MODEL_API_KEY="$' run_b8_causal.sh run_model_e2e.sh
no key assignment in either env list
$ ls -l /srv/nya02/model_env
-rw------- 1 nyasvc nyasvc 172 …
```

密钥没有进入日志、没有进入 Git（`model_env` 在 `/srv` 下，不在仓库里）。
**旧密钥的实际轮换与撤销由账户持有人在服务商后台完成**，本批没有使用任何账户操作，也没有要求把新密钥发进聊天。

## 5. 长测处理

* 修复期间**已暂停**新的自主写入（两个常驻进程都已 `STOPPED`）。
* 已有作品、Activity、Memory 与全部历史 journal **原样保留**（`/srv/nya02/resident/`、`/srv/nya02/resident2/`）。
* 修复前那段记录**保留为有效的故障发现证据**，不与修复后的时段拼接成一段"完整无故障长测"。
* 修复后的 24 小时观察窗口从本批之后重新开始记录；`soak_24h` 现在应重新计时。
* 7 天自然观察仍标记 `SOAK_PENDING_7D`。

## 6. 回归与提交

`python3 scripts/test.py`：closeout / native / memory / supply / world / life / life-v02（`MEMORY_CHECK: PASS`）/
life-memory-v02 / life-b7 / life-b8（`B8_CHECK: PASS`）/ life-b8-causal（`B8_CAUSAL_CHECK: PASS`）/
**life-b8-6a（`B8_6A_CHECK: PASS`）** 全部通过。

`FAILED_SUITES: ['host-boundaries', 'cognition-faults', 'Hermes']` —— 与 B8-6 完全一致，仍是既有环境问题
（缺 pytest 虚拟环境 / 安装器与文件系统安全测试 / vendor 测试运行器），**如实保留**，未顺手重构。

本批未修改 Memory 治理、聊天人格或发布文档。真实模型调用：本轮 **2 次**（修复后的自主写入验证）。
