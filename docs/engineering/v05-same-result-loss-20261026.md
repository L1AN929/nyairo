# 同一结果作品——方法证据——草稿的来源损失（2026-10-26）

## 一句话

在同一个全新隔离 root 里，用同一个真实 Supply 作品 A 的结果 R 串起三条既有路径
（owner 结果身份 / M0 经历与真实 Practice 评价 / Sharing 草稿来源），再只对 A 的
**来源读**注入既有故障形状，验证正文扣留、发送拒写、方法域不被牵连，最后用同一
owner 的 `archive_artifact` 结束 A，验证不 fallback、旧草稿拒发、方法不自动退休。

本次没有新增业务源码，只有新测试、新冒烟、本说明与两篇既有笔记的原地补充。

## 短链（每步都是正式入口）

| 步骤 | 入口 | 结果 |
| --- | --- | --- |
| 1 | 夹具自己的正式 Decision `evaluate_once` → AG-0/AG-1/AG-2/AR-0 | 真实写出作品 A 的版本 R；owner 回执/结果身份/可回忆经历；同一轮 `method_proposal` 存成 TENTATIVE |
| 2 | 正式 `evaluate_once`（NO_ACTION + `method_ref` + `method_trial`） | 只用对应 R 的可读经历记 HELPED，真实 Practice owner 推进 TRIAL |
| 3 | 正式 `evaluate_once`（NO_ACTION + `sharing.prepare`） | 从本轮实际请求的 `current_work` 来源显式 prepare D；D 是另一件 artifact，绑定 A/R |
| 4 | 正式 `evaluate_once`（纯读） | 同一请求带到 D、方法行与显式评价；身份仍是 A/R |
| 5 | 只对 A 的来源读注入 `SourceRefusingPort`（cycle 实际持有的同一个 Sharing owner） | D 正文扣留 `SOURCE_GOVERNED`；sentinel 不在请求/bounded/practice/owner 读回；方法域仍 TRIAL |
| 6 | 正式 `evaluate_once`（NO_ACTION + `send_local` D） | owner 拒 `stale_source` + `SOURCE_GOVERNED`，零写 |
| 7 | 解除注入后正式纯读 | 正文恢复；A/R/D 绑定不变；零自动 send/drop/rebind |
| 8 | 同一 owner `archive_artifact` A，另建可续 B | A 版本/head/正文保留；正式 CONTINUE A 在 AG-1 前 `WORK_NOT_CONTINUABLE`/`DECISION_FAILED`，无版本/receipt/新经历，不 fallback B |
| 9 | 正式纯读 + `send_local` D（来源 ARCHIVED） | 来源仍可读但标 ARCHIVED；发送拒 `SOURCE_ARCHIVED`，零写；方法仍 TRIAL |
| 10 | 最后合法无 sharing 的 NO_ACTION | 零写、不强制切事/退休/复活 |

## 身份对照（本次实际运行的一段，安全 ref 只有 id）

```
work_artifact   artifact:1b7392dbda9f48018706b356718b6066
result_version  artifact-version:87905d45c95e4b9e8d5b32705c648a9f
result_ref      artifact:1b7392dbda9f48018706b356718b6066
                @artifactversion:artifact-version:87905d45c95e4b9e8d5b32705c648a9f
experience      experience_id 01a1758b-496b-78ed-963a-6a9d10abf747
                artifact_ref  artifact:1b7392dbda9f48018706b356718b6066
                version_ref   artifactversion:artifact-version:87905d45c95e4b9e8d5b32705c648a9f
method          artifact:7ca5253230ad435cae8bbe7638db3b04 (method:2007f2fb4a8ec683)
                trial.version_id artifact-version:14a5bb4cc8374afcade8bc3497774e97
                state_after TRIAL
draft           artifact:3648e5b8dbb646d5a96ebe06aad4c43b
                source.artifact_id artifact:1b7392dbda9f48018706b356718b6066
                source.version_id  artifact-version:87905d45c95e4b9e8d5b32705c648a9f
```

要点：

- **同一 A/R**：receipt/结果身份、Memory 经历的 `artifact_ref`/`version_ref`、
  草稿 D 的 `source.artifact_id`/`version_id` 逐一相等；不是标题或子串匹配。
- **两个版本域分开**：`trial.version_id` 是受评**方法**版本
  （`artifact-version:14a5…`），不等于作品版本 R（`artifact-version:8790…`），也不等于
  方法当前 head（记录试用后方法又前进了一版）。
- **owner_result 是另一条 ref**：M0 行里的 `owner_result` 是 `rset:…` 形式的结算 ref，
  不与结果身份 `artifact:…@artifactversion:…` 混为一谈；本单只核它非空。

## 真实件与替身

真实：Supply owner（自有 AF_UNIX + 落盘 canonical）、M0 Memory（EvidenceStore +
controls）、ProductionLifeRuntime、Practice owner、Sharing owner、AG-0/AG-1/AG-2/AR-0。
替身：Decision 原始 JSON（按计划回显本轮请求里的 id/CAS head）与内容回答；"来源不可读"
是端口级故障注入（`SourceRefusingPort`），明确不是实际撤权，也不动 Memory controls/
删除。

## 实际命令与结果

WSL `/mnt/d/nyairo-rc1-body-backend`，
`PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B`：

- `-m unittest -q tests.test_v05_same_result_loss` → `Ran 1 test in 15.321s` / `OK`，exit 0。
- `scripts/v05_same_result_loss_smoke.py` → 41 项 CHECK 全 PASS，`RESULT: PASS`，exit 0。

父级独立复跑：A `Ran 1 test in 11.522s` / `OK` / `EXIT_A=0`；B41项全部PASS / `EXIT_B=0`。日志为 `.agents/worker-runs/20261026-v05-same-result-loss-parent-a.log`、`20261026-v05-same-result-loss-parent-b.log`，stdout/stderr分区保存。受限回合直接观测方法行TRIAL、evaluation=true，非条件空真；归档拒绝轮decision=None，不扩大为AG1已提交CONTINUE。上列15.321秒为worker结果，不混同父级。

冒烟收尾走生产顺序 `shutdown → server_close → drain → prepare_socket_path` 守卫，
只停本单自有服务，临时数据保留。

## 剩余与边界

- 只证明同一隔离 root 里的机制链；Decision/内容是脚本替身，不证真模型自然采用。
- 来源拒读是端口注入，不是完整治理撤权、删除、跨备份恢复或真实用户撤权。
- 归档只动既有 owner lifecycle；不证明真实居民循环、G1、V-C 恢复、understanding 或 24h。
- 不 fallback 由 `WORK_NOT_CONTINUABLE`/`DECISION_FAILED` 的分层体现，没有已提交
  CONTINUE 后再拒的证据。
- 00:40 夜间交接里“同一结果组合未交”的缺口由本文件关闭；archive/source-domain
  两个窄链仍是独立证据，不因此拼成整章或 G1 通过。
