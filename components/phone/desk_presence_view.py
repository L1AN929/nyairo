#!/usr/bin/env python3
"""双人书桌的轻量只读聚合：四态木牌、桌角作品/读物、未发短笺与明日便签。

它是什么
--------
一个**纯投影**模块：把手机服务已经读到的真实 owner 事实（World 位置、Body 恢复会话、
canonical Activity 前台、阅读工作台材料、作品 head、LifeSharingService 的留存短笺、
FutureItems 的明日条目）收成两个有限输出。它不拥有任何事实，也不写任何东西：

* 木牌四态只在证据齐时给出；读不到就 ``door_sign=None`` + 具名 ``reason_code``，
  绝不把 UNKNOWN 默认成「在家开放」。
* 短笺正文、便签标题/文本、来源正文默认不进入输出；只有本次 serve 的本地 capability
  有效时，才在「自己看」视角返回已经存在的有界明文。
* 没有真实伴侣在场依据 -> ``partner_presence=None``，不拿用户在线/共同书桌名字冒充。

它不是什么
----------
* 不是新 store、新 ACL、新 token、新权限体系；没有后台线程、定时器或模型调用。
* 不改 Life / World 的契约；只消费它们既有的只读入口。
* 不把「欲言又止」推断成真实情绪，也不替她补写「本来想说什么」。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional

PRESENCE_SCHEMA = "phone.desk.presence.v1"
NOTES_SCHEMA = "phone.desk.notes.v1"

DOOR_AT_HOME_OPEN = "AT_HOME_OPEN"
DOOR_FOCUSED_WORK = "FOCUSED_WORK"
DOOR_RESTING = "RESTING"
DOOR_OUT_IN_TOWN = "OUT_IN_TOWN"
DOOR_SIGNS = (DOOR_AT_HOME_OPEN, DOOR_FOCUSED_WORK, DOOR_RESTING, DOOR_OUT_IN_TOWN)

#: 木牌缺失时只报这三种状态；``ok`` 才是四态之一。
DOOR_STATUS_OK = "ok"
DOOR_STATUS_UNKNOWN = "unknown"
DOOR_STATUS_UNAVAILABLE = "unavailable"

#: 短笺留存状态（撤回/放弃留痕，不复活 DROPPED）。LOCAL_DELIVERED 不在这里：
#: 已投递的分享只在原 inbox/分享面按既有引用呈现，桌面聚合不偷偷转发。
RETAINED_SHARE_STATES = ("DRAFT", "DEFERRED", "DROPPED")

#: 「允许伴侣偶然翻见」不是现成机制：没有显式共享发布就不宣称支持。
PARTNER_PREVIEW_REASON = "NO_EXPLICIT_PREVIEW_GRANT"


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _bounded_body(value: Any, limit: int) -> str:
    """Bounded draft text: keep the author's own line breaks, never reflow them."""

    return str(value or "")[:limit].strip()


