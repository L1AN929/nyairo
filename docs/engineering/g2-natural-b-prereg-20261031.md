# G2 b 批预登记（离线 profile；真实运行未发生）

> 注：本文记录的内部网关地址已脱敏为 `gw.example.invalid`；历史观察、失败与结论保持原样，不表示当时实际使用占位地址。

日期：2026-10-31。本文只登记 `scripts/g2_next_live.py` 新增的封闭 b profile 与固定 CLI，
不构成新的真实运行授权。**2026-10-31 21:54 本人直接批准新时段（启动线 22:30 / 截止
2026-11-01 02:00）；旧时段（3 小时采集 / 启动线 21:08:24 / 截止 00:38:24）已过期且从未真实运行，
其历史保留如下文"时间修订"节。**

## 固定身份

| 项 | 值 |
| --- | --- |
| root | `/root/nyairo-g2-natural-20261031-b` |
| main | `<root>/run/main` |
| authority | `<root>/run/memory-authority/authority.db` |
| branches | `<root>/run/branches` |
| run_id | `run:g2-natural-20261031-b` |
| domain | `g2:natural:20261031-b` |

与 a 完全不同的根/身份，不读、不写、不重放 a 的任何数据。a 仍是缺省 profile，常量与行为不变。

## 参数

- 历史 186 / 总 300；新增硬 cap 52 = 采集 8 + 影子 40 + 承接 4 + 诊断 0；每臂 2。
- 采集 9000 真秒（2.5 小时），观察间隔 60 秒，纯读上限 151 次（9000/60 的检查时段约 5 个，
  是纯读观察次数，不是 5 次模型请求）。
- 整批 `total_window_seconds = 11400`（3 小时 10 分），且被固定绝对截止截断；22:30 启动时
  整批自然终点约 01:40，02:00 是外层更早兜底，不在总窗后偷偷加时间。
- 绝对截止：`2026-11-01T02:00:00+08:00`（不滚动、不延后）。
- 启动线 `latest_start`：`2026-10-31T22:30:00+08:00`。**到点及之后真实 run 一律拒绝**
  （`LATEST_START_PASSED`），且拒绝发生在读状态、写 window/started、发请求之前。
- 30 分钟检查间隔、静默 reconsideration、TTL、trigger、合法集、候选 source 与 adapter
  全部沿用既有实现，未改。
- 模型参数与 a 相同：`custom` / `https://gw.example.invalid/v1` / `cn:deepseek-v4.1-flash`，
  温度 0、非流式、决策 4096 / 内容 2048。本 profile 不保存也不输出密钥。

## 精确 CLI

```bash
python scripts/g2_next_live.py prepare --batch b                 # 真实 prepare（需先确认 b 根不存在）
python scripts/g2_next_live.py audit   --batch b                 # 纯读审计，不拒绝过期审计
python scripts/g2_next_live.py run --batch b --execute-approved  # 线后必拒：LATEST_START_PASSED
```

缺省 `--batch` 仍是 `a`，旧命令行为不变。夹具路径一律需要 `--offline-fixtures --root <tempdir 新 scratch>`，
a/b 两个正式根都不能夹具运行；夹具运行硬断 HTTP。

## 离线验收

```bash
wsl -e bash -lc 'cd /mnt/d/nyairo-rc1-body-backend && PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B -m unittest tests.test_g2_live_b'
```

父级独立复跑18例，20.960秒、OK/exit0；正式b根随后只读确认ABSENT，未进行prepare/source/run。
worker报告另执行过期真实run并被拒绝，属约定测试之外的执行偏差，父级没有重跑该命令。

18 例，覆盖：a 缺省未变、b 身份/预算/时间、b CLI prepare/audit、`latest_start` 前后机械注入
（不改系统时钟）、第二次 run 拒绝、跨 a/b 身份拒绝、夹具根限制、审计纯读且新增参数可读。

## 时间修订（2026-10-31 21:54 本人直接批准）

- 旧时段：采集 10800 / 纯读 181 / 整批 14400 / 启动线 `2026-10-31T21:08:24+08:00` /
  截止 `2026-11-01T00:38:24+08:00`。该时段已过期，**从未真实 prepare/source/run**，历史保留。
- 新时段：采集 9000 / 纯读 151 / 整批 11400 / 启动线 `2026-10-31T22:30:00+08:00` /
  截止 `2026-11-01T02:00:00+08:00`。cap 52、history 186、起点（两中性作品/无在办 Activity/
  无灌 H）、30 分钟检查间隔、TTL、source 与模型参数全部不变。

## 风险与边界

- 旧时段已过且未真实运行；新时段给到 22:30 启动线，过线仍不运行、不滚动延时、不缩采集凑可跑。
- 夹具正控（线前注入）用虚拟时钟与脚本 Decision，只证明接线，不是真实模型行为。
- 真实 provider 失败分类、8 次采集能否自然建起 H、9000 秒内能否形成双合法场景、
  真实影子 20 对的功效，都未验证；离线绿不等于 G2 通过。
- 采集 9000 秒后最多约 40 分钟写给 40 次影子 + 4 次承接，**不承诺跑完**；QUIET、无 H、
  场景不可比均为正常出口，不因此追加时间或补第二次 run。
- b 的两件中性作品/无在办 Activity/无灌 H/M0 的起点，只在真实 prepare 时建立；当前未创建。
