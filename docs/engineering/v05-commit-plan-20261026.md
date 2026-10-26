# v0.5未提交树：提交方案（父级核对版）

**推荐：十个逻辑组供审阅，实际四次提交。现在不直接执行：先处理公开文档证据链接与本机信息审阅。** 本会话未stage、commit或push。

基线HEAD `1057bad9d449f0458358974f2a4e5f94219ad6a3`，分支 `work/rc1-phone-world-body`；父级核暂存区为空。

## 为什么不是十个独立功能提交

cycle同时含评价回读、结果身份和低预算计数；两个note跨多块。更关键的是结果身份冒烟直接import新续接夹具，低预算冒烟直接import新分享/重启夹具。worker首版把这些分在后面的提交，会让前面的测试缺文件，父级已否决该顺序。

因此C1把全部8个业务文件、相关测试/夹具/notes/行为说明放到同一树；C2、C3、C4只分方案、真实日和状态文档。不是重新发明独立中间版本，也没有拆hunk或改写业务文件。最终工作树的已有验收不自动证明每个中间提交都绿；本单没有再跑产品测试。

## 执行前停止门

- 4篇候选文档有13条真正的Markdown链接指向被忽略的worker-runs；另外一些文档只有行内本地记录名。前者随源码分发会成为缺失目标；不能用force-add整份日志或私密JSON解决。请先经审阅改为本地证据说明或公开安全摘要；本单没有改原文档。
- NOW及少数文档/note含本机绝对路径、历史实例根和网关描述。不是据此判定泄密，但永久公开前应由提交者审阅/脱敏；行号会随NOW更新改变。
- 有限凭证规则未发现高置信命中，不等于语义层私文或完整prompt已经审完；尤其真实日和历史交接文档不能只凭正则放行。
- 暂存区若不是空，或HEAD/工作树成员发生未解释变化，停止重核白名单；不要覆盖他人的stage。

### 被忽略证据的可点击链接位置

- `docs/engineering/v05-results-expression-implementation-plan-20261025.md:99`（只定位，不搬日志）。
- `docs/engineering/v05-results-expression-implementation-plan-20261025.md:99`（只定位，不搬日志）。
- `docs/engineering/v05-results-expression-implementation-plan-20261025.md:99`（只定位，不搬日志）。
- `docs/engineering/v05-restore-authority-options-20261026.md:73`（只定位，不搬日志）。
- `docs/engineering/v05-chapter18-scope-20261025.md:73`（只定位，不搬日志）。
- `docs/engineering/v05-chapter18-scope-20261025.md:73`（只定位，不搬日志）。
- `docs/engineering/v05-chapter18-scope-20261025.md:76`（只定位，不搬日志）。
- `docs/engineering/v05-chapter18-scope-20261025.md:76`（只定位，不搬日志）。
- `docs/engineering/v05-chapter18-scope-20261025.md:76`（只定位，不搬日志）。
- `docs/engineering/v05-fastday-m-20261026-result.md:31`（只定位，不搬日志）。
- `docs/engineering/v05-fastday-m-20261026-result.md:31`（只定位，不搬日志）。
- `docs/engineering/v05-fastday-m-20261026-result.md:31`（只定位，不搬日志）。
- `docs/engineering/v05-fastday-m-20261026-result.md:31`（只定位，不搬日志）。

## 十个逻辑组与完整文件清单

同一文件可在多个逻辑组说明，但物理提交只归属一次。每条路径均相对本worktree。

### G1 · 有界评价回读与分享表达视图（含 truncated）

建议逻辑提交信息：`feat(v0.5): 方法评价与来源表达进入决策视图并如实上报截断`

