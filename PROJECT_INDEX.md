# 项目索引

NyAiro 后端：她的生活运行时（Life）+ 手机壳（P）+ Supply/World 两侧服务。
当前主线是 v0.4「旧作重访」：能读自己的旧版本、能对比、能从旧版另起一件新作品，
并让正常写作带上上一版的一小段上下文。

**这里是路由表，不是摘要。** 先按任务找到该读的文件，再只读命中的章节；
不要整目录读，也不要把本文件当必读清单。

## 按任务找文件

| 要做的事 | 先读 |
| --- | --- |
| 了解施工规矩、监工与施工怎么分工 | `AGENTS.md` |
| 看现在做到哪、还差什么、哪里是红的 | `NOW.md` |
| 跑测试、起夹具、在浏览器里点验、派工 | `RUNBOOK.md` |
| 接续上一次压缩前的进度 | `python scripts/task_resume.py` → `.agents/continuity/current.json` |
| 本轮施工方案（四项、写集、门禁） | `docs/engineering/v04-work-revisit-supervisor-plan.md` |
| 本轮已交付的结果与证据 | `docs/engineering/v04-work-revisit-result.md` |
| 查历史决策（承重墙） | `.agents/notes/implemented/`，先搜索再读命中 |
| 手机端 HTTP 面与路由 | `components/phone/server.py` |
| 手机端前端（单文件 app.js） | `components/phone/web/app.js` |
| 私人项目的服务层（版本、对比、派生） | `components/life/scripts/private_project_service.py` |
| Life 写作器（提示词、旧版摘录） | `components/life/scripts/model_continuation_writer.py` |
| Life 主循环（写作上下文在此组装） | `components/life/scripts/autonomous_life_cycle.py`，搜 `_next_content` |
| 工件端口（list_versions / read_version） | `components/life/scripts/life_artifact_port.py` |
| 生活装配（模型 writer 在此构造） | `components/life/scripts/autonomous_life_assembly.py` |
| 总体设计（仓库外） | `D:\CHIYO-总体设计-v2.0-final` |
| 派工脚本 | `scripts/dispatch_worker.py` |
| 手机端 Edge 夹具 | `scripts/phone_edge_fixture.py` |
| 测试入口（全量） | `scripts/test.py` |

## 三条硬约定

1. 作品就是 Supply 的 `ManagedArtifact`；界面不猜项目、不替 owner 签字、不 mock 数据。
2. 一个写集同一时刻只有一个 writer；并行只在写集完全不相交时开启。
3. 灯不绿不算交付：验收以实际跑过为准，不以“看起来对”为准。

## 目录速览

- `components/life/` — 生活运行时、写作、记忆、实践
- `components/phone/` — 手机 HTTP 面与前端（`web/app.js`、`web/style.css`）
- `components/supply/`、`components/world/` — 供给与世界的服务
- `tests/` — Python 套件 + 前端 `*.mjs`（`node --test`）
- `scripts/` — 运行、测试、派工、续接
- `.agents/` — 决策笔记、续接检查点、派工日志
