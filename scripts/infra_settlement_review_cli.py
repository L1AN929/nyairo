#!/usr/bin/env python3
"""INFRA F155-160 用户读口：一次动作的三轴 + 分域后果状态（只读）。

    python scripts/infra_settlement_review_cli.py --instance <life-instance> --action-id actn:...
    python scripts/infra_settlement_review_cli.py --state-root <state> --limit 20
    python scripts/infra_settlement_review_cli.py --instance <i> --action-id actn:... --format text

输出只含不透明 ref、状态与计数，不含正文/prompt/凭据。没有绑定 Memory 时，
Memory 读面显示 ``UNVERIFIED``（附原因），不显示“没有记录”。

发现未回收的 WAL / commit intent 时直接返回 ``COMMIT_WAL_PENDING_RECOVERY``：
重放是写操作，交给实例自己的 runtime。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "native"),
              str(ROOT / "components" / "native" / "src"),
              str(ROOT / "components" / "native" / "memory_runtime_v1")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import infra_settlement_review as review  # noqa: E402


def _state_root(args: argparse.Namespace) -> Path:
    if args.state_root:
        return Path(args.state_root)
    return Path(args.instance) / "life" / "state"


def _memory_service(args: argparse.Namespace):
    if args.instance:
        try:
            import one_day_life as odl

            env = dict(os.environ)
            env.update(odl.instance_environment(Path(args.instance)))
        except Exception:
            env = dict(os.environ)
    else:
        env = dict(os.environ)
    if args.memory_db:
        env["NYA02_MEMORY_DB"] = str(args.memory_db)
    if args.controls_db:
        env["NYA02_MEMORY_CONTROLS_DB"] = str(args.controls_db)
    if args.owner:
        env["NYA02_MEMORY_SUBJECT"] = str(args.owner)
    if not env.get("NYA02_MEMORY_DB") and not env.get("CHIYO_LIFE_MEMORY_DB"):
        return None
    from life_memory_service import memory_service_from_env

    return memory_service_from_env(env)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="只读复核：动作结果 / 活动生命周期 / 后果域（三轴分开）")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--instance", help="Life 实例根（state root 为 <instance>/life/state）")
    source.add_argument("--state-root", help="直接给出 state root")
    parser.add_argument("--action-id", default=None, help="只看这一条动作（actn:...）")
    parser.add_argument("--limit", type=int, default=20,
                        help="列出最近多少条动作（<=0 表示全部）；默认 20")
    parser.add_argument("--format", choices=("json", "text"), default="json")
    parser.add_argument("--memory-db", help="显式 Memory evidence db（只读，用于核实经历记录）")
    parser.add_argument("--controls-db", help="显式 Memory control ledger（只读）")
    parser.add_argument("--owner", help="显式 Memory owner")
    args = parser.parse_args(argv)

    state_root = _state_root(args)
    limit = None if args.limit is not None and args.limit <= 0 else args.limit
    envelope = review.build_settlement_review(
        state_root, action_id=args.action_id, limit=limit,
        memory_service=_memory_service(args))
    if args.format == "text":
        print(review.render_text(envelope))
    else:
        print(json.dumps(envelope, ensure_ascii=False, indent=2, default=str))
    return 0 if envelope.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
