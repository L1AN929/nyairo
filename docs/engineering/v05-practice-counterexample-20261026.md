# v0.5 第五条：多情境、反例、修订与退休的整链验证

2026-10-26。范围是既有 Practice 方法/评价版本链在**两个不同真实作品**上的贯通验证，
不是新建偏好评估系统。本单只新增测试、冒烟与事实文档，业务源码零改动，
没有新 owner / schema / usage 存储 / 偏好协议。

## 一条链

```
情境 1：项目作品 A（真实 owner 版本 + 结果级 handoff 经历 expA）
   └─ 正式 Decision 原始 JSON 的 method_proposal → AG-1 → DecisionRecord → owner 存 TENTATIVE
   └─ 同 Activity CONTINUE A，命名本轮 offered head
        └─ HELPED，证据 = expA → TRIAL
情境 2：回顾作品 B（真实 owner 版本 + 自己的 handoff 经历 expB）
   └─ CONTINUE B，命名同一做法
        └─ NOT_HELPED，证据 = expB（不是 expA）→ CONTESTED，反例计数 1
下一轮正式 Decision 的 practice 视图
   └─ CONTESTED / count 1 / evaluation(NOT_HELPED, 被评价版本, evidence_count 1)，无 detail
owner revise_method（CAS）→ TENTATIVE，旧版与两条评价、反例都保留
   └─ 下一轮正式请求与 writer 请求绑定新 head / 新指令
   └─ 已展示旧 head 后另一 owner 写者修订：本轮 usage 仍绑旧 head，trial 被 CAS 拒
owner retire_method（CAS）→ RETIRED
   └─ 仍命名旧手柄：AG-1 INVENTED_METHOD_REF，无 Decision 提交，视图具名扣留
   └─ 不引用旧手柄的 CONTINUE 仍写 B，writer 不含退休指令；NO_ACTION 合法
同一 runtime 重装配 PracticeSeam：从 owner 重读反例/修订/退休历史，纯读零写
```

## 真实 owner 与替身

- 真实：Life Supply owner（独立 Unix socket、落盘）、M0 Memory（EvidenceStore + controls）、
  Practice owner（`PersonalPracticeService`）、正式 assembly / AG-0 / AG-1 / AG-2 / AR-0、
  真实 writer 请求（`model_continuation_writer` 的正式构造）。
- 替身：Decision 的原始 JSON 与内容回答都是脚本；两段 Activity 的题目/正文由测试夹具给出。
- 不宣称：真实模型自然采用、长期偏好/稳定人格、独立评估、G1、24h、跨 PID/crash 重启。

## 可操作结果

```bash
# A：接缝级不变量与最小负控（11 例）
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -q \
  tests.test_v05_practice_counterexample

# B：真实 owner 双情境全链（0 产品模型、0 外网、finally 正规关闭服务）
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/v05_practice_counterexample_smoke.py
```

A 覆盖：无证据不能写 HELPED/NOT_HELPED（owner 与 seam 双层拒绝）；UNKNOWN 不升级；
一次正评价不抹掉反例且不存在稳定支持状态；修订 CAS 拒绝旧 head、正确 head 修订回
TENTATIVE 且旧指令/反例保留；退休后试用与修订都拒、文档仍可读、offer 扣除；
同一操作键重复评价不多写；有界评价投影不带 detail，head 变了不混读。

B 覆盖：两个不同真实作品 A（项目）与 B（回顾）各自的 owner 版本与结果级 handoff 经历；
A 的 HELPED 用 A 的经历，B 的 NOT_HELPED 用 B 的经历，两条评价分别命名各自 offered 版本；
下一轮正式请求读到 CONTESTED / count 1 / 有界评价；修订后正式请求与 writer 绑定新 head；
并发旧 head 负控（usage 绑旧 head、trial CAS 拒、反例不丢）；退休后 AG-1 拒旧手柄且无
Decision、合法 CONTINUE 仍写 B、writer 无退休指令、NO_ACTION 合法；同 runtime 重装配
纯读历史零写。47 项 check（含 4 项 seeding）全部 PASS。

父级独立复跑证据在 `.agents/worker-runs/20261026-v05-practice-counterexample-parent-a.log`
与 `-parent-b.log`。

## 边界

- 只离线机制；没有真实模型/外发/旧实例/Memory 门/默认 gate/预算变更。
- CONTESTED 是状态，不是损失：手柄仍可命名；退休才是损失入口，证据撤回仍走原合同。
- 两条评价都发生在各自 Activity 内，使用上一轮已准入、仍可读的结果经历；不能证明
  “同一轮结果在同轮被评价”，也不证明评价专门归因于最新版本。
- 情境差别由两个不同 owner 作品、版本与经历 refs 证明；没有虚构“独处/成本”等未观测变量，
  这些一律记 UNKNOWN。
- 同 runtime 重装配不是跨 PID/crash 重启；不证明自然长期轨迹。
