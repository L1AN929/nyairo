# 回顾标题截断后的收敛与同标题去重：离线实测

## Problem

既有静态核对只确认了三条：`review_seed` 用固定的「回顾：」前缀拼第一条经历节选、确认过的经历
文本以作品 title 开头、title 截到 80 字符（Memory 经历节选 200、材料节选 160），并且
`build_options` 用已存作品标题做同标题去重。它没有回答两个问题：标题在 80 字符截断下会不会
真正收敛到不动点；收敛之后 `build_options` 的同标题去重会不会真的命中，以及不产出 review 时
还会返回哪些 kind。「回顾：」+ 上一轮标题的叠加看起来会无界增长，但仅凭 80/3 的算术预测不能
当实测证据。

## Decision

新增隔离离线脚本 `scripts/review_title_boundary_offline.py`（默认新 `mkdtemp` 并保留，无真实
实例根参数、不发 HTTP、不调模型），用仓库现有函数实跑三层：

1. 纯字符串：反复调用真实 `review_seed`，经历节选取上一轮标题。
2. 真实投影：`MemoryAdmissionExperienceConsumer.confirmed_context/_text` 生成以标题开头的确认
   文本 → `PracticeSeam.experiences` 节选 200 字 → 真实 `build_options`。
3. 真实 owner：`PersonalPracticeService.create_review` 按既有 API 保存合成回顾 →
   `PrivateProjectService.list_projects` 读回标题 → 交给 `build_options` 做去重。
   另加同标题正控、相近不相等负控，以及 review 缺失时其它 kind 的返回观测。

实测结论：两层标题都收敛（短/长初始标题均在第 28 轮与上一轮相等，最终长度 80），
字符串层与真实投影层的收敛轮数和最终长度一致；owner 存满 27 份合成回顾后，第 28 轮标题与
已存标题相同，去重命中，不再产出 `REVIEW_FROM_EXPERIENCE`；此时 `build_options` 仍返回
`QUESTION_FROM_MATERIAL` 与 `FUTURE_ITEM_DUE`，已存作品本身不会作为选项出现。
正控（同标题）跳过 review，负控（相近但不相等）仍产出 review。

边界照实写：owner 层用的是 tests 里既有的内存 port 契约替身，不是 socket Supply 服务；
`_text/_refs` 是私有 helper，只证明投影这一层；合成经历不等于 Memory 准入或自然采纳；
「review 候选停止生成」不等于 runner 停止、不会选别的经历、或永不再生成。

## Alternatives considered

- **只写静态推算（「回顾：」每轮加 3 字，80/3 约 27 轮）**：最快、零夹具，且已有静态核对基础。
  否决原因：任务要求实跑函数，且静态推算无法区分「收敛到不动点」与「因经历集合/标题提取变化
  而停在别处」；事实也证明真实投影下短标题在第一轮就已是 80 字（节选包含结果描述），纯算术
  预测会漏掉这一层。
- **只给 `build_options` 手塞 `existing_projects=[{"title": ...}]`**：避免搭 owner，代码最少。
  否决原因：去重依赖 owner 已存标题的规范化（owner 侧 title 上限是 120，选项侧是 80），
  手塞字段无法证明「owner 存进去的标题真的参与去重」；任务也禁止用手塞布尔冒充 owner。
- **起真实 socket Supply 服务跑 owner**：最接近生产。否决原因：本单是离线只读，去重只需
  owner 的标题读写语义，既有内存 port 已按冻结契约提供 CAS/幂等/生命周期；起服务属于无必要
  的额外组件操作。代价是 owner 证据只到「真实服务代码 + 契约替身」，不是 socket 现场。

## Consequences

- 回顾标题在这种自我叠加下确实收敛，不会无限增长；收敛点是 80 字的固定前缀重复串，
  同标题去重因此会在第 28 轮命中，而不是永不命中。
- 去重命中只停掉「回顾」这一个候选来源；问题、到期事项等仍照常返回，已存作品不会变成选项。
- 没有改任何生产规则、标题/去重/Memory 准入或候选策略；结论只覆盖上述三层和合成数据。
- 未证明：真实 Memory 经历集合顺序、真实 owner 库上的第 28 轮行为、runner 的下游停止语义、
  其它经历是否会再生成新回顾。后续若要验证这些，需要新的现场观测，不能拿本次离线绿替代。
