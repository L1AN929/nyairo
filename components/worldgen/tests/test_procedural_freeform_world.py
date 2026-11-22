"""零预设程序化自由世界生成器验收测试（#rm-random-world）。

覆盖：
1. 经典四主题（AVAILABLE_THEMES）保持不变与稳定性；
2. theme="procedural" / None / "" / "auto" / "freeform" 及缺省参数的确定性与等价性；
3. 20+ 不同种子全链路通过 validate_package、home 全图可达、多业态开放货架（goods + sale_spec + shop_slot:*）、
   9 键 matter_profile、affordance_traits、interior 室内场景与 SVG 预览、evaluate_package 质检、
   以及 editor_bridge 导出→编辑→回写零丢失；
4. 跨种子结构多样性（4 种道路拓扑图同构特征、双地理风貌、世界名、商铺组合、多分区住宅布局）；
5. 零 LLM 调用、纯 LF 换行、无表情符号与无写死角色名/代词卫生防线。
"""

from __future__ import annotations

import copy
import io
import json
import os
import unittest
from collections import deque
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

try:
    from ._setup import WorldGenTestCase
except ImportError:  # pragma: no cover
    from _setup import WorldGenTestCase

from components.worldgen import __main__ as wg_cli
from components.worldgen import quality, schema, validator
from components.worldgen.editor_bridge import import_from_editor, to_editor_world
from components.worldgen.generator import (
    AVAILABLE_THEMES,
    PROCEDURAL_THEME,
    PROCEDURAL_THEME_ALIASES,
    STREET_LAYOUT_STYLES,
    generate_world,
)
from components.worldgen.interior import apply_interiors_to_package
from components.worldgen.instance import init_instance
from components.worldgen.loader import load as load_package
from components.worldgen.preview import render_preview
from components.worldgen.procedural import (
    COMMERCIAL_VENUE_IDS,
    EXTRA_HOME_AREA_IDS,
    synthesize_procedural_template,
)
from components.worldgen.providers import glm

import world_open_storefront as storefront

SEEDS_20 = (
    1, 7, 42, 100, 999, 1234, 8888, 929711, 20261014, 314159,
    271828, 555555, 606060, 777777, 864213, 902107, 112358, 132134,
    580081, 999983,
)

CRAFT_9_PROFILE = {
    "ambient_zone": "cold",
    "water_source": False,
    "capacity_slots": 3,
    "item_kind": "paper",
    "paper_like": True,
    "shelf_life_hours": 48,
    "portions": 2,
    "material_units": 5,
    "assembly_role": "insert_part",
}


def _reachable_from_home(locations: list[dict]) -> bool:
    adjacency = {loc["location_id"]: list(loc["reachable"]) for loc in locations}
    if "home" not in adjacency:
        return False
    seen = {"home"}
    queue = deque(["home"])
    while queue:
        cur = queue.popleft()
        for nxt in adjacency[cur]:
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return len(seen) == len(adjacency)


def _street_subgraph_degrees(topology: dict) -> tuple[int, list[int]]:
    street_ids = {
        loc["location_id"] for loc in topology["locations"] if loc["kind"] == "street"
    }
    deg = {sid: 0 for sid in street_ids}
    edge_count = 0
    for edge in topology["edges"]:
        if edge["from"] in street_ids and edge["to"] in street_ids:
            deg[edge["from"]] += 1
            deg[edge["to"]] += 1
            edge_count += 1
    return edge_count, sorted(deg.values())


class ClassicThemesPreservedTest(WorldGenTestCase):
    """保底防线：经典四主题常量与既有种子摘要 100% 不变。"""

    def test_available_themes_tuple_unchanged(self) -> None:
        self.assertEqual(
            AVAILABLE_THEMES,
            ("coastal_town", "residential_town", "mountain_town", "rural_farming"),
        )
        self.assertEqual(
            PROCEDURAL_THEME_ALIASES,
            ("procedural", "freeform", "auto"),
        )

    def test_classic_coastal_929711_summary_unchanged(self) -> None:
        docs = generate_world(seed=929711, theme="coastal_town")
        summary = schema.package_summary(docs)
        self.assertEqual(summary["world_id"], "coastal_town_929711")
        self.assertEqual(summary["theme"], "coastal_town")
        self.assertEqual(summary["locations"], 19)
        self.assertEqual(summary["edges"], 18)
        self.assertEqual(summary["npcs"], 3)
        self.assertEqual(summary["objects"], 3)


