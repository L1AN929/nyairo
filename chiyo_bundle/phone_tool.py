#!/usr/bin/env python3
"""``chiyo_phone``：个体自己看手机事实、提交手机操作的那一条窄工具。

它不拥有真相，也不是第二个手机服务：

* 读钱包/笔记/收藏/阅读/虚拟商店/**实体日用店**/物品/世界 → ``GET /api/phone/*``（世界快照与
  实体店货架都是只读的）；
* 显式购买/使用/写笔记/存收藏/移动/拿放/换姿势/休息/**柜台购买/布置自有家具/开关自己的灯** →
  同一条 HTTP 面上的 ``POST``，带 ``X-CSRF-Token``；
* 阅读工作台另有五个动作：``reading_material``（只读一页真实正文）、``reading_import``（显式导入
  正文，旧版本由 Supply 保留）、``reading_position``（先真读该页再显式保存停点）、
  ``reading_annotate``（对已取得的版本页写 ``note`` 私人笔记或 ``unclear`` 没读懂）、
  ``reading_operation``（按**原** operation_key 对账）；未知结果绝不换 key 重发；
* 全部字段、错误码与幂等语义属于 ``docs/engineering/v04-phone-api-contract.md``；世界/身体/房间
  字段与动词参数只按 C 的契约（``v04-world-body-contract.md`` + ``rc1-room-living-contract.md``）
  转发，P 不重算任何 owner 事实。

四条硬边界：

1. **同一个入口授权**：与 ``hermes_plugin.allowed`` 同一规则 —— CLI 需 ``cli_owner: true`` 且
   ``local_owner_uid`` 等于本进程真实 uid；网关只接受**已存在的 DM 绑定**；``local`` 入口与
   **被委派的会话**（``parent_session_id``）一律拒绝。``args`` 里没有任何入口字段，模型不能
   给自己发入口。
2. **显式开关**：profile ``chiyo/config.json`` 的 ``phone.enabled`` 缺失即 ``PHONE_DISABLED``；
   写操作（含 ``world_action``）还要 ``phone.write_enabled: true``（默认禁写）。
3. **只碰自己的手机**：``phone.profile_home`` 必须与服务器 ``/state`` 公布的实例指纹一致，
   否则 ``PHONE_PROFILE_MISMATCH``。``base_url`` 只能来自 profile，且必须是 loopback。
4. **私人档案是另一把钥匙**：私人 notes/references/reading 能保存她自己的过往，所以
   ``memory`` 打开且策略不是 ``unrestricted`` 时，本工具只保留
   wallet/shop/inventory/operation/world/world_operation；读写私人档案要 profile 显式
   ``phone.private_artifacts: true``。**开启手机不会绕过遗忘保护**（早先"不读 DB 所以不可能
   读到被遗忘历史"的说法不成立，已作废）。世界快照与 ``world_action`` 不是私人档案面：
   前者只有位置/摆位/姿势档位，后者只改世界与身体，两者都另受 ``write_enabled`` 约束。

来源分开：写请求带 ``X-Phone-Actor: model``；经济/Resolver 侧的 provenance 仍是
``local_owner_phone*`` —— 不伪造 ``verified_main_self``，也不伪造自主 Decision。
"""
from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping, Optional
from urllib.parse import urlsplit

SCHEMA_VERSION = "chiyo.phone.tool.v1"

READ_ACTIONS = {
    "wallet": "/api/phone/wallet",
    "notes": "/api/phone/notes",
    "references": "/api/phone/references",
    "reading": "/api/phone/reading",
    # The workbench's real text: one page, read-only.  A body is never cached by this tool.
    "reading_material": "/api/phone/reading/material",
    "shop": "/api/phone/shop",
    "store": "/api/phone/store",
    "inventory": "/api/phone/inventory",
    "world": "/api/phone/world",
}
WRITE_ACTIONS = ("note_save", "reference_save", "purchase", "store_purchase", "use",
                 "world_action", "reading_import", "reading_position", "reading_annotate")
#: Actions that read or write her private artifacts (or a private-artifact derivative).  Under a
#: restricted memory profile these need the separate ``phone.private_artifacts`` permit.
#: World/body commands are a different surface: they already require ``write_enabled``, and a
#: snapshot carries where she is, not her private notes.  The reading workbench is the most
#: private face of all -- the text she is reading, where she stopped and what she wrote about it.
PRIVATE_ARTIFACT_ACTIONS = frozenset({
    "notes", "references", "reading", "note_save", "reference_save", "purchase", "store_purchase",
    "use",
    "reading_material", "reading_import", "reading_position", "reading_annotate",
    "reading_operation",
})
ACTIONS = tuple(sorted(READ_ACTIONS)) + WRITE_ACTIONS + ("operation", "world_operation",
                                                         "reading_operation")

