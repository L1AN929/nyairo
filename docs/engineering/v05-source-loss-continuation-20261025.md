# v0.5 来源损失窄链：归档后不被偷换、旧草稿拒发（2026-10-25 单）

原 `20261025-v05-source-loss-continuation` 单 5400 秒超时 124、约定产物全无；本次是有界收窄补交，
只做原目标中**真实 owner 归档**这一条窄链，不是整单重来。

## 这条链实际跑通什么

隔离夹具，真实 Supply owner（AF_UNIX + 落盘 canonical db）、真实 M0 Memory、真实
ProductionLifeRuntime、真实 Sharing / Practice owner、AG-0/AG-1/AG-2/AR-0；Decision 与内容回答是脚本替身。

1. 正式 Decision 写出作品 A 的一个版本（v2 起，owner 结果身份与 step receipt 真实）。
2. 正式 Decision 从 A 的当前 head prepare 草稿 D：D 是另一件 artifact，精确绑定那一版。
3. 另建可续作品 B（owner `create_artifact`），既有轮选/绑定 `work_artifact_id` 指 B。
4. 同一真实 owner 合法 `archive_artifact` A：`status=OK / APPLIED`，`lifecycle=ARCHIVED`，
   版本、head、正文全部保留 —— 归档不是删除。revision 由 owner 自己的 CAS 前置值给出。
5. 下一正式请求仍由当前 Activity 点名 A，CONTINUE 候选仍在；选它触发既有具名拒绝
   `AutonomousLifeError: WORK_NOT_CONTINUABLE: artifact:<id>`，轮次记 `DECISION_FAILED`。
   A/B 版本、执行 receipt、新 handoff 均不增，**没有 fallback 到可续的 B**。父级日志该轮 `decision=None`，不能说 AG-1 已提交 CONTINUE 后由 owner 拒绝；只证正式 cycle 对候选点名作品具名拒绝，不据此声称该拒绝轮生成了完整 AG-1 请求。
6. D 的来源 `source_current`：`state=ARCHIVED`、`readable=true`、`stale=true`、
   `reason_code=SOURCE_ARCHIVED` —— 归档后仍可读，不是 deleted/withheld。
   正式 NO_ACTION 附 `send_local` D，owner 拒 `stale_source / SOURCE_ARCHIVED`；
   D 的 head/state/receipt 与 A 一字未变。
7. 下一无 sharing 的合法 NO_ACTION 零写：不自动 drop/rebind/send，不强制放弃或切 B。

以上全部沿用既有 owner / cycle 规则，本单**没有改动业务源码、原测试或 schema**，也没有新增
治理规则。`read_draft` 的投影只带 readable/stale/matches/reason，ARCHIVED 状态取自 owner 自己的
`read_share`，不靠投影补字段。

## 明确 NOT_RUN（不是通过）

- **来源不可读**（治理/撤权注入、正文扣留）：本窄链未跑，不并入本条结论。
- **方法证据组合**（结果 ↔ 方法评价/退休的交叉）：未跑。
- 崩溃/强杀恢复、完整 resident、真实模型自然采用、用户收到、G1、V-C、24h：均未证。
- 草稿 CAS 冲突、同一意图重放等属前几块已交范围，本单不重跑。

## 证据

- `scripts/v05_source_loss_continuation_smoke.py`：B，15 checks PASS，exit 0；
  关停走既有 drain + `prepare_socket_path` 守卫（`drained=true`，`STALE_SOCKET_REMOVED`），无裸删。
- `tests/test_v05_source_loss_continuation.py`：A，`Ran 1 test in 11.995s / OK`，exit 0。
- 父级独立复跑：A `Ran 1 test in 8.443s ... OK` / `EXIT_A=0`；B 15 checks PASS / `EXIT_B=0`。日志 `.agents/worker-runs/20261026-v05-source-loss-parent-a.log`、`20261026-v05-source-loss-parent-b.log`；上列11.995秒为worker先前结果，不混同父级。
- worker自述“00:44、超交盘线4分钟”不采：父级现场00:20:11，交付日志已于00:19:46落盘。只能确认00:20前已交付，mtime不用于还原每条命令。
- 0 产品模型调用；共同预算沿用 85/300（余 215），本单不新增。