class ProceduralDeterminismAndAliasesTest(WorldGenTestCase):
    """零预设默认参数、别名归一与 CLI 入口确定性测试。"""

    def test_default_and_all_aliases_produce_identical_world(self) -> None:
        seed = 929711
        baseline = generate_world(seed=seed)
        self.assertEqual(baseline["world.json"]["theme"], PROCEDURAL_THEME)
        self.assertEqual(baseline["world.json"]["world_id"], f"procedural_{seed}")

        for alias in ("procedural", "freeform", "auto", None, "", "  AUTO  "):
            with self.subTest(alias=alias):
                candidate = generate_world(seed=seed, theme=alias)
                for filename in schema.PACKAGE_FILES:
                    self.assertEqual(
                        json.dumps(candidate[filename], sort_keys=True, ensure_ascii=False),
                        json.dumps(baseline[filename], sort_keys=True, ensure_ascii=False),
                    )

    def test_cli_generate_supports_procedural_and_default_theme(self) -> None:
        out_explicit = self.temp_dir(prefix="wg-cli-proc-") / "pkg_explicit"
        out_default = self.temp_dir(prefix="wg-cli-def-") / "pkg_default"
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf):
            rc1 = wg_cli.main(["generate", "--seed", "4242", "--theme", "procedural", "--out", str(out_explicit)])
            rc2 = wg_cli.main(["generate", "--seed", "4242", "--out", str(out_default)])
        self.assertEqual(rc1, 0)
        self.assertEqual(rc2, 0)
        self.assertEqual(validator.validate_package(out_explicit), [])
        self.assertEqual(validator.validate_package(out_default), [])
        for name in (*schema.PACKAGE_FILES, "MANIFEST.json"):
            self.assertEqual(
                (out_explicit / name).read_bytes(),
                (out_default / name).read_bytes(),
            )