#: The World verbs a local owner may ask for.  The first five are the frozen v0.4 set (C's
#: contract §3.1); ``ARRANGE`` and ``TOGGLE_FIXTURE`` are this round's room verbs, exactly as C's
#: ``PhoneWorld.VERBS`` / ``PARAM_SPEC`` name them.  P never renames a verb: the owner's snapshot
#: ``actions.verbs`` and its reason codes stay the authority.
WORLD_ACTIONS = ("MOVE", "PICK_UP", "PLACE", "POSE", "REST", "ARRANGE", "TOGGLE_FIXTURE")
_WORLD_FIELDS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "MOVE": (frozenset({"destination_location_id"}), frozenset({"destination_location_id"})),
    "PICK_UP": (frozenset({"object_id", "slot_id"}), frozenset({"object_id", "slot_id"})),
    "PLACE": (frozenset({"object_id", "target"}), frozenset({"object_id", "target"})),
    "POSE": (frozenset({"pose", "orientation"}), frozenset({"pose"})),
    "REST": (frozenset({"state", "mode"}), frozenset({"state"})),
    # C's own table: one owned instance plus its target / the absolute fixture state.
    "ARRANGE": (frozenset({"object_id", "target"}), frozenset({"object_id", "target"})),
    "TOGGLE_FIXTURE": (frozenset({"object_id", "state"}), frozenset({"object_id", "state"})),
}
_ARRANGE_TARGET_KINDS = frozenset({"room", "surface"})
#: Never an input: identity, elapsed time and body values belong to the owners alone.
_WORLD_FORBIDDEN = frozenset({
    "uid", "euid", "gid", "authority", "authorities", "main_self_verified", "home",
    "hermes_home", "world_home", "profile", "profile_home", "owner", "subject", "binding",
    "elapsed", "elapsed_seconds", "elapsed_ms", "duration_seconds", "seconds", "rest_seconds",
    "fatigue", "activation", "comfort", "sensory_load", "tension", "warmth", "health", "energy",
    "body_values", "bands", "signals",
    # A price, a balance or a delivery state is the economy owner's word, never a client's.
    "price", "price_jpy", "amount", "cost", "balance", "currency", "delivery_state",
    "delivery_seconds", "elapsed_hours",
})
_WORLD_TARGET_FIELDS = {
    "room": frozenset({"kind", "location_id", "area_id"}),
    "surface": frozenset({"kind", "location_id", "surface_id"}),
    "container": frozenset({"kind", "location_id", "container_id"}),
    "hand": frozenset({"kind", "slot_id"}),
    "in_transit": frozenset({"kind", "from_location_id", "to_location_id"}),
}
_WORLD_CHOICES = {
    "slot_id": frozenset({"left_hand", "right_hand"}),
    "pose": frozenset({"standing", "seated", "lying", "transitioning"}),
    "orientation": frozenset({"north", "south", "east", "west", "up", "down"}),
    # ``state`` is a union here because two verbs use the word; each verb is pinned in
    # ``_WORLD_STATE_CHOICES``.
    "state": frozenset({"start", "stop", "on", "off"}),
    "mode": frozenset({"rest", "sleep_like"}),
}
#: ``state`` per verb: REST starts/stops a rest, the fixture switch is absolute on/off.  The two
#: never share a value, so a client cannot make one mean the other.
_WORLD_STATE_CHOICES = {
    "REST": frozenset({"start", "stop"}),
    "TOGGLE_FIXTURE": frozenset({"on", "off"}),
}
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost"})
TIMEOUT_SECONDS = 10.0