- [业务源码 / C1] `components/life/scripts/agency_cognition_adapter.py`
- [业务源码 / C1；共享逻辑文件] `components/life/scripts/autonomous_life_cycle.py`
- [业务源码 / C1] `components/life/scripts/life_practice_context.py`
- [业务源码 / C1] `components/life/scripts/life_sharing_context.py`
- [业务源码 / C1] `components/life/scripts/life_sharing_service.py`
- [测试 / C1] `tests/test_v05_results_expression_views.py`
- [测试 / C1] `tests/test_result_method_behavior.py`
- [测试 / C1] `tests/test_v04_sharing_life_chain.py`
- [冒烟/夹具 / C1] `scripts/v05_results_expression_smoke.py`
- [决定笔记 / C1] `.agents/notes/implemented/feature/2026-10-25-v05-results-expression-views.md`
- [决定笔记 / C1；共享逻辑文件] `.agents/notes/implemented/architecture/2026-11-12-v04-practice-life-review-and-method.md`
- [决定笔记 / C1；共享逻辑文件] `.agents/notes/implemented/architecture/2026-11-14-v04-body-participation-and-bounded-sharing.md`
- [文档 / C1] `docs/engineering/v05-results-expression-implementation-plan-20261025.md`

### G2 · 评价承接到同一作品后续实践

建议逻辑提交信息：`test(v0.5): 评价承接到同一作品后续实践的正负控`

- [测试 / C1] `tests/test_v05_practice_continuation.py`
- [冒烟/夹具 / C1] `scripts/v05_practice_continuation_smoke.py`
- [决定笔记 / C1] `.agents/notes/implemented/feature/2026-10-24-loss-to-continuation-owner-bridge.md`
- [决定笔记 / C1] `.agents/notes/implemented/feature/2026-10-24-past-to-candidate-owner-bridge.md`
- [文档 / C1] `docs/engineering/v05-practice-continuation-20261025.md`

### G3 · 进度交接的结果级身份

建议逻辑提交信息：`feat(v0.5): 进度交接改用 owner 确证的结果级身份`

- [业务源码 / C1] `components/life/scripts/life_integration_ag2.py`
- [业务源码 / C1；共享逻辑文件] `components/life/scripts/autonomous_life_cycle.py`
- [测试 / C1] `tests/test_v05_progress_result_identity.py`
- [冒烟/夹具 / C1] `scripts/v05_progress_result_identity_smoke.py`
- [决定笔记 / C1] `.agents/notes/implemented/architecture/2026-10-25-v05-progress-result-handoff-identity.md`
- [决定笔记 / C1] `.agents/notes/implemented/feature/2026-10-27-v04-memory-product-service.md`
- [文档 / C1] `docs/engineering/v05-progress-result-identity-20261025.md`

### G4 · 真实结果承接到有限本地表达

建议逻辑提交信息：`test(v0.5): 结果换版后旧草稿拒发与新草稿单次本地投递`

- [测试 / C1] `tests/test_v05_result_sharing_continuation.py`
- [冒烟/夹具 / C1] `scripts/v05_result_sharing_continuation_smoke.py`
- [决定笔记 / C1] `.agents/notes/implemented/feature/2026-10-25-v05-result-sharing-continuation.md`
- [决定笔记 / C1] `.agents/notes/implemented/feature/2026-11-14-v04-life-sharing-service-local-inbox.md`
- [文档 / C1] `docs/engineering/v05-result-sharing-continuation-20261025.md`

### G5 · 同根跨进程重启续接

建议逻辑提交信息：`test(v0.5): 同根三进程停启后同 Activity 续作且不重放`

- [测试 / C1] `tests/test_v05_restart_continuation.py`
- [冒烟/夹具 / C1] `scripts/v05_restart_continuation_smoke.py`
- [文档 / C1] `docs/engineering/v05-restart-continuation-20261025.md`

### G6 · 来源损失（archive / source-domain / same-result）

建议逻辑提交信息：`test(v0.5): 归档、来源域与同结果三种来源损失的分支负控`

