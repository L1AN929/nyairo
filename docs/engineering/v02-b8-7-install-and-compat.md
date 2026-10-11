# v0.2 B8-7 / E13：全新 profile 的真实安装走通记录、版本口径与发布门禁

分支 `v0.2-autonomous-life`，本批起点 `e471241`。本批**只改文档**（`INSTALL.md`、
`docs/USER_GUIDE.zh-CN.md`、`TESTING.md`、`KNOWN_ISSUES.md`、`README.md` 与本文件），
没有修改任何运行时代码、没有改安装器、没有创建标签或 Release。

## 1. 这次真的跑了什么

在一套**全新**的隔离目录 `/srv/nya02/inst/**` 上，用现有入口完整走了一遍：
全新个人目录 → 初始化 → 配置供应商 → 资源服务工作区/授权 → 打开自主生活 → 一次合法作品活动 →
回读真实结果。命令与真实输出逐条抄进 `INSTALL.md` 与 `docs/USER_GUIDE.zh-CN.md`，此处只记结论：

| 观测 | 真实值 |
|---|---|
| subject | `chiyo-install` |
| 工作区 | `workspace:9ba2cd4c8b164d028abe38efcd990132` |
| 授权 | `grant:2fce125a99c540f6948f21ed0e51af78`（`COMMIT_MANAGED_ARTIFACT`） |
| 作品 | `artifact:be778d6d32e94796abffdbf392f7a54c`，v1 head `artifact-version:d5904244225447b3b5e8190bd01ef6b9` |
| 自主写入产生的版本 | **`artifact-version:7da6005463dd4e3d87e5ea20bdf8c3dd`**（v2，parent 是 v1，owner 回执 `OK`） |
| 动作 | `actn:ba85fba2b0ee8de6` / `FILE_ACTION` / `SUCCEEDED`，Activity `actv_d19a84f0063e250b` |
| 决策 | `START`，`outcome=ACTED`，`step_path=AR0` |
| 生活经历 | M0 新增一行 `LIFE_VERIFIED_OUTCOME`（`memory_owner=chiyo`），正文由结构化字段拼成 |
| 真实模型调用 | **2 次**（1 次决策 + 1 次内容生成），没有再发第三次 |

关闭状态的验证也在同一次走通里：新 state root 的开关未设置（`enabled: null`），一次 tick 得到
`DISABLED`（"autonomous life is off; nothing was considered"）、`model_calls 0`、`actions 0`；
缺授权时 `SUPPLY_CONFIGURATION_MISSING`（带 `missing` 列表）、缺供应商时 `NO_CONTENT_WRITER`
（两次都是 `rc=3`，且都没有任何模型调用）。

## 2. 四个词的最小定义

* **state root**：这套生活的状态根目录（默认 `<安装根>/state`）。里面是开关、每日预算、
  唤醒/步骤 journal 与 Activity 快照。一个 state root 只允许一个写入进程，第二个进程被既有
  `ActivityLockError` 拒绝。
* **subject**：主体身份。授权、工作区、作品都挂在它上面；换 subject 就是另一份生活。
* **Supply socket**：本机的 Unix socket 文件，程序靠它找资源服务；不是网络端口，默认 0600。
* **grant**：由管理身份签发、写明"谁、能做什么、作用在哪、能用几次"的凭据。写自己的作品需要
  一份 `COMMIT_MANAGED_ARTIFACT`（限定到自己的工作区）。没有它，写请求被资源服务拒绝。

## 3. 版本口径（rc6 / rc9 的真实情况）

`README.md` 写 rc9，而 `INSTALL.md` 与教程写 rc6。核对结果：

* 仓库标签：`v0.1.0-rc4 … v0.1.0-rc9` 都在；`v0.1.0-rc6` = `5ab5484`（2026-10-06），
  `v0.1.0-rc9` = `56aafd4`（2026-10-09，提交信息 `chore: release NyAiro v0.1.0-rc9`）。
* 公开发行确实存在：`releases/download/v0.1.0-rc{6,7,8,9}/nyairo-v0.1.0-rc*.zip.sha256`
  四个都返回 HTTP 200；rc9 的校验值为 `ec181e819e7d203521f28189b640ecb95734f755e715133f455257dcacb5b583`。
* 网站发布的引导脚本 `website/install.sh:6` 固定 `v0.1.0-rc9`；仓库内 `scripts/bootstrap.sh:6`
  仍固定 `v0.1.0-rc6`。两个文件除这一行外**逐字节相同**（`diff` 只报版本行），
  而 `INSTALL.md` 原文声称两者"发布相同内容"——这句话在 rc9 之后已经不成立。
* `scripts/install.sh` 只做 `uv sync`，不下载任何版本，不是版本来源。