PHONE_TOOL_SCHEMA = {
    "name": "chiyo_phone",
    "description": (
        "打开她自己那部本地虚拟手机：读钱包余额与真实流水、笔记、收藏、阅读位置、虚拟商店目录、"
        "实体日用店的货架与营业状态、自己的物品，以及她所在的世界（位置、能去哪、身上的东西、"
        "姿势与身体信号、已到货的自有家具、房间灯）；也可以显式提交一条操作（写笔记、存收藏、"
        "手机虚拟店购买、实体店柜台购买、使用属于自己的物件、在世界里移动/拿放/换姿势/休息/"
        "布置自有家具/开关自己的灯）。"
        "阅读工作台另有五个动作：reading_material 只读一页真实正文；reading_import 把操作者显式给"
        "的文本写进这台实例已绑定的阅读材料（旧版本由 Supply 保留）；reading_position 显式保存停点"
        "（先真实读该页再落位置，回到前页也合法）；reading_annotate 对已取得的那一版那一页写私人笔记"
        "（kind=note）或诚实的没读懂（kind=unclear）；reading_operation 按原 operation_key 对账。"
        "所有数字与状态都来自真实 owner，工具不会补零、不会猜价格，也不会把失败说成成功："
        "购买成功不等于已到货，布置成功不等于已经可用，保存停点不等于读懂了（understanding 恒 UNKNOWN）。"
        "移动/拿放/姿势/休息/布置/开关灯必须给出 operation_key 并原样复用；若返回 status=unknown，"
        "先调用 action=world_operation 对账，绝不换 key 重发；阅读动作返回 unknown 时用 "
        "action=reading_operation 查同一个 key，同样绝不换 key 重发。这个工具不读记忆或聊天记录。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": list(ACTIONS),
                "description": (
                    "wallet/notes/references/reading/reading_material/shop/store/inventory/world 为"
                    "只读；note_save/reference_save/purchase/store_purchase/use/world_action/"
                    "reading_import/reading_position/reading_annotate 为显式写；operation/"
                    "world_operation/reading_operation 用 operation_key 对账（原 key，不换键重试）。"
                ),
            },
            "title": {"type": "string", "maxLength": 80, "description": "笔记标题（note_save）"},
            "body": {"type": "string", "maxLength": 4000, "description": "笔记正文（note_save）"},
            "note_id": {"type": "string", "maxLength": 128, "description": "要修订的笔记 id（可选）"},
            "expected_head": {"type": "string", "maxLength": 128,
                              "description": "写入基准的 owner head（可选，防并发覆盖）"},
            "content": {"type": "string", "maxLength": 24000,
                        "description": ("操作者显式提供的阅读正文（reading_import）：1..24000 UTF-8 "
                                        "字节，用 %%PAGE%% 分页；不抓取网络、不改其它 artifact")},
            "expected_revision": {"type": "string", "maxLength": 128,
                                  "description": ("当前材料的真实版本 id（reading_import 的 CAS 基准）")},
            "page": {"type": "integer", "minimum": 1, "maximum": 64,
                     "description": ("页码（从 1 开始，严格整数）：reading_material 的可选查询页、"
                                     "reading_position/reading_annotate 的目标真实页")},
            "kind": {"type": "string", "enum": ["note", "unclear"],
                     "description": ("页注类型（reading_annotate）：note=私人笔记（需 title+body），"
                                     "unclear=确实没读懂的疑问（需 note）；两种不是一回事")},
            "note": {"type": "string", "maxLength": 8000,
                     "description": "没读懂的具体疑问（reading_annotate kind=unclear，≤8000 字节）"},
            "source_id": {"type": "string", "maxLength": 200,
                          "description": "收藏的出处标识（reference_save）"},
            "source_revision": {"type": "string", "maxLength": 120,
                                "description": "收藏时的来源版本（reference_save）"},
            "label": {"type": "string", "maxLength": 60,
                      "description": "给这条收藏起的名字（reference_save）"},
            "catalog_item_id": {"type": "string", "maxLength": 80,
                                "description": "商店目录里的 catalog_item_id（purchase）"},
            "item_instance_id": {"type": "string", "maxLength": 128,
                                 "description": "自己物品的 item_instance_id（use）"},
            "operation_key": {"type": "string", "maxLength": 128,
                              "description": "幂等键：同一次意图必须复用同一个 key"},
            "world_action": {
                "type": "string", "enum": list(WORLD_ACTIONS),
                "description": ("世界动作（world_action）：MOVE/PICK_UP/PLACE/POSE/REST，以及本轮房间动作 "
                                "ARRANGE（object_id + target）与 TOGGLE_FIXTURE（object_id + 绝对 "
                                "state=on/off）；动词与参数以快照 actions.verbs 为准确认"),
            },
            "params": {
                "type": "object",
                "description": (
                    "该世界动作的参数对象，键名与 docs/engineering/v04-world-body-contract.md §3.1 "
                    "以及本轮的 docs/engineering/rc1-room-living-contract.md 一致；"
                    "uid/home/authority/elapsed_seconds/价格/身体值一律不接受。"
                ),
                "properties": {
                    "destination_location_id": {"type": "string", "maxLength": 96},
                    "object_id": {"type": "string", "maxLength": 128},
                    # (no room-specific ids: ARRANGE / TOGGLE_FIXTURE take the owner's object_id)
                    "slot_id": {"type": "string", "enum": ["left_hand", "right_hand"]},
                    "target": {
                        "type": "object",
                        "properties": {
                            "kind": {"type": "string",
                                     "enum": ["room", "surface", "container", "hand",
                                              "in_transit"]},
                            "location_id": {"type": "string", "maxLength": 128},
                            "area_id": {"type": "string", "maxLength": 128},
                            "surface_id": {"type": "string", "maxLength": 128},
                            "container_id": {"type": "string", "maxLength": 128},
                            "slot_id": {"type": "string",
                                        "enum": ["left_hand", "right_hand"]},
                            "from_location_id": {"type": "string", "maxLength": 128},
                            "to_location_id": {"type": "string", "maxLength": 128},
                        },
                        "additionalProperties": False,
                    },
                    "pose": {"type": "string",
                             "enum": ["standing", "seated", "lying", "transitioning"]},
                    "orientation": {"type": "string",
                                    "enum": ["north", "south", "east", "west", "up", "down"]},
                    # REST uses start/stop; TOGGLE_FIXTURE uses the absolute on/off.  Each verb is
                    # pinned in ``_WORLD_STATE_CHOICES`` before the request leaves this process.
                    "state": {"type": "string", "enum": ["start", "stop", "on", "off"]},
                    "mode": {"type": "string", "enum": ["rest", "sleep_like"]},
                },
                "additionalProperties": False,
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    },
}

_FIELD_BY_ACTION = {
    "note_save": frozenset({"action", "title", "body", "note_id", "expected_head",
                            "operation_key"}),
    "reference_save": frozenset({"action", "source_id", "source_revision", "label",
                                 "operation_key"}),
    "purchase": frozenset({"action", "catalog_item_id", "operation_key"}),
    "use": frozenset({"action", "item_instance_id", "operation_key"}),
    # The physical shop's counter: same economy owner, same idempotency key, no price from here.
    "store_purchase": frozenset({"action", "catalog_item_id", "operation_key"}),
    "operation": frozenset({"action", "operation_key"}),
    "world_action": frozenset({"action", "world_action", "params", "operation_key"}),
    "world_operation": frozenset({"action", "operation_key"}),
    # The reading workbench: one page read, one explicit import, one explicit stop, one private
    # annotation, one original-key lookup.  No action here accepts an artifact id, a path or a URL.
    "reading_material": frozenset({"action", "page"}),
    "reading_import": frozenset({"action", "content", "expected_revision", "operation_key"}),
    "reading_position": frozenset({"action", "page", "source_revision", "expected_head",
                                   "operation_key"}),
    "reading_annotate": frozenset({"action", "kind", "page", "source_revision", "title", "body",
                                   "note", "expected_head", "operation_key"}),
    "reading_operation": frozenset({"action", "operation_key"}),
}


class PhoneToolRefused(RuntimeError):
    """A refusal this tool made itself, before any request left the process."""

    def __init__(self, reason_code: str, message: str = "") -> None:
        super().__init__(reason_code)
        self.reason_code = str(reason_code)
        self.message = str(message)[:300]


# ---------------------------------------------------------------------------------------------
# profile settings
# ---------------------------------------------------------------------------------------------


def _profile_home(kwargs: Mapping[str, Any]) -> Path:
    explicit = kwargs.get("hermes_home")
    if explicit:
        return Path(str(explicit)).expanduser()
    environment = kwargs.get("environment") or os.environ
    if environment.get("HERMES_HOME"):
        return Path(str(environment["HERMES_HOME"])).expanduser()
    try:
        from hermes_constants import get_hermes_home
    except Exception:
        return Path.home() / ".hermes"
    return Path(str(get_hermes_home())).expanduser()


