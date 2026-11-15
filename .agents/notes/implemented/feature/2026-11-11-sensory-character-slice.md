# 2026-11-11 — 开源角色包、可组合身体与多通道感官场后端切片（`sensory_character_slice`）

## 1. 背景与目标
为 `nyairo-rc1-body-backend` 落地开源中性默认角色包合同（`38-角色包合同`）、可组合身体与日常配饰边界（`51-可组合身体与可选生理体验`），以及连接 World 环境声光与 `somatic_comfort` 局部仿真的五通道感官场与文字声景卡（`33/34/41/45`），全程零模型调用、GET 严格零写入、不触碰冻结锚点。

## 2. 交付清单
- 新增 `components/phone/sensory_character_view.py`：
  - **Domain A (`phone.character.pack.v1`)**：四套内建开源预设（默认 `neutral_resident` `"TA"`，及 `chiyo`、`haru`、`sola`）、`proposed.character_pack.v1` 完整校验与递归禁字段防火墙（`DEPLOY_ONLY | USER_ONLY`）、成年门禁预览 `adult_gate_preview`、`sexual_seed` 强制 `DRAFT` 与隐私隔离、正交可选生理体验 `optional_processes`（仅本人 capability 可写可见）、耳机 `ambient_filter` 配饰校验、键序无关规范化 `pack_ref`。
  - **Domain B (`phone.body.sensory_field.v1`)**：纯读派生 `auditory`（窗户三态声学衰减、耳机滤噪、邻接地点只闻其声不认其人）、`visual`（室内灯具档位、正午窗侧眩光、夜间未开灯阅读提示）、`olfactory`（热茶冷却 30 分钟后蒸汽消散、雨气、关窗潮衣闷气与开窗通风、气味偏好开关）、`tactile_thermal`（四模态透传）与 `sensory_load`（物理微调建议，不映射情绪或病理），以及文字声景卡（`<=12`，`has_audio_recording=False`，`operation_key` 幂等重放，伴侣默认视图零私密计数泄露）与伴侣感官小贴士（`<=3`）。
- 接线 `components/phone/service.py` 与 `components/phone/server.py`：
  - 新增 `GET/POST /api/phone/character/pack` 与 `GET/POST /api/phone/body/sensory`，严格拒绝未知 query、多余字段、空串可选字段与错误 capability。
- 新增独立预览脚本 `scripts/sensory_character_fixture.py`（支持 `--port` 与 `--root`，单行 JSON 输出凭据并支持 `SIGINT`/`SIGTERM` 干净退出）。
- 新增单元测试与真实 loopback HTTP 套件 `tests/test_sensory_character_view.py`（26 项测试，含两条冻结锚点 SHA-256 校验）。
- 新增接口工程契约文档 `docs/engineering/sensory-character-api.md`。

## 3. 边界核对
- 设计目录（`/mnt/d/CHIYO-*`）零修改；
- `components/life/scripts/autonomous_life_assembly.py` 与 `components/life/scripts/life_sharing_service.py` 逐字节未改动；
- 零在线模型调用、零遗留后台进程。
