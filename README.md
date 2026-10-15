# nyairo · 以虚拟世界为核心的数字个体框架

<p align="center">
  <img src="website/assets/nyairo-horizontal.png" alt="nyairo banner" width="600" />
</p>

<p align="center">
  <a href="https://github.com/L1AN929/nyairo/releases/tag/v0.2.0-rc3"><img src="https://img.shields.io/badge/release-v0.2.0--rc3-e11d48" alt="v0.2.0-rc3"></a>
  <a href="https://nyairo.com/"><img src="https://img.shields.io/badge/website-nyairo.com-0284c7.svg" alt="Website" /></a>
  <a href="https://qun.qq.com/universal-share/share?ac=1&authKey=jTRpypa4t5NVRLQ23fwB%2FWf%2Bv40vJIRUQRSp7NjrTlmY6LjYMJL0Yzp6ffOxAumj&busi_data=eyJncm91cENvZGUiOiIxMTI2OTQzODA1IiwidG9rZW4iOiJOeWtXdXZCYzc4UERlTE5iR2NCVG1lTWx5L2NQUnFQNXFUTnNiOGhnd3RJNERDQnhVanU0WGUrVXZDZnlnV1F6IiwidWluIjoiMjg1NTY4MDU5OSJ9&data=9V6FtP6tYYR5Mfpg2v7V9ML798haiganCXx7zcY-Corkhkofom2ie2_9CBGBnJO1JjOuSRJDu4-_SXgB6IihLQ&svctype=4&tempid=h5_group_info"><img src="https://img.shields.io/badge/QQ%20Group-1126943805-12b7f5.svg" alt="QQ Group" /></a>
  <a href="https://t.me/nyairoai"><img src="https://img.shields.io/badge/Telegram-nyairoai-229ed9.svg" alt="Telegram Channel" /></a>
  <img src="https://img.shields.io/badge/python-3.13-1f2937.svg" alt="Python 3.13" />
  <img src="https://img.shields.io/badge/license-Apache--2.0-1f2937.svg" alt="License" />
</p>

> **离开聊天窗口后，它的时间不会静止。**
>
> nyairo 是一个开源的数字个体框架，让 AI 拥有本地长期记忆、可编辑的虚拟小房间，以及按真实时间流动的日常。你们的每一次对话，不再是一次性客套的问答，而是发生在一个看得见房间、走得出门的日常场景里。
>
> **当前版本：v0.2.0-rc3（公开测试版）** — 2026 年 10 月 14 日发布。在这个版本里，我们增加了单机网页地图编辑器，实现了按真实物理距离计时的走路出行（拒绝瞬间瞬移），并打通了微信私聊接入。这还不是最终的稳定版，很多细节我们还在用心打磨。
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

*Note: This is an open test version (v0.2.0-rc3), not a stable release.*

If you find this journey of giving AI a continuous life interesting, starring the repo is the best support for us.

---

## 核心特性

- **出门真的在走路（拒绝瞬间瞬移）**：告别聊天指令下像末影人一样的瞬间瞬移。出门去便利店走 5 分钟、去公园走 9 分钟；在路上问它走到哪了，它会如实说还在路上，只有时间真正走完了才进店；中转路线会自动接力走完。
- **关掉窗口也有自己的生活**：哪怕你没有发消息找它，它在后台也会按节律自己醒来看看风景、散步或者写日记。我们在真实服务器上连续测了 58 小时，它在后台自发写出了一篇千字随笔。这项功能默认关闭，可以按需开启。
- **世界可以自己画**：包里自带单机轻量地图编辑器，双击就能在本地浏览器打开。可以在房间里画墙、装门、摆桌椅，也能直接套用做好的小城镇模板。
- **本地长期记忆**：聊天记录和记忆保存在本地 SQLite 数据库，跨越会话也能自然聊起；说错了随时用命令修改，不上传云端，数据在你自己手里。
- **日常相处，随时深度协作**：平时能够自然交流对话，遇到复杂的编程或系统问题时，敲句命令就能切成 Agent 帮你查代码、推演方案。
---
## 架构概览

