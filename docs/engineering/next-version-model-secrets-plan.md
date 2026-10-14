# 下一版：模型密钥的配置与管理（方案）

状态：**待评审的提案**，尚未实现。目标版本：v0.2 之后的下一版。

## 1. 为什么做
当前给一台新装的实例配模型，只能二选一：

- 在服务器上跑交互向导 `nyairo setup model`（需要终端）；
- 或者手工编辑 `profile/config.yaml` 的 `model:` / `providers:` 段，再把密钥写进 `profile/.env`（600）。

网页端（`app.nyairo.com`）**没有任何密钥入口**：`nyairo_web` 现有端点是
`health` / `system/info` / `system/diagnostics` / `life/status` / `life/timeline` /
`world/status` / `memory`（列表、forget、correct）/ `identity`（GET、PUT）/ `chat/*`，
设置页只能改人设（`identity` 的 `soul` 与 `display_name`）。结果是：**换个模型、换个 key
必须 ssh 到机器上改文件**，这对普通用户不可用。

## 2. 现状证据（实现前的事实基线）
| 事实 | 位置 |
| --- | --- |
| 交互向导存在 | `nyairo setup model`（等价 `hermes setup model`，支持 `--non-interactive`） |
| 外部密钥管理器存在 | `nyairo secrets bitwarden \| 1password`（启动时从外部拉 key，不落 `.env`） |
| 密钥当前存放 | `profile/.env`（600，明文）+ `config.yaml` 里 `key_env: <VAR>` 引用 |
| 生活侧读取口径 | `components/life/scripts/profile_model_config.py`：`load_configured_model_config()` 认 `model:` + `custom_providers:`（`base_url`/`key_env`），密钥来自环境变量**或 `profile/.env`**；`describe_model_config()` 只回报 `api_key_present`，不回显密钥 |
| 日志脱敏已有基础 | `nyairo_web/log_redaction.py` |
| 演示实例的"密钥只在内存"做法 | `/opt/nyairo-web-candidate-20261011/start_candidate.py`：把 key 复制成 `NYAIRO_MODEL_SECRET_*` 环境变量，只存在于进程内存 |

## 3. 提案
### 3.1 后端（`nyairo_web` 新增 `secrets` 服务）
| 方法 | 路径 | 行为 |
| --- | --- | --- |
| GET | `/api/nyairo/v1/secrets/model` | 只返回**状态**：`configured`（true/false）、`provider`、`model`、`base_url` 主机名、`key_env` 名、`updated_at`。**永不返回密钥本身，也不返回密钥长度** |
| PUT | `/api/nyairo/v1/secrets/model` | 写入 provider/base_url/model/key：`config.yaml` 写路由（`key_env` 指向固定变量名），`profile/.env` 写密钥（0600，原子替换）。响应只回 `api_key_present: true` 与 `updated_at` |
| POST | `/api/nyairo/v1/secrets/model/test` | 用刚写入的配置发**一次最小真实请求**做校验；请求前明确提示会消耗一次调用；只回报成功/失败与错误类别，不回显密钥 |
| DELETE | `/api/nyairo/v1/secrets/model` | 删除密钥（保留路由或一并清空，二选一，见待定项） |

实现要点：
- 写盘前校验：非空、无换行/控制字符、长度上限；变量名固定白名单，不接受客户端指定任意变量名。
- 写盘后触发配置重载；失败则回滚到写入前的字节（保留备份副本）。
- 复用 `log_redaction.py`；在服务层再兜一层：任何异常信息先过脱敏再返回。
- 端点鉴权沿用现有 Bearer 令牌；额外建议见待定项 4。

### 3.2 前端（设置页新增"模型与密钥"卡片）
- 供应商选择（内置常见 `base_url` 预设 + 自定义），模型名输入。
- 密钥输入：`type=password`、**只写不回显**；已配置时显示"已配置（最后更新 时间）"，提供"替换/删除"。
- "测试连接"按钮，明确标注会消耗一次调用。
- 保存成功后不把密钥写进任何前端状态、不做 toast 回显、不进 URL。

### 3.3 存储与安全
- 阶段一（最小可用）：`profile/.env` 0600 明文 + `key_env` 引用（与现状一致，用户无需学新概念）。
- 阶段二（可选加密）：引入 `secrets.enc`，用实例本地主密钥加密（口令派生或机器绑定），启动时解密进内存，磁盘上不留明文；保留"外部密钥管理器"路径（`nyairo secrets`）。
- 硬边界：密钥不进日志、不进 git、不进错误信息、不进前端状态；文档与测试里只出现变量名与 `api_key_present`。

## 4. 待拍板（实现前需要决定）
1. **范围**：只做模型密钥，还是同时做平台 token（Telegram BotFather token、QQ/微信等）？
2. **是否加密存储**：够用的 0600 明文，还是要引入主密钥/口令做静态加密？（影响复杂度与"忘记口令"的支持成本）
3. **入口**：Web 设置页、CLI 向导、还是两者都要（推荐两者：CLI 给服务器场景，Web 给普通用户）。
4. **权限**：密钥端点是否要在现有 Bearer 令牌之外再加一层（例如仅回环可写、或写入需二次确认）？
5. **多实例**：同一台机器多个 profile 时，Web 端是否允许切换/分别管理密钥（当前 `identity` 是按 profile 绑定的）。

## 5. 验收（实现后必须能证明）
- 全新实例：不 ssh、只在网页里填 key → 聊天可用；`GET` 端点回 `api_key_present: true`，**响应体里不出现密钥子串**（用测试断言扫描）。
- 错误 key：`test` 端点返回明确失败类别，不泄漏 key。
- 日志：跑一遍完整流程后 `grep` 密钥子串在 `profile/logs/*`、`nyairo_web` 日志中命中 0。
- 回滚：写坏配置后可恢复到写入前字节。
- 权限：非 owner 的进程读不到 `profile/.env`（0600 断言）。
