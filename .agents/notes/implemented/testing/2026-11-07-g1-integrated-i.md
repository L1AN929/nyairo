# Agent Note: G1 新隔离入口 i，复用 12 门并新增本地调用边界观察

Status: implemented

## Problem

`2026-11-06-g1-integrated-g` 的 g/h 已经把完整 G1 接在同一件真实 Supply 作品与同一
Activity 上，12 个布尔覆盖首次写、暂停、跨 PID、UNKNOWN、CAS、NO_ACTION 与之后续作。
但历史 G/H 的 `no_change` 原因仍是 `UNKNOWN`；`2026-11-07-writer-work-context-forwarding`
修好了 writer 对 `work_context` 的转发与分类，却没有任何一次真实链上的事实说明：
本次 artifact_edit 请求到达最外层 `transport.complete` 时，prompt 里到底有没有
`work_context`、它是否就是本轮 writer 实际拿到的作品。这个缺口不能用历史原因倒推，
也不能靠离线单测冒充真实调用边界。

## Decision

新增封闭身份 `I_PROFILE`（`g1-integrated-i`）与 `scripts/g1_integrated_i.py`：

- 身份固定 root `/tmp/nyairo-g1-real-20261107-i`、独立 authority、run
  `run:g1-real-20261107-i`、域 `g1:real:20261107-i`、history 539、窗口 3600 秒、
  max_ticks 1000/trace 10000、最晚 23:30 启动、00:50 停止新请求、收尾 60 秒、
  累计门 1000（539 + 20 最坏 559）。只进 `MODE_PROFILES`，旧集合/旧 CLI 元组不动。
- `prepare`/`run`/`audit`/`status` 薄包装 `g1_real_adoption` 与 `g1_integrated_g`；
  12 门与全部反例原样复用，不改 NO_ACTION / 合法 nochange / 模型自选 / 历史基线。
- i 专属观测只在 scripts 实验层：`WorkContextObserver` 包住真实
  `writer.next_content` 与 artifact_edit 的 `transport.complete`。期望作品从
  `next_content(point)` 的实参在调用前绑定、`finally` 清空；transport 边界只内存
  解析已生成的 `user_prompt` JSON，只取 10 个允许字段；不放宽发送次数/参数/返回/
  异常/timeout/cap。每调用至多一条、每 phase 最多 20 条；无调用
  `NOT_EXERCISED`，解析不出 `UNKNOWN`，不猜 0 条成功。
- summary 只写进既有 p1/p2 phase 报告的 `work_observation` 键，并由
  `g1_integrated_g` 的可选安全投影带进 `status`/`audit`；旧 profile 不加字段。
- 离线验收只跑 `scripts/g1_integrated_i.py offline-check`：最多 6 个聚合 case 与
  一条正式双 PID 同根短链；父/phase/Supply owner 子进程复用既有 AF_INET/AF_INET6/
  DNS/create_connection 硬拒，AF_UNIX 放行。

## Alternatives considered

- **只修 writer 单测，不再跑真实链**：最强理由是改动最小、成本最低。否决：单测只能
  证明 writer 会转发，不能证明本次真实 p1/p2 链在 transport 边界确实带上了同一件
  作品的 `work_context`；本单要的正是这个本地事实。
- **把观察写进新的日志/第二 sink/生产 schema**：最强理由是可离线读取、不碰 phase
  报告。否决：任务限定不新增文件/日志流/生产字段；复用既有 phase 报告与
  status/audit 安全投影已足够，且旧 profile 形状不变。
- **在 components 里给 writer/transport 加回调**：最强理由是位置最贴近真实发送。
  否决：`components/` 冻结是承重墙；观察只能在 scripts 实验层做装饰，才能保证产品
  路径零改动、可随时不装。
- **记录完整 payload 或 artifact id 以便更强匹配**：最强理由是排障信息最全。否决：
  会把正文/标题/self_note/endpoint 等带进报告；本单明确只允许固定字段，匹配只需
  布尔，不需要保留作品 id。
- **观察器失败时重发一次以拿到可解析请求**：最强理由是能提高观测成功率。否决：
  重发会改变发送次数与计费事实，还可能绕过原失败锁；失败只能记固定 `UNKNOWN`。

## Consequences

- 收益：完整 G1 有了 i 这个新封闭入口；同时在不改产品代码的前提下，p1/p2 各有一份
  可核对的本地调用边界 summary：有没有 `work_context`、状态是否在闭集、是否对应
  本轮 writer 的作品、research/practice/earlier/experience 的在场与有界计数。
- 代价与边界：summary 只证明**本地 transport 调用边界**，不证明服务器收到、不证明
  模型看到；它不参与 12 门判定，G1 覆盖与直接指标分别报告。历史 H/G 的 `no_change`
  根因仍 `UNKNOWN`，i 的事实不能倒推历史因果。
- 观测器 on/off 的实收字节、调用次数、timeout/cap、返回与异常行为一致；坏账仍
  fail-closed，跨 phase 失败锁不变；旧 g/h/a..f 的 profile、CLI、输出形状不动。
- 离线绿不等于真实模型自然采用；真实是否走到完整链由唯一真实 run 决定。与
  [[2026-11-06-g1-integrated-g]]（g/h 的 12 门与复用方式）和
  [[2026-11-07-writer-work-context-forwarding]]（work_context 的转发/分类修复）
  局部互链，不翻转它们的决定。
- 未覆盖：未 prepare/run 真实 i 根，未 source 密钥，未跑旧 g/h/G1 套件、全库、
  notes 树、SHA 清单或浏览器点验；产品调用 0，历史 539 未动。
