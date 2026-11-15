# 2026-11-11 `outing_places_slice`（0.2「房间外」六项日常全补齐）

## 交付内容

* 新增纯投影与原子 `0600` 状态模块 `components/phone/outing_places_view.py`：
  1. **熟悉河边长椅**（`outing_bench_bookstore.json`）：核对真实 World 非 `home` 地点并标注水岸/公园/展望台候选点，支持 `operation_key` 幂等重访留痕与公开切换，`no_streak_score=True`。
  2. **会换橱窗且影响阅读的书店**（同上）：按本地日期确定性轮换 3 个橱窗书目展位，支持本人待读收藏与伴侣橱窗短笺，`reading_completed=False`、`model_calls=False`。
  3. **自己的散步路线和随手拍**（`outing_routes_market.json`）：核对 `1..6` 个不重复真实户外地点与真实 `PhoneAlbum` 活跃图片；`partner_default` 严格剥离底层 `album_artifact_id`。
  4. **虚拟预算节日集市**（同上）：纯读 `world_property_economy` 真实虚拟币余额（未创世如实报 `GENESIS_NOT_INITIALIZED`），按日轮换 3 个节令摊位，支持量入为出心愿与伴侣小贴士，零真实扣款。
  5. **可重访作品展**（`outing_gallery_blueprint.json`）：核对真实 `PrivateProjectService` 精确 `(artifact_id, version_id)`；已归档源双视角扣留，`partner_default` 仅放行存在真实 `LOCAL_DELIVERED` 分享的精确版本。
  6. **共同画图并定义功能的新地点**（同上）：`her` / `partner` 双署名最多 6 张活跃新地点草图卡，跨作者改卡或归档 `409 AUTHOR_MISMATCH`，`does_not_mutate_world_topology=True`。
* 接线 `components/phone/service.py` 与 `components/phone/server.py` 三组 `GET/POST` 端点：
  * `/api/phone/outing/bench-bookstore`
  * `/api/phone/outing/routes-market`
  * `/api/phone/outing/gallery-blueprint`
* 新增本地合成预览脚本 `scripts/outing_places_fixture.py`（默认端口 `3859`）、完整单测与真实 loopback HTTP 测试 `tests/test_outing_places_view.py`、工程文档 `docs/engineering/outing-places-api.md`。