class ProceduralBatch20SeedsTest(WorldGenTestCase):
    """20 个不同种子的端到端结构、货架、九键物质档、室内与往返验收。"""

    def test_20_seeds_pass_validation_storefront_matter_interior_and_quality(self) -> None:
        seen_styles: set[str] = set()
        seen_biomes: set[tuple[str, ...]] = set()
        seen_world_names: set[str] = set()
        seen_shop_mixes: set[frozenset[str]] = set()
        seen_home_layouts: set[tuple[str, ...]] = set()
        seen_intros: dict[str, set[str]] = {"_all": set()}

        with mock.patch.object(
            glm, "_request_chat", side_effect=AssertionError("禁止在程序化世界生成中触发任何 LLM 调用")
        ):
            for seed in SEEDS_20:
                with self.subTest(seed=seed):
                    docs = generate_world(seed=seed, theme="procedural")
                    self.assertEqual(validator.validate_package(docs), [])
                    self.assertEqual(docs["content.json"]["source"], "local")
                    self.assertIsNone(docs["content.json"]["model"])

                    world_meta = docs["content.json"]["entries"]["world"]
                    style = world_meta["street_layout_style"]
                    self.assertIn(style, STREET_LAYOUT_STYLES)
                    seen_styles.add(style)

                    biome_mix = tuple(world_meta["biome_mix"])
                    self.assertEqual(len(biome_mix), 2)
                    self.assertNotEqual(biome_mix[0], biome_mix[1])
                    seen_biomes.add(biome_mix)
                    seen_world_names.add(docs["world.json"]["name"])

                    topology = docs["topology.json"]
                    locations_by_id = {
                        loc["location_id"]: loc for loc in topology["locations"]
                    }
                    self.assertIn("home", locations_by_id)
                    self.assertEqual(locations_by_id["home"]["area_id"], "bedroom")
                    self.assertIn("school", locations_by_id)
                    self.assertTrue(
                        docs["rules.json"]["location_rules"]["school"]["weekdays_only"]
                    )
                    self.assertTrue(_reachable_from_home(topology["locations"]))
                    for edge in topology["edges"]:
                        self.assertGreaterEqual(edge["walk_seconds"], 60)
                        self.assertLessEqual(edge["walk_seconds"], 900)

                    # 校验街道子图真实拓扑与声明的 street_layout_style 一致
                    s_edges, s_degs = _street_subgraph_degrees(topology)
                    if style == "star_hub":
                        self.assertEqual((s_edges, s_degs), (3, [1, 1, 1, 3]))
                    elif style == "ring_loop":
                        self.assertEqual((s_edges, s_degs), (4, [2, 2, 2, 2]))
                    elif style == "linear_promenade":
                        self.assertEqual((s_edges, s_degs), (3, [1, 1, 2, 2]))
                    elif style == "forked_avenue":
                        self.assertEqual((s_edges, s_degs), (4, [1, 1, 1, 2, 3]))

                    # 校验多分区 home 声明（bedroom + 1~2 个附加区域）
                    home_rooms = locations_by_id["home"]["rooms"]
                    room_ids = tuple(r["room_id"] for r in home_rooms)
                    self.assertEqual(room_ids[0], "bedroom")
                    self.assertTrue(2 <= len(room_ids) <= 3)
                    self.assertTrue(set(room_ids[1:]).issubset(set(EXTRA_HOME_AREA_IDS)))
                    seen_home_layouts.add(room_ids)

                    # 校验至少 3 家不同商业场所 + affordances + 开放营业时段
                    present_shops = frozenset(
                        loc_id for loc_id in locations_by_id if loc_id in COMMERCIAL_VENUE_IDS
                    )
                    self.assertGreaterEqual(len(present_shops), 3)
                    seen_shop_mixes.add(present_shops)
                    for shop_id in present_shops:
                        rule = docs["rules.json"]["location_rules"][shop_id]
                        self.assertIsNotNone(rule["open_hour"])
                        self.assertIsNotNone(rule["close_hour"])
                        self.assertLess(rule["open_hour"], rule["close_hour"])
                        self.assertTrue(rule["affordances"])
                        self.assertTrue(docs["content.json"]["entries"][shop_id].get("goods"))

                    # 校验初始 objects.json：affordance_traits、9 键 matter_profile、家电三件套与商铺 sale_spec
                    objects = docs["objects.json"]["objects"]
                    self.assertTrue(objects)
                    for obj in objects:
                        self.assertTrue(obj.get("affordance_traits"))
                        self.assertTrue(
                            set(obj["affordance_traits"]).issubset(
                                set(schema.CANONICAL_AFFORDANCE_TRAITS)
                            )
                        )
                        mp = obj.get("matter_profile")
                        self.assertIsInstance(mp, dict)
                        self.assertEqual(
                            schema.normalize_matter_profile(mp, "test"), mp
                        )
                    self.assertTrue(
                        any(
                            set(obj["matter_profile"]) == set(schema.MATTER_PROFILE_KEYS)
                            for obj in objects
                        ),
                        f"seed={seed} 缺少完整 9 键 matter_profile 物件",
                    )
                    home_objs = [o for o in objects if o["location_id"] == "home"]
                    self.assertTrue(any(o["matter_profile"].get("ambient_zone") == "cold" for o in home_objs))
                    self.assertTrue(any(o["matter_profile"].get("ambient_zone") == "heating" for o in home_objs))
                    self.assertTrue(any(o["matter_profile"].get("water_source") is True for o in home_objs))
                    self.assertTrue(any("抽屉" in o["name"] for o in home_objs))
                    sale_objs = [o for o in objects if "sale_spec" in o]
                    self.assertEqual({o["location_id"] for o in sale_objs}, set(present_shops))

                    # 写盘 + world_open_storefront 开放货架槽位发现
                    pkg_dir = self.temp_package(docs, prefix=f"wg-proc-{seed}-")
                    self.assertEqual(validator.validate_package(pkg_dir), [])
                    env = {"CHIYO_WORLD_PACKAGE": str(pkg_dir)}
                    sources = storefront.resolve_world_sources(environment=env)
                    self.assertIsNotNone(sources)
                    discovered_shops = set(storefront.open_store_ids(sources))
                    self.assertTrue(present_shops.issubset(discovered_shops))
                    for shop_id in present_shops:
                        slots = storefront.all_open_slots_for_location(shop_id, sources=sources)
                        self.assertTrue(slots, f"{shop_id} 未发现开放柜台槽位")
                        shelf = storefront.store_shelf_for_location(shop_id, sources=sources)
                        self.assertTrue(
                            any(row.get("open_order") and str(row["catalog_item_id"]).startswith(f"shop_slot:{shop_id}:") for row in shelf)
                        )
                        self.assertTrue(
                            any(not row.get("open_order") and str(row["catalog_item_id"]).startswith(f"shop_item:{shop_id}:") for row in shelf)
                        )

                    # 室内场景生成 + 多分区 home 对齐 + SVG 预览 + evaluate_package 质量检查
                    int_receipt = apply_interiors_to_package(pkg_dir)
                    self.assertEqual(int_receipt["status"], "written")
                    self.assertEqual(validator.validate_package(pkg_dir), [])
                    after_int = schema.load_package(pkg_dir, verify=True)
                    nav_home = after_int[schema.SCENE_FILE]["navigation"]["home"]
                    nav_area_ids = {a["area_id"] for a in nav_home["areas"]}
                    self.assertEqual(nav_area_ids, set(room_ids))
                    # 室内重新生成后：home 冷藏/加热/水源/抽屉/9键物件 与 各商铺 sale_spec 依然完好
                    after_objs = after_int["objects.json"]["objects"]
                    after_home_objs = [o for o in after_objs if o["location_id"] == "home"]
                    self.assertTrue(any(o["matter_profile"].get("ambient_zone") == "cold" for o in after_home_objs))
                    self.assertTrue(any(o["matter_profile"].get("ambient_zone") == "heating" for o in after_home_objs))
                    self.assertTrue(any(o["matter_profile"].get("water_source") is True for o in after_home_objs))
                    self.assertTrue(any("抽屉" in o["name"] for o in after_home_objs))
                    self.assertTrue(
                        any(set(o["matter_profile"]) == set(schema.MATTER_PROFILE_KEYS) for o in after_home_objs)
                    )
                    self.assertEqual(
                        {o["location_id"] for o in after_objs if "sale_spec" in o},
                        set(present_shops),
                    )

                    preview_html = render_preview(pkg_dir).read_text(encoding="utf-8")
                    self.assertIn("<svg", preview_html)
                    self.assertIn("室内布局", preview_html)

                    eval_report = quality.evaluate_package(
                        pkg_dir, consistency_days=2, seen_intros=seen_intros
                    )
                    self.assertTrue(eval_report["ok"], f"seed={seed} 质检失败: {eval_report}")

        # 跨 20 种子结构多样性断言
        self.assertEqual(seen_styles, set(STREET_LAYOUT_STYLES))
        self.assertGreaterEqual(len(seen_biomes), 8)
        self.assertGreaterEqual(len(seen_world_names), 12)
        self.assertGreaterEqual(len(seen_shop_mixes), 6)
        self.assertGreaterEqual(len(seen_home_layouts), 5)

    def test_extreme_seed_boundaries(self) -> None:
        for seed in (0, 2, 65535, 2**31 - 1):
            with self.subTest(seed=seed):
                templ = synthesize_procedural_template(seed)
                self.assertTrue(templ["procedural_mode"])
                docs = generate_world(seed=seed, theme="procedural")
                self.assertEqual(validator.validate_package(docs), [])


