"""一次性生成改前 v1 兼容 golden（不随测试运行；生成后即固定）。

用法（WSL）：
  PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B \
      tests/fixtures/_gen_ambient_v1_golden.py

输出 tests/fixtures/world_ambient_v1_legacy_golden.json：用改前代码对
daily_neighborhood_v1 的 v1 包在 2026-10-24..2026-10-27 的 materialize 结果。
"""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "examples" / "worlds" / "daily_neighborhood_v1"
for entry in (
    ROOT,
    ROOT / "components",
    ROOT / "components" / "world" / "scripts",
):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import world_ambient_events as wae  # noqa: E402
import world_runtime_data as wrd  # noqa: E402

environment = {"CHIYO_WORLD_PACKAGE": str(PACKAGE)}
wrd.reset_cache()
start = date(2026, 10, 24)
golden = {
    "package": "daily_neighborhood_v1",
    "schema_version": "worldgen.ambient.v1",
    "days": {},
}
for offset in range(4):
    day = start + timedelta(days=offset)
    golden["days"][day.isoformat()] = wae.materialize_ambient_events(
        day, environment=environment
    )
out = ROOT / "tests" / "fixtures" / "world_ambient_v1_legacy_golden.json"
out.write_text(
    json.dumps(golden, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
print(out)
