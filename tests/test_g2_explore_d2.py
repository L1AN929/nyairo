#!/usr/bin/env python3
"""G2 explore-d2 的本功能接缝：D2 身份/额度、检查节拍 10 分钟贯通、旧批不变。

这些是机械/接缝证据：不联网、不建正式根。真实短链在
``scripts/g2_explore_d2.py offline-check`` 里单独跑（共享 C 的短链实现）。
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for _entry in (str(REPO_ROOT), str(REPO_ROOT / "components"),
               str(REPO_ROOT / "components" / "life" / "scripts"),
               str(REPO_ROOT / "components" / "native"),
               str(REPO_ROOT / "components" / "native" / "memory_runtime_v1"),
               str(REPO_ROOT / "scripts")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import g2_explore_d2 as d2  # noqa: E402
import g2_next_live as live  # noqa: E402

CST = timezone(timedelta(hours=8))


class D2ProfileTest(unittest.TestCase):
    def test_d2_identity_caps_layout_and_schedule(self) -> None:
        profile = live.D2_PROFILE
        self.assertEqual(profile.run_id, "run:g2-explore-20261103-d2")
        self.assertEqual(profile.domain_id, "g2:explore:20261103-d2")
        self.assertEqual(profile.batch_root, Path("/tmp/nyairo-g2-explore-20261103-d2"))
        self.assertEqual(profile.expected_history, 239)
        self.assertEqual((profile.request_cap, profile.acquisition_cap,
                          profile.shadow_cap, profile.continuation_cap,
                          profile.reserved_cap, profile.pairs),
                         (30, 8, 20, 0, 2, 10))
        self.assertEqual(profile.check_interval_minutes, 10)
        self.assertEqual(profile.cumulative_total, 1000)
        self.assertEqual(profile.utc_day_cap, 300)
        self.assertEqual(profile.acquisition_window_seconds, 5400)
        self.assertEqual(profile.total_window_seconds, 7200)
        self.assertEqual(profile.max_observations, 91)
        self.assertEqual(profile.deadline.isoformat(), "2026-11-03T21:00:00+08:00")
        self.assertEqual(profile.latest_start.isoformat(), "2026-11-03T19:00:00+08:00")
        self.assertEqual(profile.main_root, profile.batch_root / "main")
        self.assertEqual(profile.authority_dir, profile.batch_root / "memory-authority")
        self.assertEqual(profile.branches_root, profile.batch_root / "branches")
        caps = profile.caps()
        self.assertEqual((caps["total"], caps["acquisition"], caps["shadow"],
                          caps["continuation"], caps["reserved"], caps["pairs"]),
                         (30, 8, 20, 0, 2, 10))
        self.assertEqual(caps["check_interval_minutes"], 10)
        self.assertEqual(caps["cumulative_total"], 1000)
        self.assertEqual(caps["ambient"], "0")
        self.assertEqual(caps["gate"], "off")
        self.assertEqual(live.formal_profile("d2"), profile)
        self.assertIn(live.D2_FORMAL_BATCH_ROOT, live._formal_roots())
        self.assertIs(d2.PROFILE, live.D2_PROFILE)
        self.assertEqual(d2.BATCH, live.BATCH_D2)
        self.assertEqual(set(d2.COMMANDS),
                         {"offline-check", "prepare", "audit", "run", "status"})

    def test_old_profiles_and_defaults_unchanged(self) -> None:
        for profile in (live.FORMAL_PROFILE, live.B_PROFILE, live.C_PROFILE):
            self.assertIsNone(profile.check_interval_minutes)
            self.assertIsNone(profile.cumulative_total)
        self.assertEqual(live.C_PROFILE.expected_history, 234)
        self.assertEqual((live.C_PROFILE.request_cap, live.C_PROFILE.shadow_cap),
                         (30, 20))
        self.assertEqual(live.formal_profile("a").batch_root, live.FORMAL_BATCH_ROOT)
        self.assertEqual(live.formal_profile("b").batch_root, live.B_FORMAL_BATCH_ROOT)
        self.assertEqual(live.formal_profile("c").batch_root, live.C_FORMAL_BATCH_ROOT)
        self.assertEqual(live.formal_profile("unknown").batch_root, live.FORMAL_BATCH_ROOT)


class D2PreparedScratchTest(unittest.TestCase):
    """一次真实 prepare（夹具根）：持久 10 分钟、live_env 与 audit 都看见 10。"""

    @classmethod
    def setUpClass(cls) -> None:
        # mkdtemp 本身已创建父目录；prepare 要求 batch_root 是一个全新的子目录。
        cls.scratch = Path(tempfile.mkdtemp(prefix="g2-d2-offline-")) / "root"
        cls.result = live.prepare(profile=live.D2_PROFILE, offline_fixtures=True,
                                  root=cls.scratch, batch=live.BATCH_D2)
        base, _ = live._resolve_profile(offline_fixtures=True, root=cls.scratch,
                                        batch=live.BATCH_D2)
        cls.profile = replace(live.D2_PROFILE, batch_root=base.batch_root,
                              maintain_trace_path=base.maintain_trace_path)

    def test_prepare_persists_check_interval_and_history(self) -> None:
        self.assertTrue(self.result.get("ok"), self.result.get("reason_code"))
        self.assertEqual(self.result.get("check_interval_minutes"), 10)
        prereg = json.loads(self.profile.prereg_path.read_text(encoding="utf-8"))
        self.assertEqual(prereg["caps"]["check_interval_minutes"], 10)
        self.assertEqual(prereg["caps"]["cumulative_total"], 1000)
        self.assertEqual(prereg["history"]["used"], 239)
        self.assertEqual(prereg["history"]["total_cap"], 1000)
        self.assertEqual(prereg["history"]["utc_day_cap"], 300)
        self.assertEqual(prereg["history"]["new_cap"], 30)

    def test_installed_instance_and_live_env_use_ten_minutes(self) -> None:
        self.assertEqual(live._nested_check_interval(self.profile), 10)
        real_env = live.live_env(self.profile, offline=True, fixtures=False)
        self.assertEqual(real_env[live.ENV_CHECK_INTERVAL], "10")
        fixture_env = live.live_env(self.profile, offline=True, fixtures=True)
        self.assertEqual(fixture_env[live.ENV_CHECK_INTERVAL], "0")

    def test_audit_reports_persisted_value_and_refuses_tampered_identity(self) -> None:
        result = live.audit(profile=self.profile, offline_fixtures=True,
                            root=self.scratch, batch=live.BATCH_D2)
        self.assertTrue(result.get("ok"), result.get("reason_code"))
        self.assertEqual(result.get("check_interval_minutes"), 10)
        self.assertEqual(result.get("persisted_check_interval_minutes"), 10)
        self.assertEqual(result.get("cumulative_total"), 1000)
        path = self.profile.prereg_path
        original = path.read_text(encoding="utf-8")
        data = json.loads(original)
        data["run_id"] = "run:tampered"
        path.write_text(json.dumps(data), encoding="utf-8")
        try:
            tampered = live.audit(profile=self.profile, offline_fixtures=True,
                                  root=self.scratch, batch=live.BATCH_D2)
        finally:
            path.write_text(original, encoding="utf-8")
        self.assertFalse(tampered.get("ok"))
        self.assertEqual(tampered.get("reason_code"), live.R_IDENTITY_MISMATCH)


class D2RefusalTest(unittest.TestCase):
    def test_formal_and_custom_roots_are_refused(self) -> None:
        formal = live.prepare(profile=live.D2_PROFILE, offline_fixtures=True,
                              root=live.D2_PROFILE.batch_root, batch=live.BATCH_D2)
        self.assertFalse(formal.get("ok"))
        self.assertEqual(formal.get("reason_code"), live.R_FIXTURE_ROOT_REFUSED)
        custom = live.prepare(profile=live.D2_PROFILE, offline_fixtures=False,
                              root="/tmp/some-other-root", batch=live.BATCH_D2)
        self.assertFalse(custom.get("ok"))
        self.assertEqual(custom.get("reason_code"), live.R_FIXTURE_ROOT_REFUSED)

    def test_latest_start_and_deadline_are_refusals(self) -> None:
        scratch = Path(tempfile.mkdtemp(prefix="g2-d2-refuse-"))
        past = datetime(2000, 1, 1, tzinfo=CST)
        late = replace(live.D2_PROFILE, latest_start=past)
        result = live.run(profile=late, execute_approved=True, offline_fixtures=True,
                          root=scratch, batch=live.BATCH_D2)
        self.assertEqual(result.get("reason_code"), live.R_LATEST_START_PASSED)
        expired = replace(live.D2_PROFILE, latest_start=None, deadline=past)
        result = live.run(profile=expired, execute_approved=True, offline_fixtures=True,
                          root=scratch, batch=live.BATCH_D2)
        self.assertIn(result.get("reason_code"),
                      (live.R_NOT_PREPARED, live.R_DEADLINE_PASSED))

    def test_model_endpoint_missing_or_placeholder_is_refused(self) -> None:
        empty = tempfile.mkdtemp(prefix="g2-d2-model-")
        missing = live._verify_model_config({"NYA02_PROFILE_HOME": empty})
        self.assertFalse(missing.get("ok"))
        placeholder = live._verify_model_config({
            "NYA02_PROFILE_HOME": empty, "NYA02_MODEL_PROVIDER": "custom",
            "NYA02_MODEL_BASE_URL": "https://offline.invalid/v1",
            "NYA02_MODEL_NAME": "offline-model", "NYA02_MODEL_API_KEY": "not-a-key"})
        self.assertFalse(placeholder.get("ok"))


if __name__ == "__main__":
    unittest.main()
