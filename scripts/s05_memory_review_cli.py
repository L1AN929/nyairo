#!/usr/bin/env python3
"""S05 用户读口：一条经历交接的身份、来源、准入状态与 Memory 当前读面。

只读、无正文。默认列出 state root 里全部交接的有限身份；``--handoff`` 给出单条的
完整复核（源 refs、投递 receipt、当前召回、分层）。

    python scripts/s05_memory_review_cli.py --instance /path/to/instance
    python scripts/s05_memory_review_cli.py --state-root /path/state --handoff lexp:result:...

Memory 绑定优先用 ``--memory-db/--controls-db/--owner``；否则从 ``--instance`` 的
环境或进程环境里读既有三个名字（``NYA02_MEMORY_*`` / ``CHIYO_LIFE_MEMORY_*``）。
不建库、不迁移、不写控制账本、不输出正文或描述。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"), str(ROOT / "scripts"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "native"),
              str(ROOT / "components" / "native" / "src"),
              str(ROOT / "components" / "native" / "memory_runtime_v1")):
    if entry not in sys.path:
        sys.path.insert(0, entry)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="S05 Memory 经历交接只读复核")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--state-root", help="Life state root（AG-2 状态所在目录）")
    source.add_argument("--instance", help="Life 实例根：state root 与 Memory 绑定从它推导")
    parser.add_argument("--handoff", help="只复核这一条 handoff id")
    parser.add_argument("--limit", type=int, default=20, help="列出的 handoff 上限（默认 20）")
    parser.add_argument("--memory-db", help="显式 Memory evidence db（只读）")
    parser.add_argument("--controls-db", help="显式 Memory control ledger（只读）")
    parser.add_argument("--owner", help="显式 Memory owner")
    return parser


def _environment(args) -> dict[str, str]:
    env = dict(os.environ)
    if args.instance:
        import one_day_life as odl

        env.update(odl.instance_environment(Path(args.instance)))
    if args.memory_db:
        env["NYA02_MEMORY_DB"] = str(args.memory_db)
    if args.controls_db:
        env["NYA02_MEMORY_CONTROLS_DB"] = str(args.controls_db)
    if args.owner:
        env["NYA02_MEMORY_SUBJECT"] = str(args.owner)
    return env


def _service(args, env):
    import life_memory_service as lms

    if not args.memory_db:
        return lms.memory_service_from_env(env)
    from life_experience_port import LifeExperiencePort

    owner = str(args.owner or env.get("NYA02_MEMORY_SUBJECT") or lms.MEMORY_DEFAULT_OWNER)
    controls = str(args.controls_db or args.memory_db)
    port = LifeExperiencePort(args.memory_db, controls_db=controls, subject=owner,
                              authority_db="")
    return lms.LifeMemoryService(port, owner=owner, database=str(args.memory_db),
                                 controls_database=controls, source="cli")


def _state_root(args) -> Path:
    if args.state_root:
        return Path(args.state_root)
    import one_day_life as odl

    return Path(odl.life_state_root(Path(args.instance)))


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    import life_memory_review as review

    try:
        state = review.open_state(_state_root(args))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": "STATE_UNREADABLE",
                          "detail": "%s: %s" % (type(exc).__name__, exc)},
                         ensure_ascii=False))
        return 1
    service = _service(args, _environment(args))
    if args.handoff:
        try:
            payload = review.review_handoff(state, args.handoff, service)
        except KeyError:
            print(json.dumps({"ok": False, "error": "HANDOFF_NOT_PERSISTED",
                              "handoff_id": args.handoff}, ensure_ascii=False))
            return 1
        print(json.dumps({"ok": True, "review": payload}, ensure_ascii=False, indent=1))
        return 0
    rows = review.list_handoffs(state, limit=int(args.limit))
    print(json.dumps({"ok": True, "binding": service.describe(), "handoffs": rows,
                      "count": len(rows)}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
