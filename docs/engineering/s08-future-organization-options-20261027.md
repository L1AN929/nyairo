# S08 未来组织：已交付的链与承诺域的停点（2026-10-27）

## 已经能用的部分

| 能力 | 落点 | 证据 |
|---|---|---|
| 明确认领未来事项（F-093） | 已提交 Decision 的 `later_item` → `FutureItems.propose`，归属用公开意向键重算 | `scripts/s08_future_organization_demo.py` 第 1 步 |
| 未来事项与当前 Activity 分离（F-094） | `fitem:` 不产生 Activity；`plan_today` 只写计划引用 | demo 第 2–3 步 |
| 计划与 Agenda（F-096） | 到期只进入"重新考虑"选项；LR-4 的 Agenda/Eligibility 只是恢复资格 | demo 第 3、8 步 |
| 过期取消与改期（F-097 的事务部分） | `future_item_actions` 的 `cancel`/`reschedule`；取消不因到期或重启复活 | demo 第 5、8 步 |
| 只读审阅 | `components/life/scripts/life_future_organization_review.py` + `scripts/s08_future_review_cli.py` | CLI 子进程检查 |
| 范围隔离 | `oblig:` 不能被 `future_item_actions` 修改；operator 义务 `writable_by_subject=false` | demo 第 6 步、`tests/test_s08_future_organization.py` |

## 停点：F-095 真实承诺约束 / F-097 重新协商

**现状是缺口，不是隐藏实现。** 仓库里没有 S08 的 Commitment owner：

- 没有承诺聚合、承诺状态机或"谁有权建立承诺"的入口；
- `fitem:` 是延期意向，没有"答应别人"的主体、对方、期限与解除语义；
- `oblig:` 是 operator 定义的外部义务，不是她作出的承诺；
- Agenda/Eligibility 属于 LR-4 的恢复机制，与承诺无关；
- AG-1 明确 `can_write_commitment=false`，也从不产生 `promised=true`。

因此本单**不**把任何既有对象改名为 Commitment，也不新增 `promised` 字段、承诺状态或
重新协商协议。做一条"看起来能过的演示"只能靠伪造语义，这比留缺口更糟。

设计 03 的 F-095/097 要的是：活动放弃（`ActivityAbandoned`）必须触发 S08 assessment，
S08 只能标 `AT_RISK / NEEDS_REPLAN / NEEDS_RENEGOTIATION` 或接受已获准的解除；S11 只记录
真实沟通/失约证据。这需要新的持久事实（承诺、对方、协商记录、assessment）与 owner 边界，
属于外部接口/数据格式/产品可见行为，按停止门交给本人裁定。

## 三条候选路线（按代价从低到高）

### A. 保持缺口，等承诺域立项（推荐）

只读面继续报 `NOT_IMPLEMENTED`，未来事项/计划/义务按现状工作。代价是 F-095 与
重新协商一直缺席；好处是不会把"想法"误当承诺，也不会凭空造一个无人能履行的协议。

### B. 复用 operator 义务作"承诺候选"

最强立论：`oblig:` 已经有外部来源、目标对象与完成依据，看起来最接近"答应别人的事"。

否决原因：它的作者是 operator，而她只是执行者；把它当承诺会让"别人替我许的愿"变成
"我许的愿"，正好与 F-095 的责任归属相反。且它没有解除/重新协商的历史。

### C. 新建最小 Commitment owner（需要本人批准）

最小形状：`commitment: {party_ref, body_ref, due_at, status, source_decision_ref}` +
`AT_RISK/NEEDS_REPLAN/NEEDS_RENEGOTIATION` 三态 + `ActivityAbandoned` 引起的 assessment 义务；
重新协商是新的 Decision，不是就地改字段。这会新增 owner、持久格式与 AG-1 输出键，
超出本单写集，必须单独批准后实施。

## 结论

本单交付 F-093/094/096 与 F-097 的事务部分；F-095 与 F-097 的重新协商部分保持
`NOT_IMPLEMENTED`，等本人选择 B/C 或维持 A。
