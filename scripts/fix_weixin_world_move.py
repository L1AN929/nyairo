#!/usr/bin/env python3
"""Diagnose and repair "the model only *says* it went out" on a NyAiro gateway.

Why it happens (two independent causes, both must be fixed):

1. ``platform_toolsets.<platform>: []`` in ``<profile>/config.yaml`` is an
   EXPLICIT empty opt-in, not a fallback: ``hermes_cli/tools_config.py::
   _get_platform_tools`` honours it and the platform starts with zero
   toolsets, so the plugin toolset ``chiyo_world`` (tool ``world_move``,
   registered by ``plugins/chiyo/__init__.py``) is never handed to the model.
   An explicit list also suppresses the ``agent.disabled_toolsets``-style
   implicit plugin defaults (``_enabled_plugin_toolsets``), which is why an
   empty list is worse than no list at all.
2. Nothing in the system prompt tells the model that "go for a walk" is an
   action. Fixing only (1) leaves a model that *can* call ``world_move`` but
   still prefers to answer politely.

This script repairs (1) in place (read-only unless ``--apply``) and reports on
(2). The durable fix for (2) ships in the source tree: the chiyo plugin now
registers a cache-safe system prompt section (``ctx.register_system_prompt_section``)
whose guidance is also available here via ``--soul-hint`` for a profile whose
plugin code has not been updated yet.

Usage (run it with the hermes venv python - PyYAML/ruamel.yaml live there):

    P=/srv/nyairo-v0.3.0/vendor/hermes/.venv/bin/python
    S=/srv/nyairo-v0.3.0/scripts/fix_weixin_world_move.py

    sudo -u <owner> $P $S                      # report only (default)
    sudo -u <owner> $P $S --apply              # back up config.yaml, then enable chiyo_world
    sudo -u <owner> $P $S --apply --soul-hint  # also append the prompt hint to SOUL.md

Nothing else is touched: no service restart, no database, no memory files.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_ROOT = "/srv/nyairo-v0.3.0"
DEFAULT_PLATFORM = "weixin"
TOOLSET = "chiyo_world"
TOOL = "world_move"

#: Mirrors ``_WORLD_MOVE_GUIDANCE`` in plugins/chiyo/__init__.py (keep in sync).
GUIDANCE = (
    "【移动与出门：world_move 工具】\n"
    "如果本次会话的可用工具里有 world_move，那么用户让你出门、走过去、去街上/便利店/公园/学校/"
    "某个地方时，你要调用 world_move（location 传地点名或 location_id）再回话；"
    "只口头答应、只描述画面不会产生任何位移。\n"
    "- 真实步行计时开启时，一次调用只出发当前一段：到达中转点后要再调用一次最终目的地才会继续走。\n"
    "- 位置、在途状态和剩余时间以工具返回为准，不要自行编造到达或声称已经在路上。\n"
    "如果本次会话没有 world_move 工具，就如实说现在不能移动，不要假装已经出门。"
)
SOUL_START = "<!-- nyairo:world-move-guidance:start -->"
SOUL_END = "<!-- nyairo:world-move-guidance:end -->"


# --------------------------------------------------------------------------- #
# YAML I/O: ruamel round-trip keeps the operator's comments; PyYAML is the
# lossy fallback and says so. Both are hermes dependencies.
# --------------------------------------------------------------------------- #
def read_config(path: Path):
    """Return (data, writer, writer_kind). ``writer(document) -> text``."""
    text = path.read_text(encoding="utf-8")
    try:
        from ruamel.yaml import YAML
        import io

        yaml = YAML()
        yaml.preserve_quotes = True
        data = yaml.load(text)

        def write_ruamel(document) -> str:
            buf = io.StringIO()
            yaml.dump(document, buf)
            return buf.getvalue()

        return data, write_ruamel, "ruamel.yaml (comments preserved)"
    except ImportError:
        pass
    import yaml as pyyaml

    data = pyyaml.safe_load(text) or {}

    def write_pyyaml(document) -> str:
        return pyyaml.safe_dump(document, allow_unicode=True, sort_keys=False,
                                default_flow_style=False)

    return data, write_pyyaml, "PyYAML (comments/order may be reformatted)"


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _section(config: dict, key: str) -> dict:
    value = config.get(key)
    return value if isinstance(value, dict) else {}


def _as_name_list(value) -> list:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str) and value.strip():
        raw = value.strip()
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
            except ValueError:
                return [raw]
            return [str(item) for item in parsed] if isinstance(parsed, list) else [raw]
        return [raw]
    return []


# --------------------------------------------------------------------------- #
# Diagnosis
# --------------------------------------------------------------------------- #
def plugin_source(root: Path) -> tuple[Path, str]:
    path = root / "plugins" / "chiyo" / "__init__.py"
    try:
        return path, path.read_text(encoding="utf-8")
    except OSError:
        return path, ""


def resolve_with_hermes(root: Path, profile: Path, config: dict, platform: str) -> dict:
    """Run the REAL ``_get_platform_tools`` with chiyo_world assumed registered.

    Plugin discovery is deliberately stubbed out: loading the chiyo plugin in a
    second process would open the owner's memory/life/world services just to
    answer a config question.
    """
    result = {"ok": False, "enabled": None, "error": None}
    sys.path.insert(0, str(root / "vendor" / "hermes"))
    os.environ.setdefault("HERMES_HOME", str(profile))
    try:
        # Mark discovery as finished BEFORE importing tools_config: otherwise that import
        # loads every plugin, and loading plugins/chiyo a second time would open the
        # owner's memory/life/world services just to answer a config question.
        from hermes_cli import plugins as hermes_plugins
        hermes_plugins.get_plugin_manager()._discovered = True
    except Exception:  # noqa: BLE001 - best effort; the resolve below reports its own failures
        pass
    try:
        from hermes_cli import tools_config

        original = tools_config._get_plugin_toolset_keys
        tools_config._get_plugin_toolset_keys = lambda: {TOOLSET}
        try:
            enabled = sorted(tools_config._get_platform_tools(config, platform))
        finally:
            tools_config._get_plugin_toolset_keys = original
    except Exception as exc:  # noqa: BLE001 - diagnostic only, never fatal
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result
    result.update(ok=True, enabled=enabled)
    return result


def chiyo_settings(profile: Path) -> dict:
    path = profile / "chiyo" / "config.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def report(root: Path, profile: Path, config_path: Path, platform: str,
           config: dict, writer_kind: str, plugin_path: Path, plugin_text: str) -> None:
    toolsets = _section(config, "platform_toolsets")
    raw = toolsets.get(platform, "<unset>") if platform in toolsets else "<unset>"
    known_plugin = _section(config, "known_plugin_toolsets").get(platform, "<unset>")
    known_builtin = _section(config, "known_builtin_toolsets").get(platform, "<unset>")
    disabled = _section(config, "agent").get("disabled_toolsets")
    settings = chiyo_settings(profile)

    print("== 1. files ==")
    print(f"root            : {root}")
    print(f"profile (HERMES_HOME): {profile}")
    print(f"config.yaml     : {config_path}")
    print(f"yaml writer     : {writer_kind}")
    print(f"chiyo plugin    : {plugin_path} ({len(plugin_text)} chars)")

    print("\n== 2. chiyo_world registration (static) ==")
    registers = f"toolset='{TOOLSET}'" in plugin_text and f"name='{TOOL}'" in plugin_text
    prompt_section = "register_system_prompt_section" in plugin_text
    print(f"register_tool({TOOL!r}, toolset={TOOLSET!r}) present : {registers}")
    print(f"system prompt section present (prompt hint)   : {prompt_section}")
    if not registers:
        print("  !! the plugin does not register the tool in this tree - fix that first;")
        print("     the gateway log would also show 'chiyo.world_move.registration.unavailable'.")

    print("\n== 3. config state ==")
    print(f"platform_toolsets.{platform}      : {raw!r}")
    print(f"known_plugin_toolsets.{platform}  : {known_plugin!r}")
    print(f"known_builtin_toolsets.{platform} : {known_builtin!r}")
    print(f"agent.disabled_toolsets           : {disabled!r}")
    print(f"context.engine                    : {_section(config, 'context').get('engine')!r}")
    print(f"chiyo world_body_socket (config.json): {settings.get('world_body_socket')!r}")
    print(f"WORLD_BODY_SOCKET (env)           : {os.environ.get('WORLD_BODY_SOCKET')!r}")
    print(f"CHIYO_WORLD_TRAVEL_TIME_ENABLED   : {os.environ.get('CHIYO_WORLD_TRAVEL_TIME_ENABLED')!r}")

    resolved = resolve_with_hermes(root, profile, config, platform)
    print("\n== 4. what the gateway would enable for this platform ==")
    if resolved["ok"]:
        enabled = resolved["enabled"] or []
        print(f"_get_platform_tools({platform!r}) -> {enabled}")
        print(f"{TOOLSET!r} enabled : {TOOLSET in enabled}")
        print(f"{TOOL!r} reachable  : {'YES' if TOOLSET in enabled else 'NO'}")
        if not enabled:
            print("  (empty set = the model sees no tools at all on this platform)")
    else:
        print(f"could not resolve in-process: {resolved['error']}")
        print("  estimate: explicit list -> "
              f"{TOOLSET in _as_name_list(raw)}   disabled_toolsets -> "
              f"{TOOLSET in _as_name_list(disabled)}")

    print("\n== 5. verdict ==")
    names = _as_name_list(raw)
    if TOOLSET in resolve_with_hermes(root, profile, config, platform).get("enabled", []) or TOOLSET in names:
        print(f"{TOOLSET} is enabled for {platform!r} - the toolset is not the blocker.")
    else:
        print(f"{TOOLSET} is NOT enabled for {platform!r}; the model cannot call {TOOL}!.")
        if isinstance(raw, list) and not raw:
            print("  cause A: platform_toolsets.%s is an explicit EMPTY list"
                  " (hermes_cli/toolset_validation.py line 82-84 calls this out)."
                  % platform)
        elif TOOLSET in _as_name_list(known_plugin) and TOOLSET not in names:
            print("  cause A: the toolset is recorded as 'known' for this platform and absent"
                  " from the saved list, so _enabled_plugin_toolsets excludes it"
                  " (tools_config.py:534-541).")
        if TOOLSET in _as_name_list(disabled):
            print("  cause B: agent.disabled_toolsets lists it; it is applied LAST"
                  " (tools_config.py:591-597) and overrides everything.")
    if not prompt_section:
        print("  cause C: no system prompt hint about world_move, so the model answers in prose"
              " even when the tool is available.")


# --------------------------------------------------------------------------- #
# Repair
# --------------------------------------------------------------------------- #
def apply_config(config, platform: str) -> list[str]:
    """Enable chiyo_world for *platform* in place; return human-readable changes."""
    changes: list[str] = []

    toolsets = config.get("platform_toolsets")
    if not isinstance(toolsets, dict):
        toolsets = {}
        config["platform_toolsets"] = toolsets
        changes.append("created section platform_toolsets")
    current = toolsets.get(platform)
    if isinstance(current, list):
        if TOOLSET not in [str(item) for item in current]:
            current.append(TOOLSET)
            changes.append(f"platform_toolsets.{platform}: appended {TOOLSET!r}"
                           f" -> {[str(item) for item in current]}")
        else:
            changes.append(f"platform_toolsets.{platform}: already lists {TOOLSET!r}")
    else:
        toolsets[platform] = [TOOLSET]
        changes.append(f"platform_toolsets.{platform}: replaced {current!r} with [{TOOLSET!r}]")

    known = config.get("known_plugin_toolsets")
    if isinstance(known, dict) and platform in known:
        values = _as_name_list(known[platform])
        if TOOLSET not in values:
            known[platform] = values + [TOOLSET]
            changes.append(f"known_plugin_toolsets.{platform}: appended {TOOLSET!r}")
        else:
            changes.append(f"known_plugin_toolsets.{platform}: already records {TOOLSET!r}")

    agent = config.get("agent")
    if isinstance(agent, dict):
        disabled = agent.get("disabled_toolsets")
        names = _as_name_list(disabled)
        if TOOLSET in names:
            remaining = [name for name in names if name != TOOLSET]
            agent["disabled_toolsets"] = remaining if remaining else []
            if not remaining:
                agent.pop("disabled_toolsets", None)
            changes.append(f"agent.disabled_toolsets: removed {TOOLSET!r} -> {remaining}")
    return changes


def soul_hint(profile: Path, apply: bool) -> list[str]:
    path = profile / "SOUL.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return [f"{path}: missing - skipped (no persona file to extend)"]
    if SOUL_START in text:
        return [f"{path}: guidance marker already present - nothing to do"]
    block = f"\n\n{SOUL_START}\n{GUIDANCE}\n{SOUL_END}\n"
    if not apply:
        return [f"{path}: would append the world_move guidance block"]
    shutil.copy2(path, path.with_name(f"SOUL.md-backup-{stamp()}"))
    path.write_text(text.rstrip("\n") + block, encoding="utf-8")
    return [f"{path}: appended the world_move guidance block (backup kept)"]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=os.environ.get("NYAIRO_ROOT", DEFAULT_ROOT),
                        help=f"program directory (default {DEFAULT_ROOT})")
    parser.add_argument("--profile", default=None,
                        help="personal profile dir / HERMES_HOME (default <root>/profile)")
    parser.add_argument("--config", default=None, help="explicit config.yaml path")
    parser.add_argument("--platform", default=DEFAULT_PLATFORM,
                        help=f"gateway platform key (default {DEFAULT_PLATFORM})")
    parser.add_argument("--apply", action="store_true", help="write the repaired config.yaml")
    parser.add_argument("--soul-hint", action="store_true",
                        help="also append the world_move guidance to <profile>/SOUL.md")
    args = parser.parse_args(argv)

    root = Path(args.root).expanduser().resolve()
    profile = Path(args.profile).expanduser().resolve() if args.profile else root / "profile"
    config_path = Path(args.config).expanduser().resolve() if args.config else profile / "config.yaml"

    if not config_path.exists():
        print(f"config not found: {config_path}", file=sys.stderr)
        return 2
    try:
        config, writer, writer_kind = read_config(config_path)
    except ImportError:
        print("PyYAML/ruamel.yaml missing - run this with the hermes venv python:\n"
              f"  {root}/vendor/hermes/.venv/bin/python {Path(__file__).name}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - a broken YAML must not be "fixed" blindly
        print(f"could not parse {config_path}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    if not isinstance(config, dict):
        print(f"{config_path} is not a YAML mapping - refusing to touch it", file=sys.stderr)
        return 2

    plugin_path, plugin_text = plugin_source(root)
    report(root, profile, config_path, args.platform, config, writer_kind, plugin_path, plugin_text)

    print("\n== 6. plan ==")
    before = writer(config)
    changes = apply_config(config, args.platform)
    after = writer(config)
    for line in changes:
        print(f"  - {line}")
    for line in soul_hint(profile, apply=False) if args.soul_hint else []:
        print(f"  - {line}")

    if not args.apply:
        if before != after:
            print("\n--- config.yaml diff (dry run, nothing written) ---")
            print("".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                               "config.yaml", "config.yaml (planned)")))
        print("\nDry run only. Re-run with --apply to write (a timestamped backup is kept).")
        return 0

    backup = config_path.with_name(f"config.yaml-backup-{stamp()}")
    shutil.copy2(config_path, backup)
    tmp = config_path.with_name(config_path.name + ".nyairo-tmp")
    tmp.write_text(after, encoding="utf-8")
    try:
        shutil.copymode(config_path, tmp)
    except OSError:
        pass
    os.replace(tmp, config_path)
    print(f"\nbackup : {backup}")
    print(f"written: {config_path}")
    print(f"rollback: cp -a {backup} {config_path}")

    if args.soul_hint:
        for line in soul_hint(profile, apply=True):
            print(f"  - {line}")

    check = resolve_with_hermes(root, profile, config, args.platform)
    if check["ok"]:
        enabled = check["enabled"] or []
        print(f"\nre-resolved {args.platform!r} -> {enabled}")
        print(f"{TOOLSET!r} enabled: {TOOLSET in enabled}")
        if TOOLSET not in enabled:
            print("still not enabled - inspect section 5 above (disabled_toolsets is applied last)")
            return 1
    print("\nnext: the gateway re-reads config.yaml for every turn, so the next message from "
          "this chat already sees the tool. A NEW session (or /new, /reset) is needed before the "
          "prompt hint takes effect; restart the gateway if you deployed the updated plugin file "
          "from this tree.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
