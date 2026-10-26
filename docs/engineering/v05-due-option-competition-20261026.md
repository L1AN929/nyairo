# v0.5 · 无对象到期事项与 review/question 的名额竞争（最小正式反例）

日期：2026-10-26。只读业务规则，只新增验证文件，不改任何生产代码、上限或排序。

## 问题

`life_project_options.build_options` 当前默认 `MAX_OPTIONS = 2`，构造顺序是
**review → question → due**，最后 `options[:max(0, int(limit))]`。当一个**没有对象**的
到期事项（只能作为 `FUTURE_ITEM_DUE` 项目选项，不能作为 `fitem:` 候选）与 review、
question 同时存在时，它是否会被挤出最终 Decision 请求？本次只回答「当前布置下会/不会」，
不据此改名额，也不推论「永远饥饿」。

## 方法与边界

两个隔离根，正式链均使用：真 Supply owner（真 Unix socket + 落盘）、真 M0 Memory
（EvidenceStore + controls，经验经正常准入消费者）、B 的 practice / private-project
gateway、真实 private items 文档、`AutonomousLifeCycle`、`AutonomousLifeRunner`
（正式 check-window）、AG-0 候选、AG-1 原始 JSON 校验、AG-2 采纳、AR-0 受管作品适配器。
Decision 回答是脚本 raw JSON，内容回答是固定 JSON edit，二者明示为替身；0 产品模型、0 外网。

- 场景①（对照）：同一隔离根内没有 question 标记、没有可读经历；用正式 Decision 的
  `later_item` 落一条无对象 due，到点后再跑一个正式窗口。
- 场景②（竞争）：先用操作者导入的测试文本作为研究材料，写两条真实 private `unclear`
  标记；再用 question 选项做一次真实写入，产生一条可读经历（review 的来源）；结束前台后
  用正式 `later_item` 落一条**带作品对象**的 due B。最终窗口里 review + question 已占满
  两个名额，due A/B 都到期。两条 due、两条 question 与 review 的标题互不相同，
  排除「同名项目去重」被误读成名额竞争。

## 结果（各一次隔离运行）

场景①：到点窗口为 `NO_ACTION`。源选项恰好 1 条：
`popt:4a76722f32c56a30` / `FUTURE_ITEM_DUE` / `basis_ref=fitem:c2726f0e2244f5d8`；
最终请求的候选集里有同一 `source_ref`，`legal_decisions` 含 `START`；事项仍 `OPEN`。
把同一真实 due 行交给生产纯函数（`limit=1`）也得到同一个 option_ref，说明来源本身可用。

场景②：到点窗口为 `NO_ACTION`，due refs = `[fitem:c2726f0e2244f5d8,
fitem:ce4b15214d8024d2]`（A 无对象，B 带 `artifact:` 目标）。源选项恰好 2 条：

| option_ref | kind | basis_ref |
|---|---|---|
| `popt:965ef9c39be646f7` | `REVIEW_FROM_EXPERIENCE` | `experience:01a17730-…` |
| `popt:6a293772dffbc4eb` | `RESEARCH_FROM_QUESTION` | `question:unclear-competition-2` |

最终请求里以 `popt:` 为 `source_ref` 的候选集合恰好等于这两条；没有以 A 的
`item_ref` 为依据的源选项，也没有 `source_ref == fitem:c2726f0e2244f5d8` 的候选。
A、B 在 `future_items_view` 的 `open`/`due_refs` 里仍只读可见，状态均为 `OPEN`。
B 有独立的 `fitem:` 候选（`source_ref=fitem:ce4b15214d8024d2`，
`target_refs=[artifact:7ba748fd5f644f6aa0657974e6c9f03c]`），说明带作品的 due 不靠
`popt:` 名额。本轮 `NO_ACTION` 后：内容写增量 0、作品 head 不变、前台不变。

## 由哪条原规则造成

`build_options` 先把 review（有可读经历且 practice 可用时）与每条 question 追加进列表，
再追加 due 选项，最后按 `limit` 截断。于是当前布置下：

- 源侧可构成 2 条已展示选项 + 2 条到期选项（该次运行的派生计数 4），上限 2；
- 截断发生在**选项构造返回之前**，最终请求只是消费已截断的列表。该次运行里最终请求的
  `popt:` 集合与源选项集合完全一致，没有第二层请求裁剪。
- 生产代码没有为项目选项提供“被截断”布尔字段；本报告的 `clipped` 是从
  「已展示 2 + 到期 2 > limit 2」派生的，不是读取某个标志。

## 边界（不推论）

- 只证明这一种布置下「无对象 due 的 `popt:` 不进最终请求」；不证明它在任何未来轮次都
  不可达。due 事项仍是 `OPEN`，没有自动取消/完成；出现空位或来源变化时能否重新进入，
  本次未覆盖。
- 脚本 Decision 选 `NO_ACTION` 是替身选择，不等于主体不愿意做这条 due，也不构成实现失败；
  测试没有强迫主体必须选它。
- `MAX_OPTIONS = 2` 与构造顺序是现有规则取舍，本次不判定为 bug，也不调整名额、
  不新增到期保底名额、不改上限或排序。
- 带作品 due 的独立 `fitem:` 候选与无对象 due 的 `popt:` 选项不是同一层，不能互相替代。

## 验收

WSL，cwd `/mnt/d/nyairo-rc1-body-backend`：

```
PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B \
    -m unittest -q tests.test_v05_due_option_competition      # Ran 6 tests, OK
PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B \
    scripts/v05_due_option_competition_smoke.py               # SMOKE_OK
```

## 失败史（保留，不倒写）

1. 首次尝试在 cycle 已读过 private items 的同一进程里追加 question 标记：`project_questions`
   的 `PrivateDailyItems` 实例缓存文档，新标记不可见，窗口因指纹未变成为 `QUIET`，未生成
   question 选项。改为两个隔离场景、并在 cycle 第一次读取前写入标记。
2. 竞争场景首次启动时，研究选项没有可创建材料，真实 owner 以
   `RESEARCH_CREATE_REFUSED: source_refs must name at least one ...` 拒绝；补一条操作者
   导入的测试材料后正常创建。