**结论**：当前公开发行版是 **v0.1.0-rc9**；rc6/rc7/rc8 是历史版本。本批把 `INSTALL.md` 与教程里
"今天装到的是什么版本"的说法改成 rc9，历史验收记录（`TESTING.md` 的 rc5/rc6 段、教程第 09 节的
rc6 更新验收）保持原样。**没有改任何安装器**：网站脚本本来就指向 rc9，仓库内那份副本的漂移只记录、
不修改（改它会让"仓库内脚本"变成一个新的发行决定，不属于文档工作）。

## 4. 测量中发现、本批未修的问题

1. **`NYA02_SUPPLY_SOCKET` 不是被接受的 socket 名字。**
   `autonomous_life_assembly.SUPPLY_SOCKET_ENV` 只接受 `CHIYO_NATIVE_LIFE_SUPPLY_SOCKET` /
   `LIFE_SUPPLY_SOCKET` / `CHIYO_LIFE_SUPPLY_SOCKET`；而 `NYA02_SUBJECT`、`NYA02_ARTIFACT_GRANT`
   是被接受的。`run_resident.sh` 只显式传 `NYA02_SUPPLY_SOCKET`，之所以一直能跑，是因为
   `runuser -- env` 保留了启动者环境里由 `$ROOT/env.sh` 导出的 `LIFE_SUPPLY_SOCKET`。
   在新目录里没有 `env.sh` 时，常驻进程会以 `SUPPLY_CONFIGURATION_MISSING` 拒绝启动
   （实测 `missing: ["CHIYO_NATIVE_LIFE_SUPPLY_SOCKET"]`）。本批只在实例环境文件里补上这个变量，
   没有改产品代码。
2. **`model.content_writer` 恒为 `false`。** `autonomous_life_resident.start()` 读
   `assembled.get("writer")`，而 `assemble_autonomous_life()` 的返回字典里没有 `writer` 键，
   所以即使内容写作者真的存在也报 false（本轮它确实存在：写出的正文是真实模型散文）。
3. **预算计数少算一次调用。** `RoundBudget` 的注释说"决策和内容生成都计入认知调用"，但 AR-0 路径
   在 `_classify_ar0_step` 里固定 `llm_calls=1`，所以一次成功写入（1 次决策 + 1 次内容生成）
   只记 1。`counters.model_calls` 同样只反映决策调用。
4. **`REVOKED` 被窄端口分类成 `UNKNOWN`。** `life_artifact_port._outcome()` 只把 `BLOCKED`/`DENY`
   当作明确拒绝；资源服务对已撤回授权回答的是 `status=REVOKED, reason=GOVERNANCE_REFUSED`，
   于是落进"未确认"（实测 `effect_state: UNKNOWN`）。结果是：撤回后的一次写请求虽然确实被拒绝、
   作品未变，但"这是明确拒绝"这一事实在 Life 侧丢失，需要走对账流程才能定性。
5. **M0 的 owner 名与 Supply subject 名是两件事。** 生活经历写入 M0 时用的是事件自己的
   `subject_id`，AG-2 的 handoff 不带它，于是回落到 `chiyo`（与 B8-5 §10 的记录一致）。
   隔离 profile 的 subject 是 `chiyo-install` 时，按 subject 读经历会读到空视图
   （实测：`subject=chiyo-install → 0`，`subject=chiyo → 1`）。单一主体的真实产品里两者同名，
   不会碰到；隔离/多主体实验必须显式知道这一点。

另外，B8-5 记录里"网关从隔离环境不可达"的阻塞**现在不再成立**：本轮从同一环境真实完成了
2 次 provider 调用（`gemini-3.8-flash-high`，`https://gw.929711.xyz/v1`，探测 `REACHABLE`）。
本轮没有复跑实验 B 的真实模型半边，所以那只说明"可达"，不等于那半边已经跑过。

## 5. 证据层级与门禁

逐能力判定见 `TESTING.md` 的 "E13 发布证据门禁"。口径：

`IMPLEMENTED` < `TESTED_ISOLATED` < `E2E_VERIFIED` < `SOAK_VERIFIED` < `DEPLOYED`。

* `TESTED_ISOLATED`：隔离环境里用**真实 owner**（真实 Supply socket、真实 World 服务）跑过，
  但决策来自受控替身。
* `E2E_VERIFIED`：真实 owner 链 **且** 真实供应商（真实模型调用），没有替身。
* 24 小时/7 天没有跑完 → 不得写 `SOAK_VERIFIED`；没有真实生产切换 → 不得写 `DEPLOYED`。

## 6. 本批未做、也不得声称

没有创建标签或 Release；没有部署到任何真实用户实例；没有改动 `main`；没有轮换或改动任何凭据
（旧密钥的轮换仍是账户持有人在供应商后台的事）；没有在真实 Telegram 账号上做收发；
没有跑完 24 小时/7 天；没有修 §4 列出的五个问题；没有重跑 `host-boundaries` /
`cognition-faults` / `Hermes` 三个环境相关失败套件（原因见 `KNOWN_ISSUES.md`）。
