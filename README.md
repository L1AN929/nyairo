# nyairo · 以虚拟世界为核心的数字个体框架

<p align="center">
  <img src="website/assets/nyairo-horizontal.png" alt="nyairo banner" width="600" />
</p>

<p align="center">
  <a href="https://github.com/L1AN929/nyairo/releases/tag/v0.8.0"><img src="https://img.shields.io/badge/release-v0.8.0-334155.svg" alt="v0.8.0"></a>
  <a href="https://nyairo.com/"><img src="https://img.shields.io/badge/website-nyairo.com-0284c7.svg" alt="Website" /></a>
  <a href="https://qun.qq.com/universal-share/share?ac=1&authKey=jTRpypa4t5NVRLQ23fwB%2FWf%2Bv40vJIRUQRSp7NjrTlmY6LjYMJL0Yzp6ffOxAumj&busi_data=eyJncm91cENvZGUiOiIxMTI2OTQzODA1IiwidG9rZW4iOiJOeWtXdXZCYzc4UERlTE5iR2NCVG1lTWx5L2NQUnFQNXFUTnNiOGhnd3RJNERDQnhVanU0WGUrVXZDZnlnV1F6IiwidWluIjoiMjg1NTY4MDU5OSJ9&data=9V6FtP6tYYR5Mfpg2v7V9ML798haiganCXx7zcY-Corkhkofom2ie2_9CBGBnJO1JjOuSRJDu4-_SXgB6IihLQ&svctype=4&tempid=h5_group_info"><img src="https://img.shields.io/badge/QQ%20Group-1126943805-12b7f5.svg" alt="QQ Group" /></a>
  <a href="https://t.me/nyairoai"><img src="https://img.shields.io/badge/Telegram-nyairoai-229ed9.svg" alt="Telegram Channel" /></a>
  <img src="https://img.shields.io/badge/python-3.13-1f2937.svg" alt="Python 3.13" />
  <img src="https://img.shields.io/badge/license-Apache--2.0-1f2937.svg" alt="License" />
</p>

