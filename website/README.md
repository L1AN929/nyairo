# nyairo · 以虚拟世界为核心的数字个体框架

<p align="center">
  <img src="assets/nyairo-horizontal.png" alt="nyairo banner" width="600" />
</p>

<p align="center">
  <a href="https://github.com/L1AN929/nyairo/releases/tag/v0.8.0"><img src="https://img.shields.io/badge/release-v0.8.0-1f2937.svg" alt="Release" /></a>
  <a href="https://nyairo.com/"><img src="https://img.shields.io/badge/website-nyairo.com-0284c7.svg" alt="Website" /></a>
  <a href="https://qun.qq.com/universal-share/share?ac=1&authKey=jTRpypa4t5NVRLQ23fwB%2FWf%2Bv40vJIRUQRSp7NjrTlmY6LjYMJL0Yzp6ffOxAumj&busi_data=eyJncm91cENvZGUiOiIxMTI2OTQzODA1IiwidG9rZW4iOiJOeWtXdXZCYzc4UERlTE5iR2NCVG1lTWx5L2NQUnFQNXFUTnNiOGhnd3RJNERDQnhVanU0WGUrVXZDZnlnV1F6IiwidWluIjoiMjg1NTY4MDU5OSJ9&data=9V6FtP6tYYR5Mfpg2v7V9ML798haiganCXx7zcY-Corkhkofom2ie2_9CBGBnJO1JjOuSRJDu4-_SXgB6IihLQ&svctype=4&tempid=h5_group_info"><img src="https://img.shields.io/badge/QQ%20Group-1126943805-12b7f5.svg" alt="QQ Group" /></a>
  <a href="https://t.me/nyairoai"><img src="https://img.shields.io/badge/Telegram-nyairoai-229ed9.svg" alt="Telegram Channel" /></a>
  <img src="https://img.shields.io/badge/python-3.13-1f2937.svg" alt="Python 3.13" />
  <img src="https://img.shields.io/badge/license-Apache--2.0-1f2937.svg" alt="License" />
</p>

