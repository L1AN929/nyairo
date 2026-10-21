# 当前状态（对着总纲逐条标）

更新：2026-10-21。设计基准是 `D:\CHIYO-总体设计-v2.0-final\CHIYO-总体设计-v2-最终版`
（设计 2.0，不等于软件 2.0）；软件在 0.4，分支 `work/rc1-phone-world-body`，底座
`41e818faaa24115fbf43db2977b15585c04144af`。下面的状态只对当前工作树成立，
不换算成“全项目完成百分比”——总纲 07 章明确禁止这样合成。

状态只有三档：**有真实链证据**（真 owner、真进程或真 HTTP 跑通过）、
**半做**（有代码和局部验证，但缺口列在右边）、**没做**。

## 〇、开工顺序：先核查，再施工

下面第二节是我读现有报告后的**初判，不是定论**。新窗口不要拿它直接派工，
先派只读子代理对着总纲核查一遍，再回来改这份表、再出任务书。

1. **只读核查（并行，不改任何文件）**。按 EP 分组派 2–4 个 Explore/只读代理，
   每个代理只看自己那组：已做/半做/没做、证据文件、缺口，三样写清。
   建议分组：
   - A：EP-01～03、EP-06～08（宿主、owner、决定/动作/后果）
   - B：EP-09～12（记忆、World/Artifact、Body、未来事项）
   - C：EP-13～18（共在、偏好技法、手机房间、调度、恢复）
   - D：EP-19～22 + 总纲路线图 P1/P2 出口门（没做的部分和 G1/G2 判据）
   代理必须引用具体文件与测试名；找不到证据就写“未找到”，不许猜。
2. **对表**：核查结果与本文件冲突时，以核查为准，改 NOW.md，再写任务书。
3. **施工**：任务书四件事齐全才派工；一个写集一个 writer；监工独立复跑门禁。

## 一、对照总纲 EP-01～EP-22（初判）

总纲把工程分成 22 个工作包（14 章）。逐条如下，状态待第〇步核查。

### 有真实链证据

- **EP-06 决定接纳与活动承接**：单前台、pause/lease/预算、WAIT 释放前台、
  明确 RESUME 原 Activity、joint 结束后续作资格，都有真实 owner 两进程链。
  缺口：选择与内容多为脚本替身，不是真模型自主选择。
- **EP-10 World 对象与 Artifact**：移动/手持/放置、购买→产权→送达→布置、
  作品多版本读写与比较、派生作品、私有项目创建/续写/归档。缺口：隐私删除后
  不复活（V-C）未系统验；历史目前只完整到“读某一版”，跨版本回溯产品化有限。
- **EP-12 未来事项初版**：稍后事项落盘、到期重考、取消/改期、park 后做别事再回原事。
  缺口：有界初版，不是承诺/日历系统。

### 半做

- **EP-01 运行链与文档基线**：仓库文档和检查点齐了；真实宿主、加载路径、
  生产 flags 的现场事实没有本轮复核。
- **EP-02 规范与冲突协调**：总纲内已有 ADR；本仓库靠 `.agents/notes/` 和 AGENTS.md。
  缺口：新改动与总纲条目的映射没有成体系维护。
- **EP-03 owner 与遗留 writer 边界**：Supply/AG1/AG2/AR0/LR4/LR5 已接线；
  遗留 writer 的可达写面没有完整端口图。
- **EP-05 认知工作区与候选生产**：候选来源有 Supply、World 移动机会、研究、
  个人实践、私有项目，prompt 有界裁剪，支持 NO_ACTION。缺口：真实模型采用新候选
  未实测；记忆→候选的因果桥仍薄。
- **EP-07 真实动作与 UNKNOWN**：action ledger、幂等、UNKNOWN、reconcile 底座在；
  缺口：撤权、部分效果、永久未决等故障链只有局部复验。
- **EP-08 后果结算与分域消费**：AR0/AR1、LR5、receipt 闭合都有；缺口：不是所有
  后果都有真实消费者，跨域消费仍零散。
- **EP-09 记忆准入、回忆与删除**：M0 唯一写口、显式启用、治理后召回、continuity。
  缺口：原实例 Memory 仍 UNBOUND；删除屏障/旧备份复活（V-C）未验。
- **EP-11 Body 与资源容量**：六项身体变量、负荷/休息、灯光暴露、姿态约束。
  缺口：无营养/饥饿，REST 只到机制证据，未跑 24 小时。
- **EP-13 关系、共在与披露**：本地来话、共同实践、草稿→延后→本地投递、来源撤权扣留。
  缺口：只到 local_inbox，外发关闭；真实模型接纳曾 INVALID_SCHEMA，自然共处未证。