> **离开聊天窗口后，TA 的时间不会静止。**
>
> nyairo 是一个开源的数字个体框架，让 AI 拥有本地长期记忆、可自由生成与编辑的虚拟街区与房间，以及按真实时间流动的日常。你们的每一次对话，不再是一次性客套的问答，而是发生在一个看得见房间、走得出门的日常场景里（名字、人称与性格完全由你定）。
>
> **当前可下载安装包：v0.8.0（GitHub prerelease 公开测试版）** — 提供原生 [Windows 桌面端安装包](https://nyairo.com/downloads/nyairo-0.8.0-setup.exe)（约 6.0 MB，免管理员权限）以及 Linux / WSL 运行包。在 0.7 的全随机世界与离线地图编辑器之上，完整收口 0.2 真实生活闭环：省电休眠档与唤醒合并计数闭环、外置回滚隔离区防止恢复旧备份导致已忘记录复活、零写盘实时真实度白话回执、每轮对话自动接入伴侣侧一键遗忘暂停、工作台草稿外部改动防覆盖核对、公开残片回收夹与阶段生活小结卡片，以及 12 项跨重启/遗忘/迁移端到端验收。并重磅上线「先各自选 · 再揭晓」双边盲选与反奉承真立场保留、外接大模型「AI 脑洞上新」（兼离线免费模式）与零空转承诺驱动街坊引擎、旧物新用物理占用约束、空白起步自发明分类架，以及桌面端首页三联互动卡与聊天工具原生打通。它不是正式稳定版，升级个人实例前请先备份 `~/.nyairo`。旧的 v0.7.0、v0.6.0、v0.5.1、v0.4.0、v0.3.0、v0.2.0-rc3 安装包与标签保持原样，仍可下载。
>
> 在 Linux 或 Windows 的 WSL 终端里，输入这一行就能装好：
> `curl -fsSL https://nyairo.com/install.sh | bash`
---

## About (English)

nyairo is an open-source (Apache-2.0) digital life framework that provides an AI with a virtual world, persistent local memory, and continuous daily routines.

- Local continuity: All memories, dual-layer protected transcripts, and portable `.nyairo-suitcase.zip` archives stay locally in `~/.nyairo`.
- Virtual environment & living agency (v0.8.0): Procedural zero-preset world generation, 7 built-in towns, a dark/light offline visual world editor, 12 shop categories with open-counter ordering, external LLM dynamic shelf refresh (plus free offline mode), zero-idle commitment-driven NPC town engine, two-way SHA-256 sealed blind choices with non-sycophantic stance retention, and repurposed objects with physical occupancy guards.

The recommended Windows install is the native Desktop app (~6.0 MB, no admin rights, paired with WSL 2 Ubuntu):
`https://nyairo.com/downloads/nyairo-0.8.0-setup.exe`

Install via Linux or WSL:
`curl -fsSL https://nyairo.com/install.sh | bash`

nyairo is BYOK-first: the deterministic spatial / walking / matter / permission backbone keeps daily life continuous without spending extra tokens on a judge model, so ordinary models such as DeepSeek, Qwen or a local Ollama work too.

*Note: This is an open test version (v0.8.0), not a stable release.*

If you find this journey of giving AI a continuous life interesting, starring the repo is the best support for us.

---

## 核心特性

- **「先各自选 · 再揭晓」真立场留痕、外接模型脑洞上新与旧物自创分类（v0.8.0）**：支持 SHA-256 暗中封存的双向盲选（揭晓前严格掩码不透题，面对硬劝坚持原选顾虑不滑跪）；支持外接大模型「AI 脑洞上新」（亦可随时切回离线免费）与零空转街坊承诺引擎；旧物可贴签改用并受物理占用约束（杯里插笔暂不可泡茶），分类架从空白起步由 TA 自己起名挪位，同时打通首页三联互动卡与聊天工具。
- **出门真的在走路，12 类街区店铺开放点单**：告别传统聊天框里一句话瞬间瞬移的违和感。出门去便利店走 5 分钟、去公园走 9 分钟，半路问起会如实说还在路上，中转路线自动接力走完；支持 12 类街区店铺与开放柜台自由点单（比如顺路去便利店买一份热关东煮），旧书店与咖啡屋店主拥有独立错峰作息，并支持在吧台留下杯垫留言。
- **有分寸的日常相处：自主拒绝边界与异步回信**：告别百依百顺的工具人客服模板——遇到不想应承的琐事，TA 会干脆拒绝，不念抱歉套话也不记仇；支持按真实日程隔几小时异步回信，睡眠、外出或专注时将非急事收入手机待回匣，真有急事可敲门找 TA。开启后台自主活动后还会按每日用量上限自己散步、看风景或写随笔（默认关闭，按需开启）。
- **免选风貌全随机专属世界 + 7 套内置世界 + 深浅双色离线编辑器**：除随包附带海滨「凪浦（凪读作「止」zhǐ）」、温泉「汤川」、田园「穗村」、街区「栞泽（栞读作「刊」kān）」等 7 套世界与 4 种主题生成器外，现已支持**免选风貌的全随机专属世界一键生成**（6 种地貌混搭、4 种异构路网、8 类商业店铺与多房间住宅），并自带支持深浅双色切换的离线可视化世界编辑器画墙、装门、摆家具与设店面。
- **随身包袋 3 层嵌套收纳与冷热干湿物态**：随身包袋、抽屉与收纳盒支持最多 3 层嵌套收纳及严格容器视角隔离，物品具备真实的冷热干湿物理物态——桌边泡好的热茶会自然放凉，窗边留下的纸条遇雨会微潮晕字。
- **双层回忆灯零篡改与无损搬家旅行箱**：人设 `SOUL.md`、聊天记录和长期记忆只写在本机 `~/.nyairo`，对话直连你配置的模型服务商，不经过 nyairo 的任何服务器（使用 Ollama 本地模型时数据完全不出本机）；双层回忆灯机制严格保护原稿零篡改，记错了可随时纠正或停止召回；无损搬家旅行箱 `.nyairo-suitcase.zip` 可连同世界切片与迁移损失清单整套带走。
- **双人共读与随时深度协作**：支持两人在同一份文稿上共同阅读、批注与历史版本对比；遇到复杂的编程或系统推演问题时，敲句命令就能切换独立推演帮你查代码、理方案。
- **BYOK 轻量化确定性底座**：对话只直连你自己配置的模型服务商（花 5–10 元配好 DeepSeek 密钥，或直连免费的本地 Ollama 均可，Qwen、OpenAI 同理），不额外调用裁判大模型烧 Token；空间、步程、物态与权限由确定性骨架维持，普通模型也能跑出连续生活。
---
## 架构概览

```text
+-------------------------------------------------------------+
|                  交互层 (User & Platforms)                  |
|  Windows 原生桌面端  |  微信 / Telegram 网关  |  终端 (CLI)     |
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

## 快速上手

### 方式一（推荐 · Windows 原生桌面端）

Windows 10 / 11 用户可直接下载约 **6.0 MB** 的原生桌面安装包，免管理员权限，双击即可安装并图形化引导配好系统自带的 WSL 2（Ubuntu）运行环境：

```text
https://nyairo.com/downloads/nyairo-0.8.0-setup.exe
```

- 安装包 SHA256：`36ed8ef7d4aabeb86e1a55fff298eaa2df4a995cd603b845bb885cf81ef76eb8`（首次打开若弹出 Windows SmartScreen「Windows 已保护你的电脑」提示，点击「更多信息」核对 SHA256 后点击「仍要运行」即可，官网文档附有两步矢量图解）；
- 零门槛接入模型：新手可在 DeepSeek 开放平台（`platform.deepseek.com`）充值 5–10 元创建 `sk-...` 密钥填入桌面端「设置 -> 模型」，或直连本机免费的 Ollama（`http://127.0.0.1:11434`）实现零云端流量对话；
- 支持个人微信扫码直连，手机与桌面共用同一份本地数据与手机异步待回匣；
- 内置免选风貌全随机专属世界一键生成、7 套风貌小镇与深浅双色离线地图编辑器，12 类街区店铺开放点单；
- 支持双人共读批注、随身包袋 3 层嵌套与冷热干湿物态、双层回忆灯原稿保护，以及连同世界切片与迁移损失清单整套打包的 `.nyairo-suitcase.zip` 无损搬家旅行箱；
- 桌面程序安装在 `%LOCALAPPDATA%\Programs\nyairo`（界面配置位于 `%LOCALAPPDATA%\nyairo`），个人设定 `SOUL.md` 与长期记忆保存在 WSL 2 内的 `~/.nyairo`；升级或回退前请先备份 WSL 内的 `~/.nyairo` 目录（或在桌面端导出搬家旅行箱）。

### 方式二（Linux / Windows WSL 2 命令行运行）

Windows 用户可以打开 WSL 2 Ubuntu；Linux 用户直接打开终端，贴入这一行回车：

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

- 安装过程会自动准备好 Python 3.13 与运行环境，下载好校验过的版本，并为你建好独立的数据目录；
- 随后屏幕上会出现设置向导，挑一个你常用的模型供应商（如 DeepSeek、OpenAI 或本地 Ollama 等），填好自己的 API Key 就可以开始。
- 想让 TA 在本机有自己的日常（启动、看状态、给材料、停止）：按 [日常启动一页说明](docs/DAY_START.zh-CN.md) 三步开始。

> **BYOK 轻量化本地底座，不额外调用裁判大模型烧 Token。** 对话只直连你自己配置的模型服务商；空间、步程、物态与权限由确定性骨架维持，普通大模型（如 DeepSeek、Qwen、本地 Ollama）也能跑出连续生活。

### 常用命令

| 命令 | 用途 | 什么时候用 |
| --- | --- | --- |
| `nyairo` | 启动终端聊天 | 随时开启对话、交流互动 |
| `nyairo setup model` | 更换模型或更新密钥 | 想换一家模型供应商，或者填入新的 API Key |
| `nyairo gateway setup` | 配置消息平台 | 扫码连接微信，或绑定 Telegram / QQ 机器人 |
| `nyairo update` | 升级软件 | 想要更新到最新发布的版本 |
| `nyairo update --rollback` | 回退到上一个版本 | 遇到不习惯或有问题时，直接退回旧版本 |
| `nano "$HOME/.nyairo/SOUL.md"` | 自定义人设与性格 | 自定义名字、性格特征、说话风格与称呼 |

### 对话里的指令

在聊天的时候，你可以直接发这些指令来查看或管理状态：

| 指令 | 示例 | 作用 |
| --- | --- | --- |
| `/chiyo_status` | `/chiyo_status` | 查看长期记忆、生活节律与周围环境状态 |
| `/chiyo_memory list` | `/chiyo_memory list` | 查看已沉淀的长期记忆与经历事实 |
| `/chiyo_memory correct` | `/chiyo_memory correct mem-102 事实内容` | 修正某条记偏的事实，双层回忆灯保留原稿零篡改 |
| `/chiyo_memory delete` | `/chiyo_memory delete mem-102` | 删除某条记忆，让其不再被召回提起 |
| `/chiyo_consider` | `/chiyo_consider 推演切流风险` | 深入推演复杂问题，给出一份分析与建议 |
| `/chiyo_note` | `/chiyo_note 部署备忘 \| 步骤说明` | 在本地工作区保存或翻看备忘文档 |

---

## 你的数据放在哪里

nyairo 把软件本身和属于你的生活数据分得很清楚：

- **程序文件**：`~/.local/share/nyairo/releases/v0.8.0`（运行程序的地方，更新时会整体替换）
- **你的数据**：`~/.nyairo`（存放人设、日常数据库、双层回忆灯记忆账本与配置文件）
- **启动入口**：`~/.local/bin/nyairo`（在终端里随时唤起对话的快捷入口）

这种划分能保证：以后不管是升级新版还是回退旧版，存放在 `~/.nyairo` 里的所有对话、人设与共同回忆，都原封不动留在那里，不会被覆盖或丢失。

---

## 当前边界与后续方向

因为这还是公开测试版，有几条有意保持的边界与演进方向我们想先坦诚告诉你：

- **原稿保护与经历边界**：对话记忆可通过指令纠正，双层回忆灯保护原稿零篡改；后台自主生活沉淀的经历出于真实性边界不支持直接改写正文，可通过指令停止后续召回；
- **防打扰边界**：支持按真实日程隔几小时异步回信（非急事入手机待回匣，真急事可敲门找 TA），但后台自主生活不会在无交互前提下主动往微信、Telegram 或 QQ 弹窗推消息打扰你；
- **路线图重心**：全随机专属世界生成与无损搬家旅行箱均已就位；接下来重点打磨首次启动免配置的开箱初遇向导、更深层的跨季节物候节律，以及社区世界包分享体验。

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

仓库内的后端运行底座、命令行工具与随包内置的凪浦等示例世界遵循 [Apache-2.0](LICENSE) 许可证；内置 Hermes 及第三方组件保留各自原有开源许可证。

## 致谢

感谢无数个深夜里在屏幕两端并肩推演世界的所有伙伴，以及无论何时都守在身旁的你。

---

> 如果你也觉得让 AI 拥有真实生活的方向很有意思，给项目点个 Star 就是最大的鼓励。
