# 外部叫停合同（2026-10-28）

## 调用者与凭据

- 唯一调用者是**本实例的本机 operator**。手机控制路由 `POST /api/phone/control/activity/stop`
  与只读 `GET .../stop/state` 只监听回环，并要求独立凭据 `operator-stop.json`
  （`kind=phone-operator-stop`，每次 serve 轮换 32 字节 token + epoch）。
- 日记凭据 `phone-capability.json` 与它**互不通用**：两个 reader 按 kind 各自拒绝对方的文件。
- 请求体只有 `activity_id` / `expected_revision` / `operation_key` / `reason_code`。
  `subject`、`epoch`、`authority`、`actor`、`operator`、`system`、`safe` 一律由服务端决定或直接拒绝。
  认证先于解析 body 与幂等：错 token/旧 epoch 不能变成一次重放。
- 同一个 `operation_key` 的再次请求是幂等的（不产生第二个 intent/终态）。对处于
  `SAFE_BOUNDARY` 的 Activity，**第二次**同 key 请求是"现在尝试收场"的时序信号：owner 自己
  重新核对活事实后才记录安全边界，调用方无法传 `safe=true`。
- 模型侧（`chiyo_bundle/phone_tool.py`）**没有** stop 动作。ABANDON 仍是她自己能选的 Decision。

## 持久事实

- stop intent 写在 LR-2 Activity 记录自己的 `external_stop` 块里，schema 升为
  `chiyo.life.activity.v3` / `chiyo.life.transition.v3`；v1/v2 保持可读、不迁移。
- 状态只有 `PENDING`（owner 已接纳，等待安全边界）与 `FINALIZED`（已 CANCELLED）。
- 意图属于 Activity 已有 owner，不建第二个 stop store；前台指针、checkpoint、已提交版本
  都不因意图改变。
- 同一 `operation_key` + 同 payload 重放返回原结果；同 key 异 payload 被 owner 的 payload
  指纹拒绝；随机量不写进 Action 幂等 key。

## 执行拦截

- 候选面：`CurrentActivityCandidateSource` 对 `PENDING` 的 Activity 不入候选（具名
  `ACTIVITY_EXTERNAL_STOP_PENDING`）。
- 生产提交门：AG-2 `execute_activity_step` 入口与 `submit_step_action_and_apply_post_submit_behavior`
  在真正提交前各读一次 live Activity；`PENDING` 抛 `ActivityExternalStopPending`，零 Action、
  零版本、零回执。旧 step 对象与旧 writer 不能绕过。
- **边界只在 owner 手里**：`SAFE_BOUNDARY` 的 Activity 第一次请求只到 `PENDING`；第二次同
  `operation_key` 请求会先经 LR-3 owner 的 `confirm_external_stop_boundary`（只接受 ACTIVE、
  已有 PENDING intent、有效 attention 为 `SAFE_BOUNDARY`、无 active atomic region 的 Activity），
  并且 stop 服务必须先证明 AR-0 没有该 Activity 的 `SUBMITTED`/`ACKNOWLEDGED`/`UNKNOWN`
  Action。任一不满足就继续 `PENDING`，basis 具名（`IN_FLIGHT_ACTION_UNRESOLVED` /
  `ACTION_LEDGER_UNAVAILABLE` / `ATOMIC_REGION_ACTIVE` / `SAFE_BOUNDARY_NOT_CONFIRMED_BY_OWNER`）。
  owner 写下 `applied_boundaries` 记录后，stop 服务再读一遍活事实才收场。
- 这条确认**不是**通用 BoundaryReachedEvent 生产者：它不伪造 pending interruption、不写 LR-2
  transition、不暂停 Activity；只是让一次已接纳的外部叫停在自己看到的静止点上结束。没有接入
  真实 boundary 事件源的 Activity 仍按原规则停在 `PENDING`。

## 层级与未知效果

- 未领取、可证明未执行的 PREPARED 只阻发送，不伪造 receipt。
- 已提交 IN_FLIGHT/UNKNOWN **不可撤回**：不裸重发、不改 FAILED；只允许原 operation 对账。
  CANCELLED 记录 `unresolved_action_refs`，保留已经发生的成功效果。
- ATOMIC 沿既有中断规则：`exit_atomic_region` 由 owner 自己确认区域关闭（核对 region 归属、
  live revision、boundary ref），用户/调用方不能改 store 的 `active` 标志；未关闭时回 `PENDING`。
  SAFE_BOUNDARY 走上面那条 owner 确认接口。
