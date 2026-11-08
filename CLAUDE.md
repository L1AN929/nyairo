# NyAiro 后端 · 接手须知

这份文件每次开新窗口都会自动读，不需要敲任何命令。

## 现在是什么状态

按新鲜度从高到低读这三份，**不要只读一份**：

1. `.memsearch/PROJECT.md` —— 最新。Recent Progress 是逐日流水，覆盖到当天。
2. `.agents/continuity/current.json` —— 机器可读断点（revision / stop_point / 请求账本 / 授权）。
3. `NOW.md` —— 停在 2026-11-03 22:04 / rev884，只作历史与条文依据，**不代表最新状态**。

`@NOW.md` 是 Claude Code 的引用语法，在 DeepSeek Harness 或别的 harness 里不会被展开，
请按上面的路径直接读。

开工顺序按 NOW.md 第〇步：先派只读子代理对着总纲核查，再改表，再施工。
不要拿 NOW.md 的初判直接派工。

## 施工规矩

@AGENTS.md

## 笔记留痕（write-notes）

非平凡改动必须在 `.agents/notes/` 留一篇笔记，按分类放进 `implemented/` 或 `proposed/` 下的子目录。
正文四段：Problem / Decision / Alternatives considered / Consequences；被否决的方案先写它最强的
理由，再给否决原因。纯机械小改（样式、格式化、常规 CRUD）不写。
规范细则见 AGENTS.md 第 1、2 节。

## 找文件和跑东西

- 任务 → 文件的索引：`PROJECT_INDEX.md`
- 测试、起浏览器夹具、派工命令：`RUNBOOK.md`
- 压缩后要恢复：`python scripts/task_resume.py`（一般不用，上面两份已经够用）

## 一句话版

监工不写代码，派工给 `scripts/dispatch_worker.py`；一个写集只许一个施工者；
红灯不算完成；别碰承重墙；要改契约或做产品决定时，先停下来问。

**派工链（2026-11-06 已修，临时方案）**：claude CLI 2.1.265 起会硬拒网关的私有模型 id
（`workbuddy-wg/cn:deepseek-v4.1-flash`），2.1.263 只打一条 `unrecognized_model` 告警就照常跑。
仓库外装了那份旧版，专给 worker 用，监工自己仍用新版：

```bash
python scripts/dispatch_worker.py --name <工单名> --task-file <任务文件> \
  --claude "D:/Tools/claude-263/node_modules/.bin/claude.cmd" --timeout 3600 --max-turns 300
```

也可以设 `WORKER_CLAUDE` 环境变量，省掉每次传参。实测 exit=0、模型是 DeepSeek、工具往返正常。
根因和两条彻底修法（给 `~/.claude/settings.json` 加模型映射，或让脚本绕过 CLI 直打网关）
保留在上面这段的历史里；现在这条是绕过办法，升级 CLI 时记得回来重验。
