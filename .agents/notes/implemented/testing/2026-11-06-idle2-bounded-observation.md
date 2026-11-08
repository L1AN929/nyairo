# Agent Note: idle2 独立额度与失败即停的自然观察入口

Status: implemented

## Problem

现有 fast_day 把累计总额同时用于 UTC 日保险丝，不能表达历史490、累计1000、本run新增20、UTC300四个分域；普通 resident 的失败也不等于整批永久关闭。只看 candidate_count 或 ACTED 会把候选数冒充闲适 offered，把链条执行冒充完成。

历史检索分类：[压缩时钟](../architecture/2026-10-23-explicit-compressed-demo-clock.md)、[G2 D2](2026-11-03-g2-explore-d2.md)是部分重叠，沿用时间分域、历史不灌本run和离线子进程网络守卫；[闲适候选](../feature/2026-11-05-idle-and-dawdle.md)是行为背景，NO_ACTION拒绝含义不变。没有吸收、删除或归档旧决定。

## Decision

scripts/idle2_bounded_run.py 提供 offline-check/prepare/audit/run/status 薄入口。正式 root/run/domain/authority 固定，prepare/run 只有父级执行。profile 7200真实秒、86400逻辑秒、rate12、08:00逻辑锚；累计1000/history490/new20/UTC300分别落盘，history不是UTC或本run用量。最晚22:30启动、00:50截止新请求、60秒收尾；入口不缩窗、不重开已start的根，不承诺自然选择、G1或因果。

LifeRequestBudget 的 failstop opt-in 默认关闭。开启时所有既有预算包装器的实际调用与失败返回在同一根的 attempt锁内串行，失败同步持久创建不可逆 marker，再允许返回上层；下一次 reserve、发送与同根重装配均拒绝。失败预留不退款，写预算/实验高水位失败也拒绝；停止位写不出时终止进程，不能继续循环。OUTPUT_TRUNCATED、LATE_RESULT_DISCARDED 与 provider/transport 失败分别枚举。已发送 pending 结果不重放。

fast_day supervisor 读本根 marker 后走既有 pause/control/terminate 自有resident与owner；终态不伪报成功。UTC配置与累计授权在 resident 内层显式区分，默认路径仍原参数与行为。

安全结果只投影候选source_ref与AG1正式allowed accessibility精确candidate键（不是全部materialized）、正式Decision、Activity完成证据、World消耗证据及reading元字段，不读正文关键词，不复制prompt、completion、reasoning、标题、endpoint或异常正文；缺证据UNKNOWN。audit/status不调用会建锁/推进高水位的预算view，也不建根或起服务。

## Alternatives considered

- 人工每十分钟轮询最简单，也最容易复用既有wake；否决原因是失败到下次轮询之间仍能预留和发送，无法兑现reserve前停批。
- 改默认预算门最方便，所有实验自动获得保护；否决原因是污染其它run既有重试/失败语义，所以只在本实例明确opt-in。
- 复制整个G1 harness能拿到现成批次框架；否决原因是本任务只是自然观察入口，复制会引入无关owner/影子/实验合同，故只参考发送锁边界，复用fast_day正式装配收尾。

## Consequences

8个聚合新case包含真实预算边界、正式assembly cognition/content包装器、重装配拒绝、默认兼容、安全投影与一条真Supply/World/resident supervisor短链。offline-check复用既有AF_INET/INET6/DNS/create_connection硬拒及子进程守卫，AF_UNIX保留；只使用新mkdtemp并保留，不跑旧套件、不碰正式根。

串行模型attempt牺牲本批并发换取失败到后续预留的确定边界；锁属于实验元数据而非新的owner合同。脚本transport替身只证明接线与停止，真实自然闲适、阅读采用及真实provider表现仍未验证。新note仅检查自身四段和相对链接，不全树治理。

后续：本入口已抽出不可变 `Identity`，供 [修后组合唯一新批 A](2026-11-06-combined-natural-a.md) 复用；原批默认值不变。
