# v0.5 EP-12：到期事项指向 WAITING/PAUSED 同一作品时的交叉候选

范围：只把 `ArtifactContinuationSource` 已有的
`PAUSED_TARGET_REQUIRES_RESUME` 规则接到它自己的额外机会路径（到期事项 `fitem:` /
项目选项 `popt:`），没有新增候选种类、状态字段、调度器或新的生命周期规则。

## 问题（严格反例，修前实测）

`artifact_continuation_source.py` 的 artifact 路径在 `_read_points` / `refresh` 里已经守住
「目标有 PAUSED/WAITING Activity 就只能走 RESUME」。但同一来源后来新增的
`_extra_raw_items`（到期事项与项目选项）只检查 ref 前缀与 target 是否缺失，没有再套这条规则；
`refresh` 的 extra 分支也只比较「这一轮还供不供应」。

正式链（真 Supply owner、ProductionLifeRuntime、`AutonomousLifeRunner.run_due`、
AG-1 原始 JSON、AG-2/LR-2/LR-4）在修前实际跑出：

* 作品 A 被真实 `PAUSE`（无条件，无资格）后，指向 A 的到期事项仍产出 `FUTURE_ITEM_DUE`
  候选，`legal_decisions` 含 `START`；一次 `START` 被 AG-2 采纳并让 owner 写出新版本
  ——同一个作品出现第二个 Activity，绕过 RESUME。
* 作品 A 真实 `WAIT`（WAITING、前台释放）后同样能 `START` 出第二个 Activity。

所以这不是「模型最终会被 owner 拒绝」的展示问题，而是正式采纳前的供给缺口。

## 修复（21 行，只动这一个文件）

* `_extra_raw_items`：按 owner 目标身份（去掉 `artifact:` 前缀，`artifact:artifact:<id>` 同样折叠）
  跳过已被 PAUSED/WAITING Activity 占用的目标，记 `last_extra_skip_reasons[ref] =
  PAUSED_TARGET_REQUIRES_RESUME`。`popt:` 目标不是 artifact id，不受影响。
* `refresh` 的 extra 分支：候选展示后目标才变成 PAUSED/WAITING 时，撤回为 `WITHDRAWN`，
  原因同上——采纳前的正式刷新就会挡住它。
* 没有改 AG-1/AG-2/LR-2/LR-4/owner、没有改候选种类或状态门；事项本身仍是 OPEN/due，
  只是不再成为新 START 的候选。

## 真实跑到了什么

* `tests/test_v05_due_parked_target.py`：6 例（3 个纯来源单测 + 真 owner 三条正式链）。
  修前同一个模块 4 红（含 PAUSED/WAITING 各一次正式 `START`），修后 `Ran 6 tests ... OK`。
* `scripts/v05_due_parked_target_smoke.py`：三个隔离根、35 项 check。修前 8 项失败（1.5/1.6/
  1.8/1.10/1.11/1.12 与 2.4/2.5），修后 `checks=35 failed=0` / `SMOKE_OK`。
  * PAUSED：到期窗口先展示候选，Decision 真实 PAUSE；下一窗口候选被撤回、0 新活动/版本/资格。
  * WAITING：真实 WAIT 后同一时刻 owner 的 `TIME_AT` 打开资格；到期项不再供 START，
    正式 `RESUME` 回到同一个 Activity，资格恰好消费一次，接续写一版。
  * 正控：作品仍 ACTIVE、没有挂起 Activity 时，到期项照常供应并 `START` 写出版本；
    没有对象的项目选项也不受影响。
* 日志：`.agents/worker-runs/20261026-v05-due-parked-target-{prefix-}A.log` / `-B.log`
  （修前红、修后绿各一份）。

## 没有什么

* Decision 与内容回答仍是脚本替身；没有真实产品模型调用、没有新真实短日、没有外发。
* 不证明模型会自然提出/采用事项；不证明 G1、V-C、理解或 24h。
* 只覆盖「到期事项/项目选项指向同一 artifact」这一交叉点；别的挂起对象类型
  （地点、身体目标）不在本规则里，也没有被这次修改扩到。
