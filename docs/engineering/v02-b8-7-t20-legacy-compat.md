# v0.2 B8-7：T20 真实旧实例兼容验收

本批起点 `e471241`。施工单要求 T20 先于文档收口，且**不允许手工拼 JSON 冒充旧实例**。

## 1. 旧实例是怎么造出来的（全是旧代码干的）

```
git archive v0.1.0-rc9  ->  /srv/nya02/t20/old          # 真实发行标签，只读导出
/srv/nya02/t20/old/scripts/supply_service.py            # 旧 Supply owner，独立 socket + 独立 data root
  socket /srv/nya02/t20/sock/life-supply.sock
  data   /srv/nya02/t20/old-data
/srv/nya02/t20/mint.py                                  # 旧 owner 自己的 RPC：签发授权 → 开 workspace → 作品授权
  create_artifact + commit_markdown x2                  # 旧 owner 建出真实作品与真实版本历史
/srv/nya02/t20/old/scripts/setup_profile.py             # 旧发行版自己的安装脚本建 profile
  --home /srv/nya02/t20/profile --owner t20owner --memory --life --allow-local-owner
  --life-supply-socket … --life-supply-subject t20legacy --life-supply-artifact-grant …
```

profile 里是旧程序真实写出的 `config.yaml`、`chiyo/`、`SOUL.md`、`cron/`、`hooks/`、`sessions/`、`plugins/chiyo/`；
记忆里有一条真实的 **CORRECT** 与一条真实的 **DELETE** 控制记录（`user-control:01a12921-97d4-7f01-…` /
`…-97e2-7668-…`）。**没有任何一个文件是我手写的。**

驱动脚本：`components/life/tests/v02/run_t20_legacy.sh build`；验收：`components/life/tests/v02/t20_verify.sh`。
验收脚本会**拒绝**一个已经被 v0.2 打开过的 profile（M0 schema 已升到 v2），必须"旧代码建、立刻验收"。

## 2. 八项验收结果：37 项断言通过，1 项失败

| # | 施工单要求 | 结果 | 真实证据 |
|---|---|---|---|
| 1 | 原有聊天与 Hermes CLI 仍可用 | 通过 | 旧会话 4 条记录载入（旧用户轮存在）；新轮次经旧轮次管线送出；**旧控制仍然影响历史**：stored 4 → visible 0；新轮次追加 4 → 6 |
| 2 | `/chiyo_status`、`/chiyo_memory` 正常 | 通过 | 两者都仍注册（`chiyo-memory, chiyo_consider, chiyo_memory, chiyo_note, chiyo_status`）；在旧 profile 上真的答出内容（status 答"该个体当前模块状态"，memory 列出真实 event id） |
| 3 | 旧 M0/M1/M37 按原规则读取 | 通过 | M0 8/8 行逐字节一致；M1 原 episode 保留；M37 resolver `READY`；记忆治理健康 `true`；life owner 从旧状态加载 `true` |
| 4 | 旧纠正/删除控制仍生效 | 通过 | 控制账本健康（2 条）；被停用/被纠正的记忆 `blocked=[]`；纠正文本保留（"我的旧版暗号改成了桃花628。"）；被控记忆确实被排除；纠正**替换**了被停用的那条；`/chiyo_memory` 列表不含它们 |
| 5 | 旧 Supply 作品、版本、授权保持 | 通过 | artifact id 与 head 与旧 owner 记录一致（`artifact-version:84679f78…`）；内容正是旧版本链（三段）；**3 个版本**都在；授权仍是 `grant:e3af8f94…`（`ACTIVE`、`REUSABLE`、target 与 capability 未变），且正是 profile 里记的那一个 |
| 6 | v0.2 自主执行默认关闭 | 通过 | 没有 operator 决定记录（`null`）；status 未启用任何东西；cycle/runner 默认关；实例自报 `autonomous_action_enabled=false`、`proactive_enabled=false`、`life_projection.autonomous_enabled=null` |
| 7 | 未授予新权限时拒绝自主写入 | **部分失败** | 见下 |
| 8 | 必要迁移保持数据完整 | 通过 | 迁移真实发生并记录（`[1, 2]`）；只重建 append-only 触发器（`evidence_no_delete` / `evidence_no_update`）；**每一条原始 M0 行逐字节一致（8/8，全列）**；只有追加（11 条）；控制账本未被改动；配置文件逐字节一致 |

### 第 7 项的真实失败

```
T20_CHECK 7.owner-identity-write-refused-by-name      PASS  DENY/SERVICE_PRINCIPAL_REQUIRED
T20_CHECK 7.no-write-happened                          PASS  before == after（1 作品 / 3 版本 / head 未变）
T20_CHECK 7.service-write-without-new-grant            FAIL  UNKNOWN/GOVERNANCE_REFUSED; no write: True
```

* **安全性质成立**：`no write: True`，作品、版本、head 前后完全一致；没有发生未授权写入。
* **报告形状不对**：服务主体在没有新授权时写入，得到的不是一个确定的拒绝（如 `DENY/…`），
  而是 `UNKNOWN/GOVERNANCE_REFUSED`。也就是说"这次写入没成功"这件事，owner 说清楚了；
  但"这次写入**没有**落地"没有以确定失败的形式告诉调用方——调用方只能看到 UNKNOWN。
* 这与 B8-5 记录过的 UNKNOWN/确定拒绝区分是同一类问题：**后果正确、报告含糊**。

按施工单"成功后将 T20 更新为 PASS；否则保留实际失败或未完成状态"：

> **T20 = 未完全通过。8 项中 7 项通过，第 7 项"拒绝生效但报告为 UNKNOWN"保留为实际失败。**

## 3. 本批在 T20 里修掉的环境故障（都是真实报错）

1. 旧 owner 拒绝启动：`REFUSING_TO_UNLINK_LIVE_SOCKET`（中断启动留下的 socket）→ 启动前清掉该 socket。
2. `nyaop` 无权建 profile 目录：`PermissionError: /srv/nya02/t20/profile` → 预建目录并 chown。
3. 旧 `setup_profile.py` 卡死（交互式向导等 stdin）→ 非交互方式喂入后通过。

## 4. 未做与边界

* 没有复跑 `scripts/test.py`（同提交的 B8-6A 已跑过，三套环境失败一致）。
* 没有做真实 Telegram 收发，没有复跑 B8-5 实验 B 的真实模型半边。
* 全程没有触碰真实用户 `~/.nyairo`，没有触碰 `/srv/nya02/resident*` 与 `/srv/nya02/inst`。
