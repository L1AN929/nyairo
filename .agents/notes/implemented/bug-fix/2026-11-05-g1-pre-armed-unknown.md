# Agent Note: G1 的 UNKNOWN 注入提前到第一轮之前武装

Status: implemented

## Problem

G1 六项故障里，中断、重启、UNKNOWN、版本冲突四项都能主动注入，整套仪器（`g1_real_adoption.py`
的 p1/p2 两阶段 + `g1_next_live.py` 六段判定）也是现成的。卡点在注入时机：注入是锁存式的，
武装之后**只在下一件作品 A 的 `commit_version` 提交时**才消费。f 批走到 `AWAIT_UNKNOWN` 之后，
后面八轮 `same_work` 全是 False，注入一直等不到目标写入，最后以 `PROVIDER_OR_TRANSPORT_FAILURE`
收场，`unknown_fired=0`，整批白跑。

原本的武装点放在"上一轮写完才武装"，所以只要模型只续一次作品，武装就永远等不到下一次提交。

## Decision

- 新增 `_arm_unknown_pre_round()`，在 p2 的 operator 解除暂停之后、第一轮 tick 之前武装，
  并把 `unknown_armed_before_first_round` 记进报告。
- `SubmissionInjector.view()` 增加 `unknown_armed`，让"第一轮没有 A 提交时锁存还挂着"能被报告证实。
- `_apply_round` 的续作分支加一次性守卫：已消费过的注入不再补挂。
- p2 的状态推进改成**同轮可连推两段**。这一条不在原任务书里，是施工判断加的：不加它的话，提前
  武装的那一轮虽然消费了注入、也带着 UNKNOWN 状态和新 receipt 进了 `AWAIT_UNKNOWN`，但对账证据
  会被丢掉，模型得再续一次 A 才能进 `AWAIT_CAS`——正是 f 批卡死的老路，等于没修。

每段门槛一个字都没松：receipt 必须指向 A、Activity 必须精确匹配、版本必须真涨，只是允许一轮的
证据连推两段。六段判定口径、p1/p2 结构、profile 都没动。

## Alternatives considered

- **只提高额度或拉长窗口，等模型自然多续几次。** 最强立论：完全不碰状态机，零风险。否决原因：
  f 批 658 秒 17/20 个请求只拿到一次 CONTINUE，要自然凑到三次，成本不可控，而且拿不到就说不了
  是模型不配合还是窗口不够。
- **改用混合 transport（真模型 + 指定轮脚本 CONTINUE）。** 最强立论：能保证覆盖，一次跑完六段。
  否决原因：那会动到证据口径——一旦指定轮由脚本代答，"她自己选择续写"这件事就不成立了，
  而 G1 要验的正是她自己在故障后能不能继续。这条留在最后，且必须先定证据口径才谈。

## Consequences

- 只出现一次 CONTINUE 的运行也能命中 UNKNOWN，f 批那种白跑的情形不再重演。
- 离线全链夹具 `offline-check` 跑到 `RESULT: PASS`，37 项 CHECK 全绿，
  `unknown_fired: 1`、`reconcile.result: CONFIRMED_SUCCEEDED`，状态走完
  `AWAIT_UNKNOWN → AWAIT_CAS → AWAIT_NO_ACTION → … → COVERED`。
- 仍未真跑。真实模型那条路只有离线夹具和单测支撑。
- 真跑时的边界：若 UNKNOWN 那轮 wake 和 receipt 都拿不到 `activity_ref`，链会停在 `AWAIT_UNKNOWN`
  等下一轮而不是误推进——失败关闭，不会假绿。

## 后续

- 2026-11-06 的完整 G1 新入口 `g1-integrated-g` 直接复用了这次提前武装：
  `2026-11-06-g1-integrated-g`（`.agents/notes/implemented/testing/`）要求 P2 第一轮前
  `unknown_armed_before_first_round=true`，且只被 A 一次提交消费；同一次离线链还要求
  NO_ACTION 之后真的再续同一件 A。本注记的注入时机/一次性守卫没有改。
