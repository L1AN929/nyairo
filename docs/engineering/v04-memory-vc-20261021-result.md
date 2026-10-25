# EP-09 V-C 恢复删除边界实证（2026-10-21）

结论先说：**当前扣留通过，恢复防复活不通过。** 删除后 read/list/context 三处都扣留；
但只要把删除前的合法备份（evidence 与 controls 同时）恢复回来，被删正文就又可以被读到。
现有机制没有回滚域外的控制权威，设计 17 §5.5 要求的 fail-closed 无法由现有存储自证。
需要新契约（外部权威/控制 epoch 高水位）并由用户批准，本轮不实现。**这不是 EP-09 完成。**

## 本轮改动与边界

只新增两份文件，业务源码零改动、零提交：

- `.agents/worker-runs/20261021-memory-vc-probe.py`（临时验收脚本，忽略目录）
- `docs/engineering/v04-memory-vc-20261021-result.md`（本报告）

未绑定原实例，未碰 UI demo、launch、NOW、检查点；未调用真实模型、未外发。夹具根在
WSL `/tmp/vc-probe-17682-94887458`，全部副本保留不删。

## 实跑命令与真实结果

环境：WSL，`/tmp/rc104-venv/bin/python`（Python 3.12.3），`PYTHONDONTWRITEBYTECODE=1`、`-B`。

1. 既有治理用例：

   ```bash
   /tmp/rc104-venv/bin/python -B -m unittest -v \
     tests.test_v04_memory_product.GovernanceTests
   ```

   真实 exit 0：`Ran 2 tests ... OK`。两条分别覆盖删除后不可召回、被删行正文不借给邻居。

2. 探测脚本：

   ```bash
   /tmp/rc104-venv/bin/python -B .agents/worker-runs/20261021-memory-vc-probe.py
   ```

   真实 exit 1，`V_C_RESTORE=FAIL`。脚本按失败返回非零，没有把泄露反写成绿。
   （中间修过一次脚本自身的误判：最初把响应 envelope 里回显的 `query` 当成正文命中，
   导致扣留判定假阴性；改为只看 `experience(s)` 载荷后重跑，业务代码未动。）

## 隔离事实（全部为本次新建副本）

隔离根 `/tmp/vc-probe-17682-94887458`：

- `memory/evidence.db`：真实 M0 `EvidenceStore`，两条合成 sentinel 经历
- `memory/controls.db`：真实 `MemoryControlLedger`，与 evidence 分离
- `backup/evidence-predelete.db`、`backup/controls-predelete.db`：删除前的 SQLite 一致备份
- `variants/a`、`variants/b`、`variants/c-missing`、`variants/c-corrupt`：恢复/故障变体

服务一律用产品 `LifeMemoryService`（经 `memory_service_from_env` 绑定），没有 mock 召回，
没有改服务端契约。输出只有布尔、id、状态、路径，没有正文。

## A/B/C 事实

基线：`status=READY`，read/list/context 三处都能读到 sentinel（`baseline_readable=true`）。
经真实 ledger `apply(DELETE)` 返回 `APPLIED` 后：

| 场景 | 做了什么 | 结果 |
|---|---|---|
| 删除后 | 最新 controls 保留 | `delete_withheld=true`：read `NOT_FOUND`，list/context 都不含正文 |
| A | 只恢复删除前 evidence，保留最新 controls | `A_withheld=true`：仍扣留 |
| B | 恢复删除前 evidence + 删除前 controls（合法旧备份） | `B_readable=true`：read/list/context 又都读到正文 → **泄露** |
| C-缺失 | evidence 在，controls 文件不存在 | `BLOCKED / MEMORY_CONTROLS_DB_MISSING`，`experiences=null`，不是空库 |
| C-损坏 | controls 存在但不是可读 SQLite | `BLOCKED / MEMORY_CONTROLS_UNHEALTHY`，`experiences=null`，不是空库 |

B 的状态是 `READY`，意味着 evidence 与 controls 的 `quick_check` 与治理投影都健康：
这是一次完全“合法”的旧备份回滚，不是坏库绕过。恢复后 DELETE 行随旧账本一起消失，
投影没有任何更高水位能记得“这条曾经被删”。

CORRECT：在 B 的 pre-delete 副本上追加一条直接用户纠正事件并 `apply(CORRECT)`，
旧正文在 read/list/context 三处均被扣留（`correct_withheld=true`）。这是当前扣留，
不是恢复防复活；没有扩成通用恢复架构。

## 撤权链只做具名核对

既有具名用例：`tests/test_artifact_closeout_v04.py::ArchiveCloseoutOwnerTest::test_archive_without_a_usable_grant_is_refused_and_changes_nothing`
（本轮只读定位，**未复跑**）。该用例在 revoke grant 后派发
`archive_artifact`，断言 `EffectState.REFUSED`、状态不变、操作行 `NOT_FOUND`。
本轮不外发、不扩权，也不把 `Memory DELETE` 说成权限 revoke：前者是召回投影，
后者是派发前的能力拒绝，两者不是同一条链。

## 需要用户决定（停止边界）

1. **引入回滚域外的控制权威**：例如独立单调 epoch/高水位，删除后即使旧库整体回滚，
   新权威仍能证明“至少删过这些 event”，读路径据此 fail-closed。代价是新存储/新契约，
   属于本轮明确禁止项，需用户批准后才能设计。
2. **明确不承诺跨备份恢复防复活**：把现有能力限定为“当前库+当前账本内的逻辑遗忘”，
   在文档和产品语义里写清恢复旧备份会复活；不改代码。
3. **恢复流程强制重放控制**：备份恢复必须附带最后一次 controls 权威，否则拒绝启动读路径；
   这同样需要新的恢复契约，不是现有接口能兜住的。

在上述任一决定前，不应宣称 V-C 通过，也不应新增高水位实现。

## 未验证项

- 未跑全库回归；未接触任何真实实例或真实 Supply owner。
- 未复跑撤权具名用例；未验证真实模型采用。
- 未验证真实备份工具（非 SQLite backup API）的恢复行为；本轮只用 SQLite 一致备份。
