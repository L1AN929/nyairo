# Agent Note: 提交的 NO_CHANGE 快捷路径不是权限

Status: implemented

## Problem

`_op_commit_markdown` 在进入 coordinated unit of work 之前，如果 `expected_head` 与当前 head
相同、内容也相同，就直接返回 `NO_CHANGE`。这条短路发生在 `_reserve`（Governance 的
reserve → begin_execution → consume）之前，因此**已撤销/已过期/目标不匹配的 grant 也能拿到一个
成功语义的答复**。它没有产生新版本或回执，但把“幂等”当成了“有权限”，和 S13/S14 要求的
“idempotency 不是权限、规划期检查不替提交检查”冲突。同一文件的 `import_projection` replay 路径
已经先做 `validate_grant` 再回答 `NO_CHANGE`，commit 路径是漏接。

## Decision

在 `NO_CHANGE` 短路之前调用一次**只读**的 `GovernanceAuthority.validate_grant`（subject /
capability / scope / target / payload 与实际写一致），不 reserve、不 consume、不写库。未通过就按
既有 `GOVERNANCE_REFUSED` 返回 `REVOKED` / `EXPIRED` / `SCOPE_MISMATCH` 等原始状态；通过才返回
原来的 `NO_CHANGE`。真正的提交仍走 `_reserve` 的 reserve → begin_execution → consume，这一步
没有替代它。

## Alternatives considered

1. **把 NO_CHANGE 移进 unit of work、先 reserve 再判断**：会被 `COMMIT_MARKDOWN` 的合同
   拒绝（该命令要求一次版本提交），而且会为一个没有写入的动作消耗 grant 的 use，否决。
2. **依赖已有的 reservation / 调用方缓存**：撤销后缓存仍然存在，正是本问题要排除的路径，否决。
3. **不改，认为“没有写入就不算越权”**：调用方会收到成功语义的 `NO_CHANGE`，撤销后的 grant
   仍能驱动一次“成功”判断，和既有 `import_projection` 的规则不一致，否决。

## Consequences

- 已撤销/过期/scope/target/payload 不符的 grant，即使提交内容与 head 相同，也会被具名拒绝；
  有效 grant 的行为不变（仍返回 `NO_CHANGE`，不产生版本/回执）。
- 只读检查不消耗 use、不新增 schema/owner/持久字段；只覆盖本机 Supply 的
  `COMMIT_MANAGED_ARTIFACT` 路径，不代表整个 Python 后端或网络面。
- 真实链演示与直接测试见 `scripts/existing_authority_admission_demo.py`、
  `tests/test_existing_authority_admission.py`；边界写在
  [DAY_START](../../../../docs/DAY_START.zh-CN.md) 的“她的日记”一节。
