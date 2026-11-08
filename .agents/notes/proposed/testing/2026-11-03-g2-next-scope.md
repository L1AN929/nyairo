# Agent Note: G2 回到自然历史与冻结文本对照

Status: proposed

## Problem

“历史进入候选”的正式机制链已经存在，但10月29日及10月31日自然采集没有形成合格H，不能将比较未开始写成记忆无效，也不能据此不断延长观察。D/E之后还需区分共享业务改动与独立实验profile。

## Proposal

只交[下一步窄方案](../../../../docs/engineering/g2-next-scope-20261103.md)，不在本轮实现或运行。保留A自然采集路线、精确owner版本与M0经历、同轮双合法和NO_ACTION；复用既有20对历史text对照，不以一对请求宣布因果。先列现存入口兼容缺口，再确定新身份与时间，不重开旧根。

唤醒只读结果纳入边界：ambient不是已查直接触发源，World revision间接影响仍未知；TTL到期不等资格满足。不采用每tick新增日志建议，不新造观测工程。

## Alternatives considered

- 延长到6小时：能增加遇到自然变化的机会，但没有明确新增变化来源，且既有9000秒采集也没有H，故本次撤回F，不把长窗当修复。
- 受控建立真实历史：更容易保证可比较场景，但回答的是另一个问题，会改变已选A路线，本次不采用。
- 每tick扩充维护日志：可更细区分产生与抑制，但不直接回答G2问题，本轮复用现有记录，缺证据保留UNKNOWN。

## Acceptance criteria

方案分清机制已有、自然H未成、文本比较未做；列出最小场景、单变量、既定统计判据和失败出口；未执行新产品批或修改生产规则。兼容性未核清处保留明确限制，不称已可运行。

## Risks

自然H和双合法仍可能不出现；等字符不等tokens，相同refs仍暴露关联；同场景20对不是20种独立经历。旧profile已结束/过期，新的日期、累计预算和RESUME承接须后续具名处理。

相关既有成果：[G2离线接线](../../implemented/testing/2026-10-31-g2-next-offline.md)、[过去到候选](../../implemented/feature/2026-10-24-past-to-candidate-owner-bridge.md)。两篇部分重叠并互为索引，本篇不取代或归档既有决定；本目录另三篇分别是恢复权威、世界往返和自然C窗口，与本决定不同，不改动。

后继：[G2 explore-c 维护可读与探索入口](../../implemented/testing/2026-11-03-g2-explore-observable.md)——本方案已落地为薄入口与探索 profile（10 对、EXPLORATORY_ONLY）；原 20 对/40 请求协议未被替代，本文其余边界仍有效。