```text
+-------------------------------------------------------------+
|                  交互层 (User & Platforms)                  |
|            终端交互 (CLI)  |  消息网关 (微信 / Telegram)           |
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
| `nyairo` | 启动终端聊天 | 随时开启对话、交流互动 |
| `nyairo setup model` | 更换模型或更新密钥 | 想换一家模型供应商，或者填入新的 API Key |
| `nyairo gateway setup` | 配置消息平台 | 连接 Telegram 机器人，在手机上随时互动 |
| `nyairo update` | 升级软件 | 想要更新到最新发布的版本 |
| `nyairo update --rollback` | 回退到上一个版本 | 遇到不习惯或有问题时，直接退回旧版本 |
| `nano "$HOME/.nyairo/SOUL.md"` | 自定义人设与性格 | 自定义名字、性格特征、说话风格与称呼 |

### 对话里的指令

在聊天的时候，你可以直接发这些指令来查看或管理状态：

| 指令 | 示例 | 作用 |
| --- | --- | --- |
| `/chiyo_status` | `/chiyo_status` | 查看长期记忆、生活节律与周围环境状态 |
| `/chiyo_memory list` | `/chiyo_memory list` | 查看已沉淀的长期记忆与经历事实 |
| `/chiyo_memory correct` | `/chiyo_memory correct mem-102 事实内容` | 修正某条记偏的事实，避免后续记混 |
| `/chiyo_memory delete` | `/chiyo_memory delete mem-102` | 删除某条记忆，让其不再被召回提起 |
| `/chiyo_consider` | `/chiyo_consider 推演切流风险` | 深入推演复杂问题，给出一份分析与建议 |
| `/chiyo_note` | `/chiyo_note 部署备忘 \| 步骤说明` | 在本地工作区保存或翻看备忘文档 |

---

## 你的数据放在哪里

nyairo 把软件本身和属于你的生活数据分得很清楚：

- **程序文件**：`~/.local/share/nyairo/releases/v0.2.0-rc3`（运行程序的地方，更新时会整体替换）
- **你的数据**：`~/.nyairo`（存放人设、日常数据库、记忆与配置文件）
- **启动入口**：`~/.local/bin/nyairo`（在终端里随时唤起对话的快捷入口）

这种划分能保证：以后不管是升级新版还是回退旧版，存放在 `~/.nyairo` 里的所有对话、人设与共同回忆，都原封不动留在那里，不会被覆盖或丢失。

---

## 当前已知的一些小问题

因为这还是公开测试版，有几件事我们想先坦诚告诉你：

- 用户聊天记忆可纠正；自主生活经历暂不支持直接纠正，可停止召回错误记录；
- 虽然具备了自主生活节律，但目前还做不到在没有交互的时候，主动在消息软件里弹窗找你；
- 刚搭好的新世界里，第一次尝试出门时，偶尔可能会因为路线判定而没走成。

这些地方我们正在继续调整，会在后面的更新里一步步做好。

---

## 官方网站与社区交流

- **官方主页与在线体验**：[https://nyairo.com/](https://nyairo.com/)
- **官方 QQ 交流群**：[1126943805](https://qun.qq.com/universal-share/share?ac=1&authKey=jTRpypa4t5NVRLQ23fwB%2FWf%2Bv40vJIRUQRSp7NjrTlmY6LjYMJL0Yzp6ffOxAumj&busi_data=eyJncm91cENvZGUiOiIxMTI2OTQzODA1IiwidG9rZW4iOiJOeWtXdXZCYzc4UERlTE5iR2NCVG1lTWx5L2NQUnFQNXFUTnNiOGhnd3RJNERDQnhVanU0WGUrVXZDZnlnV1F6IiwidWluIjoiMjg1NTY4MDU5OSJ9&data=9V6FtP6tYYR5Mfpg2v7V9ML798haiganCXx7zcY-Corkhkofom2ie2_9CBGBnJO1JjOuSRJDu4-_SXgB6IihLQ&svctype=4&tempid=h5_group_info) *(点击一键直达加入群聊)*
- **Telegram 官方频道**：[https://t.me/nyairoai](https://t.me/nyairoai)
- **快速起步上手指南**：[https://nyairo.com/#quickstart](https://nyairo.com/#quickstart)
- **版本更新动态与说明**：[https://nyairo.com/#changelog](https://nyairo.com/#changelog)
- **最新 Release 下载与校验包**：[GitHub Releases v0.2.0-rc3](https://github.com/L1AN929/nyairo/releases/tag/v0.2.0-rc3)

---

## 许可证

本项目新增代码（包含 Life Supply）遵循 [Apache-2.0](LICENSE) 许可证；内置 Hermes 及第三方组件保留原有开源许可证。

## 致谢

感谢无数个深夜里在屏幕两端并肩推演世界的所有伙伴，以及无论何时都守在身旁的你。

---

> 如果你也觉得让 AI 拥有真实生活的方向很有意思，给项目点个 Star 就是最大的鼓励。
