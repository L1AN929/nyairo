# v0.4 探针退出码修复 + 真实阅读材料安装工具 · worker 结果

日期：2026-10-22。范围：只交工具与离线验证；**没有**改主实例、**没有**跑 180 秒续跑、
**没有**调用真实模型。父级命令与判据见最后一节。

## 一、改了什么（文件与具名入口）

| 文件 | 具名入口 | 作用 |
|---|---|---|
| `scripts/day_capability_probe.py` | `_exit_code`、`_run_report`、`status_probe`、`cmd_prepare` / `cmd_status` / `cmd_run` | 成功 `exit_code=0` 原样保留；缺失/`None` 失败关闭为 1；非零原样透传 |
| `scripts/day_reading_material.py`（新） | `install`、`verify`、`_open_owner`、`_default_service_factory`、`_write_bindings`、`_material_operation_key`、`material_identity` | 真实 owner 建件 + 只改阅读绑定元数据；纯读核对 |
| `tests/test_day_capability_probe.py` | `ExitCodeTest`（8 例） | 0 / 非 0 / 缺失 / `None` / cmd / 真实 CLI |
| `tests/test_day_reading_material.py`（新） | `InstallTest`（5 例）、`VerifyTest`（4 例） | 真实临时 Supply owner 安装/回读、二次幂等、缺失根、故障 finally、verify 负控 |
| `.agents/notes/implemented/process/2026-10-22-day-probe-real-reading.md`（新） | — | 记录退出码口径、材料身份、绑定边界与被否方案 |

没有改其他源码、NOW、current.json、dispatch/RUNBOOK/launch/settings、memsearch 或既有 dirty；
没有删除、stage、commit、push、deploy；没有新增依赖。

## 二、材料与候选为什么真的换了

1. **新 artifact 是真的新件。** `install` 通过既有 `LifeArtifactPort.create_artifact` 调用 Supply
   owner 自己的 `create_artifact`（首版由 owner 写），不是原地换文。材料标题
   `陶渊明《桃花源记》开篇`，正文逐字为已核对的开篇节选（东晋、公版、不是全文），
   `content_sha256` 写在工具回执里。owner 没有来源列，完整来源
   `https://zh.wikisource.org/zh-hans/%E6%A1%83%E8%8A%B1%E6%BA%90%E8%A8%98` 与
   “节选/公版/operator 提供/非 AI 生成”记在 `<root>/probe/reading-material-install.json`
   和报告里，不塞进正文。
2. **候选绑定指到新件。** 运行链的 live 绑定由 `components/phone/life.py:build_binding` 生成：
   手机实例的 `reading_artifact_id` 以 `phone_live` 来源写进 `CHIYO_LIFE_READING_ARTIFACT_ID`，
   并覆盖 nested 清单里的同名值；`autonomous_life_assembly.py:159/172/974` 再把这个 id
   交给 cycle 的 `reading_artifact_id`。因此安装同时写：
   - 手机 `<root>/instance.json` 的 `reading_artifact_id`（真正生效的 live 字段）；
   - nested `<root>/one-day/instance.json` 的 `reading_artifact_id` 与
     `environment.CHIYO_LIFE_READING_ARTIFACT_ID`（两份记录保持一致）。
   两文件写前各留字节备份，写后重新加载再取 `after` 快照。
3. **旧阅读凭据没有跟着新正文走。** 私人事项里的取得/停点/批注按
   `source_id = artifact:<旧id>` + `source_revision` 记账；新件的 id 与 revision 都不同，
   旧行仍指向旧 artifact。工具从不写私人事项文档，也不清游标。
4. **幂等。** 操作键由材料标题+正文哈希+subject+workspace 派生。第二次 `install` 先读回执、
   真实 owner 回读标题/正文/版本，并确认三处绑定都仍指向它，直接返回
   `ALREADY_INSTALLED`；不建 artifact、不改文件、不清游标、不重放动作。首次建件前先查
   owner 操作行，键冲突就拒绝。
5. **不替她决定读。** 没有写阅读位置、没有注入候选提示、没有 force wake；材料由 operator
   提供，读不读由她自己的 tick 决定。

## 三、实际命令与结果（worker 执行）

环境：仓库根 WSL，`PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B`。

### A. 验收测试

```bash
python -B -m unittest -v \
  tests.test_day_capability_probe tests.test_day_reading_material tests.test_live_day_runtime
```

结果：`Ran 44 tests ... OK`，退出码 **0**。构成为 19 + 9 + 16；模型全替身，未访问网络。
覆盖：真实临时 Supply owner 安装/回读；第二次幂等且文件字节不变；缺失 root 拒绝且不创建；
建件被拒时 owned owner 仍被 `finally` 停止；lease HELD 时在启动 owner 之前拒绝；
verify 纯读、无回执拒绝、能识破手机绑定漂移与账本被重置。
旧测试没有“成功却断言退出码为 1”的错误断言，因此本轮不需要纠正旧断言；新增的
`ExitCodeTest` 专门钉住 0 / 非 0 / 缺失 / `None` 四种情况以及 cmd/CLI 边界。

### B. notes 门禁

```bash
npx tsx C:/Users/28556/.claude/skills/write-notes-like-deepseek/scripts/verify-agent-note-tree.ts
npx tsx C:/Users/28556/.claude/skills/write-notes-like-deepseek/scripts/verify-agent-note-format.ts
npx tsx C:/Users/28556/.claude/skills/write-notes-like-deepseek/scripts/verify-archived-agent-notes.ts
```

结果：tree `ok: 84 note(s) tree and relative links verified`（exit 0）；
format `ok: 84 note(s) verified`（exit 0）；
archive `ok: 0 archived note(s) verified, 0 seal(s) in manifest`（exit 0）。