- [测试 / C1] `tests/test_v05_source_loss_continuation.py`
- [测试 / C1] `tests/test_v05_source_domain.py`
- [测试 / C1] `tests/test_v05_same_result_loss.py`
- [冒烟/夹具 / C1] `scripts/v05_source_loss_continuation_smoke.py`
- [冒烟/夹具 / C1] `scripts/v05_source_domain_smoke.py`
- [冒烟/夹具 / C1] `scripts/v05_same_result_loss_smoke.py`
- [文档 / C1] `docs/engineering/v05-source-loss-continuation-20261025.md`
- [文档 / C1] `docs/engineering/v05-source-domain-20261026.md`
- [文档 / C1] `docs/engineering/v05-same-result-loss-20261026.md`

### G7 · 到期重新考虑（改期时钟 + 挂起同目标不开第二活动）

建议逻辑提交信息：`feat(v0.5): 到期事项按回合时钟改期且不为挂起同目标再开活动`

- [业务源码 / C1] `components/life/scripts/life_future_items.py`
- [业务源码 / C1] `components/life/scripts/artifact_continuation_source.py`
- [测试 / C1] `tests/test_v05_due_reconsideration.py`
- [测试 / C1] `tests/test_v05_due_parked_target.py`
- [冒烟/夹具 / C1] `scripts/v05_due_reconsideration_smoke.py`
- [冒烟/夹具 / C1] `scripts/v05_due_parked_target_smoke.py`
- [决定笔记 / C1] `.agents/notes/implemented/architecture/2026-10-26-v04-day-life-core-continuity.md`
- [决定笔记 / C1] `.agents/notes/implemented/architecture/2026-11-02-v04-autonomous-day-later-and-multi-project.md`
- [决定笔记 / C1] `.agents/notes/implemented/feature/2026-10-24-eligible-parked-candidate-supply.md`
- [文档 / C1] `docs/engineering/v05-due-reconsideration-20261026.md`
- [文档 / C1] `docs/engineering/v05-due-parked-target-20261026.md`

### G8 · 技法多情境 / 反例 / 修订 / 退休

建议逻辑提交信息：`test(v0.5): 技法多情境反例与修订退休跨两个真实作品`

- [测试 / C1] `tests/test_v05_practice_counterexample.py`
- [冒烟/夹具 / C1] `scripts/v05_practice_counterexample_smoke.py`
- [决定笔记 / C1] `.agents/notes/implemented/feature/2026-11-07-v04-personal-practice-workspace.md`
- [文档 / C1] `docs/engineering/v05-practice-counterexample-20261026.md`

### G9 · V-C 恢复权威方案（只到方案）

建议逻辑提交信息：`docs(v0.5): 恢复权威选项与可说明损失，未实施`

- [文档 / C2] `docs/engineering/v05-restore-authority-options-20261026.md`

### G10 · 低预算诚实接续

建议逻辑提交信息：`feat(v0.5): 预算拒绝按是否已提交决策如实计数`

- [业务源码 / C1；共享逻辑文件] `components/life/scripts/autonomous_life_cycle.py`
- [测试 / C1] `tests/test_v05_low_budget_continuation.py`
- [冒烟/夹具 / C1] `scripts/v05_low_budget_continuation_smoke.py`
- [决定笔记 / C1] `.agents/notes/implemented/architecture/2026-10-23-explicit-compressed-demo-clock.md`
- [决定笔记 / C1] `.agents/notes/implemented/feature/2026-10-27-v04-lived-day-runtime-entry.md`
- [文档 / C1] `docs/engineering/v05-low-budget-continuation-20261026.md`

### SCOPE · 范围 / 方案文档

建议逻辑提交信息：`docs(v0.5): 恢复权威选项与十八章节范围核对，均未实施`

- [文档 / C2] `docs/engineering/v05-chapter18-scope-20261025.md`

### M_REAL_DAY · m 真实日结果

建议逻辑提交信息：`docs(v0.5): 一次真实压缩日的实际路径、票据与未覆盖项`