def _int_or_none(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return int(value)


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _parse_iso(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def next_business_day(clock: Mapping[str, Any]) -> dict[str, Any]:
    """业务时钟的「下一个本地日」；缺时钟时退回系统时钟并具名，不混用 host 日期。

    ``clock`` 是 ``phone_service.today_business_day`` 的答案：``logical_at`` 带实例自己的
    时区偏移。``due_at`` 是 FutureItems 的 UTC ISO，筛选时换算到同一个偏移再比日期，
    所以不是简单的 ``now + 24h``。
    """

    moment = _parse_iso(clock.get("logical_at"))
    if moment is None:
        moment = _utc_now()
        reason = "BUSINESS_CLOCK_UNREADABLE"
        source = "system_clock"
    else:
        reason = clock.get("reason_code")
        source = str(clock.get("source") or "business_clock")
    tz = moment.tzinfo or timezone.utc
    day = (moment.astimezone(tz).date() + timedelta(days=1)).isoformat()
    return {
        "local_day": day,
        "timezone_offset": moment.astimezone(tz).isoformat(timespec="seconds")[-6:],
        "source": source,
        "reason_code": reason,
        "basis": "next_business_local_day",
    }


def falls_on_local_day(value: Any, *, local_day: str, offset: str) -> bool:
    """A due instant's date in the instance's business offset equals ``local_day``."""

    moment = _parse_iso(value)
    if moment is None or not local_day:
        return False
    try:
        tz = datetime.fromisoformat("2000-01-01T00:00:00" + offset).tzinfo
    except ValueError:
        tz = timezone.utc
    return moment.astimezone(tz).date().isoformat() == local_day


# ---------------------------------------------------------------------------------------------
# 木牌四态
# ---------------------------------------------------------------------------------------------


def classify_door(*, location: Mapping[str, Any], world_ok: bool,
                  world_reason: str, canonical: Mapping[str, Any],
                  body: Mapping[str, Any]) -> dict[str, Any]:
    """四态判定。每一档都带实际依据；证据不齐就是 unknown/unavailable。

    * 位置可信且 ``home=False`` -> OUT_IN_TOWN
    * 在家且 owner 恢复会话 ``active`` + ``sleep_like`` -> RESTING
      （普通 ``rest`` 是醒着的休息，不单独算睡着；依据写在 evidence 里）
    * 在家且有 ACTIVE 前台 Activity -> FOCUSED_WORK
    * 在家、恢复会话可读且不在睡、前台没有 ACTIVE Activity -> AT_HOME_OPEN
    * 位置或 canonical 读不到 / Body 恢复事实读不到 -> 不默认开放
    """

    evidence: dict[str, Any] = {
        "location": {
            "available": bool(world_ok and str(location.get("status") or "") == "available"),
            "home": location.get("home"),
            "place_id": location.get("place_id"),
            "area_id": location.get("area_id"),
            "name": location.get("name"),
            "reason_code": None if world_ok else (world_reason or "WORLD_UNAVAILABLE"),
        },
        "body": {
            "status": body.get("status"),
            "recovery_state": None,
            "recovery_mode": None,
        },
        "activity": {
            "available": bool(canonical.get("available")),
            "foreground_ref": None,
            "foreground_status": None,
            "foreground_kind": None,
            "interruptibility": None,
            "reason_code": canonical.get("reason_code") or None,
        },
        "resting_basis": None,
        "ordinary_rest_is_not_sleep": True,
        "source": "world.snapshot + canonical_life_state + body.recovery",
    }
    recovery = body.get("recovery") if isinstance(body.get("recovery"), Mapping) else None
    if isinstance(recovery, Mapping):
        evidence["body"]["recovery_state"] = _text(recovery.get("state"), 24) or None
        evidence["body"]["recovery_mode"] = _text(recovery.get("mode"), 24) or None
    foreground = canonical.get("foreground") if isinstance(canonical.get("foreground"),
                                                           Mapping) else None
    if isinstance(foreground, Mapping):
        evidence["activity"].update({
            "foreground_ref": foreground.get("ref"),
            "foreground_status": foreground.get("status"),
            "foreground_kind": foreground.get("kind"),
            "interruptibility": foreground.get("interruptibility"),
        })

    def answer(sign: Optional[str], status: str, reason: Optional[str]) -> dict[str, Any]:
        return {
            "door_sign": sign,
            "door_status": status,
            "door_reason_code": reason,
            "evidence": evidence,
        }

    if not world_ok or str(location.get("status") or "") != "available":
        return answer(None, DOOR_STATUS_UNAVAILABLE,
                      str(location.get("reason_code") or world_reason or "LOCATION_UNAVAILABLE"))
    home = location.get("home")
    if home is False:
        return answer(DOOR_OUT_IN_TOWN, DOOR_STATUS_OK, None)
    if home is not True:
        # 老 World 没给 home 标记时，不猜「应该在家」。
        return answer(None, DOOR_STATUS_UNKNOWN, "LOCATION_HOME_FLAG_MISSING")
    if not canonical.get("available"):
        return answer(None, DOOR_STATUS_UNAVAILABLE,
                      str(canonical.get("reason_code") or "CANONICAL_STATE_UNAVAILABLE"))
    if evidence["body"]["recovery_state"] == "active" \
            and evidence["body"]["recovery_mode"] == "sleep_like":
        evidence["resting_basis"] = "OWNER_RECOVERY_ACTIVE_SLEEP_LIKE"
        return answer(DOOR_RESTING, DOOR_STATUS_OK, None)
    if isinstance(foreground, Mapping) and str(foreground.get("status") or "") == "ACTIVE":
        return answer(DOOR_FOCUSED_WORK, DOOR_STATUS_OK, None)
    if evidence["body"]["recovery_state"] is None:
        # 恢复会话事实读不到：此刻可能正在睡，不能默认在家开放。
        return answer(None, DOOR_STATUS_UNAVAILABLE, "BODY_RECOVERY_UNAVAILABLE")
    if evidence["body"]["recovery_state"] == "active" \
            and evidence["body"]["recovery_mode"] == "rest":
        # 醒着的普通休息：不是睡着；有前台活动时上面的 ACTIVE 分支已优先。
        evidence["resting_basis"] = "OWNER_RECOVERY_ACTIVE_REST_NOT_SLEEP"
    return answer(DOOR_AT_HOME_OPEN, DOOR_STATUS_OK, None)


# ---------------------------------------------------------------------------------------------
# 桌面 / 通信
# ---------------------------------------------------------------------------------------------


def foreground_work_identity(activity: Mapping[str, Any]) -> tuple[Optional[str], Optional[str]]:
    """The one artifact object the canonical foreground Activity is provably working on.

    The Activity owner records a single-valued binding (``target_ref`` / ``checkpoint_ref`` /
    ``origin_ref``) before the *whole* observed target set in ``subject_refs``.  A work
    Activity's ``subject_refs`` may also carry its head revision, which is dropped because a
    revision is not an object.  So this reads, in the owner's own order:

    1. exactly one distinct artifact object among the single-valued bindings -> that object;
    2. otherwise exactly one distinct artifact object among all refs -> that object;
    3. otherwise ``(None, reason)`` -- never the first item of ``subject_refs``.

    A reading Activity names both its material and the private-items document; that stays
    ambiguous here (the caller excludes the material anyway).  World targets and unknown
    namespaces yield no artifact and therefore no current work.
    """

    try:
        from life_step_readiness import canonical_target_ref, is_artifact_target
    except Exception:
        return None, "TARGET_NORMALIZER_UNAVAILABLE"

    single: list[str] = []
    for key in ("target_ref", "checkpoint_ref", "origin_ref"):
        canonical = canonical_target_ref(activity.get(key))
        if canonical and is_artifact_target(canonical) and canonical not in single:
            single.append(canonical)
    if len(single) == 1:
        return single[0], None
    if len(single) > 1:
        return None, "FOREGROUND_TARGET_AMBIGUOUS"

    observed: list[str] = []
    for ref in activity.get("subject_refs") or ():
        canonical = canonical_target_ref(ref)
        if canonical and is_artifact_target(canonical) and canonical not in observed:
            observed.append(canonical)
    if not observed:
        return None, "FOREGROUND_TARGET_UNKNOWN"
    if len(observed) > 1:
        return None, "FOREGROUND_TARGET_AMBIGUOUS"
    return observed[0], None


def work_block(project: Mapping[str, Any]) -> dict[str, Any]:
    """The bound current work: id + head version, never its text."""

    if not project or not project.get("ok"):
        return {
            "available": False,
            "reason_code": _text(project.get("reason_code"), 64) or "WORK_NOT_READABLE",
            "artifact_id": project.get("artifact_id") if project else None,
        }
    row = project.get("project") if isinstance(project.get("project"), Mapping) else {}
    return {
        "available": True,
        "artifact_id": row.get("artifact_id") or project.get("artifact_id"),
        "title": _text(row.get("title"), 120) or None,
        "head_version_id": row.get("head_version_id"),
        "version_number": _int_or_none(row.get("version_number")),
        "private_text_included": False,
    }


def reading_block(material: Mapping[str, Any]) -> dict[str, Any]:
    """Reader progress + unclear count only; no page text and no annotation text."""

    data = material.get("data") if isinstance(material.get("data"), Mapping) else {}
    if not material.get("ok") or data.get("available") is False:
        return {
            "available": False,
            "reason_code": _text(material.get("reason_code"), 64)
            or "READING_MATERIAL_UNAVAILABLE",
        }
    annotations = [row for row in (data.get("annotations") or []) if isinstance(row, Mapping)]
    unclear = [row for row in annotations if str(row.get("kind") or "") == "unclear"]
    bookmark = data.get("bookmark") if isinstance(data.get("bookmark"), Mapping) else None
    return {
        "available": True,
        "artifact_id": data.get("artifact_id"),
        "title": _text(data.get("title"), 120) or None,
        "page": _int_or_none(data.get("page")),
        "pages": _int_or_none(data.get("pages")),
        "unclear_count": len(unclear),
        "note_count": len(annotations) - len(unclear),
        "bookmark_page": _int_or_none((bookmark or {}).get("page")),
        "bookmark_current": None if bookmark is None else not bool(bookmark.get("needs_reread")),
        "private_text_included": False,
    }


def communication_block(*, door: Mapping[str, Any], canonical: Mapping[str, Any]) -> dict[str, Any]:
    """LR-3 的通信可用性；没有前台/正在睡时如实给 null + 原因。"""

    foreground = canonical.get("foreground") if isinstance(canonical.get("foreground"),
                                                           Mapping) else None
    interruptibility = None
    if isinstance(foreground, Mapping):
        interruptibility = _text(foreground.get("interruptibility"), 32) or None
    interrupt = {
        "value": interruptibility,
        "reason_code": None if interruptibility else (
            "CANONICAL_STATE_UNAVAILABLE" if not canonical.get("available")
            else "NO_FOREGROUND_ACTIVITY"),
        "source": "canonical_life_state.foreground.interruptibility",
    }
    if door.get("door_sign") == DOOR_RESTING:
        return {
            "interruptibility": interrupt,
            "communication_availability": {
                "value": None,
                "reason_code": "RESTING_BODY_SESSION_NO_LR3_ASSESSMENT",
                "source": "activity_interruption.InterruptionPolicy",
            },
        }
    if not canonical.get("available"):
        return {
            "interruptibility": interrupt,
            "communication_availability": {
                "value": None,
                "reason_code": "CANONICAL_STATE_UNAVAILABLE",
                "source": "activity_interruption.InterruptionPolicy",
            },
        }
    try:
        from activity_interruption import InterruptionPolicy

        life_state = str((foreground or {}).get("status") or "IDLE")
        attention = interruptibility or "LIGHT"
        value = InterruptionPolicy.compute_communication_availability(
            life_state=life_state, attention_occupancy=attention,
            interruptibility=attention)
        return {
            "interruptibility": interrupt,
            "communication_availability": {
                "value": value,
                "reason_code": None,
                "source": "activity_interruption.InterruptionPolicy",
            },
        }
    except Exception:
        return {
            "interruptibility": interrupt,
            "communication_availability": {
                "value": None,
                "reason_code": "LR3_POLICY_UNAVAILABLE",
                "source": "activity_interruption.InterruptionPolicy",
            },
        }


# ---------------------------------------------------------------------------------------------
# 未发短笺 / 明日便签
# ---------------------------------------------------------------------------------------------


def retained_share_rows(listing: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Flatten one share listing into retained rows + counts.  Never carries the text."""

    data = listing.get("data") if isinstance(listing.get("data"), Mapping) else {}
    rows: list[dict[str, Any]] = []
    counts = {"DRAFT": 0, "DEFERRED": 0, "DROPPED": 0}
    for raw in data.get("shares") or ():
        if not isinstance(raw, Mapping):
            continue
        state = str(raw.get("state") or "")
        if state not in RETAINED_SHARE_STATES:
            continue
        counts[state] = counts.get(state, 0) + 1
        source = raw.get("source") if isinstance(raw.get("source"), Mapping) else {}
        rows.append({
            "share_id": raw.get("share_id"),
            "state": state,
            "state_derived": bool(raw.get("state_derived")),
            "head_version_id": raw.get("head_version_id"),
            "share_revision": _int_or_none(raw.get("share_revision")),
            "source_artifact_id": source.get("artifact_id"),
            "source_state": raw.get("source_state"),
            "text_chars": _int_or_none(raw.get("text_chars")),
            "text_withheld": True,
            "body_withheld": True,
            "visibility": "local:owner",
            "visible_to_partner": False,
            "allowed_actions": list(raw.get("actions") or []),
            "updated_at": raw.get("updated_at"),
            "created_at": raw.get("created_at") or raw.get("updated_at"),
        })
    return rows, {
        "counts": counts,
        "retained_count": len(rows),
        "truncated": bool(data.get("truncated")),
    }


def share_detail_private(detail: Mapping[str, Any]) -> dict[str, Any]:
    """From an owner detail answer, take only the bounded share text and lifecycle facts.

    The caller reached this only after a valid local capability.  Source text and the
    private-items document never travel here; ``share.text`` is the one bounded draft body.
    """

    data = detail.get("data") if isinstance(detail.get("data"), Mapping) else {}
    share = data.get("share") if isinstance(data.get("share"), Mapping) else {}
    out: dict[str, Any] = {
        "text": _bounded_body(share.get("text"), 2000),
        "title": _text(share.get("title"), 80) or None,
        "text_chars": _int_or_none(share.get("text_chars")),
        "private_text_included": bool(data.get("private_text_included")),
    }
    deferred = share.get("deferred") if isinstance(share.get("deferred"), Mapping) else {}
    if deferred:
        out["defer_condition"] = _text(deferred.get("condition"), 200) or None
        out["opportunity_ref"] = _text(deferred.get("opportunity_ref"), 200) or None
        out["deferred_at"] = deferred.get("deferred_at")
    dropped = share.get("dropped") if isinstance(share.get("dropped"), Mapping) else {}
    if dropped:
        out["dropped_reason"] = _text(dropped.get("reason"), 120) or None
        out["dropped_at"] = dropped.get("at")
    return out


def tomorrow_row(item: Mapping[str, Any], *, with_title: bool) -> dict[str, Any]:
    row: dict[str, Any] = {
        "item_ref": item.get("item_ref"),
        "due_at": item.get("due_at"),
        "status": item.get("status"),
        "revision": _int_or_none(item.get("revision")),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "title_withheld": not with_title,
        "private_text_included": bool(with_title),
    }
    if with_title:
        row["title"] = _text(item.get("title"), 120)
    return row


__all__ = [
    "DOOR_AT_HOME_OPEN",
    "DOOR_FOCUSED_WORK",
    "DOOR_OUT_IN_TOWN",
    "DOOR_RESTING",
    "DOOR_SIGNS",
    "NOTES_SCHEMA",
    "PARTNER_PREVIEW_REASON",
    "PRESENCE_SCHEMA",
    "RETAINED_SHARE_STATES",
    "classify_door",
    "communication_block",
    "falls_on_local_day",
    "foreground_work_identity",
    "next_business_day",
    "reading_block",
    "retained_share_rows",
    "share_detail_private",
    "tomorrow_row",
    "work_block",
]
