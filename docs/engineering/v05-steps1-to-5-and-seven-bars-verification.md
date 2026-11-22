# v0.5 Steps 1–5（Ch.54–125）与 Ch.65 七项真过一周收口验证记录（2026-11-18）

## 1. 交付范围与设计边界
- **`0.2` 终审补闭环（Stage 1）**：`v02_causal_ablation_runner.py`、`v02_v03_daily_loop_bridge.py`（补齐置放线索 / 次日开启 / `MATERIAL_SUPPLIED` 零写投影与回执结算）、`lived_context_bridge.py`（零写可见提示与共识遮罩）、`v02_runtime_governance_migration.py`（V-C 防备份复活隔离），配套 12 场景总检 `tests/test_v02_twelve_mandatory_scenarios_and_vd_ablation.py`。
- **`0.5` Step 1（统一空间树、防卧室打转、Ch.109–125 物理与铁骨活墨）**：`v05_unified_world_fabric.py`、`v03_clock_evaporation.py`（只读涟漪钩子）、`v02_v03_daily_loop_bridge.py`（连写微闷降权与收尾豁免）、`candidate_sources_ag0.py`、`world_move_opportunity_source.py`、`autonomous_life_cycle.py`（超时 5 分钟看窗外软着陆 + 平静闸门间隔检查）、`lived_context_bridge.py`（五场景动态工具 <=8 收窄 + 缓存前缀固定排序 + 人民币月账单估算）；同步完成 Ch.123–125 的 `guard_chalkboard_ink_output` / `strip_piggyback_chalk_tail` / `compose_deduped_three_part_scene` / `estimate_world_ink_monthly_rmb`。
- **`0.5` Step 2（桌面房间 2D 活画布与随身小手机）**：默认 `#viewHome` 呈现 2D 光影画布 + 右侧对话工作台 + 右下角 4 页签随身小手机，保留 `#btnToggleClassicHomeView` 一键切换经典清单视图。
- **`0.5` Step 3（流动推车、二手孤品、慢工程、隔日玄关包裹与平静闸门）**：`world_open_storefront.py`、`v08_universal_matter.py`、`v04_neighborhood_living.py`（边车 `world/v05_neighborhood_moments.json` + 寄售回款进真钱包）、`world_travel_delivery.py`（边车 `data/world/world_companionship_stroll.json`）、`world_phone_shopping.py`（边车 `data/world/world_entryway_parcels.json`）。
- **`0.5` Step 4（远近渠道、工作台书签与三句话开荒）**：`components/worldgen/templates/urban_apartment.json` + `ALL_TEMPLATE_THEMES`（保持 `AVAILABLE_THEMES` 四主题常量兼容）+ `hatch_world_from_three_inputs`、编辑器五微区映射与路网连通检查、免填 `original_dir` 自动绑定、跨镇玄关搬家纸箱与三包字段防窃、`evaluate_v05_antipuppet_sixth_dim` 与白话三行回执；桌面端接入渠道 `X-Hermes-Session-Key` 分流、`workbench-mode`、`hatch-world`。
- **`0.5` Step 5（轻感官声景、光线速写卡、零剧透共读、窄窗与立体巧思）**：`web/js/ambient-soundscape.js`（随窗扇开合调制 WebAudio 声景 + 4 路电台 + 翻页/放杯微音效 + 本地静音记忆）、`web/js/light-sketch-card.js`（480x320 明信片画板 + 6 张软木板退还相册）、`v05_stage.go`（<=1000 字零剧透共读分页、`280x64` 窄窗 `preMiniState` 几何保护、`Ch.109–125` 纸团/SVG皮肤/短波呼号/粉笔档位 `/api/v05-stage/twists`）及 `pocket-phone.js` / `room-stage.js` 控件接线。

## 2. 守恒红线与离线/跨端测试实测
- 三枚冻结锚点（`readonly_recall_adapter.py` `6667ff66...`、`life_sharing_service.py` `0becf7df...`、`autonomous_life_assembly.py` pre-existing `f14fe629...` 未改一字节）、`len(LIFE_UPDATE_SURFACES) == 37`、`MAX_LIVED_CONTEXT_CHARS == 1800`、`len(_PHONE_AND_WORLD_GUIDANCE) == 1199 <= 1200` 全部守恒。
- 实测通过：`0.2` + Step 3 联合门禁 `221/221 OK`、Step 1 `15/15 OK`（含 Ch.123–125）、Step 4 + Scorecard `15/15 OK`、`worldgen` `166/166 OK`、`world-editor` `74/74 OK`、`/root/nyairo-v051` 清单与门面核心联跑 `62/62 OK`、Ch.65 七项标尺连测全过、桌面 Go `go vet` + `go test -count=1 ./...`（含 Chrome Headless E2E 与 `NYAIRO_NIGHTZ_LIVE=1` 跨进程联跑）全绿。

## 3. 实机同步与有限试跑
- 已同步 `/root/nyairo-v051` 并重建 LF `MANIFEST.json`（`R1100_V02_CLOSED_AND_V05_LIVE`），拉起 `:3831` 并实测 6 路只读 `GET` 返回 `200 OK`、状态文件零改动。
- 编译无黑框生产版 `nyairo.exe`（`-ldflags "-s -w -H=windowsgui"`）并一致落盘 `D:\个人\nyairo-desktop\nyairo.exe`、`dist\nyairo.exe` 与 `%LOCALAPPDATA%\Programs\nyairo\nyairo.exe`。
- 完成隔离开环有限快进试跑：`v05a`（真实模型 300s/上限 8 次）与 `v05b`（离线确定性 `24x` 脚本闭环 18s），停止后残留进程为 `0`。
