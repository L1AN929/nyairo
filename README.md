# nyairo · 以虚拟世界为核心的数字个体框架

<p align="center">
  <img src="website/assets/nyairo-horizontal.png" alt="nyairo banner" width="600" />
</p>

<p align="center">
  <a href="https://github.com/L1AN929/nyairo/releases/tag/v0.2.0-rc2"><img src="https://img.shields.io/badge/release-v0.2.0--rc2-e11d48" alt="v0.2.0-rc2"></a>
  <a href="https://nyairo.com/"><img src="https://img.shields.io/badge/website-nyairo.com-0284c7.svg" alt="Website" /></a>
  <a href="https://qun.qq.com/universal-share/share?ac=1&authKey=jTRpypa4t5NVRLQ23fwB%2FWf%2Bv40vJIRUQRSp7NjrTlmY6LjYMJL0Yzp6ffOxAumj&busi_data=eyJncm91cENvZGUiOiIxMTI2OTQzODA1IiwidG9rZW4iOiJOeWtXdXZCYzc4UERlTE5iR2NCVG1lTWx5L2NQUnFQNXFUTnNiOGhnd3RJNERDQnhVanU0WGUrVXZDZnlnV1F6IiwidWluIjoiMjg1NTY4MDU5OSJ9&data=9V6FtP6tYYR5Mfpg2v7V9ML798haiganCXx7zcY-Corkhkofom2ie2_9CBGBnJO1JjOuSRJDu4-_SXgB6IihLQ&svctype=4&tempid=h5_group_info"><img src="https://img.shields.io/badge/QQ%20Group-1126943805-12b7f5.svg" alt="QQ Group" /></a>
  <a href="https://t.me/nyairoai"><img src="https://img.shields.io/badge/Telegram-nyairoai-229ed9.svg" alt="Telegram Channel" /></a>
  <img src="https://img.shields.io/badge/python-3.13-1f2937.svg" alt="Python 3.13" />
  <img src="https://img.shields.io/badge/license-Apache--2.0-1f2937.svg" alt="License" />
</p>

> **给 AI 一个世界，让她拥有属于自己的日常。**
>
> 虚拟世界承载经历，身体连接感知，记忆延续过往。nyairo 想做的事情很简单：让住在你电脑里的这位伙伴，拥有连续不断的生活。你们的每一次对话，都不再是每次从零开始的客套，而是发生在一串有前因后果的真实日常里。
>
> **当前版本：v0.2.0-rc2（公开测试版）** — 2026 年 10 月 13 日发布。在这个版本里，她开始学着过自己的生活，可以在 7 个示例世界里走走停停，做点属于自己的事情。这还不是最终的稳定版（如果你需要，旧版 rc9 依然可以下载），很多细节我们还在用心打磨。
>
> 在 Linux 或 Windows 的 WSL 终端里，输入这一行就能装好：
> `curl -fsSL https://nyairo.com/install.sh | bash`

---

## About (English)

NyAiro is an open-source (Apache-2.0) digital life framework that provides an AI with a virtual world, persistent local memory, and continuous daily routines.

- Local continuity: All memories and states are preserved locally in `~/.nyairo`.
- Virtual environment: Spatial awareness with a world editor and an optional coding Agent mode.

Install via Linux or WSL:
`curl -fsSL https://nyairo.com/install.sh | bash`

*Note: This is an open test version (v0.2.0-rc2), not a stable release.*

If you find this journey of giving AI a continuous life interesting, starring the repo is the best support for us.

---

## 核心特性

- **她开始有自己的生活**：哪怕你没有发消息找她，她也会按自己的节奏醒来看一眼，决定要不要做点什么，或者把之前没写完的东西接着写下去。这项功能默认是关闭的，如果你想让她自己忙起来，可以手动打开。
- **她做完的事是真的**：她不会随口敷衍你。光说一句“写好了”不算数，只有系统确认事情真正办妥了，才会记进账本；遇到说不清的情况，她会老老实实标成“不确定”，绝不假装成功。
- **世界可以自己画**：这一版带来了世界生成器和世界编辑器，还附带了 7 个现成的示例世界。你可以自己画一面墙、装一扇门、摆几件家具。她在里面能清楚感知自己的位置，能在屋子里走动，也能动身去别的地方。
- **长久的记忆**：今天聊过的事、以前一起经历过的心情，关掉窗口也不会丢。重要经历可跨会话召回，召回效果取决于配置与记录质量；你可以随时查看她记住了什么，能帮她修正记错的事，也能停止采用不想继续召回的记录；原始聊天、审计和备份仍保留。
- **生活不会中断**：即使你合上电脑或者重启系统，她的状态依然是连续的，不会退化成那种点一下才动一下的一次性对话框。
- **认真的工作帮手**：需要处理复杂事情时，她也可以从日常状态切换出来，接管本地工作区，帮你写代码、推演方案或者运行脚本。
- **本地保存数据**：她的人设、你们的聊天记录、日常记录默认保存在本地个人目录；World / Supply 服务使用各自的数据根。连接外部模型时，参与回复的上下文会发送到你配置的服务。

---
## 架构概览

