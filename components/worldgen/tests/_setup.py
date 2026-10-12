"""worldgen 测试公共设置：sys.path 与临时世界包工厂。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
for entry in (
    _REPO_ROOT,
    _REPO_ROOT / "components",
    _REPO_ROOT / "components" / "world" / "scripts",
):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))


def write_temp_package(docs: dict, prefix: str = "wg-test-") -> Path:
    """把世界包文档写入临时目录并返回路径（调用方负责清理）。"""
    from components.worldgen import schema

    directory = Path(tempfile.mkdtemp(prefix=prefix))
    schema.write_package(directory, docs)
    return directory


def make_valid_docs(seed: int = 20261014, theme: str = "coastal_town"):
    from components.worldgen.generator import generate_world

    return generate_world(seed=seed, theme=theme)


class WorldGenTestCase(unittest.TestCase):
    """带临时目录管理的基础测试类。"""

    def setUp(self) -> None:
        self._temp_dirs: list[Path] = []

    def temp_dir(self, prefix: str = "wg-test-") -> Path:
        import tempfile

        path = Path(tempfile.mkdtemp(prefix=prefix))
        self._temp_dirs.append(path)
        return path

    def temp_package(self, docs=None, prefix: str = "wg-pkg-") -> Path:
        if docs is None:
            docs = make_valid_docs()
        path = write_temp_package(docs, prefix=prefix)
        self._temp_dirs.append(path)
        return path

    def tearDown(self) -> None:
        import shutil

        for path in self._temp_dirs:
            shutil.rmtree(path, ignore_errors=True)


__all__ = ["WorldGenTestCase", "make_valid_docs", "write_temp_package"]
