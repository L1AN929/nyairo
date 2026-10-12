"""GLM 提供者与实例初始化的单元测试（不发真实网络请求）。"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

import os

from ._setup import WorldGenTestCase, make_valid_docs
from components.worldgen import schema
from components.worldgen.instance import init_instance, read_instance_meta
from components.worldgen.providers import glm

#: 实例初始化会经 world_foundation 校验 IANA 时区，需要本机有 tz 数据库。
needs_tzdata = unittest.skipIf(
    os.name == "nt",
    "原生 Windows 无 IANA tz 数据库；实例初始化在 WSL/Linux 上运行",
)


class GLMProviderTest(unittest.TestCase):
    def test_not_configured_without_key(self) -> None:
        env = {"WORLDGEN_LLM_API_KEY": ""}
        self.assertFalse(glm.is_configured(env))
        env2 = {}
        self.assertFalse(glm.is_configured(env2))
        self.assertTrue(glm.is_configured({"WORLDGEN_LLM_API_KEY": " sk-test "}))

    def test_enrich_refuses_without_key(self) -> None:
        with self.assertRaises(glm.GLMProviderError):
            glm.enrich(make_valid_docs(seed=1), api_key="")

    def test_extract_json_strips_code_fence(self) -> None:
        text = '```json\n{"entries": {"world": {"tagline": "x"}}}\n```'
        parsed = glm._extract_json(text)
        self.assertEqual(parsed["entries"]["world"]["tagline"], "x")

    def test_extract_json_rejects_non_json(self) -> None:
        with self.assertRaises(glm.GLMProviderError):
            glm._extract_json("好的，这是内容： blah")

    def test_merge_entries_rejects_unknown_ids(self) -> None:
        docs = make_valid_docs(seed=2)
        entries = {
            "world": {"tagline": "x"},
            **{
                loc["location_id"]: {"intro": "介绍"}
                for loc in docs["topology.json"]["locations"]
            },
            "made_up_place": {"intro": "幻觉地点"},
        }
        with self.assertRaises(glm.GLMProviderError) as ctx:
            glm._merge_entries(docs, entries)
        self.assertIn("未知 id", str(ctx.exception))

    def test_merge_entries_requires_all_locations(self) -> None:
        docs = make_valid_docs(seed=3)
        entries = {"world": {"tagline": "x"}}
        with self.assertRaises(glm.GLMProviderError) as ctx:
            glm._merge_entries(docs, entries)
        self.assertIn("缺少地点介绍", str(ctx.exception))


@needs_tzdata
class InstanceInitTest(WorldGenTestCase):
    def test_init_creates_isolated_instance(self) -> None:
        package_dir = self.temp_package(make_valid_docs(seed=606060))
        package = schema.load_package(package_dir)
        home = self.temp_dir(prefix="wg-inst-") / "instance"
        receipt = init_instance(package_dir, home)
        self.assertEqual(receipt["status"], "created")
        self.assertEqual(receipt["world_id"], package["world.json"]["world_id"])
        meta = read_instance_meta(home)
        self.assertEqual(meta["world_id"], package["world.json"]["world_id"])
        self.assertTrue((home / "data" / "world" / "world_state.json").exists())
        self.assertTrue((home / "data" / "world" / "world_timeline.json").exists())
        self.assertTrue((home / "data" / "world" / "body_substrate.json").exists())

    def test_init_refuses_existing_home(self) -> None:
        package_dir = self.temp_package(make_valid_docs(seed=707070))
        home = self.temp_dir(prefix="wg-inst-") / "instance"
        init_instance(package_dir, home)
        with self.assertRaises(FileExistsError):
            init_instance(package_dir, home)

    def test_init_refuses_world_id_mismatch(self) -> None:
        package_dir = self.temp_package(make_valid_docs(seed=808080))
        home = self.temp_dir(prefix="wg-inst-") / "instance"
        with self.assertRaises(schema.WorldPackageError):
            init_instance(package_dir, home, world_id="another_world")

    @unittest.skipIf(os.name == "nt", "需要 POSIX 文件锁的运行时校验在 WSL 执行")
    def test_instance_state_loads_through_runtime_path(self) -> None:
        """初始化产物能被运行时读取路径（skeleton._load_state）接受。"""
        package_dir = self.temp_package(make_valid_docs(seed=909090))
        package = schema.load_package(package_dir)
        home = self.temp_dir(prefix="wg-inst-") / "instance"
        init_instance(package_dir, home)
        import sys

        scripts = str(Path(__file__).resolve().parents[3] / "components" / "world" / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        import world_foundation as wf

        state = wf.WorldStateStore(home / "data" / "world" / "world_state.json").load()
        self.assertIsNotNone(state)
        self.assertEqual(state["world_id"], package["world.json"]["world_id"])
        self.assertEqual(state["location"]["place_id"], "home")


if __name__ == "__main__":
    unittest.main()