class ProceduralEditorRoundtripTest(WorldGenTestCase):
    """编辑器导出 → 修改 → 回写：affordance_traits / 9 键 matter_profile / sale_spec 零丢失。"""

    def test_20_seeds_unedited_roundtrip_zero_loss(self) -> None:
        for seed in SEEDS_20:
            with self.subTest(seed=seed):
                pkg_dir = self.temp_package(
                    generate_world(seed=seed, theme="procedural"),
                    prefix=f"wg-rt0-{seed}-",
                )
                before = schema.load_package(pkg_dir, verify=True)
                view = to_editor_world(load_package(pkg_dir))
                out = self.temp_dir(prefix=f"wg-rt0-out-{seed}-") / "edited"
                receipt = import_from_editor(pkg_dir, copy.deepcopy(view), out_dir=out)
                self.assertEqual(receipt["updated_objects"], {})
                self.assertEqual(receipt["updated_locations"], {})
                after = schema.load_package(out, verify=True)
                self.assertEqual(validator.validate_package(out), [])
                self.assertEqual(after["objects.json"], before["objects.json"])
                self.assertEqual(after["content.json"], before["content.json"])

    def test_real_edit_roundtrip_preserves_traits_matter_and_sale_spec(self) -> None:
        pkg_dir = self.temp_package(
            generate_world(seed=929711, theme="procedural"),
            prefix="wg-rtedit-",
        )
        apply_interiors_to_package(pkg_dir)
        before = schema.load_package(pkg_dir, verify=True)
        package = load_package(pkg_dir)
        view = to_editor_world(package)

        # 挑选一家当前镇上的商店改名、改营业时段，并新增一件带 9 键 matter_profile + sale_spec 的可售商品
        target_shop = next(
            loc["location_id"]
            for loc in before["topology.json"]["locations"]
            if loc["location_id"] in COMMERCIAL_VENUE_IDS
        )
        shop_room = next(r for r in view["rooms"] if r["id"] == target_shop)
        shop_room["name"] = "改建后的海风杂货屋"
        shop_room["hours"] = {"open": 10, "close": 18}

        template_obj = copy.deepcopy(view["objects"][0])
        template_obj.update(
            {
                "id": "custom_craft_box",
                "name": "手作香草标本礼盒",
                "description": "店主新摆上架的限定押花标本礼盒。",
                "kind": "book",
                "room_id": target_shop,
                "x": 55,
                "y": 55,
                "actions": ["pick_up", "place", "read"],
                "behavior": {"read": {"minutes": 15, "energy_cost": 3}},
                "portable": True,
                "capacity": 0,
                "servings": 0,
                "initial_status": "neutral",
                "asset_id": None,
                "interaction": {"near": False, "radius": 2},
                "affordance_traits": ["inscribable", "insertable", "movable", "combinable"],
                "matter_profile": copy.deepcopy(CRAFT_9_PROFILE),
                "sale_spec": {"category": "daily", "price_tier": "hearty", "price": 1280},
            }
        )
        view["objects"].append(template_obj)

        out = self.temp_dir(prefix="wg-rtedit-out-") / "edited"
        receipt = import_from_editor(pkg_dir, view, out_dir=out)
        self.assertIn(target_shop, receipt["updated_locations"])
        self.assertIn("custom_craft_box", receipt["added_objects"])
        self.assertEqual(validator.validate_package(out), [])

        after = schema.load_package(out, verify=True)
        before_sale_by_id = {
            o["object_id"]: o["sale_spec"]
            for o in before["objects.json"]["objects"]
            if "sale_spec" in o
        }
        after_by_id = {o["object_id"]: o for o in after["objects.json"]["objects"]}
        # 原有全部可售商品 sale_spec / matter_profile / affordance_traits 零丢失
        for oid, spec in before_sale_by_id.items():
            self.assertIn(oid, after_by_id)
            self.assertEqual(after_by_id[oid].get("sale_spec"), spec)
            self.assertTrue(after_by_id[oid].get("affordance_traits"))
            self.assertTrue(after_by_id[oid].get("matter_profile"))

        # 新增商品的 9 键 matter_profile、affordance_traits 与 sale_spec 完整落盘并被开放货架识别
        added = after_by_id["custom_craft_box"]
        self.assertEqual(added["matter_profile"], CRAFT_9_PROFILE)
        self.assertEqual(set(added["matter_profile"]), set(schema.MATTER_PROFILE_KEYS))
        self.assertEqual(
            added["affordance_traits"],
            ["inscribable", "insertable", "movable", "combinable"],
        )
        self.assertEqual(
            added["sale_spec"],
            {"category": "daily", "price_tier": "hearty", "price": 1280},
        )
        # 室内多分区导航也在回写后完整保留
        self.assertEqual(
            after[schema.SCENE_FILE]["navigation"]["home"],
            before[schema.SCENE_FILE]["navigation"]["home"],
        )

        sources = storefront.resolve_world_sources(environment={"CHIYO_WORLD_PACKAGE": str(out)})
        shelf = storefront.store_shelf_for_location(target_shop, sources=sources)
        by_name = {row["name"]: row for row in shelf}
        self.assertIn("手作香草标本礼盒", by_name)
        self.assertEqual(by_name["手作香草标本礼盒"]["price"], 1280)