def load_settings(kwargs: Mapping[str, Any]) -> dict[str, Any]:
    """The profile's own ``phone`` section.  Absent means "feature off", never "guess"."""

    home = _profile_home(kwargs)
    path = home / "chiyo" / "config.json"
    if not path.exists():
        raise PhoneToolRefused("PHONE_DISABLED", "这个 profile 没有配置手机。")
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise PhoneToolRefused("PHONE_DISABLED", "profile 配置不可读，手机保持关闭。")
    if not isinstance(config, dict):
        raise PhoneToolRefused("PHONE_DISABLED", "profile 配置不是对象，手机保持关闭。")
    section = config.get("phone")
    if not isinstance(section, dict) or section.get("enabled") is not True:
        raise PhoneToolRefused("PHONE_DISABLED", "手机功能未在 profile 里显式启用。")
    base_url = str(section.get("base_url") or "").strip()
    if not base_url:
        raise PhoneToolRefused("PHONE_BASE_URL_MISSING", "profile 没有给出手机服务地址。")
    parts = urlsplit(base_url)
    if parts.scheme != "http" or (parts.hostname or "").lower() not in LOOPBACK_HOSTS:
        raise PhoneToolRefused("PHONE_BASE_URL_NOT_LOOPBACK", "手机服务只能是本机 loopback。")
    if not parts.port:
        raise PhoneToolRefused("PHONE_BASE_URL_INVALID", "手机服务地址需要显式端口。")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise PhoneToolRefused("PHONE_BASE_URL_INVALID", "手机服务地址不能带路径。")
    return {
        "profile_home": str(section.get("profile_home") or ""),
        "base_url": "http://%s:%d" % ((parts.hostname or "").lower(), int(parts.port)),
        "write_enabled": section.get("write_enabled") is True,
        # Private artifacts (notes/references/reading) are a separate, explicit permit: they can
        # hold the operator's own private history, so "phone on" must not imply "read my notes".
        "private_artifacts": section.get("private_artifacts") is True,
        "config": config,
    }


# ---------------------------------------------------------------------------------------------
# who is asking (the same rule as chiyo_bundle.hermes_plugin.allowed)
# ---------------------------------------------------------------------------------------------


def _allowed_mirror(config: Mapping[str, Any], platform: str, conversation_key: Any) -> bool:
    """The original entry rule, verbatim, for environments where the plugin module is absent.

    ``hermes_plugin.allowed`` decides whether this chat entry is bound to this personal
    instance:

    * ``cli``: only when the profile sets ``cli_owner: true`` and ``local_owner_uid`` equals this
      process's real uid;
    * ``local``: never;
    * a gateway platform: only an existing DM binding for that platform, and the conversation key
      must be one of them.

    Nothing here reads ``args``: a model cannot hand itself an entry.
    """

    if platform == "cli":
        uid = os.getuid() if hasattr(os, "getuid") else None
        return config.get("cli_owner") is True and config.get("local_owner_uid") == uid
    if platform == "local":
        return False
    bindings = config.get("gateway_bindings") or {}
    allowed_keys = bindings.get(platform) if isinstance(bindings, dict) else None
    return bool(conversation_key and ":dm:" in str(conversation_key)
                and isinstance(allowed_keys, (list, tuple)) and conversation_key in allowed_keys)


def entry_allowed(config: Mapping[str, Any], platform: str, conversation_key: Any) -> bool:
    """Delegate to the original gate when it is importable; otherwise the same rule locally."""

    try:
        from . import hermes_plugin  # noqa: F401 - the real owner of this rule
    except Exception:
        return _allowed_mirror(config, platform, conversation_key)
    try:
        return bool(hermes_plugin.allowed(config, platform, conversation_key))
    except Exception:
        return False


def _authorize(settings: Mapping[str, Any], kwargs: Mapping[str, Any]) -> None:
    """Refuse an unbound entry or a delegated turn before any request is sent."""

    if kwargs.get("parent_session_id") or kwargs.get("delegated") is True:
        raise PhoneToolRefused("PHONE_DELEGATED_SOURCE_REFUSED",
                               "被委派的会话没有自己的手机入口。")
    platform = str(kwargs.get("platform") or "cli")
    if not entry_allowed(settings["config"], platform, kwargs.get("conversation_key")):
        raise PhoneToolRefused("PHONE_ENTRY_NOT_AUTHORIZED",
                               "这个聊天入口没有绑定你的个人实例，手机保持关闭。")


# ---------------------------------------------------------------------------------------------
# transport (loopback only; no credential ever leaves this process)
# ---------------------------------------------------------------------------------------------


