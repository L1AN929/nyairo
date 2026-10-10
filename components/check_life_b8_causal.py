#!/usr/bin/env python3
"""B8-5: the causal experiments' entry points, and the record they must keep agreeing with.

The three experiments need a real Life Supply owner on a Unix socket, so they are run by
``components/life/tests/v02/run_b8_causal.sh a|b`` inside the isolated environment -- not from
``scripts/test.py``.  What *is* checked here on every suite run is the part that can be checked
anywhere:

* the entry points still exist (two harnesses, one driver, one record);
* the trials and scenarios the harnesses declare are still the ones the record describes;
* the record still states the outcomes it claims, and still states the layer boundaries and the
  caveats that keep a single text difference from being read as a durable personality.

A later edit that drops a trial, renames a scenario, or lets the document drift fails here
instead of silently leaving the record describing an experiment that no longer runs.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent            # components/
REPO = ROOT.parent
V02 = ROOT / "life" / "tests" / "v02"
DOC = REPO / "docs" / "engineering" / "v02-b8-causal-experiments.md"

EXP_A = V02 / "b8_causal_exp_a.py"
EXP_B = V02 / "b8_causal_exp_b.py"
DRIVER = V02 / "run_b8_causal.sh"

FAILURES: list[str] = []


def check(condition: bool, label: str, detail: object = "") -> None:
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label} :: {detail}")
        FAILURES.append(f"{label} :: {detail}")


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    print("[1] the entry points exist")
    for path in (EXP_A, EXP_B, DRIVER, DOC):
        check(path.is_file(), f"{path.relative_to(REPO)} exists", path)

    if FAILURES:
        print(f"\nB8_CAUSAL_CHECK: FAIL ({len(FAILURES)} checks)")
        for item in FAILURES:
            print("  -", item)
        return 1

    print("[2] experiment A still declares three two-group trials")
    exp_a = _load("b8_causal_exp_a_doc_check", EXP_A)
    trials = exp_a.TRIALS
    check(len(trials) == 3, "three trials, so one lucky sample cannot carry the result",
          len(trials))
    check([t["trial"] for t in trials] == ["T1", "T2", "T3"],
          "the trials are T1/T2/T3", [t["trial"] for t in trials])
    for trial in trials:
        name = trial["trial"]
        for label in ("A", "B"):
            spec = trial[label]
            check(bool(spec["contents"]) and spec["marker"] in spec["contents"][-1],
                  f"{name}{label} carries its own marker in its own text", spec["marker"])
        check(len(trial["A"]["contents"]) >= 2,
              f"{name}A has a deeper history than {name}B",
              len(trial["A"]["contents"]))
        check(len(trial["B"]["contents"]) == 1, f"{name}B really has head V1",
              len(trial["B"]["contents"]))
        check(trial["A"]["contents"] != trial["B"]["contents"],
              f"{name} the two works really differ")

    print("[3] experiment B still declares the three real outcome scenarios")
    exp_b = _load("b8_causal_exp_b_doc_check", EXP_B)
    scenarios = exp_b.SCENARIOS
    check([s["key"] for s in scenarios] == ["S1", "S2", "S3"],
          "the scenarios are S1/S2/S3", [s["key"] for s in scenarios])
    check([s["produce"] for s in scenarios] == ["progress", "conflict", "abandon"],
          "one real success, one real conflict and one real abandon",
          [s["produce"] for s in scenarios])
    check([s["handoff_kind"] for s in scenarios]
          == ["SIGNIFICANT_PROGRESS", "CONFLICT", "ABANDONED"],
          "each maps to a handoff kind AG-2 really has",
          [s["handoff_kind"] for s in scenarios])
    check(exp_b.M0_SUBJECT == "chiyo",
          "the M0 memory owner the record names is the one the harness asserts",
          exp_b.M0_SUBJECT)

    print("[4] the driver still exposes both experiments")
    driver = DRIVER.read_text(encoding="utf-8")
    for token in ("b8_causal_exp_a.py", "b8_causal_exp_b.py", "a)", "b)"):
        check(token in driver, f"the driver still mentions {token!r}")

    print("[5] the record still states what it claims")
    doc = DOC.read_text(encoding="utf-8")
    for token in (
        "EXPERIMENT_A: PASS",
        "EXPERIMENT_B: PASS",
        "B8_CHECK: PASS",
        "01a1266e-7b69-7990-8f29-eef7ff2c0723",
        "01a1266e-7d22-75df-bc3f-26203e464df3",
        "01a1266e-7f1b-76db-a80a-62e046c19f7a",
        "lexp:1b65f966aa0db626",
        "lexp:a63f57aaf76cf339",
        "lexp:fb0dd1149d2a31fb",
        "信息可达",
        "行为准备变化",
        "实际后果变化",
        "NOT_RUN",
        "确定性测试不能证明真实模型必然作出不同选择",
        "单次文本不同不证明形成长期人格",
        "分别归因",
        "NO_ACTION",
        "没有做过 24 小时或 7 天",
        "没有消耗任何付费额度",
    ):
        check(token in doc, f"the record still says {token!r}")

    print()
    if FAILURES:
        print(f"B8_CAUSAL_CHECK: FAIL ({len(FAILURES)} checks)")
        for item in FAILURES:
            print("  -", item)
        return 1
    print("B8_CAUSAL_CHECK: PASS")
    print("  note: the experiments themselves need the isolated owner; run "
          "components/life/tests/v02/run_b8_causal.sh a|b there.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