- **EP-14 / EP-15 偏好、习惯与个人技法**：有范围暂定做法、试用、反例、修订、退休，
  做法能进决策和写作。缺口：不是可执行 Skill，稳定偏好/跨情境技法未证。
- **EP-16 房间、手机与作品体验**：房间和手机已接真实状态；笔记/收藏/钱包/购物/
  阅读/项目/研究/共享实践都有。缺口：相册、联系人、日历、消息、应用商店未交付；
  历史版本界面本轮刚接上，尚未真浏览器点验。
- **EP-17 唤醒、调度与低预算**：resident/runner、预算账本、静默可区分、coalescing 底座。
  缺口：低预算诚实降级只有局部证据。
- **EP-18 持久化、恢复与迁移**：CAS、ledger、crash/reconcile、跨进程同状态续接。
  缺口：同根 runtime 重启的完整验收（本版本第 4 项）未认证。

### 没做

- **EP-04 观察、信念与来源生命周期**：只有 SELF_DECLARED 来源声明，没有
  observed/reported/inferred/imagined 的完整生命周期与纠正失效。
- **EP-19 模型迁移与因果反证**：0.4 未做；只有更早版本的局部因果实验。
- **EP-20 发行、普通账号体验与运行验收**：未做，没有发布/部署/独立安装验收。
- **EP-21 数字原生世界与探索**：未做。
- **EP-22 长期社会、fork 与供给**：未做，仅保留设计。

### 总纲路线图对应

0.4 大致是 **P1 第一生活闭环 + P2 日常承接的第一批切片**。出口门 G1（含中断、重启、
UNKNOWN、版本冲突、NO_ACTION 的完整端到端）还差“同根重启”这一条；G2（历史影响实际
选择、静默可区分、低预算接下）只到机制层。P3 及以后未开始。

## 二、v0.4「旧作重访」四项收尾

1. **手机接历史/对比/派生：代码完成**。`app.js` 已接 4 条 GET 和 `POST /projects/derived`；
   `node --test tests/test_phone_projects_ui.mjs` 13/13。浏览器只在夹具上点过，
   真实 Edge 未点验。
2. **Edge 真实点验：只到夹具级**。用 `scripts/phone_edge_fixture.py` 起真实 HTTP，
   看全部版本→开 v1/v2→对比→派生都通了，派生真的建出第二件作品；owner 是内存替身，
   真实 Supply owner 未走界面。
3. **Life 写作带上一版：完成**。`_next_content` 注入有界 `earlier_versions`
   （≤2 条、≤600 字、版本固定、失败静默）。WSL 下两个套件 21 例通过。
4. **同根 runtime 重启验收：未做**。需要 WSL 起同根实例，未认证就不要说做完。

顺手修的真 bug：`private_project_service.read_project_version` 正常路径漏了
`return answer`，导致单版本接口 500。

## 三、本轮改动（已提交 b005fc7）

- `components/phone/web/app.js`、`tests/test_phone_projects_ui.mjs`
- `components/life/scripts/autonomous_life_cycle.py`、`tests/test_v04_work_revisit_writer_chain.py`
- `components/life/scripts/private_project_service.py`（一行 bug 修复）
- 准备件：`AGENTS.md`、`CLAUDE.md`、`PROJECT_INDEX.md`、`RUNBOOK.md`、`NOW.md`、
  `scripts/dispatch_worker.py`、`scripts/phone_edge_fixture.py`、
  `docs/engineering/v04-work-revisit-supervisor-plan.md`

以上连同 0.4 既有开发已在 2026-10-21 一并提交为 `b005fc7`，工作树现在是干净的。

## 四、已知红（不是本轮引入）

- `tests.test_life_v1_restart`：既有失败，回退本轮改动后同样红。
- `tests.test_v04_private_projects_stage.LifeOwnerAdapterTest
  .test_gateway_creates_one_project_through_the_owner`：旧测试按裸 id 断言，
  现实现有意保留 `artifact:` 前缀。
- 全量 `scripts/test.py` 本轮没跑，别当成全绿。

## 五、下一批该做什么（核查后再定，下面是初判）

0. **先做第〇步核查**。下面四条只是我基于现有报告的排序，核查后可能改。
1. **先收口旧作重访**：真实 Edge 点验 + 同根重启。这两条不做，G1 就不闭合。
2. **补 EP-09 的删除/撤权链**（V-C）：现在最像“做完”的模块缺的正是这个故障面。
3. **补 EP-16 的手机剩余件**：相册/联系人/日历/消息里挑用户真会用的，不一次全铺。
4. **EP-19 再动**：需要固定状态和材料，先做实验协议，不先写功能。

更细的分批记录在 `docs/engineering/v04-design-progress.md`，那里按施工批次写，
本文件只按总纲章节标状态。验收口径按总纲 14 章 V-A～V-E 分组报告，
不看测试条数，也不拿单个差异证明因果。
