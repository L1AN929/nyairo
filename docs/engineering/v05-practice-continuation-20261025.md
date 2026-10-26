# 0.5 第二块：正式评价 → 同一作品续作，含退休损失

2026-10-25。范围是第三条主干（结果→做法）接第一条（原作品续作）与第五条（损失→承接）
的既有机制正式贯通；没有新增业务规则、owner、schema 或预算。

## 一条链

```
真实作品 A 执行（owner 版本 + receipt）
   └─ AG-2 handoff → A 的可回忆经历（M0 准入）
正式 Decision（原始 JSON）
   ├─ method_proposal → AG-1 校验 → DecisionRecord → Practice owner 存 TENTATIVE
   └─ CONTINUE A（候选自带 A 的 owner 目标）
        ├─ 命名 method_ref：本轮 offered 的 head 绑定 usage
        ├─ 真实 writer 请求的 practice_context 带 instruction/scope/uncertainty
        ├─ step receipt：A 的新版本 + 版本 ref 进入 consequence_refs（做法文档不是结果）
        └─ 同轮 method_trial HELPED/NOT_HELPED（证据 = A 的可读经历）
             → Practice owner 保存 → TRIAL / CONTESTED
下一轮正式 Decision 的 situation_context
   └─ 读到方法状态、trial/反例计数与有界评价投影（outcome/被评价版本/证据条数）
        → 仍可用同 method_ref 继续同一 A
损失：owner retire_method（CAS，保留文档/版本/正文）
   └─ 下一正式回合仍命名旧 ref → AG-1 INVENTED_METHOD_REF，无 Decision 提交
   └─ 不引用旧 ref 的合法 CONTINUE 仍写 A；writer 请求不含退休指令
```

同一链里另有可续作品 B，并把本轮绑定改到 B：每一步的 step 仍写 A，B 的版本不变
（沿用 `past_candidate_bridge` 的候选 owner 目标优先规则，不改优先级或选择算法）。

## 真实 owner 与替身

- 真实：Life Supply owner（独立 Unix socket、落盘）、M0 Memory（EvidenceStore + controls）、
  Practice owner（`PersonalPracticeService`）、正式 assembly / AG-0 / AG-1 / AG-2 / AR-0、
  真实 writer 请求（`model_continuation_writer` 的正式构造）。
- 替身：Decision 的原始 JSON 回答与内容回答都是脚本；Memory 的 handoff 去重是既有规则
  （第二轮 A 的新版本结果为 `DUPLICATE`，没有为凑链造第二条经历）。
- 不宣称：真实模型自然采用、真实回合延迟、G1、24h、跨重启恢复。

## 可操作结果

```bash
# A：接缝正负控 + 既有三段回归
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -q \
  tests.test_v05_practice_continuation tests.test_result_method_behavior \
  tests.test_loss_continuation_behavior tests.test_past_candidate_behavior

# B：真实 owner 全链（0 产品模型、0 外网、finally 关闭服务/lease）
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/v05_practice_continuation_smoke.py
```

A 覆盖：AG-1 的 trial 形状（未知键/ref 不匹配/非法 outcome/伪造手柄）、正式 Decision 的
评价保存到本轮 offered 版本、无证据在写前拒绝、owner CAS 移头时评价被拒且旧绑定不改指、
反例下一轮可见、退休手柄不进入本轮 offer 且不产生 usage/trial、另一作品不成为续作目标。
B 覆盖：上面的整条链（36 项 check，含 4 项 seeding 检查）。父级独立复跑 A：44 例、0.083 秒、exit 0；B：36 项全 PASS、exit 0。日志为 `.agents/worker-runs/20261025-v05-practice-continuation-parent-a.log` 与 `-parent-b.log`。本单没有新增业务源码，仅补正式链测试、冒烟与事实文档。

评价使用第一轮 A 的已准入、仍可读经历；第二轮 handoff 为 `DUPLICATE`。因此该链不能证明每个后续版本结果都独立产生了新经历，也不能据此声称评价专门归因于最新一次结果；它只证明既有可读证据通过正式评价入口、影响后续可见方法状态。

## 边界

- 只离线机制；没有真实模型/外发/旧实例/新 usage 存储/Memory 门/默认 gate/预算变更。
- 方法退休是 owner 已有的损失入口；`TRIAL`/`CONTESTED` 不是损失。证据撤回仍走原合同。
- 同一 Activity 的 CONTINUE 与「新 START 可续作品」是两条不同路径；本链只走前者，
  没有复活终态、没有造资格或自动恢复。
- 真实 writer 请求里出现做法 instruction/scope 只证明做法进入了**请求**；它不证明模型
  按做法改变选择，也不证明写成功等于 HELPED。
