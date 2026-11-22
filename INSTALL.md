# 安装与开始使用

当前公开测试版本为 `v0.8.0`（GitHub prerelease，不是稳定版）：[GitHub 仓库](https://github.com/L1AN929/nyairo)、[v0.8.0 发行页](https://github.com/L1AN929/nyairo/releases/tag/v0.8.0)。Windows 10 / 11 用户推荐使用约 6.0 MB 的原生桌面安装包 `nyairo-0.8.0-setup.exe`；Linux 或纯命令行用户可使用下面的一行命令引导安装。历史版本（`v0.7.0`、`v0.6.0`、`v0.5.1`、`v0.4.0`、`v0.3.0`、`v0.2.0-rc3`）标签与附件保持原样归档。

---

## 方式一：Windows 10 / 11 原生桌面端（推荐）

Windows 10 / 11 用户可直接下载免管理员权限的桌面安装包，图形化完成环境准备与日常交互：

```text
https://nyairo.com/downloads/nyairo-0.8.0-setup.exe
```

- **安装包校验（SHA256）**：`36ed8ef7d4aabeb86e1a55fff298eaa2df4a995cd603b845bb885cf81ef76eb8`（约 6.0 MB，无需管理员权限，不要求预装 Python）。首次运行若弹出 Windows SmartScreen「已保护你的电脑」提示，点击「更多信息 → 仍要运行」即可。
- **运行环境与目录隔离**：桌面程序安装在 `%LOCALAPPDATA%\Programs\nyairo`（桌面端自身配置与日志位于 `%LOCALAPPDATA%\nyairo`）；后台个体运行时运行在系统自带的 WSL 2（Ubuntu）内，首次打开桌面端会按六步向导自动检测并准备环境。
- **个人数据与备份位置**：人设 `SOUL.md`、聊天记录、SQLite 记忆库与生活状态保存在 WSL 2 内的 `~/.nyairo` 目录中。程序与个人数据严格分离，升级或重装桌面端只替换程序目录；**升级或回退前，请先备份 WSL 2 内的 `~/.nyairo` 目录**（或在桌面端「设置 → 环境与诊断」导出脱敏便携旅行箱 `.nyairo-suitcase.zip`）。
- **模型与手机连接**：装好后在「设置 → 模型」填入你自己的服务商 API Key（如 DeepSeek、OpenAI、Qwen 或本地 Ollama），即可开始对话；也可在「设置 → 连接」直接扫码接入个人微信或绑定 Telegram / QQ 机器人。

---

## 方式二：一条命令引导安装（Linux / Windows WSL 2）

Windows 命令行用户先打开 WSL 2（Ubuntu）；Linux 用户打开终端，使用普通非 root 账号运行：

```bash
curl -fsSL https://nyairo.com/install.sh | bash
```

引导脚本会自动准备 `uv` 与 Python 3.13，下载并校验 `v0.8.0` 发行包、安装依赖、建立独立个人配置目录，随后打开模型设置向导。若 Ubuntu / Debian 尚未安装 `curl`，可先运行 `sudo apt update && sudo apt install -y curl`。

- **程序目录**：`~/.local/share/nyairo/releases/v0.8.0`
- **个人数据**：`~/.nyairo`（含人设 `~/.nyairo/SOUL.md`、模型配置与长期记忆数据库）
- **命令入口**：`~/.local/bin/nyairo`（日常聊天运行 `nyairo`；更换模型用 `nyairo setup model`；接入微信 / Telegram 用 `nyairo gateway setup` 与 `nyairo gateway run`）

服务器上仅安装程序、暂不打开交互向导或聊天：

```bash
curl -fsSL https://nyairo.com/install.sh | bash -s -- --no-setup --no-launch
```

自定义安装目录或指定已有个人目录（不会自动覆盖旧文件）：

```bash
curl -fsSL https://nyairo.com/install.sh | bash -s -- --prefix "$HOME/apps/nyairo" --profile "$HOME/.nyairo" --no-launch
```

引导脚本位于仓库 [scripts/bootstrap.sh](scripts/bootstrap.sh)，官网 `/install.sh` 发布同一份内容，两份脚本逐字节一致。

---

## 方式三：手动下载 ZIP 校验安装

如果你希望先人工核对压缩包哈希再运行安装，可在 Linux 或 WSL 2 终端中按以下步骤操作（全程使用普通账号，不要使用 `root`）：

```bash
# 1) 下载 v0.8.0 发行包与 SHA256 校验文件
curl -fsSL -O https://github.com/L1AN929/nyairo/releases/download/v0.8.0/nyairo-v0.8.0.zip
curl -fsSL -O https://github.com/L1AN929/nyairo/releases/download/v0.8.0/nyairo-v0.8.0.zip.sha256

# 2) 校验哈希（不一致请立即停止）
sha256sum -c nyairo-v0.8.0.zip.sha256

# 3) 解压、校验内部文件清单，并写入已核验的发行包哈希记录
umask 077
mkdir -p "$HOME/.local/share/nyairo/releases/v0.8.0"
python3 -m zipfile -e nyairo-v0.8.0.zip "$HOME/.local/share/nyairo/releases/v0.8.0"
python3 "$HOME/.local/share/nyairo/releases/v0.8.0/scripts/verify_manifest.py"
awk 'NR==1 {print tolower($1)}' nyairo-v0.8.0.zip.sha256 > "$HOME/.local/share/nyairo/v0.8.0.source-sha256"

# 4) 使用随包引导脚本完成依赖装配与个人目录初始化（识别本地已核验目录，无需重复下载 ZIP）
cd "$HOME/.local/share/nyairo/releases/v0.8.0" && bash scripts/bootstrap.sh
```

`scripts/verify_manifest.py` 输出 `"valid": true` 表示包内文件集合与逐文件 SHA256 完整无误（注意：包内文件使用 LF 行尾，请勿在 Windows 编辑器中批量转换为 CRLF 后再做校验）。

若需手动分步执行 `uv sync` 与 `setup_profile.py`，可参考 [详细中文教程](docs/USER_GUIDE.zh-CN.md) 中的手动章节：

```bash
cd "$HOME/.local/share/nyairo/releases/v0.8.0"
uv sync --project vendor/hermes --frozen --extra messaging --extra web
vendor/hermes/.venv/bin/python scripts/setup_profile.py \
  --home "$HOME/.nyairo" --owner local-owner --memory --life --allow-local-owner
HERMES_HOME="$HOME/.nyairo" bash scripts/update.sh --adopt \
  --releases-dir "$HOME/.local/share/nyairo/releases"
```

---

## 旧配置升级、权限边界与平台范围

- **升级与回滚**：已有实例请勿重新运行初始化脚本覆盖数据。升级前先备份 `~/.nyairo`，再按 [升级与回退说明](UPDATE.md) 执行 `nyairo update`（或 `nyairo update --rollback` 无损回退），并对照 [隐私修复与升级](docs/audits/PRIVACY_REVIEW.md) 检查本地授权与通道白名单。你写好的 `SOUL.md` 与历史记忆会完整保留。
- **默认权限边界**：创建个人目录时，只有显式传入 `--allow-local-owner` 才授权当前本机账号作为主体入口；微信、Telegram、QQ 等外部通道均须单独完成扫码或私聊白名单绑定。记忆模块默认拦截底层模型任意读写原始库，用户侧 `/chiyo_memory` 命令正常可用。
- **后台自主生活（默认关闭）**：基础安装不会在后台静默消耗模型额度。如需开启按节律散步、写手记与每日用量封顶（`max_cognition_calls_per_day` / `max_actions_per_day`），请按 [日常启动一页说明](docs/DAY_START.zh-CN.md) 开启与查看状态。
- **平台支持现状**：当前已验证交付覆盖 Windows 10 / 11（原生桌面端 + WSL 2）与主流 Linux 发行版（Ubuntu / Debian 等）；macOS 暂仅支持按源码方式自行尝试，完整功能边界与验收范围详见 [验收与复测](TESTING.md) 与 [已知问题](KNOWN_ISSUES.md)。
