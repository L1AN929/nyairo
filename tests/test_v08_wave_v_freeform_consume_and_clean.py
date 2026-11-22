#!/usr/bin/env python3
"""Wave V: free-form eat/drink and wash/wipe through the real matter engine.

Covers, against the real bridge / universal-matter engine (0 product-model calls):

* a brand-new custom drink (``冰镇可乐``) is recognised from its own label with
  no per-product code and one real portion is consumed (2 -> 1);
* a just-poured drink (``桂花热茶``, ``boil_scalding=True``) honestly refuses the
  free-text sip with ``DRINK_SCALDING_WAIT`` and leaves its portions untouched;
* washing ``白瓷杯`` while a brush still stands inside is refused by the real
  engine (``CONTAINER_NOT_EMPTY``); after the brush is taken out the same free
  text makes it genuinely ``clean``;
* asking for a drink that was never in the room (``柠檬水``) soft-lands as
  ``unsupported_capability`` and invents zero objects.

Run (WSL): ``python -B -m unittest tests.test_v08_wave_v_freeform_consume_and_clean -v``.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "components" / "world" / "service"),
    str(ROOT / "components" / "phone"),
    str(ROOT / "tests"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import v02_v03_daily_loop_bridge as bridge  # noqa: E402
from components.world.scripts import v08_universal_matter as engine  # noqa: E402

T0 = "2026-11-16T09:00:00+08:00"


class _FreeformBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-wave-v-")
        self.root = Path(self._tmp.name)
        (self.root / "phone").mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def introduce(self, object_id: str, label: str, kind: str, parent: str, **extra: Any) -> dict:
        step = {
            "op": "INTRODUCE_ITEM",
            "object_id": object_id,
            "label_cn": label,
            "item_kind": kind,
            "target_parent_id": parent,
        }
        step.update(extra)
        return engine.compose_open_act(
            self.root,
            {"intent_cn": f"放入{label}", "steps": [step]},
            now_iso=T0,
        )

    def objects(self) -> dict[str, Any]:
        return engine._read_state_zero_write(self.root)["matter_objects"]

    def execute(self, text: str, **extra: Any) -> dict:
        intent = {"intent_cn": text}
        intent.update(extra)
        return bridge.execute_normalized_micro_intent(
            self.root, intent, now_iso=T0
        )


class FreeformConsumeTest(_FreeformBase):
    def test_01_custom_drink_is_recognised_and_consumed_without_new_code(self) -> None:
        self.introduce("cola_01", "冰镇可乐", "drink", "dining_table", portions=2)

        out = self.execute("喝一口冰镇可乐")

        self.assertTrue(out["ok"])
        self.assertTrue(out["settled"])
        self.assertEqual(out["classification"], "direct_supported")
        self.assertEqual(out["llm_calls"], 0)
        step_result = out["receipt"]["step_results"][0]
        self.assertEqual(step_result["op"], "CONSUME_PORTION")
        self.assertEqual(step_result["portions_left"], 1)
        self.assertEqual(self.objects()["cola_01"]["portions_left"], 1)

    def test_02_scalding_drink_is_honestly_held_back(self) -> None:
        self.introduce(
            "osmanthus_tea_01",
            "桂花热茶",
            "drink",
            "dining_table",
            portions=1,
            boil_scalding=True,
        )

        out = self.execute("喝一口桂花热茶")

        self.assertFalse(out["settled"])
        self.assertTrue(out["soft_landed"])
        self.assertEqual(out["reason_code"], "DRINK_SCALDING_WAIT")
        self.assertTrue(out.get("gentle_hold"))
        self.assertFalse(out.get("drunk"))
        self.assertEqual(self.objects()["osmanthus_tea_01"]["portions_left"], 1)


class FreeformCleanTest(_FreeformBase):
    def test_03_non_empty_vessel_refuses_wash_then_succeeds_once_empty(self) -> None:
        # A default cup starts ``clean``, so first make it genuinely used and
        # empty: pour one real portion of tea into it and drink that portion
        # through the vessel.  Drinking the host's last portion marks the host
        # ``used`` and leaves it with no children.
        self.introduce(
            "tea_in_cup_01", "白瓷杯里的茶", "drink", "ceramic_cup_01", portions=1
        )
        engine.compose_open_act(
            self.root,
            {
                "intent_cn": "喝掉白瓷杯里的茶",
                "steps": [{"op": "CONSUME_PORTION", "object_id": "ceramic_cup_01"}],
            },
            now_iso=T0,
        )
        self.assertNotEqual(
            self.objects()["ceramic_cup_01"].get("cleanliness"), "clean"
        )

        engine.compose_open_act(
            self.root,
            {
                "intent_cn": "把画笔插进白瓷杯",
                "steps": [
                    {
                        "op": "PLACE_OR_MOVE",
                        "object_id": "pen_02",
                        "target_parent_id": "ceramic_cup_01",
                    }
                ],
            },
            now_iso=T0,
        )

        blocked = self.execute("把白瓷杯洗干净")
        self.assertFalse(blocked["settled"])
        self.assertTrue(blocked["soft_landed"])
        self.assertEqual(blocked["reason_code"], "CONTAINER_NOT_EMPTY")
        self.assertNotEqual(self.objects()["ceramic_cup_01"].get("cleanliness"), "clean")

        engine.compose_open_act(
            self.root,
            {
                "intent_cn": "把画笔拿出来",
                "steps": [
                    {
                        "op": "PLACE_OR_MOVE",
                        "object_id": "pen_02",
                        "target_parent_id": "dining_table",
                    }
                ],
            },
            now_iso=T0,
        )

        washed = self.execute("把白瓷杯洗干净")
        self.assertTrue(washed["ok"])
        self.assertTrue(washed["settled"])
        self.assertEqual(washed["classification"], "direct_supported")
        self.assertEqual(washed["llm_calls"], 0)
        self.assertEqual(self.objects()["ceramic_cup_01"]["cleanliness"], "clean")


class FreeformMissingTargetTest(_FreeformBase):
    def test_04_absent_drink_soft_lands_without_inventing_anything(self) -> None:
        out = self.execute("喝一口柠檬水")

        self.assertFalse(out["ok"])
        self.assertFalse(out["settled"])
        self.assertTrue(out["soft_landed"])
        self.assertEqual(out["classification"], "unsupported_capability")
        self.assertEqual(out["llm_calls"], 0)
        labels = [str(obj.get("label_cn") or "") for obj in self.objects().values()]
        self.assertFalse(any("柠檬" in label for label in labels))
        self.assertFalse(engine.state_path(self.root).exists())


if __name__ == "__main__":
    unittest.main()
