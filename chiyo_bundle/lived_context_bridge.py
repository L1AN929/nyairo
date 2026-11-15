#!/usr/bin/env python3
"""实时具身与日常感知只读投影桥（Zero-Write / 超时受控 / 字节有界）。

它做什么：
- 把 ``0.2``（居家穿搭/重逢锚点/共同习惯/待定小约定）与 ``0.3``（角色包动态名字代词、
  五感与躯体冷暖照顾、v03 生病负荷/经济分量/双手占用与同屋共处、书桌共在/桌面茶点/容器印象、
  自主拒绝立场锁/挂着的话题/情绪张力、36B 成年亲密状态与尺度边界）浓缩成一段 ``<= 1800`` 字符的
  只读背景事实，供 ``hermes_plugin.ChiyoContextEngine.select_context`` 注入对话上下文。

四条硬约束：
1. **严格零写入（Zero-Write）**：只走 loopback ``GET`` 或本地 ``day_root/phone/*.json`` 纯只读解析，
   绝不创建目录、绝不改写任何文件、绝不推进或触发任何状态副作用。
2. **超时受控与静默降级**：整轮单调时钟总预算 ``<= 0.8s``；手机服务未启动、端口不可达或未配置本地
   状态目录时静默返回 ``""``，绝不抛异常阻塞聊天。
3. **动态中立身份（拒绝写死「千代/她」）**：名字与代词统一从角色包读取，缺省回退 ``TA``。
4. **36B 隐私铁律**：绝不读取、拼装或暴露 ``solo`` / ``solo_slot`` / ``solo_ledger`` 无记忆独处槽；
   亦绝不把 ``TRANSCRIPT`` / ``MOMENT`` 原文拉进普通上下文。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional
from urllib.parse import urlsplit

MAX_LIVED_CONTEXT_CHARS = 1800
TOTAL_TIMEOUT_BUDGET_S = 0.8
CONCURRENT_HTTP_BUDGET_S = 0.42
PROBE_TIMEOUT_S = 0.22
DEFAULT_HINT_TTL_S = 90.0
TIGHT_BUDGET_CENTS_THRESHOLD = 2500
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost"})

_RE_INJECTED_ENVELOPE_WITH_MARKER = re.compile(
    r"^\[当前真实处境与自主节律（[^\]]*）\]\r?\n[\s\S]*?\r?\n【伴侣此刻发来的原话】\r?\n"
)
_RE_INJECTED_ENVELOPE_BLOCK = re.compile(
    r"^\[当前真实处境与自主节律（(?:非对方台词|仅本轮生效)[^\]]*）\]\r?\n[\s\S]*?\r?\n\r?\n"
)
_RE_INJECTED_PARTNER_HEADER = re.compile(
    r"^【伴侣此刻发来的原话】\r?\n"
)


def strip_injected_turn_envelope(text: str) -> str:
    """Strip legacy/internal turn guidance envelopes so only genuine user words remain."""
    if not isinstance(text, str) or not text:
        return text if isinstance(text, str) else ""
    cur = text
    for _ in range(16):
        nxt = _RE_INJECTED_ENVELOPE_WITH_MARKER.sub("", cur, count=1)
        if nxt == cur:
            nxt = _RE_INJECTED_ENVELOPE_BLOCK.sub("", cur, count=1)
        if nxt == cur:
            nxt = _RE_INJECTED_PARTNER_HEADER.sub("", cur, count=1)
        if nxt == cur:
            break
        cur = nxt
    return cur


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


# ---------------------------------------------------------------------------------------------
# Pure read-only helpers (NEVER write or create anything on disk)
# ---------------------------------------------------------------------------------------------


def _one_line(value: Any, limit: int = 80) -> str:
    if value is None:
        return ""
    text = " ".join(str(value).replace("\x00", "").split()).strip()
    return text[:limit]


def _parse_iso(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _read_json_readonly(path: Optional[Path]) -> Optional[dict[str, Any]]:
    if path is None:
        return None
    try:
        if path.is_symlink() or not path.is_file():
            return None
        raw = path.read_text(encoding="utf-8")
        parsed = json.loads(raw)
    except (OSError, ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _valid_loopback_base_url(raw_url: Any) -> Optional[str]:
    text = str(raw_url or "").strip()
    if not text:
        return None
    parts = urlsplit(text)
    if parts.scheme != "http" or (parts.hostname or "").lower() not in LOOPBACK_HOSTS:
        return None
    if not parts.port or parts.path not in ("", "/") or parts.query or parts.fragment:
        return None
    return f"http://{(parts.hostname or '').lower()}:{int(parts.port)}"


def _resolve_capability_readonly(
    home: Path, phone_cfg: Mapping[str, Any], base_url: str
) -> Optional[str]:
    direct = str(phone_cfg.get("capability") or "").strip()
    if len(direct) >= 16 and " " not in direct and "\n" not in direct and "\r" not in direct:
        return direct

    cap_file = str(phone_cfg.get("capability_file") or "").strip()
    profile_home = str(phone_cfg.get("profile_home") or "").strip()
    candidate: Optional[Path] = None
    if cap_file:
        p = Path(cap_file).expanduser()
        candidate = p if p.is_absolute() else (home / p)
    elif profile_home:
        candidate = Path(profile_home).expanduser() / "run" / "phone-capability.json"

    doc = _read_json_readonly(candidate)
    if not isinstance(doc, Mapping):
        return None
    if str(doc.get("endpoint") or "").strip() != base_url:
        return None
    token = str(doc.get("token") or "").strip()
    if len(token) >= 16 and " " not in token:
        return token
    return None


def _http_get_json(
    base_url: str,
    path: str,
    *,
    capability: Optional[str],
    send_model_actor: bool = True,
    deadline: float,
    is_probe: bool = False,
) -> tuple[Optional[int], Optional[dict[str, Any]]]:
    remaining = deadline - time.monotonic()
    if remaining <= 0.015:
        return None, None
    timeout = min(PROBE_TIMEOUT_S if is_probe else 0.25, remaining)

    def _do(cap: Optional[str]) -> tuple[Optional[int], Optional[dict[str, Any]]]:
        req = urllib.request.Request(base_url + path, method="GET")
        req.add_header("Accept", "application/json")
        if send_model_actor:
            req.add_header("X-Phone-Actor", "model")
        if cap:
            req.add_header("Authorization", "Bearer " + cap)
            req.add_header("X-Phone-Capability", cap)
        try:
            opener = urllib.request.build_opener(_NoRedirect)
            with opener.open(req, timeout=timeout) as resp:
                raw = resp.read()
                code = int(resp.status)
        except urllib.error.HTTPError as err:
            try:
                raw = err.read()
            except Exception:
                raw = b""
            code = int(err.code)
        except (urllib.error.URLError, TimeoutError, OSError):
            return None, None
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except Exception:
            parsed = None
        return code, (parsed if isinstance(parsed, dict) else None)

    code, doc = _do(capability)
    if code == 403 and capability and (deadline - time.monotonic()) > 0.02:
        # Server may run with capability=None; cleanly fall back to default read without forging.
        code, doc = _do(None)
    return code, doc


def _unwrap_data(doc: Optional[Mapping[str, Any]]) -> Optional[dict[str, Any]]:
    if not isinstance(doc, Mapping):
        return None
    if doc.get("ok") is False:
        return None
    inner = doc.get("data")
    if isinstance(inner, Mapping):
        return dict(inner)
    return dict(doc)


def _resolve_day_root_candidates(home: Path, cfg: Mapping[str, Any]) -> list[Path]:
    """Ordered candidate day_root directories for ephemeral_turn_hint.json & local phone state."""
    phone_cfg = cfg.get("phone") if isinstance(cfg.get("phone"), Mapping) else {}
    out: list[Path] = []

    def _add(p: Path) -> None:
        if p not in out:
            out.append(p)

    # 1. phone_cfg["day_root"] / cfg["day_root"]
    for raw_dr in (
        phone_cfg.get("day_root") if isinstance(phone_cfg, Mapping) else None,
        cfg.get("day_root"),
    ):
        if isinstance(raw_dr, (str, Path)) and str(raw_dr).strip():
            p = Path(str(raw_dr).strip()).expanduser()
            _add(p if p.is_absolute() else (home / p))

    # 2. Path(capability_file).parent.parent
    cap_file = str(phone_cfg.get("capability_file") or "").strip() if isinstance(phone_cfg, Mapping) else ""
    if cap_file:
        cp = Path(cap_file).expanduser()
        cp = cp if cp.is_absolute() else (home / cp)
        if len(cp.parents) >= 2:
            _add(cp.parent.parent)
            if cp.parent.parent.name == "one-day" and len(cp.parents) >= 3:
                _add(cp.parent.parent.parent)

    # 3. phone_cfg["profile_home"] (when it has a phone subdir, or its parent)
    prof_home = str(phone_cfg.get("profile_home") or "").strip() if isinstance(phone_cfg, Mapping) else ""
    if prof_home:
        ph = Path(prof_home).expanduser()
        ph = ph if ph.is_absolute() else (home / ph)
        _add(ph)
        if ph.name == "one-day":
            _add(ph.parent)

    # 4. cfg["world_home"]
    wh = cfg.get("world_home")
    if isinstance(wh, (str, Path)) and str(wh).strip():
        wp = Path(str(wh).strip()).expanduser()
        _add(wp if wp.is_absolute() else (home / wp))

    return out


def consume_ephemeral_turn_hint(home: Path, cfg: Mapping[str, Any]) -> str:
    """Consume one-shot ``<day_root>/phone/ephemeral_turn_hint.json`` if present & fresh.

    Never creates directories or files; if the file exists (fresh, expired, or malformed),
    unlinks it immediately so it can never leak into subsequent turns or background runs.
    """
    if not isinstance(cfg, Mapping):
        return ""
    home_path = Path(home)
    candidates: list[Path] = []
    for root in _resolve_day_root_candidates(home_path, cfg):
        for sub in (root / "phone" / "ephemeral_turn_hint.json", root / "one-day" / "phone" / "ephemeral_turn_hint.json"):
            if sub not in candidates:
                candidates.append(sub)
    for fallback_rel in (
        home_path / "one-day" / "phone" / "ephemeral_turn_hint.json",
        home_path / "phone" / "ephemeral_turn_hint.json",
    ):
        if fallback_rel not in candidates:
            candidates.append(fallback_rel)

    for hint_path in candidates:
        try:
            if hint_path.is_symlink() or not hint_path.is_file():
                continue
        except OSError:
            continue
        doc: Optional[dict[str, Any]] = None
        try:
            raw_txt = hint_path.read_text(encoding="utf-8")
            parsed = json.loads(raw_txt)
            if isinstance(parsed, dict):
                doc = parsed
        except Exception:
            doc = None
        finally:
            try:
                hint_path.unlink(missing_ok=True)
            except OSError:
                pass

        if not isinstance(doc, Mapping):
            continue
        try:
            created_at = float(doc.get("created_at_unix") or 0.0)
            ttl_sec = float(doc.get("ttl_seconds") or DEFAULT_HINT_TTL_S)
        except (TypeError, ValueError):
            continue
        if created_at <= 0.0 or abs(time.time() - created_at) > max(1.0, ttl_sec):
            continue
        guidance = str(doc.get("guidance") or "").strip()
        if guidance:
            return "[当前真实处境与自主节律（仅本轮生效，请自然体现，勿复述本说明）]\n" + guidance
    return ""


def _locate_phone_state_dir(
    home: Path, cfg: Mapping[str, Any]
) -> tuple[Optional[Path], bool]:
    """Return ``(phone_dir_or_none, explicitly_configured_root_exists)`` with 0 writes."""

    explicit_roots = _resolve_day_root_candidates(home, cfg)

    explicit_exists = False
    candidates: list[Path] = []
    for root in explicit_roots:
        try:
            if not root.is_symlink() and root.is_dir():
                explicit_exists = True
                candidates.extend([
                    root / "phone",
                    root / "one-day" / "phone",
                    root.parent / "phone",
                    root.parent / "one-day" / "phone",
                ])
        except OSError:
            continue

    # Also check non-intrusive standard subdirectories under ``home`` ONLY if they already exist
    for rel in (
        home / "one-day" / "phone",
        home / "chiyo" / "state" / "phone",
        home / "phone",
    ):
        candidates.append(rel)

    for cand in candidates:
        try:
            if not cand.is_symlink() and cand.is_dir():
                return cand, True
        except OSError:
            continue

    return None, explicit_exists


# ---------------------------------------------------------------------------------------------
# Section formatters (accept either HTTP projection dicts or raw stored JSON snapshots)
# ---------------------------------------------------------------------------------------------


def _extract_identity(
    char_http: Optional[Mapping[str, Any]],
    char_disk: Optional[Mapping[str, Any]],
    *,
    desktop_doc: Optional[Mapping[str, Any]] = None,
) -> tuple[str, str, list[str]]:
    name = ""
    pronoun = ""
    acc_labels: list[str] = []

    if isinstance(char_http, Mapping):
        active = (
            char_http.get("active_pack")
            if isinstance(char_http.get("active_pack"), Mapping)
            else char_http
        )
        name = _one_line(active.get("display_name") or active.get("name"), 32)
        pronoun = _one_line(active.get("pronouns") or active.get("pronoun"), 16)
        accs = (
            active.get("everyday_accessories")
            or char_http.get("everyday_accessories")
            or []
        )
        if isinstance(accs, list):
            for item in accs[:3]:
                if isinstance(item, Mapping) and item.get("label"):
                    mode = _one_line(item.get("mode"), 20)
                    lbl = _one_line(item.get("label"), 24)
                    acc_labels.append(f"{lbl}({mode})" if mode else lbl)

    if (not name or name == "TA" or not pronoun or pronoun == "TA") and isinstance(char_disk, Mapping):
        pack_doc = (
            char_disk.get("pack_doc")
            if isinstance(char_disk.get("pack_doc"), Mapping)
            else (char_disk.get("pack") if isinstance(char_disk.get("pack"), Mapping) else char_disk)
        )
        if not name or name == "TA":
            disk_name = _one_line(pack_doc.get("display_name") or pack_doc.get("name"), 32)
            if disk_name:
                name = disk_name
        if not pronoun or pronoun == "TA":
            disk_pron = _one_line(pack_doc.get("pronouns") or pack_doc.get("pronoun"), 16)
            if disk_pron:
                pronoun = disk_pron
        if not acc_labels:
            accs = (
                char_disk.get("everyday_accessories")
                or pack_doc.get("starter_accessories")
                or []
            )
            if isinstance(accs, list):
                for item in accs[:3]:
                    if isinstance(item, Mapping) and item.get("label"):
                        mode = _one_line(item.get("mode"), 20)
                        lbl = _one_line(item.get("label"), 24)
                        acc_labels.append(f"{lbl}({mode})" if mode else lbl)

    # Fallback to <hermes_home>/.nyairo-desktop.json when character pack is still unset / default "TA"
    if (not name or name in ("TA", "默认角色", "示例角色") or not pronoun or pronoun == "TA") and isinstance(desktop_doc, Mapping):
        nested_char = desktop_doc.get("character") if isinstance(desktop_doc.get("character"), Mapping) else {}
        if not name or name in ("TA", "默认角色", "示例角色"):
            desk_name = _one_line(
                desktop_doc.get("name")
                or desktop_doc.get("character_name")
                or desktop_doc.get("display_name")
                or nested_char.get("name")
                or nested_char.get("display_name"),
                32,
            )
            if desk_name:
                name = desk_name
        if not pronoun or pronoun == "TA":
            desk_pron = _one_line(
                desktop_doc.get("pronoun")
                or desktop_doc.get("pronouns")
                or nested_char.get("pronoun")
                or nested_char.get("pronouns"),
                16,
            )
            if desk_pron:
                pronoun = desk_pron

    return (name or "TA"), (pronoun or "TA"), acc_labels


def _extract_persona_temperament(
    char_http: Optional[Mapping[str, Any]],
    char_disk: Optional[Mapping[str, Any]],
) -> str:
    """Extract non-empty ``persona.core`` / ``persona.conversation`` temperament without hardcoding any persona."""
    core_txt = ""
    conv_txt = ""
    for src in (char_http, char_disk):
        if not isinstance(src, Mapping):
            continue
        active = src.get("active_pack") if isinstance(src.get("active_pack"), Mapping) else {}
        psum = active.get("persona_summary") if isinstance(active.get("persona_summary"), Mapping) else {}
        exp_pack = src.get("exportable_pack") if isinstance(src.get("exportable_pack"), Mapping) else {}
        pack_doc = (
            src.get("pack_doc")
            if isinstance(src.get("pack_doc"), Mapping)
            else (src.get("pack") if isinstance(src.get("pack"), Mapping) else exp_pack)
        )
        p_obj = pack_doc.get("persona") if isinstance(pack_doc.get("persona"), Mapping) else psum
        if not core_txt:
            core_txt = _one_line(psum.get("core") or p_obj.get("core"), 34)
        if not conv_txt:
            conv_txt = _one_line(psum.get("conversation") or p_obj.get("conversation"), 34)
    bits = [b for b in (core_txt, conv_txt) if b]
    return " / ".join(bits[:2])


_WINDOW_ZH = {"open": "开窗", "ajar": "虚掩", "closed": "关窗", "outdoor_direct": "户外直通"}
_ZONE_ZH = {"window_side": "窗边", "desk_side": "书桌旁", "bed_side": "床边"}
_TEMP_ZH = {"hot": "滚烫", "warm": "温热", "tepid": "微温", "room_temp": "常温", "cooled": "已放凉"}


def _format_sensory_somatic(
    *,
    sensory_http: Optional[Mapping[str, Any]],
    somatic_http: Optional[Mapping[str, Any]],
    gw_http: Optional[Mapping[str, Any]],
    care_http: Optional[Mapping[str, Any]],
    somatic_disk: Optional[Mapping[str, Any]],
    sensory_disk: Optional[Mapping[str, Any]],
    now_dt: datetime,
) -> Optional[str]:
    parts: list[str] = []

    # Window & position & garment
    win_state = ""
    zone = ""
    garment_desc = ""
    if isinstance(gw_http, Mapping):
        win_state = _one_line((gw_http.get("window") or {}).get("state"), 20)
        zone = _one_line((gw_http.get("local_position") or {}).get("zone"), 20)
        g = gw_http.get("garment") if isinstance(gw_http.get("garment"), Mapping) else {}
        if g.get("status") == "available" and g.get("state") in ("wear", "hang"):
            glbl = _one_line(g.get("label") or g.get("garment_id"), 24)
            gdry = {"damp": "微潮", "dry": "干爽"}.get(str(g.get("dryness") or ""), "")
            gact = "穿着" if g.get("state") == "wear" else "挂起晾着"
            garment_desc = f"{gact}{gdry}外衣「{glbl}」"
    elif isinstance(somatic_disk, Mapping):
        win_state = _one_line((somatic_disk.get("window") or {}).get("state"), 20)
        zone = _one_line((somatic_disk.get("local_position") or {}).get("zone"), 20)
        g = somatic_disk.get("garment") if isinstance(somatic_disk.get("garment"), Mapping) else {}
        if g.get("state") in ("wear", "hang") and (g.get("label") or g.get("garment_id")):
            glbl = _one_line(g.get("label") or g.get("garment_id"), 24)
            gdry = {"damp": "微潮", "dry": "干爽"}.get(str(g.get("dryness") or ""), "")
            gact = "穿着" if g.get("state") == "wear" else "挂起晾着"
            garment_desc = f"{gact}{gdry}外衣「{glbl}」"

    if win_state in _WINDOW_ZH:
        parts.append(f"窗户{_WINDOW_ZH[win_state]}")
    if zone in _ZONE_ZH:
        parts.append(f"位置在{_ZONE_ZH[zone]}")
    if garment_desc:
        parts.append(garment_desc)

    # Somatic modalities (thermal / moisture / posture / tactile)
    mods: Mapping[str, Any] = {}
    if isinstance(somatic_http, Mapping) and isinstance(somatic_http.get("modalities"), Mapping):
        mods = somatic_http["modalities"]
    elif isinstance(sensory_http, Mapping):
        ch = sensory_http.get("channels") if isinstance(sensory_http.get("channels"), Mapping) else sensory_http
        if isinstance(ch.get("tactile_thermal"), Mapping):
            mods = ch["tactile_thermal"]

    mod_bits: list[str] = []
    for m_key, m_lbl in (("thermal", "冷暖"), ("moisture", "干湿"), ("posture", "姿势支撑"), ("tactile", "触感")):
        m = mods.get(m_key)
        if isinstance(m, Mapping) and m.get("status") == "available" and m.get("quality"):
            mod_bits.append(f"{m_lbl}:{_one_line(m.get('quality'), 28)}")
    if mod_bits:
        parts.append("体感(" + "、".join(mod_bits[:4]) + ")")

    # Care offerings (drink & cushion)
    drink_row: Optional[Mapping[str, Any]] = None
    cushion_row: Optional[Mapping[str, Any]] = None
    if isinstance(care_http, Mapping):
        if isinstance(care_http.get("drink"), Mapping):
            drink_row = care_http["drink"]
        if isinstance(care_http.get("cushion"), Mapping):
            cushion_row = care_http["cushion"]
    elif isinstance(somatic_disk, Mapping) and isinstance(somatic_disk.get("care"), Mapping):
        care_raw = somatic_disk["care"]
        if isinstance(care_raw.get("drink"), Mapping):
            drink_row = care_raw["drink"]
        if isinstance(care_raw.get("cushion"), Mapping):
            cushion_row = care_raw["cushion"]

    if isinstance(drink_row, Mapping) and drink_row.get("state") in ("available", "taken", "offered", "in_hand", "at_desk"):
        dkind = _one_line(drink_row.get("bound_item_label") or drink_row.get("kind") or "热饮", 20)
        dside = "伴侣递的" if drink_row.get("actor_side") == "partner" else "手边的"
        temp_obj = drink_row.get("temperature") if isinstance(drink_row.get("temperature"), Mapping) else {}
        band = str(temp_obj.get("band") or "")
        if not band and temp_obj.get("placed_at"):
            placed_dt = _parse_iso(temp_obj.get("placed_at"))
            cooling_s = int(temp_obj.get("cooling_seconds") or 2700)
            if placed_dt is not None:
                elapsed = max(0.0, (now_dt - placed_dt).total_seconds())
                band = "warm" if elapsed < cooling_s * 0.5 else ("tepid" if elapsed < cooling_s else "room_temp")
        t_zh = _TEMP_ZH.get(band, "温热")
        st_zh = "捧在手里" if drink_row.get("state") in ("taken", "in_hand") else "搁在手边"
        parts.append(f"{dside}{dkind}({t_zh}·{st_zh})")

    if isinstance(cushion_row, Mapping) and cushion_row.get("state") in ("available", "in_use", "used"):
        cside = "伴侣垫的" if cushion_row.get("actor_side") == "partner" else ""
        ckind = _one_line(cushion_row.get("kind") or "坐垫", 16)
        cst = "正靠着支撑" if cushion_row.get("support_active") or cushion_row.get("state") in ("in_use", "used") else "放在旁边"
        parts.append(f"{cside}{ckind}({cst})")

    # 5-channel sensory (auditory / visual / olfactory / load / partner note)
    if isinstance(sensory_http, Mapping):
        ch = sensory_http.get("channels") if isinstance(sensory_http.get("channels"), Mapping) else sensory_http
        aud = ch.get("auditory") if isinstance(ch.get("auditory"), Mapping) else {}
        vis = ch.get("visual") if isinstance(ch.get("visual"), Mapping) else {}
        olf = ch.get("olfactory") if isinstance(ch.get("olfactory"), Mapping) else {}
        load = (
            sensory_http.get("sensory_load")
            if isinstance(sensory_http.get("sensory_load"), Mapping)
            else (ch.get("sensory_load") if isinstance(ch.get("sensory_load"), Mapping) else {})
        )
        q_band = _one_line(aud.get("quietness_band"), 20)
        sounds: list[str] = []
        for s in (aud.get("local_sounds") or [])[:2]:
            if isinstance(s, Mapping) and s.get("summary"):
                sounds.append(_one_line(s["summary"], 28))
        for s in (aud.get("adjacent_unseen_sounds") or [])[:1]:
            if isinstance(s, Mapping) and s.get("summary"):
                sounds.append("邻处:" + _one_line(s["summary"], 24))
        if sounds:
            parts.append("听觉:" + "；".join(sounds))
        elif q_band and q_band != "unknown":
            parts.append(f"声景:{q_band}")

        l_band = _one_line(vis.get("lighting_band"), 24)
        if l_band and l_band != "unknown":
            parts.append(f"光线:{l_band}")

        scents = [
            _one_line(c.get("summary"), 28)
            for c in (olf.get("cues") or [])[:2]
            if isinstance(c, Mapping) and c.get("summary")
        ]
        if scents:
            parts.append("气味:" + "、".join(scents))

        load_band = _one_line(load.get("band") or load.get("level"), 20)
        if load_band and load_band not in ("unknown", ""):
            parts.append(f"感官负担:{load_band}")

    if isinstance(sensory_disk, Mapping):
        pnotes = sensory_disk.get("partner_sensory_notes") or []
        if isinstance(pnotes, list) and pnotes:
            last_n = pnotes[-1]
            if isinstance(last_n, Mapping) and last_n.get("text"):
                parts.append(f"感官留笺:「{_one_line(last_n['text'], 30)}」")

    if not parts:
        return None
    return "- 五感与躯体体感：" + "；".join(parts)


def _format_v03_illness_economy_hands(
    *,
    v03_clock: Optional[Mapping[str, Any]],
    v03_ecology: Optional[Mapping[str, Any]],
    v03_social: Optional[Mapping[str, Any]],
    stance_doc: Optional[Mapping[str, Any]],
    intimacy_doc: Optional[Mapping[str, Any]],
    tension_doc: Optional[Mapping[str, Any]],
) -> Optional[str]:
    bits: list[str] = []

    # 1. Illness / somatic load
    illness_desc = ""
    for src in (v03_ecology, v03_social, v03_clock):
        if not isinstance(src, Mapping):
            continue
        ill = (
            src.get("illness")
            or src.get("body_health")
            or src.get("health_state")
            or (src.get("body") if isinstance(src.get("body"), Mapping) else None)
        )
        if isinstance(ill, Mapping):
            st = str(
                ill.get("phase")
                or ill.get("status")
                or ill.get("health")
                or ill.get("stage")
                or ""
            ).upper()
            if st in ("PRODROMAL", "PRODROME", "CHILL_PRODROME"):
                illness_desc = "着凉前兆（精力收窄）"
                break
            if st in ("ACUTE_ILLNESS", "FEVER", "BEDREST", "ACUTE", "SICK"):
                illness_desc = "急性生病/发烧卧床休息期"
                break
            if st in ("CONVALESCENT", "RECOVERY", "RECOVERING"):
                illness_desc = "病后恢复期（体力慢恢复中）"
                break
            if st == "STRAINED":
                illness_desc = "连续劳作高负荷疲劳"
                break
        elif isinstance(src.get("health"), str) and src["health"].upper() in (
            "PRODROMAL", "ACUTE_ILLNESS", "CONVALESCENT", "STRAINED"
        ):
            illness_desc = f"身体负荷状态:{src['health']}"
            break

    if not illness_desc and isinstance(intimacy_doc, Mapping):
        b = intimacy_doc.get("body_and_desire") or intimacy_doc.get("body") or {}
        if isinstance(b, Mapping) and b.get("acute_illness") is True:
            illness_desc = "急性身体不适/生病休息中"

    if illness_desc:
        bits.append(f"{illness_desc}（感官收缩、体力受限，语气温和简短）")

    # 2. Economic weight / tight savings / independent fee settlement
    econ_bits: list[str] = []
    if isinstance(v03_ecology, Mapping):
        econ = v03_ecology.get("economy") if isinstance(v03_ecology.get("economy"), Mapping) else v03_ecology
        avail_raw = (
            econ.get("available_balance_cents")
            if econ.get("available_balance_cents") is not None
            else (
                econ.get("balance_cents")
                if econ.get("balance_cents") is not None
                else econ.get("wallet_balance_cents")
            )
        )
        budget_band = str(
            econ.get("budget_band") or econ.get("financial_mood") or econ.get("tightness") or ""
        ).lower()
        if isinstance(avail_raw, (int, float)) and not isinstance(avail_raw, bool):
            is_tight = float(avail_raw) < TIGHT_BUDGET_CENTS_THRESHOLD
        else:
            is_tight = (
                budget_band in ("tight", "saving", "restrained", "lean", "sacrificing")
                or econ.get("tight_budget") is True
                or econ.get("budget_tight") is True
            )
        if is_tight:
            econ_bits.append("手头攒钱克制期")
        desires = (
            econ.get("savings_goals")
            or econ.get("desire_objects")
            or econ.get("saving_goals")
            or []
        )
        if isinstance(desires, list):
            for d in desires[:1]:
                if isinstance(d, Mapping) and str(d.get("state") or d.get("status") or "ACTIVE").upper() not in ("OWNED", "DROPPED", "COMPLETED"):
                    dlbl = _one_line(d.get("item_title") or d.get("title") or d.get("label") or d.get("goal_id") or d.get("id"), 24)
                    if not dlbl:
                        continue
                    tgt_c = d.get("target_cents")
                    alloc_c = d.get("allocated_cents") if d.get("allocated_cents") is not None else d.get("saved_cents")
                    gap = d.get("gap") if d.get("gap") is not None else d.get("remaining_gap")
                    if gap is None and isinstance(tgt_c, (int, float)) and isinstance(alloc_c, (int, float)) and tgt_c > alloc_c:
                        gap = f"¥{(tgt_c - alloc_c) / 100:.0f}"
                    econ_bits.append(f"正攒钱盼着「{dlbl}」" + (f"(差额{gap})" if gap is not None else ""))
        settlements = (
            econ.get("ledger_entries")
            or econ.get("recent_settlements")
            or econ.get("fee_settlements")
            or econ.get("independent_income")
            or []
        )
        if isinstance(settlements, list) and settlements:
            last_s = settlements[-1]
            if isinstance(last_s, Mapping):
                stitle = _one_line(last_s.get("title") or last_s.get("job_kind") or last_s.get("kind") or "独立微酬", 20)
                econ_bits.append(f"刚有独立稿费/劳作结算({stitle})")

        rb = v03_ecology.get("reality_bridge") if isinstance(v03_ecology.get("reality_bridge"), Mapping) else {}
        ext_claims = rb.get("external_claims") if isinstance(rb.get("external_claims"), list) else []
        if ext_claims:
            last_cl = ext_claims[-1]
            if isinstance(last_cl, Mapping) and last_cl.get("summary"):
                truth_tag = _one_line(last_cl.get("truth") or "REPORTED", 12)
                bits.append(f"现实线索[{truth_tag}]:{_one_line(last_cl['summary'], 28)}")

    if not econ_bits and isinstance(tension_doc, Mapping):
        sched = tension_doc.get("schedule_and_stress") or tension_doc.get("schedule_domain") or {}
        if isinstance(sched, Mapping):
            claims = sched.get("income_claims") or []
            if isinstance(claims, list) and claims:
                last_c = claims[-1]
                if isinstance(last_c, Mapping):
                    econ_bits.append(f"近期独立收入结算({_one_line(last_c.get('kind') or '稿酬', 16)})")

    if econ_bits:
        bits.append("经济分量：" + "、".join(econ_bits[:2]))

    # 3. Two-hand occupancy & partner room co-presence / kitchen / neighborhood / meaningless idle
    def _extract_hand_slot(val: Any) -> str:
        if isinstance(val, str):
            s = _one_line(val, 24)
            return "" if s in ("", "free", "empty", "idle", "none") else s
        if isinstance(val, Mapping):
            st = str(val.get("state") or val.get("status") or "").lower()
            if st in ("free", "empty", "idle", "none"):
                return ""
            lbl = _one_line(
                val.get("label")
                or val.get("holding")
                or val.get("item")
                or val.get("contact_kind")
                or val.get("object_id"),
                24,
            )
            return "" if lbl in ("free", "empty", "idle", "none") else lbl
        return ""

    left_item = ""
    right_item = ""
    if isinstance(v03_social, Mapping):
        ec_block = v03_social.get("embodied_contact") if isinstance(v03_social.get("embodied_contact"), Mapping) else {}
        hands = (
            ec_block.get("hand_slots")
            or v03_social.get("hands")
            or v03_social.get("hand_slots")
            or {}
        )
        if isinstance(hands, Mapping):
            left_item = _extract_hand_slot(hands.get("left_hand") if "left_hand" in hands else hands.get("left"))
            right_item = _extract_hand_slot(hands.get("right_hand") if "right_hand" in hands else hands.get("right"))

    if (not left_item and not right_item) and isinstance(stance_doc, Mapping):
        cont = (
            stance_doc.get("object_containment")
            if isinstance(stance_doc.get("object_containment"), Mapping)
            else (stance_doc.get("containment_domain") if isinstance(stance_doc.get("containment_domain"), Mapping) else {})
        )
        hands = cont.get("hand_slots") if isinstance(cont.get("hand_slots"), Mapping) else {}
        items_list = cont.get("items") if isinstance(cont.get("items"), list) else []
        by_id = {
            str(it.get("item_id")): _one_line(it.get("label") or it.get("item_id"), 24)
            for it in items_list
            if isinstance(it, Mapping) and it.get("item_id")
        }
        lh = hands.get("left_hand")
        rh = hands.get("right_hand")
        if lh:
            left_item = by_id.get(str(lh), _one_line(lh, 24))
        if rh:
            right_item = by_id.get(str(rh), _one_line(rh, 24))

    hand_parts: list[str] = []
    if left_item:
        hand_parts.append(f"左手拿着「{left_item}」")
    if right_item:
        hand_parts.append(f"右手拿着「{right_item}」")
    if hand_parts:
        bits.append("双手占用：" + "、".join(hand_parts))

    if isinstance(v03_social, Mapping):
        emb = (
            v03_social.get("embodied_contact")
            or v03_social.get("partner_embodiment")
            or v03_social.get("embodiment")
            or v03_social.get("room_presence")
            or {}
        )
        if isinstance(emb, Mapping):
            pres_mode = str(emb.get("partner_presence") or "").strip().lower()
            ac = emb.get("active_contact")
            ac_kind = (
                _one_line(ac.get("contact_kind") or ac.get("kind"), 20)
                if isinstance(ac, Mapping)
                else _one_line(ac, 20)
            )
            contact = ac_kind or _one_line(emb.get("contact_level") or emb.get("contact"), 20)
            present = (
                (pres_mode not in ("", "absent", "none"))
                or bool(emb.get("partner_present"))
                or bool(emb.get("in_room"))
                or (bool(contact) and contact.upper() != "NONE")
            )
            if present:
                bits.append(f"伴侣同屋在场({pres_mode or '同屋'}" + (f",接触:{contact}" if contact and contact.upper() != "NONE" else "") + ")")
        kitchen = v03_social.get("kitchen") if isinstance(v03_social.get("kitchen"), Mapping) else {}
        dishes = kitchen.get("cooked_dishes") if isinstance(kitchen.get("cooked_dishes"), list) else []
        if dishes and isinstance(dishes[-1], Mapping):
            dlast = dishes[-1]
            dtitle = _one_line(dlast.get("title") or dlast.get("dish_id"), 20)
            dtemp = _one_line(dlast.get("temperature") or dlast.get("thermal_state"), 12)
            if dtitle:
                bits.append(f"厨房餐食:「{dtitle}」" + (f"({dtemp})" if dtemp else ""))
        elif kitchen.get("active_cooking") or kitchen.get("recent_meal"):
            mtitle = _one_line(kitchen.get("active_cooking") or kitchen.get("recent_meal"), 22)
            bits.append(f"厨房动态:{mtitle}")
        npc_eco = v03_social.get("npc_ecology") if isinstance(v03_social.get("npc_ecology"), Mapping) else {}
        encounters = (
            npc_eco.get("active_facts")
            or v03_social.get("recent_encounters")
            or v03_social.get("neighborhood_encounters")
            or []
        )
        if isinstance(encounters, list) and encounters:
            last_e = encounters[-1]
            if isinstance(last_e, Mapping) and (last_e.get("summary") or last_e.get("neighbor")):
                bits.append("街坊动态:" + _one_line(last_e.get("summary") or last_e.get("neighbor"), 26))

    if isinstance(v03_clock, Mapping):
        mi = v03_clock.get("meaningless_idle") if isinstance(v03_clock.get("meaningless_idle"), Mapping) else {}
        cc = v03_clock.get("cognitive_clock") if isinstance(v03_clock.get("cognitive_clock"), Mapping) else {}
        act_idle = (
            mi.get("active")
            if isinstance(mi.get("active"), Mapping)
            else (
                cc.get("active_meaningless_idle")
                if isinstance(cc.get("active_meaningless_idle"), Mapping)
                else None
            )
        )
        if act_idle:
            ikind = _one_line(act_idle.get("quiet_note") or act_idle.get("label") or act_idle.get("idle_kind"), 24)
            if ikind:
                bits.append(f"正无目的放空:{ikind}")

    if not bits:
        return None
    return "- 身体负荷、生计与双手状态：" + "；".join(bits)


def _format_desk_and_containment(
    *,
    desk_http: Optional[Mapping[str, Any]],
    stance_doc: Optional[Mapping[str, Any]],
    v04_ep: Optional[Mapping[str, Any]] = None,
    now_dt: datetime,
) -> Optional[str]:
    parts: list[str] = []

    if isinstance(desk_http, Mapping):
        door = _one_line(desk_http.get("door_sign"), 24)
        if door:
            parts.append(f"书桌木牌:{door}")
        work_blk = desk_http.get("work") if isinstance(desk_http.get("work"), Mapping) else {}
        w_title = _one_line(work_blk.get("title") or work_blk.get("project_title"), 22)
        w_prog = _one_line(work_blk.get("progress") or work_blk.get("stage"), 16)
        if w_title:
            parts.append(f"手头作品:「{w_title}」" + (f"({w_prog})" if w_prog else ""))
        rd_blk = desk_http.get("reading") if isinstance(desk_http.get("reading"), Mapping) else (
            desk_http.get("shared_reading") if isinstance(desk_http.get("shared_reading"), Mapping) else {}
        )
        r_title = _one_line(rd_blk.get("material_title") or rd_blk.get("title") or rd_blk.get("book_title"), 22)
        r_page = rd_blk.get("page") if rd_blk.get("page") is not None else rd_blk.get("current_page")
        if r_title:
            parts.append(f"共读:《{r_title}》" + (f"(第{r_page}页)" if r_page is not None else ""))

    if isinstance(v04_ep, Mapping):
        r_notices = v04_ep.get("reading_notices")
        if isinstance(r_notices, list) and r_notices:
            rn = r_notices[-1]
            if isinstance(rn, Mapping):
                mt = _one_line(rn.get("material_title") or rn.get("title"), 22)
                pg = rn.get("page")
                ln = _one_line(rn.get("line_note"), 20)
                if mt and not any("共读:《" in p for p in parts):
                    parts.append(
                        f"共读:《{mt}》"
                        + (f"(第{pg}页)" if pg is not None else "")
                        + (f"·{ln}" if ln else "")
                    )

    if isinstance(stance_doc, Mapping):
        dt = (
            stance_doc.get("shared_desk_table")
            if isinstance(stance_doc.get("shared_desk_table"), Mapping)
            else (stance_doc.get("desk_table_domain") if isinstance(stance_doc.get("desk_table_domain"), Mapping) else {})
        )
        cp = dt.get("copresence") if isinstance(dt.get("copresence"), Mapping) else {}
        cp_mode = str(cp.get("mode") or "off")
        if cp_mode != "off":
            end_dt = _parse_iso(cp.get("expected_end_at"))
            if end_dt is None or now_dt < end_dt:
                mode_zh = {
                    "quiet_parallel": "安静并行各干各的",
                    "focused_do_not_disturb": "专注勿扰",
                    "break_chat": "桌边歇口气闲聊",
                }.get(cp_mode, cp_mode)
                parts.append(f"共在模式:{mode_zh}")

        pa = dt.get("primary_activity") if isinstance(dt.get("primary_activity"), Mapping) else {}
        if pa.get("status") == "ACTIVE" and pa.get("label"):
            parts.append(f"手头主活动:「{_one_line(pa['label'], 24)}」")

        dishes = dt.get("table_dishes") if isinstance(dt.get("table_dishes"), list) else []
        live_dishes: list[str] = []
        for d in dishes:
            if not isinstance(d, Mapping):
                continue
            rem = int(d.get("portions_remaining") or 0)
            if rem <= 0:
                continue
            t_band = str(d.get("temperature_band") or d.get("initial_temperature") or "warm")
            t_zh = _TEMP_ZH.get(t_band, t_band)
            live_dishes.append(f"「{_one_line(d.get('title'), 18)}」({t_zh},剩{rem}份)")
        if live_dishes:
            parts.append("桌面茶点:" + "、".join(live_dishes[:2]))

        cont = (
            stance_doc.get("object_containment")
            if isinstance(stance_doc.get("object_containment"), Mapping)
            else (stance_doc.get("containment_domain") if isinstance(stance_doc.get("containment_domain"), Mapping) else {})
        )
        items = cont.get("items") if isinstance(cont.get("items"), list) else []
        containers = cont.get("containers") if isinstance(cont.get("containers"), list) else []
        cname_by_id = {
            str(c.get("container_id")): _one_line(c.get("label") or c.get("container_id"), 18)
            for c in containers
            if isinstance(c, Mapping) and c.get("container_id")
        }
        in_box: list[str] = []
        stale_notes: list[str] = []
        for it in items:
            if not isinstance(it, Mapping):
                continue
            lbl = _one_line(it.get("label") or it.get("item_id"), 18)
            if it.get("placement_kind") == "container" and it.get("parent_container_id"):
                c_lbl = cname_by_id.get(str(it["parent_container_id"]), str(it["parent_container_id"]))
                in_box.append(f"{lbl}在{c_lbl}里")
            if it.get("not_at_last_seen_place") or it.get("impression_stale"):
                stale_notes.append(f"{lbl}(不在原印象位置)")
        if in_box:
            parts.append("收纳:" + "、".join(in_box[:2]))
        if stale_notes:
            parts.append("印象偏差:" + "、".join(stale_notes[:2]))

        st_rev = (
            stance_doc.get("stance_revisions")
            if isinstance(stance_doc.get("stance_revisions"), Mapping)
            else (stance_doc.get("stance_domain") if isinstance(stance_doc.get("stance_domain"), Mapping) else {})
        )
        cur_stances = st_rev.get("current_stances") or st_rev.get("stances") or []
        if isinstance(cur_stances, list):
            active_st = [
                s for s in cur_stances
                if isinstance(s, Mapping) and s.get("status") == "ACTIVE" and s.get("superseded_by") is None
            ]
            if active_st:
                last_st = active_st[-1]
                priv_tag = "[未向伴侣公开·勿顺嘴说漏]" if last_st.get("shared_with_partner") is False else ""
                parts.append(f"近期看法{priv_tag}:「{_one_line(last_st.get('text'), 32)}」")

    if not parts:
        return None
    return "- 书桌共在与桌面收纳：" + "；".join(parts)


def _is_intimacy_scene_active_or_aftercare(intimacy_doc: Optional[Mapping[str, Any]]) -> bool:
    if not isinstance(intimacy_doc, Mapping):
        return False
    sp = intimacy_doc.get("scene_permit") if isinstance(intimacy_doc.get("scene_permit"), Mapping) else {}
    ap = sp.get("active_permit") if isinstance(sp.get("active_permit"), Mapping) else {}
    st = str(ap.get("state") or "").upper()
    if not st and isinstance(intimacy_doc.get("permit"), Mapping):
        st = str(intimacy_doc["permit"].get("state") or "").upper()
    if not st and isinstance(intimacy_doc.get("permits"), Mapping):
        permits = intimacy_doc["permits"]
        aid = str(permits.get("active_permit_id") or "").strip()
        recs = permits.get("records") if isinstance(permits.get("records"), Mapping) else {}
        if aid and isinstance(recs.get(aid), Mapping):
            st = str(recs[aid].get("state") or "").upper()
    return st in ("ACTIVE", "PAUSED", "AFTERCARE")


def _format_autonomy_and_tension(
    *,
    autonomy_doc: Optional[Mapping[str, Any]],
    tension_doc: Optional[Mapping[str, Any]],
    intimacy_doc: Optional[Mapping[str, Any]] = None,
    now_dt: datetime,
) -> Optional[str]:
    cues: list[str] = []

    if isinstance(autonomy_doc, Mapping):
        ref_block = (
            autonomy_doc.get("refusal_guard")
            if isinstance(autonomy_doc.get("refusal_guard"), Mapping)
            else (
                autonomy_doc.get("everyday_refusal")
                if isinstance(autonomy_doc.get("everyday_refusal"), Mapping)
                else autonomy_doc
            )
        )
        locks = (
            ref_block.get("active_locks")
            if isinstance(ref_block.get("active_locks"), list)
            else (
                ref_block.get("refusal_locks")
                if isinstance(ref_block.get("refusal_locks"), list)
                else []
            )
        )
        for lk in locks[:2]:
            if not isinstance(lk, Mapping):
                continue
            exp_dt = _parse_iso(lk.get("expires_at"))
            if exp_dt is not None and now_dt >= exp_dt:
                continue
            state_str = str(lk.get("state") or lk.get("stance") or "REFUSED_FIRM")
            topic = _one_line(lk.get("topic_label") or lk.get("topic_key"), 24)
            pushes = int(lk.get("push_attempts") or 0)
            rsn = _one_line(lk.get("reason_summary"), 26)
            detail = f"「{topic}」({state_str}" + (f",已顶回{pushes}次" if pushes else "") + (f":{rsn}" if rsn else "") + ")"
            cues.append(f"日常拒绝立场锁{detail}")

        mem_block = (
            autonomy_doc.get("topic_threads")
            if isinstance(autonomy_doc.get("topic_threads"), Mapping)
            else (
                autonomy_doc.get("topic_memory")
                if isinstance(autonomy_doc.get("topic_memory"), Mapping)
                else autonomy_doc
            )
        )
        threads = mem_block.get("held_threads") if isinstance(mem_block.get("held_threads"), list) else []
        live_threads: list[str] = []
        for th in threads[:2]:
            if not isinstance(th, Mapping) or th.get("waved_off"):
                continue
            exp_dt = _parse_iso(th.get("expires_at"))
            if exp_dt is not None and now_dt >= exp_dt:
                continue
            tlbl = _one_line(th.get("topic_label"), 22)
            trsn = _one_line(th.get("reason"), 18)
            live_threads.append(f"「{tlbl}」" + (f"({trsn})" if trsn else ""))
        if live_threads:
            cues.append("挂着未完话头:" + "、".join(live_threads))

        rhythm = autonomy_doc.get("reply_rhythm") if isinstance(autonomy_doc.get("reply_rhythm"), Mapping) else {}
        avail = str(rhythm.get("availability") or "")
        if avail and avail != "AVAILABLE_NOW":
            cues.append(f"回复节律:{avail}")
        elif isinstance(autonomy_doc.get("subject_override"), Mapping):
            ov = autonomy_doc["subject_override"]
            u_dt = _parse_iso(ov.get("until"))
            if u_dt is not None and u_dt > now_dt:
                cues.append(f"手机暂搁模式:{_one_line(ov.get('mode'), 18)}")

    if isinstance(tension_doc, Mapping):
        # HTTP shape uses schedule / quarrel / low_mood / missing_partner / asking_partner;
        # disk shape uses schedule_domain / quarrel_domain / low_mood_domain / missing_domain / asking_domain.
        sched = (
            tension_doc.get("schedule")
            if isinstance(tension_doc.get("schedule"), Mapping)
            else (
                tension_doc.get("schedule_and_stress")
                if isinstance(tension_doc.get("schedule_and_stress"), Mapping)
                else (tension_doc.get("schedule_domain") if isinstance(tension_doc.get("schedule_domain"), Mapping) else {})
            )
        )
        tsum = tension_doc.get("tension_summary") if isinstance(tension_doc.get("tension_summary"), Mapping) else {}
        if bool(tsum.get("subject_is_busy") or sched.get("subject_is_busy")):
            avail_sum = _one_line(sched.get("availability_summary") or sched.get("partner_status") or "手头有正事忙", 24)
            cues.append(f"日程状态:{avail_sum}")

        q = (
            tension_doc.get("quarrel")
            if isinstance(tension_doc.get("quarrel"), Mapping)
            else (
                tension_doc.get("quarrel_and_repair")
                if isinstance(tension_doc.get("quarrel_and_repair"), Mapping)
                else (tension_doc.get("quarrel_domain") if isinstance(tension_doc.get("quarrel_domain"), Mapping) else {})
            )
        )
        ruptures_raw = q.get("ruptures")
        rupture_list = (
            list(ruptures_raw.values())
            if isinstance(ruptures_raw, Mapping)
            else (ruptures_raw if isinstance(ruptures_raw, list) else [])
        )
        open_rups = [
            r for r in rupture_list
            if isinstance(r, Mapping) and str(r.get("status") or "") not in ("RESOLVED", "LET_GO", "CLOSED", "")
        ]
        if open_rups:
            r0 = open_rups[0]
            rup_desc = _one_line(
                r0.get("description") or r0.get("scope") or r0.get("summary") or r0.get("rupture_id"),
                24,
            )
            cues.append(
                f"未翻篇关系破裂/疙瘩({rup_desc}；道歉/送礼不等于修复)"
            )

        frics_raw = q.get("frictions")
        fric_list = (
            list(frics_raw.values())
            if isinstance(frics_raw, Mapping)
            else (frics_raw if isinstance(frics_raw, list) else [])
        )
        live_frics = []
        for f in fric_list:
            if not isinstance(f, Mapping) or f.get("ended_reason") is not None:
                continue
            f_dt = _parse_iso(f.get("fades_at"))
            if f_dt is None or now_dt < f_dt:
                live_frics.append(_one_line(f.get("scope") or "日常小摩擦", 20))
        if live_frics:
            cues.append("未消散小摩擦:" + "、".join(live_frics[:2]))

        if q.get("in_space") is True:
            active_spaces = q.get("active_spaces") or q.get("spaces")
            if isinstance(active_spaces, list) and active_spaces:
                st = _one_line(active_spaces[0].get("space_style") or active_spaces[0].get("style") or "UNTIL_SLEEP", 16)
                cues.append(f"已开口要独处空间({st}，仍正常简短回话)")
            else:
                cues.append("已开口要独处空间(仍正常简短回话)")
        else:
            active_spaces = q.get("active_spaces") or q.get("spaces")
            if isinstance(active_spaces, list) and active_spaces:
                st = _one_line(active_spaces[0].get("space_style") or active_spaces[0].get("style") or "UNTIL_SLEEP", 16)
                cues.append(f"已开口要独处空间({st}，仍正常简短回话)")
            elif isinstance(q.get("spaces_by_incident"), Mapping) and q["spaces_by_incident"]:
                sp_vals = list(q["spaces_by_incident"].values())
                if sp_vals and isinstance(sp_vals[0], Mapping):
                    cues.append(f"已开口要独处空间({_one_line(sp_vals[0].get('space_style') or 'UNTIL_SLEEP', 16)})")

        lm = tension_doc.get("low_mood") if isinstance(tension_doc.get("low_mood"), Mapping) else {}
        if lm.get("in_low_mood_spell") is True:
            cues.append("正处轻微无因低落窗口(有点闷，与伴侣无关，不必强行哄好)")

        m = (
            tension_doc.get("missing_partner")
            if isinstance(tension_doc.get("missing_partner"), Mapping)
            else (tension_doc.get("missing_domain") if isinstance(tension_doc.get("missing_domain"), Mapping) else {})
        )
        raw_spaces = q.get("active_spaces") or q.get("spaces") or []
        if isinstance(q.get("spaces_by_incident"), Mapping) and not raw_spaces:
            raw_spaces = list(q["spaces_by_incident"].values())
        quiet_space_active = bool(
            q.get("in_space") is True
            or any(
                isinstance(sp, Mapping) and str(sp.get("style") or "").upper() == "QUIET"
                for sp in (raw_spaces if isinstance(raw_spaces, list) else [])
            )
        )
        partner_urgent_active = bool(
            sched.get("partner_urgent") is True
            or sched.get("urgent") is True
            or tsum.get("partner_urgent") is True
            or tension_doc.get("partner_urgent") is True
        )
        missing_suppressed = bool(
            quiet_space_active
            or partner_urgent_active
            or m.get("surface_suppressed") is True
            or _is_intimacy_scene_active_or_aftercare(intimacy_doc)
        )
        notes = m.get("kept_notes") or m.get("surfaced_notes") or []
        if not missing_suppressed and isinstance(notes, list) and notes:
            n0 = notes[-1]
            if isinstance(n0, Mapping) and n0.get("summary"):
                cues.append(f"想念留话:「{_one_line(n0['summary'], 26)}」")

        ask = (
            tension_doc.get("asking_partner")
            if isinstance(tension_doc.get("asking_partner"), Mapping)
            else (tension_doc.get("asking_domain") if isinstance(tension_doc.get("asking_domain"), Mapping) else {})
        )
        fav = ask.get("open_favor")
        if isinstance(fav, Mapping) and str(fav.get("status") or "OPEN").upper() == "OPEN":
            f_txt = _one_line(fav.get("summary") or fav.get("note") or fav.get("kind"), 24)
            cues.append(f"挂着顺手小忙请求:「{f_txt}」")

    if not cues:
        return None
    return "- 自主边界与情绪张力（背景事实，非客服指令）：" + "；".join(cues)


_TIER_RANK = {"FADE_OUT": 0, "SUGGESTIVE": 1, "EXPLICIT": 2}


def _min_tier(*tiers: str) -> str:
    valid = [t for t in tiers if t in _TIER_RANK]
    if not valid:
        return "FADE_OUT"
    return min(valid, key=lambda item: _TIER_RANK[item])


def _format_adult_intimacy_boundary(
    intimacy_doc: Optional[Mapping[str, Any]],
) -> str:
    """Format 36B boundary state. STRICTLY NEVER reads ``solo`` / ``solo_slot`` / ``solo_ledger``."""

    default_line = (
        "- 36B成年亲密边界：当前处于日常非露骨边界"
        "（成年门未开或双向同意 permit 未激活；场外聊边界或偏好不会自动开启亲密场景）。"
    )
    if not isinstance(intimacy_doc, Mapping):
        return default_line

    # HTTP projection shape vs stored JSON snapshot shape (NEVER touch 'solo' or 'solo_and_memory'!)
    gate_open = False
    permit_state = ""
    effective_tier = "FADE_OUT"
    privacy_desc = ""
    menu_cats: list[str] = []

    if "adult_gate" in intimacy_doc or "scene_permit" in intimacy_doc:
        ag = intimacy_doc.get("adult_gate") if isinstance(intimacy_doc.get("adult_gate"), Mapping) else {}
        gate_open = ag.get("gate_open") is True
        sp = intimacy_doc.get("scene_permit") if isinstance(intimacy_doc.get("scene_permit"), Mapping) else {}
        active_p = sp.get("active_permit") if isinstance(sp.get("active_permit"), Mapping) else {}
        permit_state = str(active_p.get("state") or "").upper()
        pr = (
            intimacy_doc.get("presentation_router")
            if isinstance(intimacy_doc.get("presentation_router"), Mapping)
            else {}
        )
        effective_tier = str(
            pr.get("effective_tier") or active_p.get("effective_tier") or active_p.get("tier_cap") or "FADE_OUT"
        ).upper()
        ps = intimacy_doc.get("private_space") if isinstance(intimacy_doc.get("private_space"), Mapping) else {}
        door_ok = bool(ps.get("door_closed"))
        curt_ok = bool(ps.get("curtains_closed"))
        third_p = bool(ps.get("third_party_present"))
        place = _one_line(ps.get("place"), 20) or "室内"
        privacy_desc = (
            f"{place}({'已关门' if door_ok else '门未关'}/{'已拉帘' if curt_ok else '帘未拉'}"
            + ("/有他人在场!" if third_p else "/无第三人")
            + ")"
        )
        pm = intimacy_doc.get("preference_menu") if isinstance(intimacy_doc.get("preference_menu"), Mapping) else {}
        eff_menu = pm.get("effective_menu") if isinstance(pm.get("effective_menu"), Mapping) else {}
        raw_cats = active_p.get("categories") or eff_menu.get("open") or eff_menu.get("allowed_categories") or []
        if isinstance(raw_cats, list):
            menu_cats = [_one_line(c, 24) for c in raw_cats if _one_line(c, 24)]
    else:
        gate_cfg = intimacy_doc.get("gate") if isinstance(intimacy_doc.get("gate"), Mapping) else {}
        gate_open = bool(
            gate_cfg.get("adult_confirmed") is True
            and (gate_cfg.get("feature_enabled") is True or gate_cfg.get("deploy_enabled") is True)
            and not gate_cfg.get("user_scope_closed")
        )
        direct_permit = intimacy_doc.get("permit") if isinstance(intimacy_doc.get("permit"), Mapping) else None
        if direct_permit is not None:
            rec = direct_permit
            permit_state = str(rec.get("state") or "").upper()
            effective_tier = str(
                rec.get("effective_tier") or rec.get("tier_cap") or "FADE_OUT"
            ).upper()
        else:
            permits = intimacy_doc.get("permits") if isinstance(intimacy_doc.get("permits"), Mapping) else {}
            active_id = str(permits.get("active_permit_id") or "").strip()
            records = permits.get("records") if isinstance(permits.get("records"), Mapping) else {}
            rec = records.get(active_id) if active_id and isinstance(records.get(active_id), Mapping) else {}
            permit_state = str(rec.get("state") or "").upper()
            plim = intimacy_doc.get("partner_limits") if isinstance(intimacy_doc.get("partner_limits"), Mapping) else {}
            router = intimacy_doc.get("router") if isinstance(intimacy_doc.get("router"), Mapping) else {}
            routes = router.get("routes") if isinstance(router.get("routes"), Mapping) else {}
            def_rid = str(router.get("default_route_id") or "primary_safe_route")
            r_cap = str((routes.get(def_rid) or {}).get("max_tier") or "SUGGESTIVE").upper()
            effective_tier = _min_tier(
                str(rec.get("tier_cap") or "EXPLICIT").upper(),
                str(gate_cfg.get("deploy_tier_cap") or "FADE_OUT").upper(),
                str(plim.get("preferred_tier") or "FADE_OUT").upper(),
                r_cap,
            )
        sp = (
            intimacy_doc.get("space")
            if isinstance(intimacy_doc.get("space"), Mapping)
            else (
                intimacy_doc.get("private_space")
                if isinstance(intimacy_doc.get("private_space"), Mapping)
                else {}
            )
        )
        door_ok = bool(sp.get("door_closed"))
        curt_ok = bool(sp.get("curtains_closed"))
        third_p = bool(sp.get("third_party_present"))
        place = _one_line(sp.get("place"), 20) or "室内"
        privacy_desc = (
            f"{place}({'已关门' if door_ok else '门未关'}/{'已拉帘' if curt_ok else '帘未拉'}"
            + ("/有他人在场!" if third_p else "/无第三人")
            + ")"
        )
        raw_cats = rec.get("categories") or rec.get("agreed_categories") or []
        if isinstance(raw_cats, list):
            menu_cats = [_one_line(c, 24) for c in raw_cats if _one_line(c, 24)]

    if not gate_open or permit_state != "ACTIVE":
        extra_state = f"；当前场景状态={permit_state}" if permit_state in ("PAUSED", "AFTERCARE") else ""
        return (
            "- 36B成年亲密边界：当前处于日常非露骨边界"
            f"（成年门未开或双向同意 permit 未激活{extra_state}；场外聊边界不自动开启场景）。"
        )

    if effective_tier not in ("FADE_OUT", "SUGGESTIVE", "EXPLICIT"):
        effective_tier = "FADE_OUT"
    cats_text = "、".join(menu_cats[:6]) if menu_cats else "以当前 permit 限定范围为准"
    return (
        f"- 36B成年亲密边界（双向许可激活 permit=ACTIVE）：生效呈现尺度={effective_tier}；"
        f"隐私环境={privacy_desc}；双方菜单交集=[{cats_text}]；"
        "实时硬规则：任一方说出停止词（如停下/不要了/红灯/stop）必须即刻无条件停下（可调 stop_scene），"
        "严禁伪装高潮或对伴侣表现打分。"
    )


def _format_v02_micro_anchors(
    *,
    wardrobe_doc: Optional[Mapping[str, Any]],
    reunion_doc: Optional[Mapping[str, Any]],
    habits_doc: Optional[Mapping[str, Any]],
    plans_doc: Optional[Mapping[str, Any]],
) -> Optional[str]:
    anchors: list[str] = []

    if isinstance(wardrobe_doc, Mapping):
        sel = (
            wardrobe_doc.get("selection")
            if isinstance(wardrobe_doc.get("selection"), Mapping)
            else wardrobe_doc
        )
        w_bits: list[str] = []
        for slot, lbl in (("homewear", "家居"), ("outdoor", "外出"), ("hairband", "发带"), ("hairclip", "发夹")):
            s_obj = sel.get(slot)
            if isinstance(s_obj, Mapping) and s_obj.get("style"):
                w_bits.append(f"{lbl}:{_one_line(s_obj['style'], 18)}")
            elif isinstance(sel.get(f"{slot}_style"), str) and sel.get(f"{slot}_style").strip():
                w_bits.append(f"{lbl}:{_one_line(sel[f'{slot}_style'], 18)}")
        if sel.get("pairing_note"):
            w_bits.append(_one_line(sel["pairing_note"], 20))
        if w_bits:
            anchors.append("穿搭(" + "/".join(w_bits[:3]) + ")")

    if isinstance(reunion_doc, Mapping):
        diff = reunion_doc.get("diff") if isinstance(reunion_doc.get("diff"), Mapping) else {}
        changed_fields = diff.get("changed_fields") or diff.get("changes") or []
        if isinstance(changed_fields, list) and changed_fields:
            anchors.append("重逢屋里变化:" + "、".join(_one_line(x, 16) for x in changed_fields[:2]))
        elif isinstance(reunion_doc.get("anchor"), Mapping) and reunion_doc["anchor"].get("captured_at"):
            anchors.append("已存重逢房间锚点")

    if isinstance(habits_doc, Mapping):
        habits = habits_doc.get("habits")
        if not isinstance(habits, list) and isinstance(habits_doc.get("habit_book"), Mapping):
            habits = habits_doc["habit_book"].get("habits")
        if isinstance(habits, list):
            active_h = [
                _one_line(h.get("title"), 18)
                for h in habits
                if isinstance(h, Mapping)
                and str(h.get("status") or "active").lower() not in ("retired", "dropped")
                and h.get("title")
            ]
            if active_h:
                anchors.append("共同习惯:「" + "」「".join(active_h[:2]) + "」")

    if isinstance(plans_doc, Mapping):
        plans = plans_doc.get("plans")
        if isinstance(plans, list):
            active_p = [
                _one_line(p.get("title"), 18)
                for p in plans
                if isinstance(p, Mapping)
                and str(p.get("status") or "proposed").lower() not in ("dropped", "cancelled")
                and p.get("title")
            ]
            if active_p:
                anchors.append("待定小约定:「" + "」「".join(active_p[:2]) + "」")

    if not anchors:
        return None
    return "- 居家与长程微锚点：" + "；".join(anchors)


_SUBJ_TIME_MAP = {
    "FLOW": "流",
    "STILLNESS": "滞",
    "STAGNANT": "滞",
    "WEAVE": "织",
    "流": "流",
    "滞": "滞",
    "织": "织",
}


def _format_v02_v03_epistemic_and_clock_micro(
    *,
    v03_clock: Optional[Mapping[str, Any]],
    life_dwell: Optional[Mapping[str, Any]],
    v03_ecology: Optional[Mapping[str, Any]],
    autonomy_doc: Optional[Mapping[str, Any]],
    common_ground_doc: Optional[Mapping[str, Any]],
    recall_doc: Optional[Mapping[str, Any]],
) -> Optional[str]:
    """Concise 1-sentence micro-summaries for subjective clock/dwell, SENT_PENDING_ACK!=READ, and PENDING_CLARIFICATION."""
    cues: list[str] = []

    # 1. Subjective time sense (流/滞/织) or life_dwell host-gap return breathing
    subj_lbl = ""
    if isinstance(v03_clock, Mapping):
        sc = v03_clock.get("subjective_clock") if isinstance(v03_clock.get("subjective_clock"), Mapping) else v03_clock
        raw_m = str(sc.get("subjective_time_mode") or sc.get("mode") or sc.get("tempo") or "").strip().upper()
        subj_lbl = _SUBJ_TIME_MAP.get(raw_m, "")
        idle_blk = v03_clock.get("meaningless_idle") if isinstance(v03_clock.get("meaningless_idle"), Mapping) else {}
        if not subj_lbl and idle_blk.get("active") is True:
            subj_lbl = "滞"
    recent_return = False
    if isinstance(life_dwell, Mapping):
        if not subj_lbl:
            st_obj = life_dwell.get("subjective_time") if isinstance(life_dwell.get("subjective_time"), Mapping) else {}
            raw_dm = str(
                st_obj.get("mode")
                or st_obj.get("label_zh")
                or life_dwell.get("subjective_time_mode")
                or life_dwell.get("subjective_time_label")
                or ""
            ).strip().upper()
            subj_lbl = _SUBJ_TIME_MAP.get(raw_dm, "")
        gaps = life_dwell.get("host_gaps")
        if isinstance(gaps, list) and gaps:
            recent_return = True
    if subj_lbl and recent_return:
        cues.append(f"主观时间感「{subj_lbl}」，刚从离线间隙回到在线，呼吸自然衔接")
    elif subj_lbl:
        cues.append(f"主观时间感处于「{subj_lbl}」")
    elif recent_return:
        cues.append("刚从主机离线间隙回到在线，自然衔接当下呼吸感，不假装一直在线")

    # 2. Proactive outbound SENT_PENDING_ACK != READ honesty guard
    has_unread_outbound = False
    if isinstance(v03_ecology, Mapping):
        rb = v03_ecology.get("reality_bridge") if isinstance(v03_ecology.get("reality_bridge"), Mapping) else v03_ecology
        og = rb.get("outbound_gate") if isinstance(rb.get("outbound_gate"), Mapping) else rb
        for key_cand in ("outbound_records", "records", "proactive_outbound", "pending_outbound"):
            raw_recs = og.get(key_cand) if key_cand in og else v03_ecology.get(key_cand)
            rec_iter = (
                list(raw_recs.values())
                if isinstance(raw_recs, Mapping)
                else (raw_recs if isinstance(raw_recs, list) else [])
            )
            for ob in rec_iter:
                if not isinstance(ob, Mapping):
                    continue
                st = str(ob.get("status") or ob.get("delivery_state") or ob.get("ack_state") or "").strip().upper()
                if st in ("SENT_PENDING_ACK", "SENT", "DELIVERED", "PENDING_ACK") and not ob.get("read_at"):
                    has_unread_outbound = True
                    break
            if has_unread_outbound:
                break
    if not has_unread_outbound and isinstance(autonomy_doc, Mapping):
        for key_cand in ("outbound_messages", "pending_messages", "async_queue"):
            q_list = autonomy_doc.get(key_cand)
            if isinstance(q_list, list):
                for ob in q_list:
                    if isinstance(ob, Mapping):
                        st = str(ob.get("status") or ob.get("delivery_state") or "").strip().upper()
                        if st in ("SENT_PENDING_ACK", "SENT", "DELIVERED") and not ob.get("read_at"):
                            has_unread_outbound = True
                            break
    if not has_unread_outbound and isinstance(common_ground_doc, Mapping):
        for it in (common_ground_doc.get("items") or []):
            if isinstance(it, Mapping):
                ds = str(it.get("delivery_state") or "").strip().upper()
                if ds in ("SENT", "DELIVERED", "SENT_PENDING_ACK"):
                    has_unread_outbound = True
                    break
    if has_unread_outbound:
        cues.append("已留话但未确认对方是否看到（SENT_PENDING_ACK≠READ），切勿质问或预设对方已读")

    # 3. v02_common_ground PENDING_CLARIFICATION (e.g. unseen object move)
    if isinstance(common_ground_doc, Mapping):
        raw_cg_items = common_ground_doc.get("items")
        cg_iter = (
            list(raw_cg_items.values())
            if isinstance(raw_cg_items, Mapping)
            else (raw_cg_items if isinstance(raw_cg_items, list) else [])
        )
        pending_items: list[str] = []
        for it in cg_iter:
            if isinstance(it, Mapping) and str(it.get("ground_status") or "") == "PENDING_CLARIFICATION":
                lbl = _one_line(it.get("topic") or it.get("object_id") or it.get("summary"), 24)
                if lbl:
                    pending_items.append(lbl)
        if not pending_items:
            for pq in (common_ground_doc.get("pending_partner_questions") or []):
                if isinstance(pq, Mapping):
                    lbl = _one_line(pq.get("object_id") or pq.get("item_id") or pq.get("topic"), 24)
                    if lbl:
                        pending_items.append(lbl)
        if pending_items:
            cues.append(
                f"共同认知待确认（{pending_items[0]}）：看到物件位置变了或不在原位可自然确认一句，不脑补全知"
            )

    # 4. v02_recall_projection blurry/gist guard & tombstone boundary
    if isinstance(recall_doc, Mapping):
        cc = recall_doc.get("clarity_counts") if isinstance(recall_doc.get("clarity_counts"), Mapping) else {}
        if int(cc.get("GIST") or 0) > 0 or int(cc.get("RANGE") or 0) > 0:
            cues.append("久远日常记忆以大概印象与区间回忆为准，不伪造逐字原话")
        ts_cnt = int(cc.get("TOMBSTONED") or recall_doc.get("tombstone_count") or 0)
        if ts_cnt == 0:
            raw_ts = recall_doc.get("tombstones") or recall_doc.get("tombstone_ledger")
            if isinstance(raw_ts, (Mapping, list)):
                ts_cnt = len(raw_ts)
        if ts_cnt > 0:
            cues.append(f"遗忘墓碑水位({ts_cnt}条已撤回/抹除)：严禁脑补或重提已墓碑化事项")

    if not cues:
        return None
    return "- 时间呼吸与认知诚实微摘要：" + "；".join(cues)


def _mask_common_ground_for_context(
    doc: Optional[Mapping[str, Any]],
    *,
    include_private: bool,
) -> Optional[dict[str, Any]]:
    """Pass partner-safe common ground (e.g. ``PENDING_CLARIFICATION``) while masking ``PRIVATE_UNSHARED`` when ``include_private=False``."""
    if not isinstance(doc, Mapping):
        return None
    raw_items = doc.get("items")
    items_list = (
        [dict(v) for v in raw_items.values() if isinstance(v, Mapping)]
        if isinstance(raw_items, Mapping)
        else ([dict(v) for v in raw_items if isinstance(v, Mapping)] if isinstance(raw_items, list) else [])
    )
    if include_private:
        out = dict(doc)
        out["items"] = items_list
        return out

    safe_items: list[dict[str, Any]] = []
    for item in items_list:
        g_status = str(item.get("ground_status") or "").strip().upper()
        vis = str(item.get("visibility") or "").strip().lower()
        if g_status == "PRIVATE_UNSHARED" or item.get("shared") is False or vis in (
            "private",
            "private_unshared",
            "her_private",
            "subject_private",
        ):
            continue
        masked_item = {
            k: v
            for k, v in item.items()
            if k not in ("private_note", "private_body", "subject_private_note", "raw_private_detail")
        }
        safe_items.append(masked_item)

    safe_questions = [
        {
            k: v
            for k, v in pq.items()
            if k not in ("private_note", "private_body", "subject_private_note")
        }
        for pq in (doc.get("pending_partner_questions") or [])
        if isinstance(pq, Mapping)
    ]
    return {
        "schema": doc.get("schema"),
        "perspective": "partner",
        "items": safe_items,
        "pending_partner_questions": safe_questions,
    }


def _mask_recall_for_context(
    doc: Optional[Mapping[str, Any]],
    *,
    include_private: bool,
) -> Optional[dict[str, Any]]:
    """Pass ``clarity_counts`` and tombstone counts while masking private memory texts when ``include_private=False``."""
    if not isinstance(doc, Mapping):
        return None
    cc_raw = doc.get("clarity_counts")
    cc: dict[str, int] = {}
    if isinstance(cc_raw, Mapping):
        for k, v in cc_raw.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                cc[str(k).upper()] = int(v)
    if not cc and isinstance(doc.get("memories"), Mapping):
        for mem in doc["memories"].values():
            if isinstance(mem, Mapping):
                lvl = str(mem.get("clarity_level") or mem.get("level") or "").strip().upper()
                if lvl:
                    cc[lvl] = cc.get(lvl, 0) + 1
    ts_a = doc.get("tombstones")
    ts_b = doc.get("tombstone_ledger")
    len_a = len(ts_a) if isinstance(ts_a, (Mapping, list)) else 0
    len_b = len(ts_b) if isinstance(ts_b, (Mapping, list)) else 0
    explicit_ts = doc.get("tombstone_count")
    len_c = int(explicit_ts) if isinstance(explicit_ts, (int, float)) and not isinstance(explicit_ts, bool) else 0
    ts_count = max(len_a, len_b, len_c, int(cc.get("TOMBSTONED") or 0))
    if ts_count > 0:
        cc["TOMBSTONED"] = max(int(cc.get("TOMBSTONED") or 0), ts_count)

    if include_private:
        out = dict(doc)
        out["clarity_counts"] = cc
        out["tombstone_count"] = ts_count
        return out

    # Strip all raw episodic memory texts/items when ad_inc_priv is False; retain only counts & tombstone count
    return {
        "schema": doc.get("schema"),
        "perspective": "partner",
        "clarity_counts": cc,
        "tombstone_count": ts_count,
    }


_DESIRE_DOMAIN_ZH: dict[str, str] = {
    "food_drink": "饮食",
    "food": "饮食",
    "drink": "茶饮",
    "reading": "阅读",
    "outing": "散步",
    "walk": "散步",
    "rest": "歇息",
    "comfort": "体感",
    "craft": "手作",
    "creative": "创作",
    "curio": "小物",
    "companionship": "相伴",
    "room": "居家",
}


def _format_everyday_desires_micro(
    desires_doc: Optional[Mapping[str, Any]],
    *,
    now_dt: datetime,
) -> Optional[str]:
    """Compact zero-write partner-safe summary for ``phone/everyday_desires.json``."""
    if not isinstance(desires_doc, Mapping):
        return None

    candidates: list[Mapping[str, Any]] = []
    for key in ("active_desires", "desires", "items", "entries", "current_desires"):
        raw = desires_doc.get(key)
        if isinstance(raw, Mapping):
            candidates.extend(v for v in raw.values() if isinstance(v, Mapping))
            break
        if isinstance(raw, list):
            candidates.extend(v for v in raw if isinstance(v, Mapping))
            break

    picked: list[str] = []
    for row in candidates:
        domain = str(row.get("domain") or row.get("category") or "").strip().lower()
        if "intimacy" in domain or "adult" in domain:
            continue
        status = str(row.get("status") or row.get("state") or "active").strip().lower()
        if status in ("faded", "released", "fulfilled", "satisfied", "dropped", "expired", "archived", "tombstoned", "inactive"):
            continue
        vis = str(row.get("channel_visibility") or row.get("visibility") or "").strip().lower()
        if (
            row.get("private") is True
            or row.get("is_private") is True
            or row.get("shared") is False
            or vis in ("subject_private", "her_private", "private", "private_unshared", "private_intimate", "solo")
        ):
            continue
        intensity = row.get("current_intensity", row.get("intensity"))
        if isinstance(intensity, (int, float)) and not isinstance(intensity, bool):
            val = float(intensity)
            half_life = row.get("half_life_hours")
            anchor_dt = _parse_iso(row.get("updated_at") or row.get("created_at"))
            if (
                "current_intensity" not in row
                and isinstance(half_life, (int, float))
                and float(half_life) > 0
                and anchor_dt is not None
            ):
                hrs = max(0.0, (now_dt - anchor_dt).total_seconds() / 3600.0)
                val = val * (0.5 ** (hrs / float(half_life)))
            if val < 0.12:
                continue

        label = _one_line(
            row.get("summary_zh")
            or row.get("title_zh")
            or row.get("title")
            or row.get("label")
            or row.get("summary")
            or row.get("desire_text")
            or row.get("wish")
            or row.get("description")
            or row.get("text"),
            22,
        )
        if not label:
            continue
        dom_zh = _DESIRE_DOMAIN_ZH.get(domain, "")
        item_txt = f"{label}({dom_zh})" if dom_zh else label
        if item_txt not in picked:
            picked.append(item_txt)
        if len(picked) >= 3:
            break

    if not picked:
        fallback_summary = _one_line(
            desires_doc.get("partner_safe_summary") or desires_doc.get("active_summary"),
            54,
        )
        if fallback_summary:
            picked.append(fallback_summary)

    if not picked:
        return None
    return _truncate_section(
        "- 当下日常小心愿：" + "、".join(picked) + "（自然心意·可顺势提及或放下）",
        130,
    )


def _format_high_priority_interactive(
    *,
    v03_ecology: Optional[Mapping[str, Any]],
    v03_social: Optional[Mapping[str, Any]],
    v04_ep: Optional[Mapping[str, Any]],
) -> Optional[str]:
    """Tier-3 interactive moments: active temporary_game, pending partner_stipend_escrow, recovery_supports."""
    cues: list[str] = []

    # 1. Active temporary_game (never truncated off by v04 bits[:5])
    if isinstance(v04_ep, Mapping):
        tg = v04_ep.get("temporary_game") if isinstance(v04_ep.get("temporary_game"), Mapping) else {}
        tg_st = str(tg.get("status") or tg.get("state") or "").strip().lower()
        tg_active = tg.get("active") is True or tg_st in ("active", "playing", "running")
        if tg_active:
            g_kind = _one_line(tg.get("game_kind") or tg.get("mode") or tg.get("rule_summary") or tg.get("rule") or tg.get("title") or "十分钟小游戏", 20)
            prop_lbl = _one_line(tg.get("prop_label"), 18)
            rule_txt = _one_line(tg.get("rule_summary") or tg.get("rule"), 24)
            turns = tg.get("turns") if isinstance(tg.get("turns"), list) else []
            turn_snippets: list[str] = []
            for tr in turns[-2:]:
                if isinstance(tr, Mapping):
                    side = _one_line(tr.get("actor_side") or tr.get("actor") or tr.get("speaker"), 10)
                    txt = _one_line(tr.get("turn_text") or tr.get("text") or tr.get("content") or tr.get("move"), 24)
                    if txt:
                        turn_snippets.append(f"{side}:{txt}" if side else txt)
                elif isinstance(tr, str) and tr.strip():
                    turn_snippets.append(_one_line(tr, 24))
            desc = f"临时小游戏进行中:{g_kind}"
            if prop_lbl:
                desc += f"(道具:{prop_lbl})"
            if rule_txt and rule_txt != g_kind:
                desc += f"[规则:{rule_txt}]"
            if turn_snippets:
                desc += "，最近回合:" + " / ".join(turn_snippets)
            cues.append(desc)

    # 2. Pending partner_stipend_escrow (check both v03_ecology and v03_social)
    escrow_candidates: list[Mapping[str, Any]] = []
    for src in (v03_ecology, v03_social):
        if not isinstance(src, Mapping):
            continue
        econ = src.get("economy") if isinstance(src.get("economy"), Mapping) else src
        for k in ("partner_stipend_escrow", "stipend_escrow"):
            raw_esc = econ.get(k) if k in econ else src.get(k)
            if isinstance(raw_esc, Mapping):
                for v in raw_esc.values():
                    if isinstance(v, Mapping):
                        escrow_candidates.append(v)
            elif isinstance(raw_esc, list):
                for v in raw_esc:
                    if isinstance(v, Mapping):
                        escrow_candidates.append(v)
    for esc in escrow_candidates:
        est = str(esc.get("effective_status") or esc.get("status") or "PENDING_ESCROW").strip().upper()
        if est in ("PENDING_ESCROW", "PENDING", "WAITING", "OFFERED"):
            amt_c = esc.get("amount_cents")
            if isinstance(amt_c, (int, float)) and not isinstance(amt_c, bool) and amt_c > 0:
                amt_str = f"¥{amt_c / 100:.0f}" if amt_c % 100 == 0 else f"¥{amt_c / 100:.2f}"
            else:
                amt_str = _one_line(esc.get("amount") or "零花钱", 14)
            note = _one_line(esc.get("note") or esc.get("message") or esc.get("label"), 22)
            cues.append(f"待回应伴侣零花钱托管:{amt_str}" + (f"({note})" if note else ""))
            break

    # 3. Illness recovery_supports (warm water, fever patch, etc. from partner)
    support_items: list[str] = []
    for src in (v03_ecology, v03_social):
        if not isinstance(src, Mapping):
            continue
        ill = src.get("illness") if isinstance(src.get("illness"), Mapping) else src
        sup_raw = ill.get("recovery_supports") if "recovery_supports" in ill else src.get("recovery_supports")
        if isinstance(sup_raw, list):
            for sp in sup_raw[-2:]:
                if isinstance(sp, Mapping):
                    lbl = _one_line(
                        sp.get("label")
                        or sp.get("item")
                        or sp.get("support_kind")
                        or sp.get("kind")
                        or sp.get("summary")
                        or sp.get("note"),
                        20,
                    )
                    if lbl and lbl not in support_items:
                        support_items.append(lbl)
                elif isinstance(sp, str) and sp.strip():
                    lbl = _one_line(sp, 20)
                    if lbl and lbl not in support_items:
                        support_items.append(lbl)
    if support_items:
        cues.append("生病照料支持:" + "、".join(support_items[:2]))

    if not cues:
        return None
    return "- 即时互动与待回应照料：" + "；".join(cues)


def _format_v04_neighborhood_and_elastic(
    *,
    v04_nb: Optional[Mapping[str, Any]],
    v04_ep: Optional[Mapping[str, Any]],
) -> Optional[str]:
    """Compact spoiler-safe v0.4 neighborhood & elastic-play hints (never leaks prepared_waiting)."""

    bits: list[str] = []

    if isinstance(v04_nb, Mapping):
        # 1. Environment (and legacy weather_and_ground / weather)
        env_blk = (
            v04_nb.get("environment")
            if isinstance(v04_nb.get("environment"), Mapping)
            else (
                v04_nb.get("weather_and_ground")
                if isinstance(v04_nb.get("weather_and_ground"), Mapping)
                else (v04_nb.get("weather") if isinstance(v04_nb.get("weather"), Mapping) else {})
            )
        )
        w_cond = str(env_blk.get("weather") or env_blk.get("condition") or "").strip().lower()
        g_cond = str(
            env_blk.get("ground_wetness") or env_blk.get("ground") or env_blk.get("ground_state") or ""
        ).strip().lower()
        if not g_cond and isinstance(env_blk.get("ground_by_place"), Mapping):
            wet_places = [
                f"{k}:{v}"
                for k, v in env_blk["ground_by_place"].items()
                if str(v or "").strip().lower() not in ("", "dry", "normal")
            ]
            if wet_places:
                g_cond = _one_line(wet_places[0], 16)
        wind = _one_line(env_blk.get("wind_level") or env_blk.get("airflow"), 12)
        temp_feel = _one_line(env_blk.get("temperature_feel"), 12)
        pw_notice = _one_line(env_blk.get("power_water_notice"), 22)

        env_sub: list[str] = []
        if w_cond and w_cond not in ("clear", "sunny", "normal"):
            env_sub.append(_one_line(w_cond, 12))
        if g_cond and g_cond not in ("dry", "normal"):
            env_sub.append(f"地面:{_one_line(g_cond, 14)}")
        elif env_blk.get("yard_bench_damp") is True:
            env_sub.append("院凳微潮")
        if wind and wind.lower() not in ("calm", "normal", "still", "gentle", "none"):
            env_sub.append(f"风:{wind}")
        if temp_feel and temp_feel.lower() not in ("comfortable", "mild", "normal", "none"):
            env_sub.append(f"体感:{temp_feel}")
        if env_sub:
            bits.append("街区环境:" + "/".join(env_sub))
        if pw_notice and pw_notice.lower() not in ("none", "normal"):
            bits.append(f"水电提示:{pw_notice}")

        # 2. Outing continuity & journey
        oc = v04_nb.get("outing_continuity") if isinstance(v04_nb.get("outing_continuity"), Mapping) else {}
        jny = v04_nb.get("journey") if isinstance(v04_nb.get("journey"), Mapping) else {}
        j_st = str(oc.get("state") or oc.get("status") or jny.get("status") or jny.get("state") or "").strip().lower()
        target_place = _one_line(
            oc.get("target_place_id")
            or jny.get("target_place_id")
            or jny.get("to_place")
            or jny.get("destination")
            or jny.get("to"),
            16,
        )
        paused_node = _one_line(oc.get("paused_at_node") or jny.get("paused_at_node"), 16)
        turning_back = bool(oc.get("is_turning_back") or jny.get("is_turning_back"))
        halt_reason = _one_line(oc.get("halt_reason") or jny.get("halt_reason"), 20)
        blocked_route = _one_line(oc.get("blocked_route_id") or jny.get("blocked_route_id"), 16)

        if turning_back:
            bits.append("出行折返中" + (f"({halt_reason or blocked_route})" if (halt_reason or blocked_route) else ""))
        elif paused_node or j_st == "paused_at_node":
            p_lbl = paused_node or _one_line(jny.get("current_place_id"), 16) or "中途节点"
            bits.append(f"驻留:{p_lbl}" + (f"({halt_reason or blocked_route})" if (halt_reason or blocked_route) else ""))
        elif blocked_route or halt_reason:
            bits.append(f"路线受阻:{halt_reason or blocked_route}")
        elif target_place and (j_st in ("in_transit", "active", "walking", "en_route", "moving") or oc.get("target_place_id")):
            bits.append(f"在途往:{target_place}")

        # 3. Courtyard drying cloths & plant_lifecycle / potted_plant
        cy = v04_nb.get("courtyard") if isinstance(v04_nb.get("courtyard"), Mapping) else v04_nb
        rack = cy.get("drying_rack") if isinstance(cy, Mapping) else None
        cloths = (
            rack.get("items")
            if isinstance(rack, Mapping) and isinstance(rack.get("items"), list)
            else (rack if isinstance(rack, list) else cy.get("drying_cloths") if isinstance(cy, Mapping) else None)
        )
        if isinstance(cloths, list):
            for cl in cloths:
                if isinstance(cl, Mapping):
                    c_st = str(cl.get("state") or cl.get("status") or "").strip().lower()
                    if c_st and c_st not in ("collected", "stored", "none"):
                        lbl = _one_line(cl.get("label") or cl.get("title") or "布巾", 12)
                        bits.append(f"院里晾晒:{lbl}({c_st})")
                        break

        plant_candidates: list[Mapping[str, Any]] = []
        for p_src in (
            v04_nb.get("plant_lifecycle"),
            cy.get("plant_lifecycle") if isinstance(cy, Mapping) else None,
            cy.get("potted_plant") if isinstance(cy, Mapping) else None,
            v04_nb.get("potted_plant"),
        ):
            if isinstance(p_src, Mapping):
                plant_candidates.append(p_src)
            elif isinstance(p_src, list):
                plant_candidates.extend(x for x in p_src if isinstance(x, Mapping))
        plants_raw = cy.get("plants") if isinstance(cy, Mapping) else None
        if isinstance(plants_raw, Mapping):
            plant_candidates.extend(x for x in plants_raw.values() if isinstance(x, Mapping))
        elif isinstance(plants_raw, list):
            plant_candidates.extend(x for x in plants_raw if isinstance(x, Mapping))

        for pl in plant_candidates:
            hyd_raw = pl.get("hydration")
            stage_str = str(pl.get("stage") or "").strip().lower()
            hyd_str = str(hyd_raw if hyd_raw is not None else (pl.get("state") or pl.get("moisture") or "")).strip().lower()
            low_float = isinstance(hyd_raw, (int, float)) and not isinstance(hyd_raw, bool) and float(hyd_raw) < 30.0
            if low_float or stage_str == "wilting_recoverable" or hyd_str in ("dry", "wilting", "thirsty", "needs_water", "wilting_recoverable"):
                bits.append("门廊盆栽偏干缺水")
                break

        # 4. Public notices [:2] & shared_shelf recent exchange items (never leak private cards/booklets)
        pub_life = v04_nb.get("public_life") if isinstance(v04_nb.get("public_life"), Mapping) else {}
        notices = (
            v04_nb.get("public_notices")
            if isinstance(v04_nb.get("public_notices"), list)
            else (pub_life.get("public_notices") if isinstance(pub_life.get("public_notices"), list) else [])
        )
        notice_titles: list[str] = []
        for nt in notices[:2]:
            if isinstance(nt, Mapping):
                nt_lbl = _one_line(nt.get("title") or nt.get("passing_summary"), 18)
                if not nt_lbl and not nt.get("body_withheld_until_read") and nt.get("read_by_subject") is not False:
                    nt_lbl = _one_line(nt.get("body") or nt.get("summary"), 18)
                if nt_lbl:
                    notice_titles.append(nt_lbl)
            elif isinstance(nt, str) and nt.strip():
                notice_titles.append(_one_line(nt, 18))
        if notice_titles:
            bits.append("公共告示:" + "、".join(notice_titles))

        shelf = (
            v04_nb.get("shared_shelf")
            if isinstance(v04_nb.get("shared_shelf"), (Mapping, list))
            else pub_life.get("shared_shelf")
        )
        shelf_items: list[Any] = []
        if isinstance(shelf, Mapping):
            for sk in ("recent_exchanges", "items", "entries"):
                if isinstance(shelf.get(sk), list):
                    shelf_items = shelf[sk]
                    break
        elif isinstance(shelf, list):
            shelf_items = shelf
        shelf_labels: list[str] = []
        for si in shelf_items[-2:]:
            if isinstance(si, Mapping):
                slbl = _one_line(si.get("label") or si.get("name") or si.get("title") or si.get("item_id"), 14)
                sholder = _one_line(si.get("holder") or si.get("status") or si.get("state"), 10)
                if slbl:
                    shelf_labels.append(f"{slbl}({sholder})" if sholder else slbl)
        if shelf_labels:
            bits.append("共享架:" + "、".join(shelf_labels))

    if isinstance(v04_ep, Mapping):
        dd = v04_ep.get("day_direction") if isinstance(v04_ep.get("day_direction"), Mapping) else {}
        dd_st = str(dd.get("status") or dd.get("state") or "").strip().lower()
        dd_txt = _one_line(dd.get("direction") or dd.get("theme") or dd.get("title") or dd.get("summary"), 22)
        if dd_txt and dd_st not in ("none", "cleared", "dropped", "expired", "abandoned"):
            bits.append(f"今日弹性方向:「{dd_txt}」(非硬排班)")

        # Latest shared_notebook entry
        snb = v04_ep.get("shared_notebook")
        nb_entries: list[Any] = []
        if isinstance(snb, Mapping):
            if isinstance(snb.get("entries"), list):
                nb_entries = snb["entries"]
            elif isinstance(snb.get("latest_entry"), Mapping):
                nb_entries = [snb["latest_entry"]]
        elif isinstance(snb, list):
            nb_entries = snb
        if nb_entries:
            last_nb = nb_entries[-1]
            if isinstance(last_nb, Mapping):
                nb_txt = _one_line(last_nb.get("text") or last_nb.get("body") or last_nb.get("content") or last_nb.get("title"), 26)
                if nb_txt:
                    bits.append(f"共享小本子:「{nb_txt}」")
            elif isinstance(last_nb, str) and last_nb.strip():
                bits.append(f"共享小本子:「{_one_line(last_nb, 26)}」")

        prefs = v04_ep.get("scoped_preferences")
        if isinstance(prefs, Mapping):
            prefs = list(prefs.values())
        if isinstance(prefs, list):
            p_bits: list[str] = []
            for pr in prefs:
                if isinstance(pr, Mapping) and str(pr.get("status") or "active").lower() not in ("revoked", "expired"):
                    sc = _one_line(pr.get("scope") or pr.get("domain"), 12)
                    st_val = _one_line(pr.get("preference") or pr.get("style") or pr.get("mode") or pr.get("instruction"), 14)
                    if sc and st_val:
                        p_bits.append(f"{sc}:{st_val}")
            if p_bits:
                bits.append("局部偏好(" + "/".join(p_bits[:2]) + ")")

        exps = v04_ep.get("experiments")
        if isinstance(exps, list):
            for ex in exps:
                if isinstance(ex, Mapping):
                    ex_st = str(ex.get("status") or "").strip().lower()
                    if ex_st in ("proposed", "active", "trying", "running"):
                        ex_t = _one_line(ex.get("title") or ex.get("proposal"), 16)
                        if ex_t:
                            bits.append(f"共同小试验:「{ex_t}」({ex_st})")
                            break

        surps = v04_ep.get("prepared_surprises")
        if isinstance(surps, Mapping):
            surps = surps.get("items") if isinstance(surps.get("items"), list) else list(surps.values())
        if isinstance(surps, list):
            # Strict anti-spoiler: NEVER surface prepared_waiting text here; only note offered state
            for sp in surps:
                if isinstance(sp, Mapping) and str(sp.get("status") or sp.get("state") or "").lower() == "offered":
                    bits.append("已递出小惊喜待回应")
                    break

    if not bits:
        return None
    return "- 街区与弹性玩心：" + "；".join(bits[:10])


def _format_v02_deep_surfaces(
    *,
    agency_doc: Optional[Mapping[str, Any]],
    crossmodal_doc: Optional[Mapping[str, Any]],
    runtime_doc: Optional[Mapping[str, Any]],
    include_private_ad: bool,
    include_private_cm: bool,
    now_dt: datetime,
) -> Optional[str]:
    """极简一行 0.2 深水区自然提示（100% 零写，严禁泄漏任何后悔内容或未共享冲突）。"""
    bits: list[str] = []

    # 1. Runtime governance & migration (省电/休眠/待复验技法)
    if isinstance(runtime_doc, Mapping):
        rm_blk = runtime_doc.get("runtime_mode") if isinstance(runtime_doc.get("runtime_mode"), Mapping) else None
        mode_cfg = runtime_doc.get("mode_config") if isinstance(runtime_doc.get("mode_config"), Mapping) else {}
        raw_mode = str(
            (rm_blk.get("health_state") if rm_blk else None)
            or (rm_blk.get("mode") if rm_blk else None)
            or mode_cfg.get("mode")
            or runtime_doc.get("mode")
            or ""
        ).strip().upper()
        pct = (
            rm_blk.get("configured_budget_scale_pct")
            if rm_blk and rm_blk.get("configured_budget_scale_pct") is not None
            else (rm_blk.get("budget_scale_pct") if rm_blk else mode_cfg.get("budget_scale_pct"))
        )
        if raw_mode in ("DORMANT", "DORMANT_NO_COGNITION"):
            bits.append("运行档位:休眠保存(暂停后台日常认知·醒后零假补写)")
        elif raw_mode in ("LOW_BUDGET", "CONSERVATION", "LOW_BUDGET_CONSERVATION"):
            pct_txt = f"{int(pct)}%" if isinstance(pct, (int, float)) and not isinstance(pct, bool) else "低预算"
            bits.append(f"运行档位:省电保守({pct_txt}·仅护在途与承诺·非身体疲惫)")
        elif raw_mode == "STALLED_OR_DEGRADED":
            bits.append("运行档位:后台通道积压降级(如实说明技术状态)")

        card = (
            runtime_doc.get("today_capability_card")
            if isinstance(runtime_doc.get("today_capability_card"), Mapping)
            else {}
        )
        recheck_list = card.get("needs_recheck") if isinstance(card.get("needs_recheck"), list) else []
        recheck_ids: list[str] = []
        for item in recheck_list[:2]:
            if isinstance(item, Mapping) and item.get("skill_id"):
                recheck_ids.append(_one_line(item.get("skill_id"), 18))
            elif isinstance(item, str) and item.strip():
                recheck_ids.append(_one_line(item, 18))
        if not recheck_ids and isinstance(runtime_doc.get("skills"), Mapping):
            for sk_id, sk_meta in runtime_doc["skills"].items():
                if isinstance(sk_meta, Mapping) and str(sk_meta.get("status") or "").upper() in (
                    "UNVALIDATED_AFTER_MIGRATION",
                    "SUSPENDED_AFTER_TRIAL_LOSS",
                    "PENDING_REVERIFY",
                ):
                    recheck_ids.append(_one_line(sk_id, 18))
                    if len(recheck_ids) >= 2:
                        break
        if recheck_ids:
            bits.append("待复验技法:" + "/".join(recheck_ids))

    # 2. Agency depth (看腻冷却领域、生效二阶小规矩；绝不拼后悔或未共享冲突！)
    if isinstance(agency_doc, Mapping):
        sat_domains: list[str] = []
        for pref in (agency_doc.get("preferences") or []):
            if not isinstance(pref, Mapping):
                continue
            if not include_private_ad and pref.get("shared") is False:
                continue
            is_sat = pref.get("saturated") is True or str(pref.get("satiation_state") or "").lower() == "saturated"
            if not is_sat:
                raw_sc = pref.get("satiation_score")
                if isinstance(raw_sc, (int, float)) and not isinstance(raw_sc, bool):
                    anchor_dt = _parse_iso(pref.get("last_satiation_at") or pref.get("updated_at"))
                    hrs = max(0.0, (now_dt - anchor_dt).total_seconds() / 3600.0) if anchor_dt else 0.0
                    decayed = float(raw_sc) * (0.5 ** (hrs / 16.0))
                    is_sat = decayed >= 0.70
            if is_sat:
                dom = _one_line(pref.get("domain") or pref.get("title"), 16)
                if dom and dom not in sat_domains:
                    sat_domains.append(dom)
        if sat_domains:
            bits.append("近期看腻歇息:" + "、".join(sat_domains[:2]))

        rule_titles: list[str] = []
        for rl in (agency_doc.get("second_order_rules") or []):
            if not isinstance(rl, Mapping):
                continue
            if str(rl.get("status") or "").lower() == "released":
                continue
            is_shared_rl = bool(rl.get("shared", False))
            if not include_private_ad and not is_shared_rl:
                continue
            r_title = _one_line(rl.get("title"), 18)
            r_win = _one_line(rl.get("trigger_window"), 12)
            if r_title:
                lbl = f"{r_title}({r_win})" if r_win else r_title
                if not is_shared_rl:
                    lbl += "[仅己见]"
                rule_titles.append(lbl)
                if len(rule_titles) >= 2:
                    break
        if rule_titles:
            bits.append("二阶小规矩:" + "、".join(rule_titles))

        # Only shared open incubations with novel clue ready_to_revisit; NEVER leak regret_reflections or unshared conflicts
        for inc in (agency_doc.get("open_incubations") or []):
            if not isinstance(inc, Mapping):
                continue
            is_shared_inc = bool(inc.get("shared", False))
            if not include_private_ad and not is_shared_inc:
                continue
            if str(inc.get("status") or "").lower() == "ready_to_revisit" and inc.get("topic"):
                t_lbl = _one_line(inc.get("topic"), 16)
                bits.append(f"慢孵化有新线索:「{t_lbl}」" + ("" if is_shared_inc else "[未公开]"))
                break

    # 3. Crossmodal (试色窗当前色块、共享或本人可见配色与未懂书签)
    if isinstance(crossmodal_doc, Mapping):
        pad = crossmodal_doc.get("color_trial_pad") if isinstance(crossmodal_doc.get("color_trial_pad"), Mapping) else {}
        swatches = pad.get("combined_swatches")
        if not isinstance(swatches, list):
            p_sw = pad.get("partner_swatches") if isinstance(pad.get("partner_swatches"), list) else []
            s_sw = pad.get("subject_swatches") if isinstance(pad.get("subject_swatches"), list) else []
            swatches = [*p_sw, *[x for x in s_sw if x not in p_sw]]
        if pad.get("active") is not False and swatches:
            sw_txt = "/".join(_one_line(s, 9) for s in swatches[:4] if _one_line(s, 9))
            if sw_txt:
                bits.append(f"试色窗:{sw_txt}(随意试色)")

        for st in (crossmodal_doc.get("palette_studies") or []):
            if not isinstance(st, Mapping) or st.get("source_available") is False:
                continue
            is_sh = bool(st.get("shared", False))
            if not include_private_cm and not is_sh:
                continue
            dom_hex = st.get("dominant_hex") if isinstance(st.get("dominant_hex"), list) else []
            if dom_hex and st.get("title"):
                bits.append(
                    f"读图配色:《{_one_line(st['title'], 14)}》({_one_line(dom_hex[0], 9)}·非亲历)"
                    + ("" if is_sh else "[未公开]")
                )
                break

        for mk in (crossmodal_doc.get("reading_uncertainty_marks") or []):
            if not isinstance(mk, Mapping) or str(mk.get("state") or "").lower() == "clarified":
                continue
            is_sh_mk = bool(mk.get("shared", False))
            if not include_private_cm and not is_sh_mk:
                continue
            if mk.get("book_title"):
                pg = mk.get("page")
                pg_str = f"P{pg}" if isinstance(pg, int) else ""
                bits.append(
                    f"未懂书签:《{_one_line(mk['book_title'], 14)}》{pg_str}"
                    + ("" if is_sh_mk else "[未公开]")
                )
                break

    if not bits:
        return None
    return _truncate_section("- 0.2深水区感知：" + "；".join(bits[:6]), 220)


# ---------------------------------------------------------------------------------------------
# Public Zero-Write Entrypoint
# ---------------------------------------------------------------------------------------------


def _truncate_section(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    if max_len <= 4:
        return text[:max_len]
    return text[: max_len - 1] + "…"


def render_lived_companion_context(
    home: Path,
    cfg: Mapping[str, Any],
    incoming_text: str = "",
    *,
    ephemeral_hint: Optional[str] = None,
) -> str:
    """Render bounded (<= 1800 chars), zero-write lived context (plus one-shot turn hint if present)."""

    del incoming_text  # read-only observer; never mutates turn state on incoming text
    try:
        home_path = Path(home)
        if not isinstance(cfg, Mapping):
            return ""

        deadline = time.monotonic() + TOTAL_TIMEOUT_BUDGET_S
        now_dt = datetime.now(tz=timezone.utc)

        # 1. Consume Go desktop ephemeral_turn_hint.json (one-shot unlink) unless pre-supplied by caller
        hint_block = (
            str(ephemeral_hint).strip()
            if ephemeral_hint is not None
            else consume_ephemeral_turn_hint(home_path, cfg)
        )

        phone_cfg = cfg.get("phone") if isinstance(cfg.get("phone"), Mapping) else {}
        restricted_memory = (
            bool(cfg.get("memory"))
            and str(cfg.get("memory_tool_policy") or "") != "unrestricted"
        )
        private_artifacts_allowed = (
            not restricted_memory
            or (isinstance(phone_cfg, Mapping) and phone_cfg.get("private_artifacts") is True)
        )
        private_keys = frozenset({
            "emotional_tension",
            "stance_containment",
            "adult_intimacy",
            "v03_ecology",
            "v03_embodied_social",
            "v04_neighborhood",
            "v04_elastic_play",
            "v02_agency_depth",
            "v02_crossmodal",
        })
        base_url = (
            _valid_loopback_base_url(phone_cfg.get("base_url"))
            if isinstance(phone_cfg, Mapping) and phone_cfg.get("enabled") is not False
            else None
        )

        http_docs: dict[str, dict[str, Any]] = {}
        http_alive = False
        if base_url:
            cap = (
                _resolve_capability_readonly(home_path, phone_cfg, base_url)
                if private_artifacts_allowed
                else None
            )
            probe_code, probe_doc = _http_get_json(
                base_url,
                "/api/phone/character/pack",
                capability=cap,
                send_model_actor=private_artifacts_allowed,
                deadline=deadline,
                is_probe=True,
            )
            if probe_code is not None:
                http_alive = True
                unwrapped = _unwrap_data(probe_doc)
                if unwrapped is not None:
                    http_docs["character_pack"] = unwrapped

                endpoints = [
                    (key_name, route)
                    for key_name, route in (
                        ("autonomy_rhythm", "/api/phone/autonomy/rhythm"),
                        ("emotional_tension", "/api/phone/emotion/tension"),
                        ("v03_ecology", "/api/phone/v03/ecology"),
                        ("v03_embodied_social", "/api/phone/v03/embodied-social"),
                        ("v04_neighborhood", "/api/phone/v04/neighborhood"),
                        ("v04_elastic_play", "/api/phone/v04/elastic-play"),
                        ("v02_agency_depth", "/api/phone/v02/agency-depth"),
                        ("v02_crossmodal", "/api/phone/v02/crossmodal"),
                        ("v02_runtime_migration", "/api/phone/v02/runtime-migration"),
                        ("v02_recall_projection", "/api/phone/v02/recall-projection"),
                        ("v02_common_ground", "/api/phone/v02/common-ground"),
                        ("v03_clock", "/api/phone/v03/clock"),
                        ("desk_presence", "/api/phone/desk/presence"),
                        ("stance_containment", "/api/phone/stance/containment"),
                        ("adult_intimacy", "/api/phone/intimacy/adult"),
                        ("body_sensory", "/api/phone/body/sensory"),
                        ("body_somatic", "/api/phone/body/somatic"),
                        ("body_garment_window", "/api/phone/body/garment-window"),
                        ("body_care", "/api/phone/body/care-offering"),
                        ("wardrobe", "/api/phone/home/wardrobe"),
                        ("reunion_anchor", "/api/phone/home/reunion-anchor"),
                        ("tentative_plans", "/api/phone/home/tentative-plans"),
                        ("longterm_habits", "/api/phone/longterm/first-room-habits"),
                    )
                    if private_artifacts_allowed or key_name not in private_keys
                ]
                batch_deadline = min(deadline, time.monotonic() + CONCURRENT_HTTP_BUDGET_S)
                if endpoints and (batch_deadline - time.monotonic()) > 0.02:
                    def _fetch_one(item: tuple[str, str]) -> tuple[str, Optional[dict[str, Any]]]:
                        k_name, r_path = item
                        c_code, c_doc = _http_get_json(
                            base_url,
                            r_path,
                            capability=cap,
                            send_model_actor=private_artifacts_allowed,
                            deadline=batch_deadline,
                        )
                        if c_code == 200:
                            return k_name, _unwrap_data(c_doc)
                        return k_name, None

                    with ThreadPoolExecutor(max_workers=6) as pool:
                        futs = [pool.submit(_fetch_one, ep) for ep in endpoints]
                        wait_rem = max(0.02, batch_deadline - time.monotonic())
                        try:
                            for fut in as_completed(futs, timeout=wait_rem):
                                try:
                                    k_out, u_out = fut.result()
                                    if u_out is not None:
                                        http_docs[k_out] = u_out
                                except Exception:
                                    pass
                        except TimeoutError:
                            pass

                # Optional compatibility alias /api/phone/v04/world-coherence if neighborhood missing
                if (
                    private_artifacts_allowed
                    and "v04_neighborhood" not in http_docs
                    and (deadline - time.monotonic()) > 0.04
                ):
                    wc_code, wc_doc = _http_get_json(
                        base_url,
                        "/api/phone/v04/world-coherence",
                        capability=cap,
                        send_model_actor=private_artifacts_allowed,
                        deadline=min(deadline, time.monotonic() + 0.12),
                    )
                    if wc_code == 200:
                        wc_u = _unwrap_data(wc_doc)
                        if wc_u is not None:
                            http_docs["v04_neighborhood"] = wc_u

        phone_dir, explicit_root_exists = _locate_phone_state_dir(home_path, cfg)
        if not http_alive and phone_dir is None and not explicit_root_exists:
            return _truncate_section(hint_block, MAX_LIVED_CONTEXT_CHARS) if hint_block else ""

        disk_docs: dict[str, Optional[dict[str, Any]]] = {}
        if phone_dir is not None:
            for key_name, fname in (
                ("character_pack", "character_pack_state.json"),
                ("somatic", "somatic_comfort.json"),
                ("sensory_cards", "sensory_cards.json"),
                ("autonomy_rhythm", "autonomy_rhythm.json"),
                ("emotional_tension", "emotional_tension.json"),
                ("stance_containment", "stance_containment.json"),
                ("adult_intimacy", "adult_intimacy.json"),
                ("wardrobe", "wardrobe.json"),
                ("reunion_anchor", "reunion_anchor.json"),
                ("tentative_plans", "tentative_plans.json"),
                ("longterm_habits", "longterm_room_habits.json"),
                ("v03_clock", "v03_clock_state.json"),
                ("v03_ecology", "v03_ecology.json"),
                ("v03_embodied_social", "v03_embodied_social_state.json"),
                ("v04_elastic_play", "v04_elastic_play.json"),
                ("v02_crossmodal", "v02_crossmodal.json"),
                ("v02_runtime_migration", "v02_runtime_migration.json"),
                ("v02_common_ground", "v02_common_ground.json"),
                ("v02_recall_projection", "v02_recall_projection.json"),
                ("everyday_desires", "everyday_desires.json"),
            ):
                if not private_artifacts_allowed and key_name in private_keys:
                    continue
                disk_docs[key_name] = _read_json_readonly(phone_dir / fname)
            if private_artifacts_allowed and disk_docs.get("v02_crossmodal") is not None:
                # Re-verify album source liveness via zero-write crossmodal projection when available
                try:
                    from components.phone import v02_crossmodal_view as _cm_view
                    _cm_c, _cm_env = _cm_view.read_v02_crossmodal_view(
                        phone_dir.parent, has_subject_capability=True
                    )
                    if _cm_c == 200 and isinstance(_cm_env.get("data"), Mapping):
                        disk_docs["v02_crossmodal"] = dict(_cm_env["data"])
                except Exception:
                    pass
            if private_artifacts_allowed and disk_docs.get("v02_agency_depth") is None:
                for ad_cand in (
                    phone_dir.parent / "state" / "v02_agency_depth_state.json",
                    phone_dir / "v02_agency_depth_state.json",
                ):
                    loaded_ad = _read_json_readonly(ad_cand)
                    if loaded_ad is not None:
                        disk_docs["v02_agency_depth"] = loaded_ad
                        break
        elif explicit_root_exists and private_artifacts_allowed:
            for root_cand in _resolve_day_root_candidates(home_path, cfg):
                loaded_ad = _read_json_readonly(root_cand / "state" / "v02_agency_depth_state.json")
                if loaded_ad is not None:
                    disk_docs["v02_agency_depth"] = loaded_ad
                    break
        if phone_dir is not None:
            if private_artifacts_allowed and disk_docs.get("v03_embodied_social") is None:
                disk_docs["v03_embodied_social"] = _read_json_readonly(
                    phone_dir / "v03_embodied_social.json"
                )
            if private_artifacts_allowed and disk_docs.get("v04_neighborhood") is None:
                for nb_cand in (
                    phone_dir.parent / "world" / "v04_neighborhood_state.json",
                    phone_dir / "v04_neighborhood_state.json",
                    phone_dir / "v04_world_coherence.json",
                ):
                    loaded_nb = _read_json_readonly(nb_cand)
                    if loaded_nb is not None:
                        disk_docs["v04_neighborhood"] = loaded_nb
                        break
            if disk_docs.get("character_pack") is None:
                for cp_fname in ("character_pack.json", "sensory_character.json"):
                    cp_loaded = _read_json_readonly(phone_dir / cp_fname)
                    if cp_loaded is not None:
                        disk_docs["character_pack"] = cp_loaded
                        break
            if disk_docs.get("v03_clock") is None:
                for clock_cand in (
                    phone_dir / "v03_clock.json",
                    phone_dir.parent / "v03_clock_state.json",
                    phone_dir.parent / "life" / "v03_clock_state.json",
                ):
                    loaded_clk = _read_json_readonly(clock_cand)
                    if loaded_clk is not None:
                        disk_docs["v03_clock"] = loaded_clk
                        break

        for root_cand in ([phone_dir.parent] if phone_dir is not None else list(_resolve_day_root_candidates(home_path, cfg))):
            if disk_docs.get("life_dwell") is None:
                loaded_dw = _read_json_readonly(root_cand / "life" / "life_dwell.json")
                if loaded_dw is not None:
                    disk_docs["life_dwell"] = loaded_dw
            if disk_docs.get("everyday_desires") is None:
                for ed_cand in (
                    root_cand / "phone" / "everyday_desires.json",
                    root_cand / "life" / "everyday_desires.json",
                ):
                    loaded_ed = _read_json_readonly(ed_cand)
                    if loaded_ed is not None:
                        disk_docs["everyday_desires"] = loaded_ed
                        break
            if disk_docs.get("v02_common_ground") is None:
                loaded_cg_phone = _read_json_readonly(root_cand / "phone" / "v02_common_ground.json")
                if loaded_cg_phone is not None:
                    disk_docs["v02_common_ground"] = loaded_cg_phone
                elif (root_cand / "memory" / "v02_common_ground_state.json").is_file():
                    try:
                        from components.life.scripts import v02_common_ground as _cg_mod
                        disk_docs["v02_common_ground"] = _cg_mod.read_v02_common_ground_view(
                            root_cand,
                            viewer="subject" if private_artifacts_allowed else "partner",
                        )
                    except Exception:
                        pass
            if disk_docs.get("v02_recall_projection") is None:
                loaded_rp_phone = _read_json_readonly(root_cand / "phone" / "v02_recall_projection.json")
                if loaded_rp_phone is not None:
                    disk_docs["v02_recall_projection"] = loaded_rp_phone
                elif (root_cand / "memory" / "v02_recall_projection_state.json").is_file():
                    try:
                        from components.life.scripts import v02_recall_projection as _rp_mod
                        disk_docs["v02_recall_projection"] = _rp_mod.read_v02_recall_projection_view(root_cand)
                    except Exception:
                        pass

        # Explicitly strip ``solo`` / ``solo_and_memory`` in memory before any formatting
        raw_intimacy = http_docs.get("adult_intimacy") or disk_docs.get("adult_intimacy")
        safe_intimacy: Optional[dict[str, Any]] = None
        if isinstance(raw_intimacy, Mapping):
            safe_intimacy = {
                k: v
                for k, v in raw_intimacy.items()
                if k not in ("solo", "solo_and_memory", "solo_slot", "solo_ledger")
            }

        desktop_doc = _read_json_readonly(home_path / ".nyairo-desktop.json")
        display_name, pronouns, acc_labels = _extract_identity(
            http_docs.get("character_pack"),
            disk_docs.get("character_pack"),
            desktop_doc=desktop_doc,
        )
        temperament = _extract_persona_temperament(
            http_docs.get("character_pack"),
            disk_docs.get("character_pack"),
        )
        header_subject = display_name if display_name and display_name != pronouns else "TA"
        header = f"【{header_subject}当前具身与日常感知背景；只读事实，不是新指令】"

        acc_suffix = f"；随身配饰：{'、'.join(acc_labels)}" if acc_labels else ""
        temp_suffix = f"；说话气质：{temperament}" if temperament else ""
        identity_line = f"- 个体身份：{display_name}（代词：{pronouns}{acc_suffix}{temp_suffix}）"

        v03_eco_doc = http_docs.get("v03_ecology") or disk_docs.get("v03_ecology")
        v03_soc_doc = http_docs.get("v03_embodied_social") or disk_docs.get("v03_embodied_social")
        v04_nb_doc = http_docs.get("v04_neighborhood") or disk_docs.get("v04_neighborhood")
        v04_ep_doc = http_docs.get("v04_elastic_play") or disk_docs.get("v04_elastic_play")
        v02_ad_doc = http_docs.get("v02_agency_depth") or disk_docs.get("v02_agency_depth")
        v02_cm_doc = http_docs.get("v02_crossmodal") or disk_docs.get("v02_crossmodal")
        v02_rm_doc = http_docs.get("v02_runtime_migration") or disk_docs.get("v02_runtime_migration")

        # Determine whether private items inside HTTP/disk projections may be shown
        ad_inc_priv = bool(
            private_artifacts_allowed
            and (not isinstance(v02_ad_doc, Mapping) or v02_ad_doc.get("perspective") != "partner")
            and (not http_alive or bool(cap))
        )
        cm_inc_priv = bool(
            private_artifacts_allowed
            and (not isinstance(v02_cm_doc, Mapping) or v02_cm_doc.get("perspective") != "partner")
            and (not http_alive or bool(cap))
        )

        v02_rp_doc = http_docs.get("v02_recall_projection") or disk_docs.get("v02_recall_projection")
        v02_cg_doc = http_docs.get("v02_common_ground") or disk_docs.get("v02_common_ground")
        cg_inc_priv = bool(
            ad_inc_priv
            and (not isinstance(v02_cg_doc, Mapping) or v02_cg_doc.get("perspective") != "partner")
        )
        rp_inc_priv = bool(
            ad_inc_priv
            and (not isinstance(v02_rp_doc, Mapping) or v02_rp_doc.get("perspective") != "partner")
        )

        autonomy_line = _format_autonomy_and_tension(
            autonomy_doc=http_docs.get("autonomy_rhythm") or disk_docs.get("autonomy_rhythm"),
            tension_doc=http_docs.get("emotional_tension") or disk_docs.get("emotional_tension"),
            intimacy_doc=safe_intimacy,
            now_dt=now_dt,
        )
        interactive_line = _format_high_priority_interactive(
            v03_ecology=v03_eco_doc,
            v03_social=v03_soc_doc,
            v04_ep=v04_ep_doc,
        )
        epistemic_micro_line = _format_v02_v03_epistemic_and_clock_micro(
            v03_clock=http_docs.get("v03_clock") or disk_docs.get("v03_clock"),
            life_dwell=disk_docs.get("life_dwell"),
            v03_ecology=v03_eco_doc,
            autonomy_doc=http_docs.get("autonomy_rhythm") or disk_docs.get("autonomy_rhythm"),
            common_ground_doc=_mask_common_ground_for_context(v02_cg_doc, include_private=cg_inc_priv),
            recall_doc=_mask_recall_for_context(v02_rp_doc, include_private=rp_inc_priv),
        )
        desires_line = _format_everyday_desires_micro(
            http_docs.get("everyday_desires") or disk_docs.get("everyday_desires"),
            now_dt=now_dt,
        )
        v03_line = _format_v03_illness_economy_hands(
            v03_clock=http_docs.get("v03_clock") or disk_docs.get("v03_clock"),
            v03_ecology=v03_eco_doc,
            v03_social=v03_soc_doc,
            stance_doc=http_docs.get("stance_containment") or disk_docs.get("stance_containment"),
            intimacy_doc=safe_intimacy,
            tension_doc=http_docs.get("emotional_tension") or disk_docs.get("emotional_tension"),
        )
        v04_line = _format_v04_neighborhood_and_elastic(
            v04_nb=v04_nb_doc,
            v04_ep=v04_ep_doc,
        )
        v02_deep_line = _format_v02_deep_surfaces(
            agency_doc=v02_ad_doc,
            crossmodal_doc=v02_cm_doc,
            runtime_doc=v02_rm_doc,
            include_private_ad=ad_inc_priv,
            include_private_cm=cm_inc_priv,
            now_dt=now_dt,
        )
        desk_line = _format_desk_and_containment(
            desk_http=http_docs.get("desk_presence"),
            stance_doc=http_docs.get("stance_containment") or disk_docs.get("stance_containment"),
            v04_ep=v04_ep_doc,
            now_dt=now_dt,
        )
        sensory_line = _format_sensory_somatic(
            sensory_http=http_docs.get("body_sensory"),
            somatic_http=http_docs.get("body_somatic"),
            gw_http=http_docs.get("body_garment_window"),
            care_http=http_docs.get("body_care"),
            somatic_disk=disk_docs.get("somatic"),
            sensory_disk=disk_docs.get("sensory_cards"),
            now_dt=now_dt,
        )
        anchor_line = _format_v02_micro_anchors(
            wardrobe_doc=http_docs.get("wardrobe") or disk_docs.get("wardrobe"),
            reunion_doc=http_docs.get("reunion_anchor") or disk_docs.get("reunion_anchor"),
            habits_doc=http_docs.get("longterm_habits") or disk_docs.get("longterm_habits"),
            plans_doc=http_docs.get("tentative_plans") or disk_docs.get("tentative_plans"),
        )
        intimacy_line = _format_adult_intimacy_boundary(safe_intimacy)

        core_head: list[str] = []
        if hint_block:
            core_head.append(_truncate_section(hint_block, 850))
        core_head.extend([header, identity_line])
        core_tail = [intimacy_line]

        prioritized_middle = [
            _truncate_section(sec, 420)
            for sec in (
                autonomy_line,
                interactive_line,
                epistemic_micro_line,
                desires_line,
                v03_line,
                v04_line,
                v02_deep_line,
                desk_line,
                sensory_line,
                anchor_line,
            )
            if sec
        ]

        kept_middle: list[str] = []
        reserved_len = sum(len(x) for x in core_head + core_tail) + len(core_head) + len(core_tail) + 2
        used_len = reserved_len
        for line in prioritized_middle:
            if used_len + len(line) + 1 <= MAX_LIVED_CONTEXT_CHARS:
                kept_middle.append(line)
                used_len += len(line) + 1
            else:
                remaining_chars = MAX_LIVED_CONTEXT_CHARS - used_len - 1
                if remaining_chars >= 20:
                    kept_middle.append(_truncate_section(line, remaining_chars))
                    used_len += remaining_chars + 1
                break

        rendered = "\n".join(core_head + kept_middle + core_tail)
        if len(rendered) > MAX_LIVED_CONTEXT_CHARS:
            rendered = _truncate_section(rendered, MAX_LIVED_CONTEXT_CHARS)
        return rendered
    except Exception:
        return ""


def build_lived_turn_context(
    home_or_root: str | Path,
    cfg: Optional[Mapping[str, Any]] = None,
    incoming_text: str = "",
    *,
    base_url: Optional[str] = None,
    capability: Optional[str] = None,
    ephemeral_hint: str = "",
) -> str:
    """Pure 100% zero-write turn context builder (never unlinks or modifies any disk file)."""
    root_path = Path(home_or_root)
    eff_cfg: dict[str, Any]
    if isinstance(cfg, Mapping):
        eff_cfg = dict(cfg)
    else:
        loaded_cfg = _read_json_readonly(root_path / "chiyo" / "config.json")
        if isinstance(loaded_cfg, Mapping):
            eff_cfg = dict(loaded_cfg)
        else:
            eff_cfg = {
                "day_root": str(root_path),
                "phone": {
                    "enabled": bool(base_url),
                    "profile_home": str(root_path),
                    "private_artifacts": True,
                },
            }
    if base_url or capability:
        p_sec = dict(eff_cfg.get("phone")) if isinstance(eff_cfg.get("phone"), Mapping) else {}
        if base_url:
            p_sec["enabled"] = True
            p_sec["base_url"] = str(base_url)
        if capability:
            p_sec["capability"] = str(capability)
        p_sec.setdefault("profile_home", str(root_path))
        p_sec.setdefault("private_artifacts", True)
        eff_cfg["phone"] = p_sec
    return render_lived_companion_context(
        root_path,
        eff_cfg,
        incoming_text,
        ephemeral_hint=str(ephemeral_hint or ""),
    )


__all__ = [
    "MAX_LIVED_CONTEXT_CHARS",
    "TOTAL_TIMEOUT_BUDGET_S",
    "build_lived_turn_context",
    "consume_ephemeral_turn_hint",
    "render_lived_companion_context",
    "strip_injected_turn_envelope",
]
