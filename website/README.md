# nyairo · 以虚拟世界为核心的数字个体框架

<p align="center">
  <img src="website/assets/nyairo-horizontal.png" alt="nyairo banner" width="600" />
</p>

<p align="center">
  <a href="https://github.com/L1AN929/nyairo/releases/tag/v0.3.0"><img src="https://img.shields.io/badge/release-v0.3.0-1f2937.svg" alt="Release" /></a>
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
> **当前版本：v0.3.0（GitHub prerelease 预发布）** — 后端连续生活版：出门、等待、改主意、消费、留下经历与回家接着做原来那件事，接成可以暂停、可以恢复的连续过程。这还不是稳定版，隔离新装与 rc3 升级/回滚链已有验证，未对现有实例/生产升级或真实模型一天/24h 验收；旧的 v0.2.0-rc3 安装包与标签保持原样，升级前请先备份个人目录。
>
> 在 Linux 或 Windows 的 WSL 终端里，输入这一行就能装好：
> `curl -fsSL https://nyairo.com/install.sh | bash`
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

## 极简快速上手

### 一行命令全自动安装（Linux / Windows WSL 2）

Windows 用户先打开 WSL 2 Ubuntu；Linux 用户打开终端。直接运行下面这一行：

```bash
curl -fsSL https://nyairo.com/install.sh | bash
```

- 脚本会自动配齐 Python 3.13 与必要工具，下载官方校验版本，建立本地隔离数据目录；
- 随后屏幕会自动弹出模型设置向导，选择你的服务商（OpenAI、DeepSeek 等）并填入 API Key 即可。

### 常用命令速查

| 命令 | 用途 | 适用场景 |
| --- | --- | --- |
| `nyairo` | 启动终端聊天 | 日常对话与交互 |
| `nyairo setup model` | 重新选择模型或更新 Key | 更换供应商或新模型 |
| `nyairo setup messaging` | 配置并绑定消息平台 | 连接 Telegram 机器人 |
| `nyairo update` | 一键整包升级程序 | 升级到最新发布版本 |
| `nyairo update --rollback` | 一键回滚到上一版本 | 出现兼容问题时无损回退 |
| `nano "$HOME/.nyairo/SOUL.md"` | 编辑人设与个性定义 | 自定义性格、语气与称呼 |

### 会话内功能指令

| 指令 | 示例 | 效果说明 |
| --- | --- | --- |
| `/chiyo_status` | `/chiyo_status` | 查看记忆、生活与环境感知模块运行状态 |
| `/chiyo_memory list` | `/chiyo_memory list` | 查看系统已沉淀的长期经历事实与编号 |
| `/chiyo_memory correct` | `/chiyo_memory correct mem-102 事实内容` | 纠正记错的事实，作废旧假设防幻觉 |
| `/chiyo_memory delete` | `/chiyo_memory delete mem-102` | 停止召回某条记忆，保护特定隐私 |
| `/chiyo_consider` | `/chiyo_consider 推演切流风险` | 触发 Shadow 深度分析，生成决策建议 |
| `/chiyo_note` | `/chiyo_note 部署备忘 \| 步骤说明` | 在本地工作区保存或读取独立文档 |

---

## 目录与数据隔离

nyairo 采用严格的“程序运行体与个人数据解耦”架构设计：

- **程序核心**：`~/.local/share/nyairo/releases/v0.3.0`（只读程序包，更新时整体切换）
- **个人数据**：`~/.nyairo`（完全独立的本地目录，包含人设、数据库、记忆、密钥与配置）
- **命令入口**：`~/.local/bin/nyairo`（系统 PATH 启动项）

即使升级或回滚程序，你的个人回忆、对话数据库与人设也绝对不会被改写或丢失。

---

## 官方网站与社区交流

- **官方主页与在线体验**：[https://nyairo.com/](https://nyairo.com/)
- **官方 QQ 交流群**：[1126943805](https://qun.qq.com/universal-share/share?ac=1&authKey=jTRpypa4t5NVRLQ23fwB%2FWf%2Bv40vJIRUQRSp7NjrTlmY6LjYMJL0Yzp6ffOxAumj&busi_data=eyJncm91cENvZGUiOiIxMTI2OTQzODA1IiwidG9rZW4iOiJOeWtXdXZCYzc4UERlTE5iR2NCVG1lTWx5L2NQUnFQNXFUTnNiOGhnd3RJNERDQnhVanU0WGUrVXZDZnlnV1F6IiwidWluIjoiMjg1NTY4MDU5OSJ9&data=9V6FtP6tYYR5Mfpg2v7V9ML798haiganCXx7zcY-Corkhkofom2ie2_9CBGBnJO1JjOuSRJDu4-_SXgB6IihLQ&svctype=4&tempid=h5_group_info) *(点击一键直达加入群聊)*
- **Telegram 官方频道**：[https://t.me/nyairoai](https://t.me/nyairoai)
- **快速起步上手指南**：[https://nyairo.com/#quickstart](https://nyairo.com/#quickstart)
- **版本更新动态与说明**：[https://nyairo.com/#changelog](https://nyairo.com/#changelog)
- **最新 Release 下载与校验包**：[GitHub Releases v0.3.0](https://github.com/L1AN929/nyairo/releases/tag/v0.3.0)（旧 v0.2.0-rc3 仍在 Releases 页面）

---

## 许可证

本项目新增代码（包含 Life Supply）遵循 [Apache-2.0](LICENSE) 许可证；内置 Hermes 及第三方组件保留原有开源许可证。

## 致谢

感谢无数个深夜里在屏幕两端并肩推演世界的所有伙伴，以及无论何时都守在身旁的你。

---


源站维护配置见仓库 `config/nginx-nyairo.conf`。backup 文件保存在公开目录之外，同类 URL 返回 404。部署静态站时，不应复制私有备份或部署配置到公开目录。
