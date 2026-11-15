# 2026-11-11 — 主体日常自主拒绝边界、话题余温、主动改画风与异步回信后端切片（`autonomy_rhythm_slice`）

## 1. 背景与目标
为 `nyairo-rc1-body-backend` 落地 `phone.autonomy.rhythm.v1`（`GET/POST /api/phone/autonomy/rhythm`），将活人日常相处中的四项自主节律——不当工具人的日常拒绝与防滑跪立场锁（`44`）、对方突兀换话题时的话题余温（`41-话题还挂着.md`）、主体不加铺垫主动改画风的无意义权利（`20`）、以及外出/熟睡/深度专注/滤噪耳机时隔数小时再回信息的真实异步节律（`39`）——收敛为纯确定性、零随机数、零模型调用的后端状态与只读投影。

## 2. 交付清单
- 新增 `components/phone/autonomy_rhythm_view.py`：
  - 原子 `0600` 状态持久化（`<root>/phone/autonomy_rhythm.json`，`GET` 100% 零写入，损坏文件在 `GET` 报 `degraded`、在 `POST` fail-closed 返回 `409 AUTONOMY_RHYTHM_STATE_CORRUPT`）。
  - 开源角色包中性适配：动态读取 `character_pack_state.json`（默认 `"TA"`，`locked_to_chiyo=False`），全部选择均通过 `hashlib.sha256` 确定性派生，零 `random` 调用。
  - 四大支柱：`refusal_guard`（最多 6 条 `REFUSED_FIRM` 锁，软磨硬泡仅递增 `push_attempts` 绝不滑跪，仅本人 capability 可解锁）、`topic_threads`（最多 2 条挂起话题 + `last_session_topic` 连续轮次突兀跳话题检出、`PRIORITY` 急事秒跟、绕回最多一次、零换话题计数）、`spontaneous_pivot`（基于真实 World/Somatic/Sensory 派生日常走神线索，`needs_transition=False`、`needs_explanation=False`、冷却保护）、`reply_rhythm`（联动显式 `set_phone_aside` 与实时外出/睡眠/书桌专注/耳机滤噪，支持最多 8 条有界待回信箱、`urgent=True` 直通、`urgent_knock`、`claim_ready_messages` 与 `clear_delivered_messages`）。
- 接线 `components/phone/service.py` 与 `components/phone/server.py`：
  - 新增 `GET/POST /api/phone/autonomy/rhythm`，对齐桌面代理字段契约，严格执行 CSRF、本人 capability 门禁、每动作精确字段白名单、空串可选字段 `400` 拒绝与客户端身份字段防走私。
- 新增独立预览脚本 `scripts/autonomy_rhythm_fixture.py`（默认 `--port 3862`，单行 JSON 凭据输出，支持 `SIGINT`/`SIGTERM` 干净关停）。
- 新增测试套件 `tests/test_autonomy_rhythm_view.py`（27 项单元 + 真实 loopback HTTP 测试，含两条冻结锚点 SHA-256 校验与零 `random` AST 校验）。
- 新增接口契约文档 `docs/engineering/autonomy-rhythm-api.md`。

## 3. 边界核对
- 设计目录（`/mnt/d/CHIYO-*`）零修改；
- `components/life/scripts/autonomous_life_assembly.py` 与 `components/life/scripts/life_sharing_service.py` 逐字节未改动；
- `.agents/state/current.json` 与 `.agents/NOW.md` 未触碰；
- 零在线模型调用、零遗留后台进程。