- [文档 / C3] `docs/engineering/v05-fastday-m-20261026-result.md`

### SUMMARY · 夜间汇总与项目状态

建议逻辑提交信息：`docs(v0.5): 夜间交付归拢与项目状态更新`

- [文档 / C4] `docs/engineering/v05-20261026-night-handoff.md`
- [状态文档 / C4] `NOW.md`

## 实际可执行顺序（命令仅文本，未执行）

下面命令供获准的提交者在同一worktree使用。每个命令单独一块；消息文件在本地ignored目录，不会被add。若换机器，按每组的一句信息重建消息并保留末行Co-Authored-By。不得执行push/tag/部署。

```bash
git rev-parse HEAD
```

```bash
git branch --show-current
```

```bash
git diff --cached --name-only
```

### C1 · feat(v0.5): 整合评价回读、结果身份、到期重考与低预算接续

文件数：61；依赖：无。

```bash
git add -- components/life/scripts/agency_cognition_adapter.py components/life/scripts/autonomous_life_cycle.py components/life/scripts/life_practice_context.py components/life/scripts/life_sharing_context.py
```

```bash
git add -- components/life/scripts/life_sharing_service.py tests/test_v05_results_expression_views.py tests/test_result_method_behavior.py tests/test_v04_sharing_life_chain.py
```

```bash
git add -- scripts/v05_results_expression_smoke.py .agents/notes/implemented/feature/2026-10-25-v05-results-expression-views.md .agents/notes/implemented/architecture/2026-11-12-v04-practice-life-review-and-method.md .agents/notes/implemented/architecture/2026-11-14-v04-body-participation-and-bounded-sharing.md
```

```bash
git add -- docs/engineering/v05-results-expression-implementation-plan-20261025.md components/life/scripts/life_integration_ag2.py tests/test_v05_progress_result_identity.py scripts/v05_progress_result_identity_smoke.py
```

```bash
git add -- .agents/notes/implemented/architecture/2026-10-25-v05-progress-result-handoff-identity.md .agents/notes/implemented/feature/2026-10-27-v04-memory-product-service.md docs/engineering/v05-progress-result-identity-20261025.md tests/test_v05_low_budget_continuation.py
```

```bash
git add -- scripts/v05_low_budget_continuation_smoke.py .agents/notes/implemented/architecture/2026-10-23-explicit-compressed-demo-clock.md .agents/notes/implemented/feature/2026-10-27-v04-lived-day-runtime-entry.md docs/engineering/v05-low-budget-continuation-20261026.md
```

```bash
git add -- components/life/scripts/life_future_items.py components/life/scripts/artifact_continuation_source.py tests/test_v05_due_reconsideration.py tests/test_v05_due_parked_target.py
```

```bash
git add -- scripts/v05_due_reconsideration_smoke.py scripts/v05_due_parked_target_smoke.py .agents/notes/implemented/architecture/2026-10-26-v04-day-life-core-continuity.md .agents/notes/implemented/architecture/2026-11-02-v04-autonomous-day-later-and-multi-project.md
```

```bash
git add -- .agents/notes/implemented/feature/2026-10-24-eligible-parked-candidate-supply.md docs/engineering/v05-due-reconsideration-20261026.md docs/engineering/v05-due-parked-target-20261026.md tests/test_v05_practice_continuation.py
```

```bash
git add -- scripts/v05_practice_continuation_smoke.py .agents/notes/implemented/feature/2026-10-24-loss-to-continuation-owner-bridge.md .agents/notes/implemented/feature/2026-10-24-past-to-candidate-owner-bridge.md docs/engineering/v05-practice-continuation-20261025.md
```

```bash
git add -- tests/test_v05_result_sharing_continuation.py scripts/v05_result_sharing_continuation_smoke.py .agents/notes/implemented/feature/2026-10-25-v05-result-sharing-continuation.md .agents/notes/implemented/feature/2026-11-14-v04-life-sharing-service-local-inbox.md
```

