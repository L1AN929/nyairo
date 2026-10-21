# NyAiro 0.4：自主日常与私人多项目阶段

## 阶段目标

按用户纠正，先交可运行的整阶段，再沿实际问题修。不再把暂停、收尾和计时分别当一阶段，不新增精读队/独立审计/全量回归。标准仍为D盘CHIYO总体设计ZIP，唯一开发树 `D:\nyairo-rc1-body-backend`，软件仍0.4，既有改动与实例历史保留。

## 已完成

- **私人多项目工作台**：手机新增项目入口，能列多份作品、查看详情、写新版本、归档；复用Supply ManagedArtifact，不建平行作品库。材料和PDI不当项目。
- **项目服务真实运行**：父级 `stage_projects_smoke.py` 用真实Supply创建两份项目、修改其中一份、归档收尾，另一份仍ACTIVE，材料/PDI head不变。证据 `scratch/project-stage-smoke-1792394805143771448.json`。共3项目（含种子），2 ACTIVE/1 ARCHIVED。
- **Gemini原实例模型**：用户指定 `gemini-3.8-flash-high`，沿用 `https://gw.929711.xyz/v1` 与私有凭据。一次连通HTTP200后，只改原实例model_env模型名，备份 `model_env-backup-20261019T063815Z`（0600）。回滚模型只需覆盖该配置备份，不回滚业务历史。
- **真实Life决策已通**：原实例真实resident一次tick新增1请求，合法NO_ACTION，不再OUTPUT_TRUNCATED/INVALID_JSON。原run:one-day账本2→3、钱包2860JPY/4交易不变，本次服务已停止。证据 `scratch/original-life-bounded-1792394831392955957.json`。NO_ACTION不等于发生了项目动作。

## 自主日常主循环已收口

稍后事项真实保存、到期重考、改期取消、多项目选择和创建均已接入主循环。模型提示已公开有界处境/候选语义、later_item/park_current/future_item_actions；park不是只留helper，而是实际走AG1→AG2→LR4释放前台。两条真实Supply集成通过：park原活动→期间实际创建/做另一项目→到期明确RESUME原活动；DEFER稍后事项→到期选项→B真实创建→推进第二作品。决策与内容为脚本替身，owner和状态机是真实实现。

## 初版限制与后续

项目版本历史目前只展示当前head并明确history.complete=false；不会为这个单独阻塞整阶段。真实模型当前仅证合法选择，完整生活链的脚本决策证据与自主选择分开。Memory原实例未绑定、长期成长/关系/多模态/社会能力仍为后续，不把压缩包研究储备算成本阶段已完成，也不宣称24h生活已验。

## 验证

B22项Python+6项Node、A18项（含2条真实owner链）通过；父级未重复旧组。父级真实项目使用流程及Gemini合法NO_ACTION通过。最终补线后原实例一次tick为QUIET/0请求，账本仍3，不把累计调用当新调用。最终notes tree/format各60篇、git diff --check均exit0。无全树SHA、发布或整库回归。

## 最终运行与交付

最终代码的原实例记录 `scratch/original-life-bounded-1792401397092131418.json`：QUIET、零新增请求、钱包2860JPY/4交易不变、服务停止。两条实际链日志 `/tmp/stageA-final.log` 为2项OK/rc0，整阶段测试文件18项OK/rc0。阶段初版已交付，不等于全压缩包或24h生活验收完成。可用入口沿原phone serve与Life resident，不需迁移/重建实例；手机主屏“私人项目”可列出、编辑、创建和收尾作品。