> **离开聊天窗口后，TA 的时间不会静止。**
>
> nyairo 是一个开源的数字个体框架，让 AI 拥有本地长期记忆、可自由生成与编辑的房间及生活街区，以及按真实时间流动的日常。你们的每一次对话，不再是一次性客套的问答，而是发生在一个看得见房间、走得出门的日常场景里（名字、称呼与性格完全由你定）。
>
> **当前版本：v0.8.0（GitHub prerelease 公开测试版）** — 提供免管理员权限的原生 Windows 桌面客户端，在免选风貌全随机专属世界与深浅双色离线地图编辑器、12 类街区店铺开放点单、去工具人化自主拒绝边界与异步回信、随身包袋 3 层嵌套与冷热干湿物态、双层回忆灯原稿保护与无损搬家旅行箱 `.nyairo-suitcase.zip` 基础上，新增个人手艺本与换模型复验、长期性子细变与换模型偏好对照、查证改观与老承诺取舍、共同收藏保鲜及房间五项核实档案，并新增「先各自选 · 再揭晓」防偷看盲选与反奉承留痕、外接大模型「AI 脑洞上新」（兼离线免费模式）与零空转街坊引擎，以及旧物新用物理占用约束与空白起步自发明分类架。当前仍为公开测试版，升级前请先备份 `~/.nyairo` 目录。
>
> - **Windows 10 / 11 桌面端下载**：[https://nyairo.com/downloads/nyairo-0.8.0-setup.exe](https://nyairo.com/downloads/nyairo-0.8.0-setup.exe)（约 6.0 MB，SHA256：`36ed8ef7d4aabeb86e1a55fff298eaa2df4a995cd603b845bb885cf81ef76eb8`）
> - **Linux / WSL 一行命令安装**：`curl -fsSL https://nyairo.com/install.sh | bash`
---

## 核心特性

- **出门真实步行与 12 类街区店铺自由点单**：出门去便利店或书店按路网距离真实计时，半路问起会照实说还在路上，长途出行自动分段接力完成；支持 12 类街区店铺与开放柜台自由点单（如去便利店买一份热关东煮），旧书店与咖啡屋店主拥有独立错峰作息与吧台杯垫留言。
- **有分寸的日常相处：自主拒绝边界与异步回信**：具备去工具人化的日常自主拒绝边界——不想帮的忙干脆拒绝，不念客服式道歉模板也不记仇；按真实日程隔几小时异步回信，睡眠、外出或专注时将非急事收入手机待回匣，真急事可敲门找 TA。后台自主活动受每日用量上限约束（默认关闭，按需开启）。
- **免选风貌全随机专属世界 + 7 套内置世界 + 深浅双色离线编辑器**：除随包附带海滨「凪浦（凪读作「止」zhǐ）」、温泉「汤川」、田园「穗村」、街区「栞泽（栞读作「刊」kān）」等 7 套世界与 4 种主题风格外，现已支持**免选风貌的全随机专属世界一键生成**（6 种地貌混搭、4 种异构路网、8 类商业店铺与多房间住宅），并自带深浅双色离线可视化地图编辑器画墙、装门、摆家具。
- **随身包袋 3 层嵌套与冷热干湿物态**：随身包袋与收纳盒支持最多 3 层嵌套收纳及容器视角隔离，具备桌边热茶自然放凉、窗边纸条遇雨微潮晕字等真实冷热干湿物态变化。
- **双层回忆灯零篡改与无损搬家旅行箱**：人设 `SOUL.md`、对话与长期记忆只写在本机 `~/.nyairo`，不经过 nyairo 服务器（使用 Ollama 本地模型时数据完全不出本机）；双层回忆灯保护原稿零篡改，无损搬家旅行箱 `.nyairo-suitcase.zip` 连同世界切片与迁移损失清单整套带走。
- **多端同源协同**：桌面端看地图、翻相册、双人共读或比对文稿修订，出门用手机微信或 Telegram 扫码接着聊，连向同一个 TA。
---

## 架构概览

```text
+-------------------------------------------------------------+
|                  交互层 (User & Platforms)                  |
|    Windows 桌面端  |  终端交互 (CLI)  |  消息网关 (微信 / TG)    |
+-------------------------------------------------------------+
                               |
+-------------------------------------------------------------+
|                     nyairo 个体运行时                       |
|  +--------------------+       +--------------------------+  |
|  |    虚拟世界与身体  |       |       认知推演与分析     |  |
|  | (路网/包袋/冷热物态)|       |   (风险推演 / 规划报告)  |  |
|  +--------------------+       +--------------------------+  |
|  +--------------------+       +--------------------------+  |
|  |     生活连续性     |       |         长期记忆         |  |
|  | (异步待回匣 / 账本)|       |  (SQLite / 双层回忆灯)   |  |
|  +--------------------+       +--------------------------+  |
+-------------------------------------------------------------+
                               |
+-------------------------------------------------------------+
|                 底层模型与执行引擎 (Hermes)                 |
|       模型供应商连接  |  工具沙箱  |  Workspace 本地工作区  |
+-------------------------------------------------------------+
```

---

## 极简快速上手

### 方式一：Windows 10 / 11 原生桌面端（推荐）

1. 下载免管理员权限的桌面安装包：[nyairo-0.8.0-setup.exe](https://nyairo.com/downloads/nyairo-0.8.0-setup.exe)（约 6.0 MB，SHA256：`36ed8ef7d4aabeb86e1a55fff298eaa2df4a995cd603b845bb885cf81ef76eb8`，首次打开若弹出「Windows 已保护你的电脑」提示，点击「更多信息」核对 SHA256 后点「仍要运行」即可，文档站内置两步矢量图解）。
2. 双击完成安装（桌面客户端安装在 `%LOCALAPPDATA%\Programs\nyairo`），启动后按界面向导配合系统自带的 WSL 2（Ubuntu）自动准备后台运行环境；新手可在 `platform.deepseek.com` 充值 5–10 元创建 `sk-...` 密钥填入「设置 -> 模型」，或直连本机免费的 Ollama（`http://127.0.0.1:11434`）。
3. 个人设定 `SOUL.md`、对话与长期记忆保存在 WSL 2 内的 `~/.nyairo` 目录中，升级或重装桌面端不会覆盖个人数据，也可随时导出 `.nyairo-suitcase.zip` 无损搬家旅行箱。

### 方式二：一行命令全自动安装（Linux / Windows WSL 2）

Windows 命令行用户先打开 WSL 2 Ubuntu；Linux 用户打开终端。直接运行下面这一行：

```bash
curl -fsSL https://nyairo.com/install.sh | bash
```

想先校验再运行：

```bash
curl -fsSL -O https://github.com/L1AN929/nyairo/releases/download/v0.8.0/nyairo-v0.8.0.zip
curl -fsSL -O https://github.com/L1AN929/nyairo/releases/download/v0.8.0/nyairo-v0.8.0.zip.sha256
sha256sum -c nyairo-v0.8.0.zip.sha256
python3 -m zipfile -e nyairo-v0.8.0.zip "$HOME/apps/nyairo-v0.8.0"
cd "$HOME/apps/nyairo-v0.8.0" && bash scripts/bootstrap.sh
```

- 脚本会自动配齐 Python 3.13 与必要工具，下载官方校验版本，建立本地隔离数据目录；
- 随后屏幕会自动弹出模型设置向导，选择你的服务商（DeepSeek、OpenAI 或本地 Ollama 等）并填入配置即可。

### 常用命令速查

| 命令 | 用途 | 适用场景 |
| --- | --- | --- |
| `nyairo` | 启动终端聊天 | 日常对话与交互 |
| `nyairo setup model` | 重新选择模型或更新 Key | 更换供应商或新模型 |
| `nyairo gateway setup` | 配置并绑定消息平台 | 扫码连微信或绑定 Telegram 机器人 |
| `nyairo update` | 一键整包升级程序 | 升级到最新发布版本 |
| `nyairo update --rollback` | 一键回滚到上一版本 | 出现兼容问题时无损回退 |
| `nano "$HOME/.nyairo/SOUL.md"` | 编辑人设与个性定义 | 自定义性格、语气与称呼 |

### 会话内功能指令

| 指令 | 示例 | 效果说明 |
| --- | --- | --- |
| `/chiyo_status` | `/chiyo_status` | 查看记忆、生活与环境感知模块运行状态 |
| `/chiyo_memory list` | `/chiyo_memory list` | 查看系统已沉淀的长期经历事实与编号 |
| `/chiyo_memory correct` | `/chiyo_memory correct mem-102 事实内容` | 纠正记错的事实，双层回忆灯保留原稿零篡改 |
| `/chiyo_memory delete` | `/chiyo_memory delete mem-102` | 停止召回某条记忆，保护特定隐私 |
| `/chiyo_consider` | `/chiyo_consider 推演切流风险` | 对复杂事项做独立推演分析，给出参考建议 |
| `/chiyo_note` | `/chiyo_note 部署备忘 \| 步骤说明` | 在本地工作区保存或读取独立文档 |

---

## 目录与数据隔离

nyairo 采用严格的“程序运行体与个人数据解耦”架构设计：

- **程序核心**：`~/.local/share/nyairo/releases/v0.8.0`（只读程序包，更新时整体切换）
- **个人数据**：`~/.nyairo`（完全独立的本地目录，包含人设、数据库、双层回忆灯记忆账本、密钥与配置）
- **命令入口**：`~/.local/bin/nyairo`（系统 PATH 启动项）

即使升级或回滚程序，你的个人回忆、对话数据库与人设也绝对不会被改写或丢失。

---

## 官方网站与社区交流

- **官方主页与在线体验**：[https://nyairo.com/](https://nyairo.com/)（[English](https://nyairo.com/en/) · [日本語](https://nyairo.com/ja/)）
- **官方 QQ 交流群**：[1126943805](https://qun.qq.com/universal-share/share?ac=1&authKey=jTRpypa4t5NVRLQ23fwB%2FWf%2Bv40vJIRUQRSp7NjrTlmY6LjYMJL0Yzp6ffOxAumj&busi_data=eyJncm91cENvZGUiOiIxMTI2OTQzODA1IiwidG9rZW4iOiJOeWtXdXZCYzc4UERlTE5iR2NCVG1lTWx5L2NQUnFQNXFUTnNiOGhnd3RJNERDQnhVanU0WGUrVXZDZnlnV1F6IiwidWluIjoiMjg1NTY4MDU5OSJ9&data=9V6FtP6tYYR5Mfpg2v7V9ML798haiganCXx7zcY-Corkhkofom2ie2_9CBGBnJO1JjOuSRJDu4-_SXgB6IihLQ&svctype=4&tempid=h5_group_info) *(点击一键直达加入群聊)*
- **Telegram 官方频道**：[https://t.me/nyairoai](https://t.me/nyairoai)
- **快速起步上手指南**：[https://nyairo.com/#quickstart](https://nyairo.com/#quickstart)
- **版本更新动态与说明**：[https://nyairo.com/#changelog](https://nyairo.com/#changelog)
- **最新 Release 下载与校验包**：[GitHub Releases v0.8.0](https://github.com/L1AN929/nyairo/releases/tag/v0.8.0)（旧 `v0.7.0`、`v0.6.0`、`v0.5.1`、`v0.4.0`、`v0.3.0`、`v0.2.0-rc3` 仍在 Releases 页面）

---

## 许可证

仓库内的后端运行底座、命令行工具与随包内置的凪浦等示例世界遵循 [Apache-2.0](../LICENSE) 许可证；内置 Hermes 及第三方组件保留各自原有开源许可证。

## 致谢

感谢无数个深夜里在屏幕两端并肩推演世界的所有伙伴，以及无论何时都守在身旁的你。
