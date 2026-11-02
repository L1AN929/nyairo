#!/usr/bin/env python3
"""发行脱敏：真实网关地址不在仓库默认值里，必须由运行期显式配置提供。

覆盖点（全部离线，不构造任何网络请求）：

1. 模块不再内置真实域名；
2. 缺 ``NYA02_MODEL_BASE_URL`` 时 ``_verify_model_config`` 在参数核对处 fail-closed；
3. 显式合成地址（``model.example.invalid``）被接受，provider/model 仍严格比对；
4. 离线夹具用不可联网的合成地址，且不继承父 shell 的生产模型变量；
5. phase 子进程 env：离线剥掉生产模型键，真实路径保留显式地址；
6. 真实 ``prepare`` 缺地址时在建实例/写 prereg 之前拒绝。

测试只用 ``mkdtemp`` 并保留目录，不联网、不读真实 secrets。
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
for _entry in (str(REPO_ROOT), str(REPO_ROOT / "scripts")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import g1_real_adoption as g1  # noqa: E402

SYNTHETIC = "https://model.example.invalid/v1"
PRODUCTION_LOOKING = "https://prod-looking.invalid/v1"


def _full_env(base_url: str) -> dict[str, str]:
    return {
        "NYA02_MODEL_PROVIDER": g1.MODEL_PROVIDER,
        "NYA02_MODEL_BASE_URL": base_url,
        "NYA02_MODEL_NAME": g1.MODEL_NAME,
        "NYA02_MODEL_API_KEY": "test-not-a-real-key",
    }


class RedactedDefaultTest(unittest.TestCase):
    def test_module_constant_is_not_a_real_address(self):
        self.assertEqual(g1.MODEL_BASE_URL, "")

    def test_source_text_contains_no_real_gateway_host(self):
        blob = Path(g1.__file__).read_bytes()
        self.assertNotIn(b"929711.xyz", blob)
        self.assertNotIn(b"gw.929711", blob)


class ExplicitConfigTest(unittest.TestCase):
    def test_missing_base_url_is_refused_before_model_lookup(self):
        env = _full_env("")
        env.pop("NYA02_MODEL_BASE_URL")
        with mock.patch.dict(os.environ, env, clear=False):
            result = g1._verify_model_config(env)
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("reason_code"), g1.REASON_NO_MODEL_CONFIG)

    def test_explicit_synthetic_address_is_accepted_without_network(self):
        env = _full_env(SYNTHETIC)
        result = g1._verify_model_config(env)
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result.get("base_url"), SYNTHETIC)
        self.assertEqual(result.get("model"), g1.MODEL_NAME)

    def test_model_name_mismatch_still_fails(self):
        env = _full_env(SYNTHETIC)
        env["NYA02_MODEL_NAME"] = "some-other-model"
        result = g1._verify_model_config(env)
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("reason_code"), g1.REASON_MODEL_PARAM_MISMATCH)


class OfflineFixtureTest(unittest.TestCase):
    def test_offline_env_is_synthetic_and_ignores_production_env(self):
        with mock.patch.dict(os.environ,
                             {"NYA02_MODEL_BASE_URL": PRODUCTION_LOOKING},
                             clear=False):
            env = g1._offline_model_env()
        self.assertEqual(env["NYA02_MODEL_BASE_URL"], "https://offline.invalid/v1")
        self.assertNotEqual(env["NYA02_MODEL_BASE_URL"], PRODUCTION_LOOKING)


class PhaseChildEnvTest(unittest.TestCase):
    def test_offline_child_env_strips_production_model_keys(self):
        with mock.patch.dict(os.environ, _full_env(PRODUCTION_LOOKING), clear=False):
            env = g1._phase_child_env(offline=True)
        self.assertNotIn("NYA02_MODEL_BASE_URL", env)
        self.assertNotIn("NYA02_MODEL_API_KEY", env)

    def test_real_child_env_keeps_explicit_address(self):
        with mock.patch.dict(os.environ, _full_env(SYNTHETIC), clear=False):
            env = g1._phase_child_env(offline=False)
        self.assertEqual(env.get("NYA02_MODEL_BASE_URL"), SYNTHETIC)


class PrepareGuardTest(unittest.TestCase):
    def test_real_prepare_without_address_refuses_before_writing(self):
        root = Path(tempfile.mkdtemp(prefix="release-redaction-prepare-"))
        root.mkdir(parents=True, exist_ok=True)
        with mock.patch.dict(os.environ, {"NYA02_MODEL_BASE_URL": ""}, clear=False):
            result = g1.prepare(root, expected_root=root, offline=False)
        self.assertFalse(result.get("ok"), result)
        self.assertEqual(result.get("reason_code"), g1.REASON_NO_MODEL_CONFIG)
        # 拒绝发生在建实例/写 prereg 之前：根里没有 experiment-g1，也没有根外 authority。
        self.assertFalse((root / "experiment-g1").exists())
        self.assertFalse((root.parent / (root.name + "-memory-authority")).exists())


if __name__ == "__main__":
    unittest.main()
