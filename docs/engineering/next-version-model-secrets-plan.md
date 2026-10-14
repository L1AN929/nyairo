# 下一版：模型密钥的配置与管理（方案）

状态：待评审提案（目标版本：v0.2 之后的下一版）。

## 现状
| 维度 | 现状事实与已有基线 | 缺失与痛点 |
| --- | --- | --- |
| 入口 | 仅 CLI（`nyairo setup model`，向导支持 `--non-interactive`）或手动编辑 `profile/config.yaml`（`model:`/`providers:`）+ `profile/.env`（0600） | `app.nyairo.com` 无密钥入口，改模型/密钥须 SSH 到机器 |
| Web 端点 | `nyairo_web` 仅有 `health`、`system/info`、`system/diagnostics`、`life/status`、`life/timeline`、`world/status`、`memory`（列表/forget/correct）、`identity`（GET/PUT）、`chat/*` | 设置页仅能修改 `identity` 的 `soul` 与 `display_name` |
| 存储与读取 | 外部支持 `nyairo secrets bitwarden \| 1password`；`profile_model_config.py` 的 `load_configured_model_config()` 认 `model:` 与 `custom_providers:`（`base_url`/`key_env`），从环境变量或 `profile/.env` 读 key；候选实例 `/opt/nyairo-web-candidate-20261011/start_candidate.py` 存内存变量 `NYAIRO_MODEL_SECRET_*` | 无 Web 写入、持久化与自动化重载链路 |
| 脱敏与规范 | 复用 `nyairo_web/log_redaction.py`；`describe_model_config()` 仅返回 `api_key_present`，不回显密钥 | - |

## 方案
### 后端端点（`nyairo_web` 新增 `secrets` 服务）
| 方法 | 路径 | 行为与安全约束 |
| --- | --- | --- |
| GET | `/api/nyairo/v1/secrets/model` | 仅返回 `configured`、`provider`、`model`、`base_url` 主机名、`key_env`、`updated_at`；永不返回密钥本身及长度 |
| PUT | `/api/nyairo/v1/secrets/model` | 写入 provider/base_url/model/key：`config.yaml` 写路由（固定 `key_env`），`profile/.env` 写密钥（0600 原子替换）；仅回显 `api_key_present: true` 与 `updated_at` |
| POST | `/api/nyairo/v1/secrets/model/test` | 发送最小真实请求校验（明确提示消耗一次调用）；仅返回成功/失败与错误类别，不显密钥 |
| DELETE | `/api/nyairo/v1/secrets/model` | 删除密钥（保留路由或一并清空） |

- **后端实现**：输入校验（非空、无换行/控制字符、限长、白名单变量名）；写盘后触发重载，失败回滚至备份字节；异常经 `log_redaction.py` 脱敏；沿用 Bearer 令牌鉴权。
- **前端设计**：设置页新增“模型与密钥”卡片。供应商预设/自定义 `base_url`，模型名输入；密钥输入框 `type=password` 且只写不回显；展示“已配置（更新时间）”并提供替换/删除与测试连接按钮；密钥不进前端状态、toast 或 URL。
- **存储两阶段**：阶段一使用 `profile/.env`（0600 明文）+ `key_env` 引用；阶段二引入 `secrets.enc`（本地主密钥加密，启动解密入内存，磁盘无明文），保留外部管理器 `nyairo secrets`。密钥严禁进入日志、git、错误信息与前端状态。

## 已定决策（2026-10-14）
1. **范围**：一期仅模型密钥（provider/base_url/model/key）；平台 Token（Telegram、QQ/微信）二期复用同服务。
2. **存储**：一期 `profile/.env`（0600 明文 + 原子替换 + 写入前备份）；存储层插拔解耦，二期切本地主密钥静态加密不改接口。
3. **入口**：网页与 CLI 双端支持（`nyairo setup model` 保留并补非交互参数）。
4. **权限**：写操作强制要求**本机回环 + 现有 Bearer 令牌**双重校验；读操作沿用 Bearer。
5. **多实例**：按 profile 隔离；网页端仅作用于当前绑定 profile，不支持跨 profile 切换。

## 验收标准
1. **网页配置全流程**：全新实例仅经网页填 key 即可聊天；GET 返回 `api_key_present: true`，响应体断言无密钥子串。
2. **测试连接安全性**：错误 key 经 `test` 端点仅返回错误类别，不泄露 key。
3. **全链路日志脱敏**：全流程执行后，`grep` 密钥子串在 `profile/logs/*` 与 `nyairo_web` 日志中命中为 0。
4. **异常自动回滚**：写配置失败时自动恢复至写入前的备份字节。
5. **文件权限合规**：断言 `profile/.env` 权限为 0600，非 owner 进程无权读取。

## 跨端形态与访问探讨（服务器 / 电脑 / 手机）
当前 NyAiro 存在多种物理部署场景，下一版可视化控制台需要兼顾不同设备形态的访问痛点：
1. **云端服务器部署（Linux VPS）**：后端跑在远端服务器上，手机与电脑通过浏览器远程管理。痛点在于公网暴露风险：需要引入轻量一次性 Token 或受限配对码，同时适配私有局域网/Tailscale/反向代理，避免把配置端口裸露在公网。
2. **家用电脑本地部署（Windows / Mac）**：直接以本地轻量 Web 服务或桌面托盘形式启动，电脑浏览器秒开（`127.0.0.1` 极简回环访问），免鉴权或简易本地握手。
3. **手机端移动体验**：控制台界面必须采用移动端优先的响应式布局，无论在手机竖屏查看运行状态指示灯，还是随手粘贴 API Key 测通，都能单手流畅操作，彻底告别在手机上开 SSH 终端敲命令改 YAML 的痛苦体验。