```bash
git add -- docs/engineering/v05-result-sharing-continuation-20261025.md tests/test_v05_restart_continuation.py scripts/v05_restart_continuation_smoke.py docs/engineering/v05-restart-continuation-20261025.md
```

```bash
git add -- tests/test_v05_source_loss_continuation.py tests/test_v05_source_domain.py tests/test_v05_same_result_loss.py scripts/v05_source_loss_continuation_smoke.py
```

```bash
git add -- scripts/v05_source_domain_smoke.py scripts/v05_same_result_loss_smoke.py docs/engineering/v05-source-loss-continuation-20261025.md docs/engineering/v05-source-domain-20261026.md
```

```bash
git add -- docs/engineering/v05-same-result-loss-20261026.md tests/test_v05_practice_counterexample.py scripts/v05_practice_counterexample_smoke.py .agents/notes/implemented/feature/2026-11-07-v04-personal-practice-workspace.md
```

```bash
git add -- docs/engineering/v05-practice-counterexample-20261026.md
```

逐文件核暂存清单与diff；只看stat不等于完成内容审阅。若不匹配本组成员，停止，不reset他人stage。

```bash
git diff --cached --name-only
```

```bash
git diff --cached --stat
```

```bash
git diff --cached --check
```

```bash
git diff --cached
```

本地消息：`.agents/worker-runs/20261026-v05-C1-message.txt`；末行含 `Co-Authored-By: Claude Code <noreply@anthropic.com>`。

```bash
git commit -F .agents/worker-runs/20261026-v05-C1-message.txt
```

### C2 · docs(v0.5): 记录建设范围与恢复权威方案，未实施恢复变更

文件数：2；依赖：C1。

```bash
git add -- docs/engineering/v05-restore-authority-options-20261026.md docs/engineering/v05-chapter18-scope-20261025.md
```

逐文件核暂存清单与diff；只看stat不等于完成内容审阅。若不匹配本组成员，停止，不reset他人stage。

```bash
git diff --cached --name-only
```

```bash
git diff --cached --stat
```

```bash
git diff --cached --check
```

```bash
git diff --cached
```

本地消息：`.agents/worker-runs/20261026-v05-C2-message.txt`；末行含 `Co-Authored-By: Claude Code <noreply@anthropic.com>`。

```bash
git commit -F .agents/worker-runs/20261026-v05-C2-message.txt
```

### C3 · docs(v0.5): 记录一次真实压缩日的行为与未覆盖边界

文件数：1；依赖：C1。

```bash
git add -- docs/engineering/v05-fastday-m-20261026-result.md
```

逐文件核暂存清单与diff；只看stat不等于完成内容审阅。若不匹配本组成员，停止，不reset他人stage。

```bash
git diff --cached --name-only
```

```bash
git diff --cached --stat
```

```bash
git diff --cached --check
```

```bash
git diff --cached
```

本地消息：`.agents/worker-runs/20261026-v05-C3-message.txt`；末行含 `Co-Authored-By: Claude Code <noreply@anthropic.com>`。

```bash
git commit -F .agents/worker-runs/20261026-v05-C3-message.txt
```

### C4 · docs(v0.5): 归拢限定交付、失败史与项目当前停点

文件数：2；依赖：C1, C2, C3。

```bash
git add -- docs/engineering/v05-20261026-night-handoff.md NOW.md
```

逐文件核暂存清单与diff；只看stat不等于完成内容审阅。若不匹配本组成员，停止，不reset他人stage。

```bash
git diff --cached --name-only
```

```bash
git diff --cached --stat
```

```bash
git diff --cached --check
```

```bash
git diff --cached
```

本地消息：`.agents/worker-runs/20261026-v05-C4-message.txt`；末行含 `Co-Authored-By: Claude Code <noreply@anthropic.com>`。

