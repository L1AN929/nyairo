#!/usr/bin/env python3
"""本地普通消息入口：投递一句、看状态、按消息 id 取她已经说过的那句本地回复。

它只做三件事，全部走 ``life_local_contact.LocalContactInbox`` 这个 owner：

* ``send``   —— 投递一条普通 ``LOCAL_MESSAGE``（本机来话，不是邀请、不是外发）；
* ``status`` —— 按 ``event_ref`` 或 ``operation_key`` 看这条消息的处理状态与处理结果；
* ``reply``  —— 取这条消息**已经存下来的本地回复**。默认只给 ``reply_present``/``reply_chars``；
  要正文必须显式 ``--include-text``（本机操作者的显式读取，不写日志、不进 prompt、不外发）。

``--include-text`` 是本机操作者读自己这台机器的数据，不构成对外交付：没有外发通道，
也没有任何人被通知"用户已收到/已读"。没有回复就是没有，不会为了取到一句话去重新生成。

用法（POSIX/WSL 与 Windows 均可，python 用 ``python``）::

    python scripts/local_reply_cli.py --state-root <life state> --subject <subject> \
        --home <home ref> send --text "在吗"
    python scripts/local_reply_cli.py ... status --operation-key <key>
    python scripts/local_reply_cli.py ... reply --event-ref contact_evt:... --include-text
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import life_local_contact as lc  # noqa: E402


def _inbox(args: argparse.Namespace) -> lc.LocalContactInbox:
    env = os.environ
    root = (args.state_root or env.get("NYA02_LIFE_STATE") or "").strip()
    subject = (args.subject or env.get("NYA02_SUBJECT") or "").strip()
    home = (args.home or env.get("NYA02_LIFE_HOME") or "").strip()
    if not root or not subject or not home:
        raise SystemExit("需要 --state-root/--subject/--home（或对应的 NYA02_* 环境变量）")
    return lc.LocalContactInbox(Path(root), subject_id=subject, home_ref=home)


def _emit(payload: dict[str, Any], *, ok: Optional[bool] = None) -> int:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    sys.stdout.flush()
    good = bool(payload.get("ok")) if ok is None else bool(ok)
    return 0 if good else 1


def _send(args: argparse.Namespace) -> int:
    inbox = _inbox(args)
    payload: dict[str, Any] = {"kind": lc.KIND_MESSAGE, "text": args.text}
    if args.operation_key:
        payload["operation_key"] = args.operation_key
    if args.message_ref:
        payload["message_ref"] = args.message_ref
    if args.occurred_at:
        payload["occurred_at"] = args.occurred_at
    answer = dict(inbox.submit(payload) or {})
    event = ((answer.get("data") or {}).get("event") or {})
    return _emit({"ok": bool(answer.get("ok")), "command": "send",
                  "status": answer.get("status"), "reason_code": answer.get("reason_code") or "",
                  "event_ref": answer.get("event_ref"),
                  "operation_key": answer.get("operation_key"),
                  "processing_state": event.get("processing_state"),
                  "pause_requested": False, "activity_started": False,
                  "owners_written": int(answer.get("owners_written") or 0)})


def _status(args: argparse.Namespace) -> int:
    inbox = _inbox(args)
    if args.operation_key:
        answer = dict(inbox.operation(args.operation_key) or {})
        data = dict(answer.get("data") or {})
        return _emit({"ok": bool(answer.get("ok")), "command": "status",
                      "status": answer.get("status"),
                      "reason_code": answer.get("reason_code") or "",
                      "event_ref": data.get("event_ref"),
                      "processing_state": data.get("event_state"),
                      "outcome": data.get("outcome"),
                      "reply_present": bool(((data.get("event") or {}).get("reply_present")))})
    answer = dict(inbox.get_event(args.event_ref or "") or {})
    event = dict((answer.get("data") or {}).get("event") or {})
    return _emit({"ok": bool(answer.get("ok")), "command": "status",
                  "status": answer.get("status"),
                  "reason_code": answer.get("reason_code") or "",
                  "event_ref": answer.get("event_ref") or args.event_ref,
                  "processing_state": answer.get("processing_state"),
                  "outcome": event.get("outcome"),
                  "reply_present": bool(event.get("reply_present")),
                  "reply_chars": int(event.get("reply_chars") or 0)})


def _reply(args: argparse.Namespace) -> int:
    inbox = _inbox(args)
    ref = str(args.event_ref or args.operation_key or args.message_ref or "").strip()
    answer = dict(inbox.reply(ref, include_text=bool(args.include_text)) or {})
    view = dict((answer.get("data") or {}).get("reply") or {})
    out: dict[str, Any] = {"ok": bool(answer.get("ok")), "command": "reply",
                           "status": answer.get("status"),
                           "reason_code": answer.get("reason_code") or "",
                           "event_ref": view.get("event_ref") or ref,
                           "reply_present": bool(view.get("reply_present")),
                           "reply_chars": int(view.get("reply_chars") or 0),
                           "recorded_at": view.get("recorded_at"),
                           "delivered_to_user": False,
                           "text_included": bool(view.get("text_included"))}
    if args.include_text and view.get("reply_present"):
        out["text"] = str(view.get("text") or "")
    return _emit(out, ok=bool(answer.get("ok")) and bool(view.get("reply_present")))


def _list(args: argparse.Namespace) -> int:
    inbox = _inbox(args)
    answer = dict(inbox.list_events(limit=args.limit) or {})
    data = dict(answer.get("data") or {})
    rows = [{"event_ref": row.get("event_ref"), "seq": row.get("seq"),
             "event_kind": row.get("event_kind"),
             "processing_state": row.get("processing_state"),
             "outcome": row.get("outcome"),
             "reply_present": bool(row.get("reply_present")),
             "reply_chars": int(row.get("reply_chars") or 0)}
            for row in (data.get("events") or [])]
    return _emit({"ok": bool(answer.get("ok")), "command": "list",
                  "status": answer.get("status"),
                  "reason_code": answer.get("reason_code") or "",
                  "events": rows, "returned": len(rows),
                  "truncated": bool(data.get("truncated")),
                  "ledger_state": data.get("ledger_state")})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="本地普通消息：投递、看状态、按 id 取已存下的本地回复")
    parser.add_argument("--state-root", default="", help="Life 状态根（含 %s）"
                                                        % lc.STATE_FILENAME)
    parser.add_argument("--subject", default="", help="本实例的 Life subject")
    parser.add_argument("--home", default="", help="本实例的 home ref（与 co-presence 同一个）")
    sub = parser.add_subparsers(dest="command", required=True)

    send = sub.add_parser("send", help="投递一条普通本地消息")
    send.add_argument("--text", required=True)
    send.add_argument("--operation-key", default="")
    send.add_argument("--message-ref", default="")
    send.add_argument("--occurred-at", default="")
    send.set_defaults(func=_send)

    status = sub.add_parser("status", help="一条消息的处理状态")
    group = status.add_mutually_exclusive_group(required=True)
    group.add_argument("--event-ref", default="")
    group.add_argument("--operation-key", default="")
    status.set_defaults(func=_status)

    reply = sub.add_parser("reply", help="取一条消息已经存下来的本地回复")
    group2 = reply.add_mutually_exclusive_group(required=True)
    group2.add_argument("--event-ref", default="")
    group2.add_argument("--operation-key", default="")
    group2.add_argument("--message-ref", default="")
    reply.add_argument("--include-text", action="store_true",
                       help="显式读取本机正文（默认只给是否存在与字数）")
    reply.set_defaults(func=_reply)

    listing = sub.add_parser("list", help="最近的本地消息与回复计数")
    listing.add_argument("--limit", type=int, default=20)
    listing.set_defaults(func=_list)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        return int(args.func(args))
    except lc.LocalContactError as exc:
        return _emit({"ok": False, "command": args.command,
                      "reason_code": exc.reason_code, "detail": exc.detail})


if __name__ == "__main__":
    sys.exit(main())
