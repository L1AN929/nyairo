# Agent Note: 拒写结算 consumer 以自身域名注册（F-168）

Status: implemented

## Problem

生产装配把 `RefusingSettlementConsumer("memory")`、`("goal")`、`("world")` 三个对象注册到
AR-1 `ResultSettlementService`，本意是让 memory/goal/world 三个域各自留下一条
“策略拒写”的通知记录。但 `RefusingSettlementConsumer` 继承
`ar1.FakeMemoryConsumer`，后者的 `__init__` 固定调用
`BaseSandboxDomainConsumer.__init__(CONSUMER_DOMAIN_MEMORY)`，把
`consumer_domain` 写死成 `"memory"`；构造函数里再设的 `self._domain` 只用于异常文案。
`register_consumer` 按 `consumer.consumer_domain` 入字典，于是 goal、world 依次覆盖
memory，`settlement_service.consumers` 最终只有 `{life, memory}`。后果不是写错域（三个都
拒写），而是 **AR-1 的分域后果记录与 CLI 分域状态不完整**：goal/world 的通知失败没有
独立 delivery，恢复读面无法回答“哪个域按策略拒绝了”。

## Decision

`RefusingSettlementConsumer.__init__` 在调用父类后，用构造参数（并校验属于
`ar1.VALID_CONSUMER_DOMAINS`）重设 `self.consumer_domain = domain`。拒写行为、异常文案格式
与注册顺序都不变，四域各自留下 `consumer_domain` 正确的 `DELIVERY_FAILED` 记录。

## Alternatives considered

- **给 `FakeMemoryConsumer` 增加 `consumer_domain` 参数**：最强理由是能一次修掉所有继承者；
  否决——`FakeMemoryConsumer` 是 AR-1 的沙箱合同对象，改它的构造签名会波及该模块全部
  调用方，超出本单“只补装配缺口”的范围。
- **在 `register_consumer` 里按对象身份而不是 `consumer_domain` 建索引**：能避开覆盖，
  但会把 AR-1 的公开注册键改成非域名字符串，影响 delivery 索引与既有读口；否决。
- **保持只有 life+memory 两条记录，CLI 只报 life 消费**：最省事，但正好掩盖了
  goal/world 没有被独立记录这一事实，与“拒绝也要具名”的验收相冲突。

## Consequences

- 每个结算事件固定最多四条 delivery：life 消费，memory/goal/world 各自
  `DELIVERY_FAILED` 且 `last_error_ref` 分别指向 `LPC0A_{MEMORY,GOAL,WORLD}_WRITE_DISABLED`；
  outbox 仍因策略拒绝域保持 pending，这是设计保留，不是漏投。
- 读面（`runtime_recovery_review._consequence_projection`）与 CLI 得以逐域显示；
  本单不改变任何消费合同、Memory 准入或 AR-1 状态机。
- 验证：`tests/test_life_recovery_consequences.py::test_production_registers_four_distinct_settlement_domains`
  与 `scripts/life_recovery_consequences_demo.py`（11 项 PASS）；原有 15 例恢复测试不回归。