- CANCELLED 不是 World 副作用撤销，不改旧 Action/Artifact 结果，不解除未知承诺。
- **竞态界限（不声称线性化）**：接纳意图与派发之间靠两处 live 读（候选面 + 真实 submit 门）
  收窄窗口，不是把 check-then-submit 变成一次原子事务。已经跨过 dispatch 的 Action 不会因
  CANCELLED 被撤回或重写；它留在 AR-0 里由原 operation 对账。能保证的是：从 owner 已落盘的
  `PENDING` intent 被下一处门读到之后，不再产生新的 Action/版本/回执；在此之前已发出的动作
  按 UNKNOWN 规则处理。

## 结算与交接

- 三轴独立：Activity 终态、Action 结果、Artifact 版本各自保留。
- AG-2 新增 `HANDOFF_KIND_CANCELLED`，单 operation 一条 handoff，idempotent；不写成 ABANDONED。
  handoff 的 id 由 `(activity, kind)` 决定，重投只 repair/重试同一条，不会变第二条；终态已提交后
  同 key 重投也会尝试补发（AG-2 幂等 emit）。这是显式重投的修复入口；本次没有在 CANCEL
  与 emit 之间注入崩溃，不据此声称 resident 自动恢复该故障已验。
- 结果里的 `status` 不再把两种投递结果合并：`DELIVERED`（consumer 回 ACCEPT/IGNORE/DUPLICATE）、
  `DEFERRED`（consumer 主动 DEFER）、`FAILED_PENDING_RETRY`（consumer 抛异常，`consumer_disposition`
  为 null，另带 `error_class`/`error_reason`）、`QUEUED`（还没有投递记录）。**交接投递失败不等于
  主动拒绝**；两者都保留在 AG-2 的 `pending_handoff_ids` 里等重试，不冒认 Memory 已接纳。
- 没有配置 Memory 时，生产装配用的是 fail-closed 的 `RefusingExperienceConsumer`，CANCELLED
  handoff 会得到 `FAILED_PENDING_RETRY` + `ProductionExecutionRefused` /
  `LPC0A_EXPERIENCE_WRITE_DISABLED`。这是默认关闭 Memory 的诚实结果，不是本功能漏接；配置了
  M0 的实例沿既有 `MemoryAdmissionExperienceConsumer` 与准入门处理。本功能单测用真实 consumer
  类配替身 `_Port.append_outcome` 验了 CANCELLED 映射与 ACCEPT/DUPLICATE 分支；不是实际 M0
  写库验收，也不保证所有 CANCELLED 都获接纳。未改 Memory admission/controls。
- Memory 准入/删除权威不变；手机与 today 只读投影停止状态、原因与未决 refs。

## 跨进程边界

- phone 没有 Activity lease。独立 serve 时它只把请求写进 Life state root 的
  `external-stop/` 邮箱，由 resident 在自己的 owner 边界消费并回写结果文件；此时 HTTP
  答复是明确的 `PENDING / RUNTIME_MAILBOXED`，不是假 CANCELLED。
- **队列 ≠ owner 接纳**：手机读面用 `QUEUED` 表示"文件在盘上、还没有 owner intent"，
  只有 Activity 记录里的 `PENDING` 才是 owner 接纳；`accepted_as_stop` 按这个区分返回。
- resident 不在时不会有人消费邮箱，因此没有"已停"的假象。
- 邮箱文件必须带当前 serve 的实例、Life subject、epoch 和由该 serve token 派生的 MAC；
  旧 serve 写过但从未被接纳的文件在重启后按 `STALE_SERVE_EPOCH` 拒绝，不会变成新授权。
  owner **已经接纳**的 intent 存在 Activity 里，重启后拿新 serve 凭据重投同 key 可以继续收场。
  demo 实测：正规 stop/drain 旧 serve → 新进程重新 serve → 旧 token（即便同 operation key）
  403、旧 epoch 邮箱文件 `STALE_SERVE_EPOCH`、新凭据可读可请求、已接纳 intent 收成 CANCELLED。

## 明确未覆盖

- 同 uid 能读凭据文件的进程与管理员仍在保护边界之外；NTFS ACL 不核验，Windows 上不签发
  （`operator_stop` 在 Windows fail closed，CLI 也只在 POSIX 上能读到合规凭据）。
- ATOMIC 由 owner 的 `exit_atomic_region` 确认；SAFE_BOUNDARY 由上面的窄确认接口确认。
  两者都不是通用 BoundaryReachedEvent 生产者，也不覆盖所有中断来源。
- 两处 live 检查只收窄竞态窗口，不是 CPU 级线性化；已跨 dispatch 的 Action 不假撤回。
- 本功能不证明真实模型会自然采用任何选择，也不是 G1 或 24h 验收。
