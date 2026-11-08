# Agent Note: writer 转发 `work_context`，且不把 work-only payload 误标成裸研究

Status: implemented

## Problem

`AutonomousLifeCycle._next_content` 早已按现有合同把同一件作品的上下文读成
`work_context`（`life_work_context.WorkContextReader`，只在该轮 state 为 `OK` / `PARTIAL`
时放入 payload），与 `research_context` / `practice_context` / `earlier_versions` 并列。
但 `model_continuation_writer.ModelContinuationWriter` 这一端有两处接线遗漏：

1. `_user_prompt` 只解包 research / practice / earlier，从未读取 `work_context`；
   这块本已由 cycle 读到的字段在进入 prompt 前被丢弃，模型收到的 JSON 里没有它。
2. `_split_context` 判断「非 typed」的规则是「既没有 `research_context` 也没有
   `practice_context` 就当成旧式裸 research」，于是**只有 `work_context`**（或只有
   `earlier_versions`）的 payload 被整体塞进 `research_context`：research 被伪造，
   work 又被包在它里面，既错标又没落到该落的键。

这是纯接线缺口：work 字段本来就被 cycle 读取，本单没有新增任何输入来源、权限或准入。

## Decision

- `_user_prompt` 从现有 typed container 中取出 `work_context`，**原样**放进 prompt 顶层
  `work_context`；不嵌进 `research_context`，也不新增伪字段。新增的
  `_work_context_view` 与既有 `_earlier_view` 同形：`Mapping` 就 `dict(...)`，其它类型
  视为缺席。
- `_split_context` 把「typed container」的判据扩为四键：`research_context` /
  `practice_context` / `work_context` / `earlier_versions`。**任一键在场即视为已拆分**，
  只有四键都不在的 mapping 才保留旧式裸 research 语义；research / practice 各自的
  「`Mapping` 才收、否则缺席」行为一字未改。
- 不改 `next_content` / merge / no_change / cap / timeout / self_note 处理，不改
  Memory 准入、Decision 合法集、持久 schema、默认开关或任何资源上限。

## Alternatives considered

- **让 `_split_context` 返回三元组，把 work 也一起解包**：最强理由是「一个入口拆完所有
  half」，调用点只改一次。否决：它改的是一个内部函数签名，却没有带来可读性收益；work
  的取值规则与 `_earlier_view` 同属「按需取一块」，用同形 helper 更贴近现有结构。
- **只在 `_next_content` 里把 work 塞进 research 键（`research_context.work`）**：最强理由
  是 writer 一行不用动。否决：那等于把误标固化成契约，研究正控/负控读面仍会看到
  `research_wraps_work`，问题没解决，只是换了位置。
- **要求至少出现两个已知键才算 typed**：最强理由是避免把一个恰好含 `work_context` 的
  旧式裸 research dict 误判。否决：work-only 与 earlier-only 都是 cycle 会真实发出的合法
  payload，本次任务明确要求它们能被识别；为虚构的边界收紧反而会漏掉真实路径。
- **给 context 引入全局 schema 校验框架**：最强理由是一次性统一所有非法类型。否决：
  超出本单「补既有字段接线」的边界，会改对外可观察的提示词构造语义。

## Consequences

- work-only payload 现在得到顶层 `work_context`，`research_context` / `practice_context`
  缺席；earlier-only payload 保留有界的 `earlier_versions`，不再冒出伪 research。
- 混合 work + research + practice + earlier 时四块各走各键，值与来源哨兵不串类，整段
  user_prompt 仍是可解析 JSON；旧式裸 research mapping、`None` / 空 context、非法类型
  的既有安全处理均保持。
- 定向验收：新增 `tests/test_writer_work_context_forwarding.py`（真实
  `ModelContinuationWriter` + 正式 `AgencyModelClient` + 脚本 transport 实收 JSON，
  0 网络/0 真实模型）4 个聚合 case，`Ran 4 tests ... OK`、`EXIT=0`。
- 不可修改的原调查反例 `.agents/worker-runs/20261107-context-admission-check.py`
  未改一行断言，修复后复跑 `EXIT=0`：`harness_complete=true`、`defects=[]`、15 项检查
  全 true。此前红的 `baseline_research_absent` 与三处 `*_work_forwarded` 自然转绿；
  研究正控/负控、实践正控、archive 负控保持绿。
- 未覆盖：没有因此宣称历史 H/G 的 `no_change` 根因已知，也没有跑 G1/G2、全库套件、
  notes 树校验、浏览器点验或真实 provider；没有新增输入权限，产品批次与计数未动。
- 与既有口径一致：[[2026-11-12-v04-practice-life-review-and-method]]（写作 payload 的
  `work_context` 只读视图）。
