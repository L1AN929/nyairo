#!/usr/bin/env python3
"""Diagnose and repair "model only says it went out" across ALL messaging & gateway platforms.

Why it happens:
1. `platform_toolsets.<platform>: []` or missing `chiyo_world` in `<profile>/config.yaml`.
   Hermes treats empty or unlisted toolsets as an explicit exclusion on that platform,
   so the plugin toolset `chiyo_world` (providing `world_move`) is never exposed.
2. The model needs an explicit behavioral contract: when the user asks to go somewhere,
   it is an ACTION that must invoke `world_move`, not a polite conversation response.

This script ensures `chiyo_world` is universally enabled across all configured gateway platforms
(CLI, Web, Weixin, Telegram, Discord, Feishu, etc.) without manual per-platform fragmentation.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_ROOT = "/srv/nyairo-v0.3.0"
TOOLSET = "chiyo_world"
TOOL = "world_move"

GUIDANCE = (
    "【移动与出门：world_move 工具】\n"
    "如果本次会话的可用工具里有 world_move，那么用户让你出门、走过去、去街上/便利店/公园/学校/"
    "某个地方时，你要调用 world_move（location 传地点名或 location_id）再回话；"
    "只口头答应、只描述画面不会产生任何位移。\n"
    "- 真实步行计时开启时，一次调用只出发当前一段：到达中转点后要再调用一次最终目的地才会继续走。\n"
    "- 位置、在途状态和剩余时间以工具返回为准，不要自行编造到达或声称已经在路上。\n"
    "如果本次会话没有 world_move 工具，就如实说现在不能移动，不要假装已经出门。"
)


def resolve_paths(root: Path | str | None, profile: Path | str | None):
    root = Path(root or os.environ.get("NYAIRO_ROOT", DEFAULT_ROOT)).resolve()
    if profile:
        prof = Path(profile).resolve()
    else:
        prof = Path(os.environ.get("HERMES_HOME", root / "profile")).resolve()
    return root, prof


def main():
    parser = argparse.ArgumentParser(description="Universal gateway world_move tool repair")
    parser.add_argument("--root", default=None, help="NyAiro installation root")
    parser.add_argument("--profile", default=None, help="Hermes profile directory")
    parser.add_argument("--apply", action="store_true", help="Write changes to config.yaml")
    parser.add_argument("--platform", default="all", help="Target platform or 'all'")
    args = parser.parse_args()

    root, profile = resolve_paths(args.root, args.profile)
    config_path = profile / "config.yaml"
    if not config_path.exists():
        print(f"Error: {config_path} not found")
        sys.exit(1)

    try:
        from ruamel.yaml import YAML
        yaml = YAML()
        yaml.preserve_quotes = True
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.load(f) or {}
    except Exception as e:
        import yaml
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

    platform_toolsets = cfg.setdefault("platform_toolsets", {})

    # Discover all platforms present in config
    platforms = set(cfg.get("platforms", {}).keys())
    if args.platform == "all":
        target_platforms = platforms.union(platform_toolsets.keys()).union({"weixin", "cli", "telegram", "web"})
    else:
        target_platforms = {args.platform}

    changed = False
    print(f"Targeting platforms: {sorted(target_platforms)}")
    for p in sorted(target_platforms):
        current_list = platform_toolsets.get(p)
        if current_list is None:
            current_list = []
        elif not isinstance(current_list, list):
            current_list = list(current_list)

        if TOOLSET not in current_list:
            current_list.append(TOOLSET)
            platform_toolsets[p] = current_list
            changed = True
            print(f"  + Added '{TOOLSET}' to platform '{p}': {current_list}")
        else:
            print(f"  = Platform '{p}' already includes '{TOOLSET}'")

    if not changed:
        print("\nAll target platforms already have chiyo_world enabled. Nothing to do.")
        return

    if not args.apply:
        print("\nDry-run complete. Run with --apply to commit changes.")
        return

    # Backup & write
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = profile / f"config.yaml-backup-{ts}"
    shutil.copy2(config_path, backup)
    print(f"\nBackup saved to {backup}")

    try:
        from ruamel.yaml import YAML
        yaml = YAML()
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f)
    except Exception:
        import yaml
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True)

    print(f"Universal configuration updated: {config_path}")


if __name__ == "__main__":
    main()
