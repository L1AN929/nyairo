# v0.5 进度交接的结果级身份

日期：2026-10-25。基线：`1057bad`。范围：只改 AG-2 交接身份与 cycle 的进度出口，
不改 Memory 准入、Activity/Decision/Action 策略、事件字段或旧数据。

## 身份对照

```
旧（缺省，未删）:
  SIGNIFICANT_PROGRESS  lexp:<sha256(activity_id + ":" + kind + ":v" + Activity.revision)[:16]>
  COMPLETED/ABANDONED   lexp:<sha256(activity_id + ":" + kind)[:16]>

新（仅 owner 确证的 SIGNIFICANT_PROGRESS，显式传参）:
  lexp:result:<sha256(activity_id + ":" + kind + ":result:" + <result_ref>)[:16]>
  result_ref = owner_confirmed_context.artifact_id + "@" + owner_confirmed_context.version_ref
             = "artifact:<id>@artifactversion:<version>"
```

两个命名域由不同前缀显式区分；摘要不是数学无碰撞保证，命中后仍核载荷结果。新 id 不含 Activity revision，所以同一结果在 revision 变化或重新装配
后仍是同一 id；不同 owner 版本则因为 `result_ref` 不同而有不同 id。

## 调用兼容

| 调用形态 | 行为 |
| --- | --- |
| 不传 `progress_result_ref` | 旧公式、旧载荷、旧重投语义，逐字不变 |
| `progress_result_ref` + 有效确证上下文 | 新结果级 id；同结果幂等重投，异结果独立 |
| 传了参数但无确证上下文 / 终态或其它 kind | 写入前 `LifeIntegrationError`，零副作用 |
| 参数值与确证上下文不一致，或两个 owner ref 不在本次 `artifact_refs` 里 | 写入前拒绝 |
| 同 Activity、同 kind、旧命名域里已有同结果事件（**任意 revision**） | 复用**最早**一条，返回 `reused_legacy_handoff: true`，不新增、不改写旧载荷 |
| 同范围内的旧事件都是另一结果 | 新结果走新 id，旧事件原样保留 |
| 同范围内任一旧事件读不回或没有可比较的确证上下文 | 拒绝并具名报错；不把“当前 revision 没找到”当作“没有同结果” |
| 新 id 与旧事件同时指向同一结果 | 结果级 id 优先；选择不随 revision 变化 |
| 新 id 已存在但载荷结果是另一个版本 | 拒绝，不静默重放 |

旧事件查找只在 AG-2 已加载的 `handoff_events_by_id` 上按 Activity+kind+旧命名域过滤，
不开新索引、不迁移数据、不查询其它 Activity 的结果或读取 Memory；但实现会遍历整个已加载
handoff map 后过滤，代价为 O(n)，n 是该 store 的 handoff 总条数，并非只遍历目标 Activity。
此兼容查找仅在结果级 id 未命中时发生。

`LifeExperienceHandoffEvent` 字段集合没有增加；`owner_confirmed_context` 是既有字段。
consumer/M0 仍按 `handoff_id` 作 `source_ref` 去重，准入结果原样返回（ACCEPT / DUPLICATE /
DEFER 等）。

## 真实加载与镜像

- 本仓库测试与 `autonomous_life_resident` / `life_runtime_production` 加载
  `components/life/scripts/life_integration_ag2.py`。
- `components/alpha/chiyo/life_runtime/life_integration_ag2.py` 是 `chiyo._vendor_paths`
  pin 的冻结 flat 发布树（CLI demo/import-check 用），不包含 `owner_confirmed_context`，
  也不是本次 Life 链的实际加载模块，因此本次未同步、未改它；测试里有加载路径与内容对照。
  若未来产品的 `current/scripts` 发布树改用该 vendored 版本，需要另行裁定同步范围。

## 验收

WSL，`PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B`：

```
A  -m unittest -q tests.test_v05_progress_result_identity \
       tests.test_world_daily_life_experience_wiring tests.test_life_continuity_inputs
B  scripts/v05_progress_result_identity_smoke.py
```

- A：父级最终独立复跑 67 例、4.808 秒、exit 0（本模块 29 例），日志 `.agents/worker-runs/20261025-v05-progress-result-identity-final-parent-a.log`。覆盖旧黄金公式/载荷/终态、旧事件读取与重投、同结果复用旧 id、
  异结果独立、缺确证/伪造/未携带/终态/异 kind 的前置拒绝、同结果跨 revision 稳定、
  两进程同文件重投、consumer DEFER/DUPLICATE 原样；新增用**真实 LR-2 owner**（lease、
  canonical store、command service；Decision/Adoption 读口为替身）经合法 checkpoint 推进
  revision 后升级重投：最早旧事件被复用、载荷不变、异结果不受阻、不可比较旧事件具名拒绝、
  新旧命名域并存时结果级 id 稳定优先、同根新进程仍复用旧事件。
- B：父级最终独立复跑 24/24 checks PASS、exit 0，日志 `.agents/worker-runs/20261025-v05-progress-result-identity-final-parent-b.log`。真实 Supply + M0 + 正式 assembly/AG0/AG1 脚本 Decision/AG2/AR0；
  同一 Activity（revision 2）三个真实版本 → 三个 `lexp:result:` handoff，全部 ACCEPT；
  同结果重投原 id、事件数与经历数不变；同根新进程重建 runtime 重投仍原 id、事件数不变；
  第三轮正式 `method_trial` 引用第二个经历，owner 记录的 trial 与第二个 owner 版本对得上；
  第 4 个真实 owner 版本先走旧调用写 `lexp:` 事件，再经 LR-2 合法 checkpoint（revision 2→3）
  后以新结果模式重投 → 复用该旧事件，事件数与经历数不增、旧载荷逐字段不变；同根新进程再投
  仍复用同一旧事件、事件数不变。
- 旧功能回归：`scripts/v05_practice_continuation_smoke.py` 36 checks、0 failed（该冒烟里
  第二轮断言已从宽松 `ACCEPT/DUPLICATE` 收紧为“独立结果级 handoff + ACCEPT”）。

## 边界

- Decision/内容仍是脚本替身；不证真实模型自然采用、G1、24h 或完整 resident 恢复。
- 旧事件查找限定同一 Activity 的旧命名域，按 owner 结果精确比对并复用最早一条；不扫其它
  Activity、不读 Memory、不开索引。若范围内存在读不回或不可比较的旧事件，本次结果级交接被
  具名拒绝，而不是新增第二条——该拒绝需要人工核对旧事件，属刻意 fail-closed 边界。
- 不保证每个结果都进入 Memory；准入被拒/DEFER 时 smoke 只如实报告。
- 不迁移旧实例、不改默认 gate/预算/事件 schema；旧 `lexp:` 事件可读可重投。