class ProceduralRuntimeAndBatchIntegrationTest(WorldGenTestCase):
    """隔离实例初始化、室内多分区寻路与 quality.run_batch 集成验收。"""

    @unittest.skipIf(os.name == "nt", "rc9 世界运行时与 tz 数据库在 WSL/POSIX 环境执行")
    def test_procedural_instance_and_multi_room_home_routing(self) -> None:
        import world_runtime_data as wrd

        pkg_dir = self.temp_package(
            generate_world(seed=314159, theme="procedural"),
            prefix="wg-proc-inst-",
        )
        apply_interiors_to_package(pkg_dir)
        inst_home = self.temp_dir(prefix="wg-proc-home-") / "instance"
        receipt = init_instance(pkg_dir, inst_home)
        self.assertEqual(receipt["status"], "created")
        self.assertEqual(receipt["world_id"], "procedural_314159")

        env = {"CHIYO_WORLD_PACKAGE": str(pkg_dir)}
        wrd.reset_cache()
        try:
            world = wrd.active_world(env)
            self.assertEqual(world.world_id, "procedural_314159")
            home_areas = [a["area_id"] for a in world.areas_of("home")]
            self.assertIn("bedroom", home_areas)
            self.assertGreaterEqual(len(home_areas), 2)
            for loc_id in world.known_location_ids:
                route = world.route_to("home/bedroom", loc_id)
                self.assertIsNotNone(route, f"从 home/bedroom 无法路由到 {loc_id}")
        finally:
            wrd.reset_cache()

    def test_quality_run_batch_accepts_procedural_theme(self) -> None:
        batch_out = self.temp_dir(prefix="wg-proc-batch-") / "batch"
        report = quality.run_batch(
            out_dir=batch_out,
            themes=["procedural"],
            seeds=[101, 202],
            consistency_days=1,
        )
        self.assertEqual(report["summary"]["total"], 2)
        self.assertEqual(report["summary"]["ok"], 2)
        self.assertEqual(report["summary"]["issues"], 0)