def _request(settings: Mapping[str, Any], method: str, path: str,
             body: Optional[Mapping[str, Any]] = None, *,
             csrf: Optional[str] = None) -> tuple[int, dict[str, Any]]:
    data = None if body is None else json.dumps(dict(body), ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(str(settings["base_url"]) + path, data=data, method=method)
    request.add_header("Accept", "application/json")
    if data is not None:
        request.add_header("Content-Type", "application/json")
        request.add_header("X-Phone-Actor", "model")
    if csrf:
        request.add_header("X-CSRF-Token", str(csrf))
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return int(response.status), _parse(response.read())
    except urllib.error.HTTPError as error:
        return int(error.code), _parse(error.read())
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise PhoneToolRefused("PHONE_UNREACHABLE", type(error).__name__)


def _parse(raw: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return {"ok": False, "status": "unknown", "reason_code": "PHONE_BAD_RESPONSE"}
    return payload if isinstance(payload, dict) else {
        "ok": False, "status": "unknown", "reason_code": "PHONE_BAD_RESPONSE"}


def _profile_fingerprint(value: str) -> str:
    """The same non-reversible name the phone service publishes for one profile home."""

    return "phone:" + hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]


def _state(settings: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    code, doc = _request(settings, "GET", "/api/phone/state")
    if code != 200 or doc.get("ok") is not True:
        raise PhoneToolRefused(str(doc.get("reason_code") or "PHONE_STATE_UNAVAILABLE"))
    csrf = doc.get("csrf_token")
    if not isinstance(csrf, str) or not csrf:
        raise PhoneToolRefused("PHONE_CSRF_MISSING")
    expected = str(settings.get("profile_home") or "")
    if expected:
        instance = doc.get("instance") if isinstance(doc.get("instance"), Mapping) else {}
        if instance.get("fingerprint") != _profile_fingerprint(expected):
            raise PhoneToolRefused("PHONE_PROFILE_MISMATCH",
                                   "这部手机不是当前 profile 登记的那一部。")
    return doc, csrf


# ---------------------------------------------------------------------------------------------
# handler
# ---------------------------------------------------------------------------------------------


def _operation_key(action: str, args: Mapping[str, Any]) -> str:
    supplied = args.get("operation_key")
    if isinstance(supplied, str) and supplied.strip():
        return supplied.strip()
    payload = {k: v for k, v in args.items() if k not in {"action", "operation_key"}}
    digest = hashlib.sha256(
        ("chiyo|phone_tool|" + action + "|" + json.dumps(payload, ensure_ascii=False,
                                                         sort_keys=True)).encode("utf-8")
    ).hexdigest()[:32]
    return "tool:" + digest


def _result(ok: bool, status: str, message: str, **extra: Any) -> str:
    payload: dict[str, Any] = {"ok": bool(ok), "status": str(status), "message": message}
    payload.update({k: v for k, v in extra.items() if v is not None})
    return json.dumps(payload, ensure_ascii=False)


def _summary(doc: Mapping[str, Any]) -> dict[str, Any]:
    """The service answer, verbatim (it is already the one envelope; nothing is re-shaped)."""

    result = dict(doc)
    result.pop("csrf_token", None)
    return result

# ---------------------------------------------------------------------------------------------
# world action pre-flight (shape only; C's contract owns every semantic)
# ---------------------------------------------------------------------------------------------


def _world_target(value: Any, *, kinds: Optional[frozenset[str]] = None) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PhoneToolRefused("INVALID_PARAMS", "target 必须是一个对象。")
    target = dict(value)
    forbidden = sorted(set(target) & _WORLD_FORBIDDEN)
    if forbidden:
        raise PhoneToolRefused("CLIENT_IDENTITY_REFUSED",
                               "这些字段不能由客户端提供：%s" % ", ".join(forbidden))
    kind = target.get("kind")
    expected = _WORLD_TARGET_FIELDS.get(kind) if isinstance(kind, str) else None
    if expected is None or set(target) != set(expected):
        raise PhoneToolRefused("INVALID_PARAMS", "target 的形状与 kind 不一致。")
    if kinds is not None and kind not in kinds:
        raise PhoneToolRefused("INVALID_PARAMS",
                               "自己的家具只能布置到房间或已声明的承载面。")
    for field in sorted(expected - {"kind"}):
        item = target.get(field)
        if not isinstance(item, str) or not item.strip():
            raise PhoneToolRefused("INVALID_PARAMS", "target.%s 必须是非空字符串。" % field)
        if field == "slot_id" and item not in _WORLD_CHOICES["slot_id"]:
            raise PhoneToolRefused("INVALID_PARAMS", "target.slot_id 不合法。")
        target[field] = item.strip()
    return target


def _world_params(verb: str, value: Any) -> dict[str, Any]:
    """Validate one verb's params here, before any request leaves this process."""

    if not isinstance(value, Mapping):
        raise PhoneToolRefused("INVALID_PARAMS", "世界动作需要一个 params 对象。")
    params = dict(value)
    forbidden = sorted(set(params) & _WORLD_FORBIDDEN)
    if forbidden:
        raise PhoneToolRefused("CLIENT_IDENTITY_REFUSED",
                               "这些字段不能由客户端提供：%s" % ", ".join(forbidden))
    allowed, required = _WORLD_FIELDS[verb]
    unknown = sorted(set(params) - set(allowed))
    if unknown:
        raise PhoneToolRefused("UNKNOWN_FIELD",
                               "这个动作不接受这些参数：%s" % ", ".join(unknown))
    missing = sorted(set(required) - set(params))
    if missing:
        raise PhoneToolRefused("PARAM_REQUIRED", "缺少参数：%s" % ", ".join(missing))
    for field, choices in _WORLD_CHOICES.items():
        if field in params:
            item = params[field]
            if not isinstance(item, str) or item not in choices:
                raise PhoneToolRefused("INVALID_PARAMS", "参数 %s 的取值不合法。" % field)
    states = _WORLD_STATE_CHOICES.get(verb)
    if "state" in params and (states is None or params["state"] not in states):
        raise PhoneToolRefused("INVALID_PARAMS", "参数 state 不属于这个动作。")
    if "mode" in params and params.get("state") != "start":
        raise PhoneToolRefused("INVALID_PARAMS", "mode 只能随 state=start 一起给出。")
    if "target" in params:
        params["target"] = _world_target(
            params["target"], kinds=_ARRANGE_TARGET_KINDS if verb == "ARRANGE" else None)
    for field in ("destination_location_id", "object_id"):
        if field in params:
            item = params[field]
            if not isinstance(item, str) or not item.strip():
                raise PhoneToolRefused("INVALID_PARAMS", "参数 %s 必须是非空字符串。" % field)
            params[field] = item.strip()
    return params


# ---------------------------------------------------------------------------------------------
# reading workbench pre-flight (shape only; C's PhoneReading owns every reading fact)
# ---------------------------------------------------------------------------------------------

#: C's own bounds (``rc1-reading-contract.md``): the whole text, the page, and the private note.
#: C measures annotation text in UTF-8 **bytes** (``PhoneReading._text``), so the tool does too.
MAX_READING_CONTENT_BYTES = 24000
MAX_READING_PAGE = 64
MAX_READING_TITLE_BYTES = 200
MAX_READING_ANNOTATION_BYTES = 8000


def _reading_page(value: Any, *, required: bool) -> int:
    """A page is a strict positive integer: no bool, no float, no numeric string."""

    if value is None and not required:
        return 0
    if isinstance(value, bool) or not isinstance(value, int):
        raise PhoneToolRefused("INVALID_ARGUMENTS", "page 必须是整数页码（1 开始）。")
    if value < 1 or value > MAX_READING_PAGE:
        raise PhoneToolRefused("INVALID_ARGUMENTS", "page 超出 1..%d。" % MAX_READING_PAGE)
    return int(value)


def _reading_content(value: Any) -> str:
    """The operator's own text: 1..24000 UTF-8 bytes.  Nothing is fetched, nothing is split here."""

    if not isinstance(value, str):
        raise PhoneToolRefused("INVALID_ARGUMENTS", "reading_import 需要 content 正文。")
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip() or "\x00" in text:
        raise PhoneToolRefused("INVALID_ARGUMENTS", "content 不能是空白，也不能含 NUL。")
    size = len(text.encode("utf-8"))
    if size < 1 or size > MAX_READING_CONTENT_BYTES:
        raise PhoneToolRefused("INVALID_ARGUMENTS",
                               "content 必须 1..%d UTF-8 字节。" % MAX_READING_CONTENT_BYTES)
    return text


def _reading_text(value: Any, maximum_bytes: int, reason: str) -> str:
    """One annotation text: non-blank, no NUL, within C's own UTF-8 byte bound."""

    if not isinstance(value, str):
        raise PhoneToolRefused("INVALID_ARGUMENTS", reason)
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip() or "\x00" in text:
        raise PhoneToolRefused("INVALID_ARGUMENTS", reason)
    if len(text.encode("utf-8")) > int(maximum_bytes):
        raise PhoneToolRefused("INVALID_ARGUMENTS", reason)
    return text


def _reading_annotation(args: Mapping[str, Any]) -> dict[str, Any]:
    """Pin one annotation kind exactly: ``note`` has title+body, ``unclear`` has a note."""

    kind = args.get("kind")
    if not isinstance(kind, str) or kind not in ("note", "unclear"):
        raise PhoneToolRefused("INVALID_ARGUMENTS", "kind 只能是 note 或 unclear。")
    if kind == "note":
        if args.get("note") is not None:
            raise PhoneToolRefused("UNKNOWN_FIELD", "kind=note 不接受 note 字段。")
        return {
            "title": _reading_text(args.get("title"), MAX_READING_TITLE_BYTES,
                                   "kind=note 需要 title。"),
            "body": _reading_text(args.get("body"), MAX_READING_ANNOTATION_BYTES,
                                  "kind=note 需要 body。"),
        }
    for field in ("title", "body"):
        if args.get(field) is not None:
            raise PhoneToolRefused("UNKNOWN_FIELD", "kind=unclear 不接受 %s 字段。" % field)
    return {"note": _reading_text(args.get("note"), MAX_READING_ANNOTATION_BYTES,
                                  "kind=unclear 需要 note。")}


def _reading_message(action: str, status: str, reason_code: str) -> str:
    """What the owner's status means for one reading action -- never an upgrade, never a guess."""

    base = {
        "reading_import": {
            "applied": "owner 已把正文写进这台实例绑定的阅读材料；旧版本保留，旧停点仍标记为需要重看。",
            "already_applied": "同一意图此前已导入，没有再次写入版本。",
            "rejected": "owner 拒绝了这次导入（正文界限或版本冲突），材料没有改变。",
            "unavailable": "Supply/阅读 owner 当前不可用，导入没有确认。",
            "unknown": "结果未知：不要换 key 重发，先用 action=reading_operation 查原 key。",
        },
        "reading_position": {
            "applied": "owner 已真实读取该页并保存停点；这只是位置，不是理解证明。",
            "already_applied": "同一意图此前已保存，没有再次落位置。",
            "rejected": "owner 拒绝了这次停点（版本/head 冲突或该页不存在），位置没有改变。",
            "unavailable": "Supply/阅读 owner 当前不可用，停点没有确认。",
            "unknown": "结果未知：不要换 key 重发，先用 action=reading_operation 查原 key。",
        },
        "reading_annotate": {
            "applied": "owner 已把这条页注记成私人事项；它只属于你，不进正文也不代表理解。",
            "already_applied": "同一意图此前已记下，没有第二条页注。",
            "rejected": ("owner 拒绝了这条页注：这一版这一页还没有被真实取得"
                         "（PAGE_NOT_ACQUIRED 时先保存停点）或 head 已过期。"),
            "unavailable": "Supply/阅读 owner 当前不可用，页注没有确认。",
            "unknown": "结果未知：不要换 key 重发，先用 action=reading_operation 查原 key。",
        },
    }.get(action, {})
    if reason_code == "PAGE_NOT_ACQUIRED":
        return "这一版这一页还没有真实取得：先 reading_position 保存停点，再写页注。"
    if reason_code in ("READING_REVISION_CONFLICT", "READING_HEAD_CONFLICT"):
        return "版本/head 已变：重新读当前材料页再提交，不要换 key 重发。"
    return base.get(status, "结果未知：不要换 key 重发，先按 operation_key 对账。")


def phone_handler(args: Mapping[str, Any], **kwargs: Any) -> str:
    """One bounded call.  Every refusal is typed; nothing is retried with a new key."""

    try:
        if not isinstance(args, Mapping):
            return _result(False, "rejected", "参数必须是对象。", reason_code="INVALID_ARGUMENTS")
        action = str(args.get("action") or "").strip()
        if action not in ACTIONS:
            return _result(False, "rejected", "未知的手机动作。", reason_code="UNKNOWN_ACTION")
        allowed = _FIELD_BY_ACTION.get(action)
        if allowed is not None:
            unknown = sorted(set(args) - set(allowed))
            if unknown:
                return _result(False, "rejected",
                               "这个动作不接受这些字段。", reason_code="UNKNOWN_FIELD",
                               fields=unknown)
        elif set(args) != {"action"}:
            return _result(False, "rejected", "只读动作只接受 action。",
                           reason_code="UNKNOWN_FIELD")

        settings = load_settings(kwargs)
        _authorize(settings, kwargs)
        write = action in WRITE_ACTIONS
        if write and not settings["write_enabled"]:
            return _result(False, "rejected",
                           "profile 没有启用手机写入，操作没有提交。",
                           reason_code="PHONE_WRITE_DISABLED")
        # A private artifact can hold the operator's own past, so a restricted memory profile must
        # not gain that surface just because the phone is on: only the economy reads stay open.
        config = settings["config"]
        restricted_memory = bool(config.get("memory")) and \
            str(config.get("memory_tool_policy") or "") != "unrestricted"
        if restricted_memory and action in PRIVATE_ARTIFACT_ACTIONS \
                and settings["private_artifacts"] is not True:
            return _result(False, "rejected",
                           "记忆处于受限模式：读/写私人笔记需要 profile 显式允许 phone.private_artifacts。",
                           reason_code="PHONE_PRIVATE_ARTIFACTS_NOT_PERMITTED")
        _state_doc, csrf = _state(settings)

        if action == "reading_material":
            # The page is a caller choice and the absence of one is meaningful: the owner then
            # answers with the most recent explicit stop, not with "page 1".
            page = _reading_page(args.get("page"), required=False)
            path = READ_ACTIONS[action] + ("" if page == 0 else "?page=%d" % page)
            code, doc = _request(settings, "GET", path)
            result = _summary(doc)
            data = doc.get("data") if isinstance(doc.get("data"), Mapping) else {}
            if not doc.get("ok"):
                message = "这一页读不到；服务说明了原因（没有绑定材料时是诚实的 unavailable）。"
            elif data.get("content_available") is False:
                message = "这一版没有正文可读；没有材料时工具不会补演示文本。"
            else:
                message = "已读取这一页真实正文（只读，没有保存停点）。"
            return _result(bool(doc.get("ok")), str(doc.get("status") or "unknown"), message,
                           http_status=code, result=result)

        if action in READ_ACTIONS:
            code, doc = _request(settings, "GET", READ_ACTIONS[action])
            result = _summary(doc)
            message = "已读取。" if doc.get("ok") else "读取失败，服务说明了原因。"
            return _result(bool(doc.get("ok")), str(doc.get("status") or "unknown"), message,
                           http_status=code, result=result)

        if action == "operation":
            key = str(args.get("operation_key") or "").strip()
            if not key:
                return _result(False, "rejected", "对账需要 operation_key。",
                               reason_code="OPERATION_KEY_REQUIRED")
            code, doc = _request(settings, "GET", "/api/phone/operations/" + key)
            result = _summary(doc)
            if doc.get("ok"):
                message = "这条操作已经在 owner 那里有确定结果。"
            elif str(doc.get("status")) == "not_found":
                message = "owner 没有这条操作；它从未落地。"
            else:
                message = "这条操作仍未有确定结果；不要换 key 重发。"
            return _result(bool(doc.get("ok")), str(doc.get("status") or "unknown"), message,
                           http_status=code, operation_key=key, result=result)
        if action == "world_operation":
            key = str(args.get("operation_key") or "").strip()
            if not key:
                return _result(False, "rejected", "对账需要 operation_key。",
                               reason_code="OPERATION_KEY_REQUIRED")
            code, doc = _request(settings, "GET", "/api/phone/world/operations/" + key)
            result = _summary(doc)
            if doc.get("ok"):
                message = "这次世界动作在 owner 那里已有确定结果。"
            elif str(doc.get("status")) == "not_found":
                message = "owner 没有这条世界操作；它从未落地。"
            else:
                message = "这条世界操作仍未有确定结果；不要换 key 重发。"
            return _result(bool(doc.get("ok")), str(doc.get("status") or "unknown"), message,
                           http_status=code, operation_key=key, result=result)
        if action == "reading_operation":
            key = str(args.get("operation_key") or "").strip()
            if not key:
                return _result(False, "rejected", "对账需要 operation_key。",
                               reason_code="OPERATION_KEY_REQUIRED")
            # The original key, verbatim: an unknown key stays unknown, and the tool never mints
            # a replacement key to "try again" (that would double-write the owner's document).
            code, doc = _request(settings, "GET", "/api/phone/reading/operations/" + key)
            result = _summary(doc)
            if doc.get("ok"):
                message = "这条阅读操作在 owner 那里已有确定结果。"
            elif str(doc.get("status")) == "not_found":
                message = "owner 没有这条阅读操作；它从未落地。"
            else:
                message = "这条阅读操作仍未有确定结果；不要换 key 重发。"
            return _result(bool(doc.get("ok")), str(doc.get("status") or "unknown"), message,
                           http_status=code, operation_key=key, result=result)

        key = _operation_key(action, args)
        if action == "note_save":
            path = "/api/phone/notes"
            body = {"title": args.get("title"), "body": args.get("body"),
                    "operation_key": key}
            if args.get("note_id"):
                body["note_id"] = args["note_id"]
            if args.get("expected_head"):
                body["expected_head"] = args["expected_head"]
        elif action == "reference_save":
            path = "/api/phone/references"
            body = {"source_id": args.get("source_id"),
                    "source_revision": args.get("source_revision"),
                    "label": args.get("label"), "operation_key": key}
        elif action == "purchase":
            path = "/api/phone/purchases"
            body = {"catalog_item_id": args.get("catalog_item_id"), "operation_key": key}
        elif action == "store_purchase":
            # 实体店柜台：同一个经济 owner、同一把幂等键；价格/余额/交付状态不由这里给。
            path = "/api/phone/store/purchases"
            body = {"catalog_item_id": args.get("catalog_item_id"), "operation_key": key}
        elif action == "world_action":
            verb = str(args.get("world_action") or "").strip().upper()
            if verb not in WORLD_ACTIONS:
                return _result(False, "rejected", "未知的世界动作。",
                               reason_code="UNKNOWN_WORLD_ACTION")
            path = "/api/phone/world/actions"
            body = {"action": verb, "params": _world_params(verb, args.get("params")),
                    "operation_key": key}
        elif action == "reading_import":
            # The operator's own text for the artifact this instance is already bound to.  No
            # artifact id, path, URL, title or receipt ever leaves this process.
            path = "/api/phone/reading/material"
            body = {"content": _reading_content(args.get("content")),
                    "expected_revision": str(args.get("expected_revision") or "").strip(),
                    "operation_key": key}
        elif action == "reading_position":
            path = "/api/phone/reading/positions"
            body = {"page": _reading_page(args.get("page"), required=True),
                    "source_revision": str(args.get("source_revision") or "").strip(),
                    "expected_head": str(args.get("expected_head") or "").strip(),
                    "operation_key": key}
        elif action == "reading_annotate":
            path = "/api/phone/reading/annotations"
            body = {"kind": str(args.get("kind") or "").strip(),
                    "page": _reading_page(args.get("page"), required=True),
                    "source_revision": str(args.get("source_revision") or "").strip(),
                    "expected_head": str(args.get("expected_head") or "").strip(),
                    "operation_key": key}
            body.update(_reading_annotation(args))
        else:  # use
            path = "/api/phone/items/use"
            body = {"item_instance_id": args.get("item_instance_id"), "operation_key": key}

        code, doc = _request(settings, "POST", path, body, csrf=csrf)
        status = str(doc.get("status") or "unknown")
        result = _summary(doc)
        if status in ("applied", "already_applied"):
            message = ("owner 已提交。" if status == "applied"
                       else "同一意图此前已提交，没有再次扣款或消耗。")
        elif status == "rejected":
            message = "owner 拒绝了这次操作，什么都没改。"
        elif status == "unavailable":
            message = "依赖的 owner 当前不可用，操作没有确认。"
        elif status == "disabled":
            message = "这个功能当前关闭。"
        else:
            message = "结果未知：不要换 key 重发，先按 operation_key 对账。"
        if action == "world_action":
            # A world command being accepted is not the same as its effect having happened.
            if status == "applied":
                message = ("owner 已接受这次世界动作；出发/姿势/休息的事实只以 owner 的快照为准。"
                           if verb == "MOVE" else
                           "owner 已接受这次世界动作；身体与世界的变化只以 owner 的快照为准。")
            elif status == "already_applied":
                message = "同一意图此前已提交，没有再次改变世界或身体。"
            elif status == "rejected":
                message = "owner 拒绝了这次世界动作，什么都没改。"
            elif status in ("unavailable", "disabled"):
                message = "世界/身体服务当前不可用或关闭，动作没有确认。"
            else:
                message = "结果未知：不要换 key 重发，先用 action=world_operation 查原 key。"
        if action == "store_purchase":
            # 柜台成交 ≠ 已到货：到货、可布置、可用都是 owner 之后的快照事实。
            if status == "applied":
                message = ("owner 已在实体店柜台成交；是否已到货、能否布置只以世界快照为准。")
            elif status == "already_applied":
                message = "同一意图此前已在柜台成交，没有再次扣款。"
            elif status == "rejected":
                message = "owner 拒绝了这次店内购买，什么都没改（未扣款、未交付）。"
            elif status in ("unavailable", "disabled"):
                message = "实体店/经济 owner 当前不可用或关闭，购买没有确认。"
            else:
                message = "结果未知：不要换 key 重发，先按 operation_key 对账。"
        if action in ("reading_import", "reading_position", "reading_annotate"):
            # 阅读写操作的每一条都是 owner 的话：导入 ≠ 读过，位置 ≠ 理解，页注 ≠ 读懂。
            message = _reading_message(action, status, str(doc.get("reason_code") or ""))
        return _result(bool(doc.get("ok")), status, message, http_status=code,
                       operation_key=key, result=result)
    except PhoneToolRefused as exc:
        return _result(False, "rejected" if exc.reason_code != "PHONE_UNREACHABLE"
                       else "unavailable",
                       exc.message or "手机服务不可用，操作没有提交。",
                       reason_code=exc.reason_code)
    except Exception as exc:  # never leak paths, tokens or raw exception text
        return _result(False, "unavailable", "手机状态无法确认，操作没有提交。",
                       reason_code="PHONE_TOOL_ERROR", error_type=type(exc).__name__)


__all__ = [
    "ACTIONS",
    "MAX_READING_ANNOTATION_BYTES",
    "MAX_READING_CONTENT_BYTES",
    "MAX_READING_PAGE",
    "MAX_READING_TITLE_BYTES",
    "PHONE_TOOL_SCHEMA",
    "PRIVATE_ARTIFACT_ACTIONS",
    "PhoneToolRefused",
    "READ_ACTIONS",
    "SCHEMA_VERSION",
    "WORLD_ACTIONS",
    "WRITE_ACTIONS",
    "entry_allowed",
    "load_settings",
    "phone_handler",
]