### C. 空白检查

`git diff --check`（tracked 写集）退出码 0；4 个新文件用
`git diff --no-index --check /dev/null <file>` 检查，无 trailing whitespace / space-before-tab /
new blank line at EOF。全部未 stage。

### D. 额外冒烟（不是主实例）

`py_compile` 4 个改动/新增 Python 文件通过。默认 Supply 子进程助手在一次性 `/tmp` 根上实跑：
`READY ... supply-smoke-1.sock pid=... owned=True`，随后
`STOP {'stopped': True, 'exit_code': -15}`；证明默认装配能起、能停，且只停自己启动的 pid。
该根不是产品实例，用完不清理（按仓库惯例保留）。

## 四、父级命令与输出判据

> 父级执行；worker 没有碰主实例 `/root/nyairo-day-20261022`。共同请求上限仍是 96，
> 已发生 6 次 + 旧预检 1 次；不得重建实例、prepare、改账本/窗口或注入唤醒。

1. **安装前 status**（保存真实退出码，不用无 pipefail 的 tail 掩盖）：
   ```bash
   <python> scripts/day_capability_probe.py status --root /root/nyairo-day-20261022
   echo $?
   ```
   判据：JSON `ok/status`、lease、run id、账本；退出码现在应如实反映 `exit_code`（成功 0）。
   若 `lease.status == HELD`，先停掉占用者，安装会拒绝。
2. **安装**（继承 `~/.nyairo-model.env`，但工具本身不需要 key）：
   ```bash
   <python> scripts/day_reading_material.py install --root /root/nyairo-day-20261022
   echo $?
   ```
   判据：`status == "INSTALLED"`、`exit_code == 0`；`reading_artifact_id` 为新 id 且
   `!= previous.artifact_id`；`readback.title == "陶渊明《桃花源记》开篇"`、
   `readback.content_sha256` 与正文哈希一致；`run.same_run_id == true`、
   `ceilings_unchanged == true`、`ledger_unchanged == true`；`services` 里自己的子进程
   `stopped == true`（若复用活 owner 则 `reused_live_owner == true` 且不拥有）；
   `lease` 为 FREE/UNKNOWN；`backups` 列出两份实例文件备份。
   第二次 install 应为 `ALREADY_INSTALLED`，`changed.artifact_created == false`。
3. **verify**（纯读，不发模型）：
   ```bash
   <python> scripts/day_reading_material.py verify --root /root/nyairo-day-20261022
   echo $?
   ```
   判据：`status == "VERIFIED"`、`exit_code == 0`；`checks` 全 true，尤其
   `phone_binding_points_to_material`、`nested_binding_points_to_material`、
   `nested_environment_points_to_material`、`previous_head_unchanged`、
   `previous_content_unchanged`、`same_run_id`、`ceilings_unchanged`、`ledger_not_reset`、
   `no_new_run_window`；`writes == false`、`model_called == false`；
   `previous.observed` 仍是旧合成材料，`previous.versions.total_versions` 未减少。
4. **续跑前保存第一段 probe 报告**（避免被覆盖，只是证据不是业务真相），再执行一次：
   ```bash
   <python> scripts/day_capability_probe.py run --root /root/nyairo-day-20261022 \
     --duration-seconds 180 --max-new-requests 6
   echo $?
   ```
   判据：只跑一次、不重试；`requests.delta` 不超过 6 且共同总调用 ≤ 96；
   `requests.same_run_window == true`、`ledger_reset == false`；
   `doing` / `changed.owner_version_refs` / `stop_point` 能看到她是否真的选了这份材料。
   **材料未被选择就如实说未选择，不 force。**
5. **事后**再跑一次 `verify` / `status`：核对真实请求增量、mrun 决策、动作/材料引用、
   服务收尾（自起自停）与 lease。

判据口径：不以测试数量或一个 diff 证明因果；以 owner 回读的标题/正文/版本、两处绑定、
旧材料仍在、同一 run/预算、账本未重置为准。探针成功段的退出码现在应如实为 0；
业务结论仍看 JSON 的 `status/ok`，不看退出码本身。

## 五、未验证 / 已知缺口 / 失败

- **主实例未动**：安装、真实 180 秒续跑、请求增量与“她是否真的读了”全部留给父级。
  离线测试的零模型调用不能当作主实例已验。
- **默认子进程装配只在一次性 `/tmp` 根上冒烟**，没有在主实例上启动过；父级安装时才会
  第一次在主实例上起停 Supply 子进程。
- **不证明模型采用**：候选绑定正确只说明下一次装配会拿到新 id；不证明模型会选它、读了它
  或理解了它。`understanding` 仍是 `UNKNOWN`。
- **owner 没有来源列**：来源 URL 只在工具回执/报告里，artifact 本身只有标题与正文；
  作者已写进标题，节选身份由正文长度与回执说明保留。
- **旧 `stop_point` 仍可能指向已归档的种子作品**：任务书明确本轮不修，保留为已知缺口。
- **安装依赖既有授权**：若主实例的 artifact grant 已失效/被撤，`create_artifact` 会按 owner
  的拒绝返回 `CREATE_REFUSED`，不会绕过授权；需要先恢复授权再安装。
- **verify 依赖 install 回执**：没有回执时拒绝 `NO_INSTALL_RECEIPT`，它不会去猜旧材料。
- **未跑全库/浏览器**：没有跑完整 `scripts/test.py`、没有开浏览器点验、没有调用产品模型；
  这些不在任务书写集内。
- **无两轮不明失败**：唯一一次红灯是本轮新测试里 `_refused()` 重复 `schema` 关键字，
  修好后复跑通过，不是同一原因两轮不明。
