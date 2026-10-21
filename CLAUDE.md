# NyAiro 后端 · 接手须知

这份文件每次开新窗口都会自动读，不需要敲任何命令。

## 现在是什么状态

@NOW.md

开工顺序按 NOW.md 第〇步：先派只读子代理对着总纲核查，再改表，再施工。
不要拿 NOW.md 的初判直接派工。

## 施工规矩

@AGENTS.md

## 找文件和跑东西

- 任务 → 文件的索引：`PROJECT_INDEX.md`
- 测试、起浏览器夹具、派工命令：`RUNBOOK.md`
- 压缩后要恢复：`python scripts/task_resume.py`（一般不用，上面两份已经够用）

## 一句话版

监工不写代码，派工给 `scripts/dispatch_worker.py`；一个写集只许一个施工者；
红灯不算完成；别碰承重墙；要改契约或做产品决定时，先停下来问。