class WorldGenHygieneGuardTest(unittest.TestCase):
    """代码卫生防线：纯 LF 换行、无写死代词/角色名、无表情符号。"""

    def test_pure_lf_and_no_forbidden_tokens(self) -> None:
        wg_root = Path(__file__).resolve().parents[1]
        forbidden_name = "\u5343\u4ee3"
        forbidden_pronoun = "\u5979"

        py_files = sorted(wg_root.rglob("*.py"))
        self.assertTrue(py_files)
        for path in py_files:
            raw = path.read_bytes()
            with self.subTest(file=str(path.relative_to(wg_root))):
                self.assertNotIn(b"\r", raw, f"{path.name} 存在 CRLF 换行")

        checked_modules = (
            wg_root / "procedural.py",
            wg_root / "generator.py",
            wg_root / "interior.py",
            wg_root / "schema.py",
            wg_root / "validator.py",
            wg_root / "quality.py",
            wg_root / "editor_bridge.py",
            wg_root / "__main__.py",
            Path(__file__).resolve(),
        )
        for path in checked_modules:
            text = path.read_text(encoding="utf-8")
            with self.subTest(module=path.name):
                self.assertNotIn(forbidden_name, text)
                self.assertNotIn(forbidden_pronoun, text)
                for ch in text:
                    cp = ord(ch)
                    self.assertFalse(
                        0x1F300 <= cp <= 0x1FAFF or 0x2600 <= cp <= 0x27BF,
                        f"{path.name} 包含禁止的表情/装饰符号 U+{cp:04X}",
                    )


if __name__ == "__main__":
    unittest.main()