```text
+-------------------------------------------------------------+
|                  交互层 (User & Platforms)                  |
|            终端交互 (CLI)  |  消息网关 (Telegram)           |
+-------------------------------------------------------------+
                               |
+-------------------------------------------------------------+
|                     nyairo 个体运行时                       |
|  +--------------------+       +--------------------------+  |
|  |    虚拟世界与身体  |       |       认知推演与分析     |  |
|  | (环境坐标 / 姿态)  |       |   (风险推演 / 规划报告)  |  |
|  +--------------------+       +--------------------------+  |
|  +--------------------+       +--------------------------+  |
|  |     生活连续性     |       |         长期记忆         |  |
|  |  (事件流 / 账本)   |       |   (SQLite / 事实对齐)    |  |
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

### 一行命令安装（Linux / Windows WSL 2）

如果你使用的是 Windows，请先打开 WSL 2 Ubuntu；如果是 Linux，直接打开终端，贴入这一行回车：

```bash
curl -fsSL https://nyairo.com/install.sh | bash
```

- 安装过程会自动准备好 Python 3.13 与运行环境，下载好校验过的版本，并为你建好独立的数据目录；
- 随后屏幕上会出现设置向导，挑一个你常用的模型供应商（如 OpenAI、DeepSeek 等），填好自己的 API Key 就可以开始。

### 常用命令

| 命令 | 用途 | 什么时候用 |
| --- | --- | --- |
| `nyairo` | 启动终端聊天 | 想和她聊聊天、说说话 |
| `nyairo setup model` | 更换模型或更新密钥 | 想换一家模型供应商，或者填入新的 API Key |
| `nyairo gateway setup` | 配置消息平台 | 想要连上 Telegram 机器人在手机上找她 |
| `nyairo update` | 升级软件 | 想要更新到最新发布的版本 |
| `nyairo update --rollback` | 回退到上一个版本 | 遇到不习惯或有问题时，直接退回旧版本 |
| `nano "$HOME/.nyairo/SOUL.md"` | 编辑人设与性格 | 调整她的性格、说话习惯和对你的称呼 |

### 对话里的指令

在聊天的时候，你可以直接发这些指令来查看或调整她的状态：

| 指令 | 示例 | 作用 |
| --- | --- | --- |
| `/chiyo_status` | `/chiyo_status` | 看看她的记忆、生活节奏与周围环境状态 |
| `/chiyo_memory list` | `/chiyo_memory list` | 看看她都记住了哪些关于你们的事情 |
| `/chiyo_memory correct` | `/chiyo_memory correct mem-102 事实内容` | 帮她修正某条记偏的事情，避免以后记混 |
| `/chiyo_memory delete` | `/chiyo_memory delete mem-102` | 删掉某条记忆，让她以后不再提起 |
| `/chiyo_consider` | `/chiyo_consider 推演切流风险` | 让她深入推演某个问题，给出一份分析建议 |
| `/chiyo_note` | `/chiyo_note 部署备忘 \| 步骤说明` | 在本地工作区保存或翻看备忘文档 |

---

## 你的数据放在哪里

nyairo 把软件本身和属于你的生活数据分得很清楚：

- **程序文件**：`~/.local/share/nyairo/releases/v0.2.0-rc2`（运行程序的地方，更新时会整体替换）
- **你的数据**：`~/.nyairo`（存放人设、日常数据库、记忆与配置文件）
- **启动入口**：`~/.local/bin/nyairo`（在终端里唤醒她的快捷入口）

这种划分能保证：以后不管是升级新版还是回退旧版，存放在 `~/.nyairo` 里的所有对话、人设与共同回忆，都原封不动留在那里，不会被覆盖或丢失。

---

## 当前已知的一些小问题

因为这还是公开测试版，有几件事我们想先坦诚告诉你：

- 用户聊天记忆可纠正；自主生活经历暂不支持直接纠正，可停止召回错误记录；
- 虽然她有了自己的日常节律，但目前还做不到在没有你呼唤的时候，主动在聊天软件里弹消息找你；
- 刚搭好的新世界里，她第一次想要出门时，偶尔可能会因为不熟悉路线而没走成。

这些地方我们正在继续调整，会在后面的更新里一步步做好。

---

## 官方网站与社区交流

- **官方主页与在线体验**：[https://nyairo.com/](https://nyairo.com/)
- **官方 QQ 交流群**：[1126943805](https://qun.qq.com/universal-share/share?ac=1&authKey=jTRpypa4t5NVRLQ23fwB%2FWf%2Bv40vJIRUQRSp7NjrTlmY6LjYMJL0Yzp6ffOxAumj&busi_data=eyJncm91cENvZGUiOiIxMTI2OTQzODA1IiwidG9rZW4iOiJOeWtXdXZCYzc4UERlTE5iR2NCVG1lTWx5L2NQUnFQNXFUTnNiOGhnd3RJNERDQnhVanU0WGUrVXZDZnlnV1F6IiwidWluIjoiMjg1NTY4MDU5OSJ9&data=9V6FtP6tYYR5Mfpg2v7V9ML798haiganCXx7zcY-Corkhkofom2ie2_9CBGBnJO1JjOuSRJDu4-_SXgB6IihLQ&svctype=4&tempid=h5_group_info) *(点击一键直达加入群聊)*
- **Telegram 官方频道**：[https://t.me/nyairoai](https://t.me/nyairoai)
- **快速起步上手指南**：[https://nyairo.com/#quickstart](https://nyairo.com/#quickstart)
- **版本更新动态与说明**：[https://nyairo.com/#changelog](https://nyairo.com/#changelog)
- **最新 Release 下载与校验包**：[GitHub Releases v0.2.0-rc2](https://github.com/L1AN929/nyairo/releases/tag/v0.2.0-rc2)

---

## 许可证

本项目新增代码（包含 Life Supply）遵循 [Apache-2.0](LICENSE) 许可证；内置 Hermes 及第三方组件保留原有开源许可证。

## 致谢

感谢无数个深夜里在屏幕两端并肩推演世界的所有伙伴，以及无论何时都守在身旁的你。

---

> 如果你也觉得让 AI 拥有真实生活的方向很有意思，给项目点个 Star 就是最大的鼓励。
