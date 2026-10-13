#!/usr/bin/env python3
"""worldgen 命令行入口。

    python -m components.worldgen generate --seed 929711 --theme coastal_town --out ./worlds/sakura-town [--llm]
    python -m components.worldgen validate  ./worlds/sakura-town
    python -m components.worldgen preview   ./worlds/sakura-town [--instance <实例目录>]
    python -m components.worldgen instance  --package ./worlds/sakura-town --home ./instances/demo
    python -m components.worldgen demo      world|service|restart|full --package ... --home ...
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from . import GENERATOR_VERSION, __doc__


def _cmd_generate(args: argparse.Namespace) -> int:
    from .generator import generate_world, load_theme
    from .providers import glm
    from . import schema

    load_theme(args.theme)  # 校验主题名

    docs = generate_world(seed=args.seed, theme=args.theme, timezone_name=args.timezone)
    content_doc = docs["content.json"]

    if args.llm:
        print("正在调用 GLM 生成内容（可随时 Ctrl+C 中止，回退本地内容）...", file=sys.stderr)
        try:
            content_doc = glm.enrich(docs)
            print(f"GLM 内容生成完成（模型: {content_doc.get('model')}）。", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 - CLI 边界
            if not args.llm_fallback:
                print(f"GLM 内容生成失败: {exc}", file=sys.stderr)
                return 2
            print(f"GLM 内容生成失败，回退本地模板内容: {exc}", file=sys.stderr)
    else:
        print("未指定 --llm：使用本地模板内容（完整可运行）。", file=sys.stderr)
    docs["content.json"] = content_doc

    from .validator import validate

    messages = validate(docs)
    if messages:
        print("生成的世界未通过结构校验（这是生成器缺陷，请报告）：", file=sys.stderr)
        for message in messages:
            print(f"  - {message}", file=sys.stderr)
        return 2

    out = Path(args.out)
    if out.exists() and not args.force:
        print(f"输出目录已存在: {out}（用 --force 覆盖）", file=sys.stderr)
        return 2
    if out.exists() and args.force:
        import shutil

        shutil.rmtree(out)
    schema.write_package(out, docs)

    summary = schema.package_summary(docs)
    print(json.dumps({"status": "generated", "out": str(out), **summary}, ensure_ascii=False, indent=2))
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    from .validator import validate_package_dir

    messages = validate_package_dir(Path(args.package), verify_manifest=not args.skip_manifest)
    if messages:
        print(f"世界包校验失败（{len(messages)} 个问题）：", file=sys.stderr)
        for message in messages:
            print(f"  - {message}", file=sys.stderr)
        return 1
    print(json.dumps({"status": "valid", "package": str(Path(args.package).resolve())}, ensure_ascii=False))
    return 0


def _cmd_preview(args: argparse.Namespace) -> int:
    from .preview import render_preview

    out = render_preview(
        Path(args.package),
        instance_home=Path(args.instance) if args.instance else None,
        output_path=Path(args.output) if args.output else None,
    )
    print(json.dumps({"status": "preview_written", "html": str(out)}, ensure_ascii=False))
    return 0


def _cmd_instance(args: argparse.Namespace) -> int:
    from .instance import init_instance

    receipt = init_instance(args.package, args.home)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


def _cmd_demo(args: argparse.Namespace) -> int:
    from . import demo

    return demo.main(
        [args.phase, "--package", args.package, "--home", args.home, "--date", args.date]
    )


def _cmd_editor_export(args: argparse.Namespace) -> int:
    from .editor_bridge import export_for_editor

    receipt = export_for_editor(
        args.package,
        args.output,
        initial_time=args.time,
        resident_name=args.resident_name,
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


def _cmd_editor_import(args: argparse.Namespace) -> int:
    from .editor_bridge import import_from_editor

    receipt = import_from_editor(
        args.package,
        args.edited,
        out_dir=args.out,
        force=args.force,
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


def _cmd_interior(args: argparse.Namespace) -> int:
    from .interior import apply_interiors_to_package

    locations = args.location or None
    receipt = apply_interiors_to_package(
        args.package,
        locations=locations,
        seed_override=args.seed,
        use_glm=args.glm,
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


def _cmd_enrich(args: argparse.Namespace) -> int:
    from .loader import load
    from .providers import glm
    from . import schema

    package = load(args.package)
    result = glm.enrich(package.snapshot())
    content_doc = result["content"]
    usage = result["usage"]
    docs = package.snapshot()
    docs["content.json"] = content_doc

    # NPC 公开背景同步进 npcs.json（只改描述，身份与日程不动）。
    npc_updates = 0
    for npc in docs["npcs.json"]["npcs"]:
        entry = content_doc["entries"].get(npc["npc_id"]) or {}
        note = entry.get("public_note")
        if note and note != npc["public_description"]:
            npc["public_description"] = note[:160]
            npc_updates += 1

    # 环境事件候选 → ambient.json 追加（确定性 event_type，去重）。
    added_events = 0
    if result["ambient_candidates"]:
        templates = docs["ambient.json"]["templates"]
        existing = {item["event_type"] for item in templates}
        kind_location = {}
        for location in docs["topology.json"]["locations"]:
            kind_location.setdefault(location["kind"], location["location_id"])
        hint_kind = {"shop": "store", "school": "school", "park": "park",
                     "home": "home", "street": "street", "public": "public"}
        import hashlib

        for candidate in result["ambient_candidates"]:
            slug = "local_" + hashlib.sha1(candidate["summary"].encode("utf-8")).hexdigest()[:8]
            if slug in existing:
                continue
            location_id = kind_location.get(hint_kind.get(candidate["kind_hint"], "public"))
            if location_id is None:
                continue
            templates.append({
                "event_type": slug,
                "location_id": location_id,
                "summary": candidate["summary"],
                "start_hour": 0,
                "end_hour": 24,
            })
            existing.add(slug)
            added_events += 1
        docs["ambient.json"]["templates"] = templates[:16]

    from .validator import validate

    messages = validate(docs)
    if messages:
        print("丰富化结果未通过校验（未写盘）：", file=sys.stderr)
        for message in messages:
            print(f"  - {message}", file=sys.stderr)
        return 2
    schema.write_package(args.package, docs)
    log = glm.append_usage_log(args.package, usage, purpose="enrich")
    print(json.dumps({
        "status": "enriched",
        "package": str(args.package),
        "npc_descriptions_updated": npc_updates,
        "ambient_events_added": added_events,
        "usage": usage,
        "usage_log": str(log) if log else None,
    }, ensure_ascii=False, indent=2))
    return 0


def _cmd_batch(args: argparse.Namespace) -> int:
    from .quality import review_with_glm, run_batch

    themes = [item.strip() for item in args.themes.split(",") if item.strip()]
    seeds = [int(item) for item in args.seeds.split(",")] if args.seeds else None
    report = run_batch(
        out_dir=args.out,
        themes=themes,
        seeds=seeds,
        worlds_per_theme=args.worlds_per_theme,
        consistency_days=args.consistency_days,
    )
    if args.review:
        report["glm_review"] = review_with_glm(report)
        report_path = Path(report["report_path"])
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(
        {
            "status": "done",
            "summary": report["summary"],
            "report": report["report_path"],
            "glm_review": bool(report.get("glm_review")),
        },
        ensure_ascii=False, indent=2,
    ))
    return 0


def _cmd_llm_check(args: argparse.Namespace) -> int:
    from .providers import glm

    receipt = glm.llm_check(
        base_url=args.base_url,
        api_key=args.api_key,
        model=args.model,
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m components.worldgen",
        description="NyAiro WorldGen v0.1：程序化世界生成与 rc9 World/Body 运行时适配",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="从种子生成一个世界包")
    gen.add_argument("--seed", type=int, required=True, help="随机种子（0..2^31-1）")
    gen.add_argument("--theme", required=True, choices=("coastal_town", "residential_town", "mountain_town", "rural_farming"))
    gen.add_argument("--timezone", default="Asia/Tokyo", help="IANA 时区（默认 Asia/Tokyo）")
    gen.add_argument("--out", required=True, help="输出目录")
    gen.add_argument("--llm", action="store_true", help="调用 GLM 生成文本内容（需 WORLDGEN_LLM_API_KEY）")
    gen.add_argument(
        "--llm-no-fallback",
        dest="llm_fallback",
        action="store_false",
        help="GLM 失败时不回退本地内容（默认回退）",
    )
    gen.add_argument("--force", action="store_true", help="覆盖已存在的输出目录")
    gen.set_defaults(func=_cmd_generate)

    val = sub.add_parser("validate", help="校验一个世界包")
    val.add_argument("package", help="世界包目录")
    val.add_argument("--skip-manifest", action="store_true", help="跳过 SHA256 清单核对")
    val.set_defaults(func=_cmd_validate)

    prev = sub.add_parser("preview", help="生成本地 HTML/SVG 地图预览")
    prev.add_argument("package", help="世界包目录")
    prev.add_argument("--instance", help="可选：实例目录（显示当前位置）")
    prev.add_argument("--output", help="输出 HTML 路径（默认 <包目录>/preview.html）")
    prev.set_defaults(func=_cmd_preview)

    inst = sub.add_parser("instance", help="为世界包初始化一个隔离 World/Body 实例")
    inst.add_argument("--package", required=True, help="世界包目录")
    inst.add_argument("--home", required=True, help="新实例目录（必须不存在）")
    inst.set_defaults(func=_cmd_instance)

    dem = sub.add_parser("demo", help="隔离 World/Body 运行演示（需 POSIX/WSL）")
    dem.add_argument("phase", choices=("world", "service", "restart", "full"))
    dem.add_argument("--package", required=True)
    dem.add_argument("--home", required=True)
    dem.add_argument("--date", default="2026-10-14")
    dem.set_defaults(func=_cmd_demo)

    ed = sub.add_parser(
        "editor-export",
        help="把世界包转换为世界编辑器（components/world-editor）可导入的 v3 场景",
    )
    ed.add_argument("package", help="worldgen 世界包目录")
    ed.add_argument("--output", help="输出 JSON 路径（默认 <包目录>/editor-world.json）")
    ed.add_argument("--time", default="2026-10-14T08:00", help="编辑器试运行的起始虚拟时间")
    ed.add_argument("--resident-name", default="试住居民", help="编辑器测试居民名称")
    ed.set_defaults(func=_cmd_editor_export)

    im = sub.add_parser(
        "editor-import",
        help="把编辑器修改回写为新的完整世界包（原始包 + 编辑器修改 = 新包）",
    )
    im.add_argument("package", help="原始 worldgen 世界包目录（不会被修改）")
    im.add_argument("edited", help="编辑器导出的 v3 JSON 路径")
    im.add_argument("--out", help="新世界包目录（默认 <原包名>-edited，自动跳过已存在目录）")
    im.add_argument("--force", action="store_true", help="覆盖已存在的输出目录")
    im.set_defaults(func=_cmd_editor_import)

    it = sub.add_parser(
        "interior",
        help="自动生成建筑室内空间与家具（写入 scene.json + objects.json）",
    )
    it.add_argument("package", help="worldgen 世界包目录")
    it.add_argument(
        "--location",
        action="append",
        help="只生成指定地点（可多次）；缺省生成全部室内建筑",
    )
    it.add_argument("--seed", type=int, help="覆盖建筑种子（默认由世界种子+地点 ID 派生）")
    it.add_argument(
        "--glm",
        action="store_true",
        help="先请求 GLM 语义设计（需 WORLDGEN_LLM_API_KEY；失败回退程序化）",
    )
    it.set_defaults(func=_cmd_interior)

    en = sub.add_parser(
        "enrich",
        help="用 GLM 丰富世界内容（地点介绍/NPC 公开背景/环境事件候选，需 API Key）",
    )
    en.add_argument("package", help="worldgen 世界包目录")
    en.set_defaults(func=_cmd_enrich)

    bt = sub.add_parser(
        "batch",
        help="批量生成与质量分析（多主题 × 多种子，含自动室内与一致性模拟）",
    )
    bt.add_argument("--out", required=True, help="实验输出目录")
    bt.add_argument("--themes", default=",".join(("coastal_town", "residential_town", "mountain_town", "rural_farming")), help="逗号分隔的主题")
    bt.add_argument("--seeds", help="逗号分隔的种子；缺省 500001 起连续 10 个")
    bt.add_argument("--worlds-per-theme", type=int, default=10)
    bt.add_argument("--consistency-days", type=int, default=7, help="长期一致性模拟天数")
    bt.add_argument("--review", action="store_true", help="用 GLM 对合格世界抽样评审（需 API Key）")
    bt.set_defaults(func=_cmd_batch)

    lc = sub.add_parser(
        "llm-check",
        help="GLM API 真实连通性自检（最小调用 + 用量记录）",
    )
    lc.add_argument("--base-url", help="覆盖 WORLDGEN_LLM_BASE_URL")
    lc.add_argument("--api-key", help="覆盖 WORLDGEN_LLM_API_KEY")
    lc.add_argument("--model", help="覆盖 WORLDGEN_LLM_MODEL")
    lc.set_defaults(func=_cmd_llm_check)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:  # noqa: BLE001 - CLI 边界统一报错
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