```bash
git commit -F .agents/worker-runs/20261026-v05-C4-message.txt
```

## 排除项：保留本地，不删除、不提交

- `.memsearch/.index-state.json`：检索索引状态；索引/pid/私人日记整体不入提交。
- `.memsearch/.index.pid`：检索进程 pid。
- `.memsearch/.maintenance-state.json`：检索维护状态。
- `.memsearch/PROJECT.md`：检索索引生成的私人项目摘要。
- `.memsearch/memory/2026-10-25.md`：私人日记。
- `.memsearch/memory/2026-10-26.md`：私人日记。
- `.agents/continuity/current.json`：本机检查点，父级独占维护。
- `dist/nyairo-v0.4.0.zip`：既有 v0.4 发行产物，84647481 字节。
- `dist/nyairo-v0.4.0.zip.sha256`：既有发行校验文件。
- `.agents/release-id.txt`：发行标识，不属本次源码。
- `.agents/worker-runs/**`：gitignore 忽略：派工原文/日志/失败史/临时审计脚本，不 force add。
- `docs/engineering/v05-commit-plan-20261026.md`：本计划自身产物，扫描后才创建；是否单独提交由监护决定。

失败史应保留为公开文档中的简短事实：真实失败/超时、修复范围、最终验收、未验证边界；原日志、私有证据JSON和临时脚本仍留ignored worker-runs，不force add。路径文字/root引用不等于仓库中存在旧实例目录；本单未访问WSL旧root。

## 有限安全检查与本次未做

父级对66个候选文件独立做私钥头、Bearer字面量、常见token前缀、凭证赋值、本机绝对路径、私网URL规则扫描：前四类未命中，绝对路径规则8处命中（含既有note/状态及测试环境文档），未输出命中值。worker较窄扫描另报NOW的网关文字，应人工复核，不以父级另一规则未命中抵消。

- `.agents/notes/implemented/feature/2026-10-27-v04-lived-day-runtime-entry.md:103`，规则 `ABSOLUTE_LOCAL_PATH`；只报告元数据。
- `NOW.md:168`，规则 `ABSOLUTE_LOCAL_PATH`；只报告元数据。
- `NOW.md:293`，规则 `ABSOLUTE_LOCAL_PATH`；只报告元数据。
- `NOW.md:317`，规则 `ABSOLUTE_LOCAL_PATH`；只报告元数据。
- `NOW.md:713`，规则 `ABSOLUTE_LOCAL_PATH`；只报告元数据。
- `NOW.md:716`，规则 `ABSOLUTE_LOCAL_PATH`；只报告元数据。
- `docs/engineering/v05-low-budget-continuation-20261026.md:99`，规则 `ABSOLUTE_LOCAL_PATH`；只报告元数据。
- `docs/engineering/v05-same-result-loss-20261026.md:65`，规则 `ABSOLUTE_LOCAL_PATH`；只报告元数据。

候选中没有>=1MiB文件；排除的旧v0.4 ZIP为84,647,481字节，不解压、不重打包，不随源码提交。候选中未见DB/log/jsonl/instance/socket路径；不是全机或所有ignored内容扫描。未扫描私有memsearch/worker日志正文，不声称零泄露。

父级核了路径去重/实际变更覆盖、静态跨文件夹具依赖、引用目标与索引空；未构造或验证中间Git树，未重跑旧绿测试，未执行上述add/commit。原worker十次物理提交方案已被父级本版替代，manifest保留其路径分配历史。

提交后工作树预期仍有排除的memsearch/current/dist/release-id/计划本身等dirty；不要为变clean而reset、clean或丢掉现场。清单随NOW和memsearch自动更新会漂移，执行时以显式候选白名单逐项复核。

产品累计94/300、余206未变。V-C仍FAIL、G1未闭合、真实跨日/24h未验等边界不因提交而升级；没有发行、版本号修改或部署。
