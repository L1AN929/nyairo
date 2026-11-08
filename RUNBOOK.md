# 运行手册

只写“怎么做、前置条件、怎么算成功”。命令都是在本仓库根目录执行。

## 环境

- Windows 11 + git-bash；`python` 是 3.13，没有 `python3`。
- 依赖 Life 的套件在 Windows 原生跑不了（`fcntl`），要用 WSL：
  `wsl -e bash -lc "cd /mnt/d/nyairo-rc1-body-backend && python3 ..."`
- 测试依赖装在临时 venv 里，见 `requirements-dev.txt`；没有 venv 时 `python -m pytest` 会报找不到模块。

## 跑测试

全量（较慢，按需）：

```bash
python scripts/test.py
```

本轮相关的四组：

```bash
node --test tests/test_phone_projects_ui.mjs
wsl -e bash -lc "cd /mnt/d/nyairo-rc1-body-backend && python3 -m unittest -q tests.test_v04_work_revisit_writer_chain tests.test_v04_work_revisit_history"
wsl -e bash -lc "cd /mnt/d/nyairo-rc1-body-backend && python3 -m unittest -q tests.test_v04_private_projects_stage tests.test_phone_projects_api"
wsl -e bash -lc "cd /mnt/d/nyairo-rc1-body-backend && python3 -m unittest -q tests.test_life_v1_restart"
```

已知红见 `NOW.md`；红的套件不要当成自己弄坏的，先对照 `NOW.md`。

## 在浏览器里点验手机端

起夹具（真实 HTTP、真实前端，owner 是内存替身）：

```bash
python scripts/phone_edge_fixture.py --port 3816
```

打开 `http://localhost:3816/`。路径：主屏 → 私人项目 → 夜航手记 → 查看全部版本 → 点某一版 →
和当前版本对比 / 从这一版派生新作品。

夹具说明写在脚本开头；它证明界面链路，不替代真实 owner 验收。

## 压缩日的逻辑起点与起跑时段

**想让她的一天从早上开始，就用 `--logical-anchor`，不要等真实时间。** 逻辑起点不必跟
真实时间同步，可以虚构：不传时它仍等于按下 `prepare` 的真实时刻；传了就按那个起点算
`horizon_logical`（起点 + `logical_seconds`），下午五点起跑也能让她从早上开始。

```bash
python scripts/fast_day.py prepare --root /abs/new-root --run-id day-x \\
    --memory-authority /abs/outside-root/authority.db \\
    --logical-anchor 2026-11-05T08:00:00+08:00
```

只有**逻辑起点**的本地钟点决定她这一天的开场：起点落在本地 21:00–07:00 仍然拒绝
`FASTDAY_NIGHT_START_REFUSED`（夜间起点把一天的额度烧在逻辑 00–06 点几乎没有候选的
时段上，2026-11-04 u/v 批就是因此空手而归），不传起点时退回看创建时刻，与旧行为一致；
`--clock-only` 的时钟冒烟不拦。确实要从夜间开场才显式加 `--allow-night-start`，返回里
的 `allow_night_start` / `started_local_hour`（以及传起点时的 `real_local_hour`）
会记下这是显式放行。真实时间那一侧（超时、暂停、预算、心跳、`created_utc`）不跟着变。

## 派工给施工模型

任务正文写进文件（不要放命令行参数，Windows 下中文会被截断）：

```bash
# 普通单（文档、窄改、单组件）
python scripts/dispatch_worker.py --name <工单名> --task-file <任务文件> --timeout 3600 --max-turns 300

# 大型跨组件、含长验收的单
python scripts/dispatch_worker.py --name <工单名> --task-file <任务文件> --timeout 5400 --max-turns 300
```

默认模型 `workbuddy-wg/cn:deepseek-v4.1-flash`，网关 `http://127.0.0.1:3425`。

**CLI 版本**：claude CLI 2.1.265 起会硬拒网关的私有模型 id（`unrecognized_model` 之后直接不发
请求），2.1.263 只打一条告警就照常跑。仓库外的 `D:\Tools\claude-263` 装了那一版，专给 worker 用：

```bash
python scripts/dispatch_worker.py --name <工单名> --task-file <任务文件> \
  --claude "D:/Tools/claude-263/node_modules/.bin/claude.cmd" --timeout 3600 --max-turns 300
```

或者设 `WORKER_CLAUDE` 环境变量，省掉每次传参。2026-11-06 实测 exit=0、模型 DeepSeek、
工具往返正常。升级或重装 CLI 后回来重验这一条。
日志落在 `.agents/worker-runs/`。施工跑完监工独立复跑任务书里写的那几条验收命令，
不看施工自述；任务书之外的全量套件和浏览器点验不在监工会话里跑。

监工直接跑这条命令，不要再套一层子代理转手（见 `AGENTS.md` 第 3 节第 7 条）。
要并行就后台起多个 worker，写集必须互不相交，按日志和退出码跟踪进度。

轮数按用途给，不要一套数字用到底：只读核查 20–30 轮，并写明最多读哪几份文件；
施工一律 `--max-turns 300`。时间按单显式给：普通单 `--timeout 3600`，大型跨组件且含
长验收的单 `--timeout 5400`。轮数与时间是两个独立约束，90 分钟不等于 450 轮，也不追加
产品调用额度；`dispatch_worker.py` 的默认值不改。worker 用的是便宜的 DeepSeek，
轮数给足比中途断线重派划算。任务书里写清“改完必须跑验收并汇报，不要磨到断线”。
到点超时（退出 124）后先确认原 writer 已停、无残留，再按原任务验收盘上产物：
有绿收绿、只返修缺口，不整单重派；不能单凭退出码判成果全败，也不能直接认完成。

## 接续压缩前的进度

```bash
python scripts/task_resume.py
```

它只读不改。返回 STOP 时按提示先解决具名差异，不要 reset、不要覆盖别人的 dirty。

## 决策门禁

写了 `.agents/notes/` 下的决策笔记后，跑 write-notes 的格式与树校验脚本；
非零退出即视为未达标。具体脚本见 `AGENTS.md` 第 2 节。
