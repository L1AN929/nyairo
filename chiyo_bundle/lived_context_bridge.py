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
3. **动态中立身份（拒绝写死特定角色名或性别代词）**：名字与代词统一从角色包读取，缺省回退 ``TA``。
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
from typing import Any, Mapping, Optional, Sequence
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


def _one_line_clean(value: Any, limit: int = 80) -> str:
    if value is None:
        return ""
    text = " ".join(str(value).replace("\x00", "").split()).strip()
    if len(text) <= limit:
        return text
    if limit <= 2:
        return text[:limit]
    return text[: limit - 1].rstrip("，、；：,;: ") + "…"


def _desire_matches_surfaced(candidate: str, surfaced_texts: Optional[Sequence[str]]) -> bool:
    if not surfaced_texts:
        return False
    cand = " ".join(str(candidate or "").split()).strip().rstrip("…")
    if not cand:
        return False
    for raw in surfaced_texts:
        surf = " ".join(str(raw or "").split()).strip().rstrip("…")
        if not surf:
            continue
        if cand == surf:
            return True
        shorter, longer = (cand, surf) if len(cand) <= len(surf) else (surf, cand)
        if len(shorter) >= 6 and longer.startswith(shorter):
            return True
    return False


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


def _read_microzone_ecology_view_zero_write(
    root: Path, *, include_private: bool
) -> Optional[Mapping[str, Any]]:
    """Read the microzone ecology projection read-only, or ``None`` when unavailable.

    Worker 8A owns ``v08_microzone_ecology``; that module may not exist yet, so this
    import is deliberately guarded and degrades to ``None`` instead of raising.  It
    never creates a directory, file or mtime -- the projection is a pure read.
    """

    mod: Any = None
    try:
        from components.world.scripts import v08_microzone_ecology as mod  # type: ignore[no-redef]
    except Exception:  # noqa: BLE001 - absence is a clean no-op
        try:
            import v08_microzone_ecology as mod  # type: ignore[no-redef]
        except Exception:  # noqa: BLE001
            return None
    reader = getattr(mod, "read_microzone_ecology_view", None)
    if not callable(reader):
        return None
    actor = "subject" if include_private else "partner"
    try:
        # The real engine (Worker 8A) takes ``actor_side`` and redacts the private
        # work breakpoint itself under the partner door; the keyword ladders below
        # keep this safe should a different signature ever land.
        view = reader(root, actor_side=actor)
    except TypeError:
        try:
            view = reader(root, include_private=include_private)
        except TypeError:
            try:
                view = reader(root)
            except Exception:  # noqa: BLE001
                return None
        except Exception:  # noqa: BLE001
            return None
    except Exception:  # noqa: BLE001
        return None
    return view if isinstance(view, Mapping) else None


def _read_matter_space_projection_zero_write(
    root: Path, *, include_private: bool, now_iso: Optional[str] = None
) -> Optional[Mapping[str, Any]]:
    """Read the real physical room read-only, partner-safe by construction.

    ``include_private=False`` asks the engine for its partner door, which already
    omits private zones, private objects and unshared corner names -- so nothing
    here can leak a private fact.  A missing engine or state file degrades to
    ``None`` and never creates a byte.
    """

    try:
        from components.world.scripts import v08_universal_matter as matter  # type: ignore[no-redef]
    except Exception:  # noqa: BLE001 - absence is a clean no-op
        try:
            import v08_universal_matter as matter  # type: ignore[no-redef]
        except Exception:  # noqa: BLE001
            return None
    actor = "subject" if include_private else "partner"
    try:
        view = matter.read_matter_space_view(root, actor_side=actor, now_iso=now_iso)
    except Exception:  # noqa: BLE001
        return None
    return view if isinstance(view, Mapping) else None


def _body_num_or_none(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    fval = float(value)
    if fval != fval:
        return None
    return fval


def _read_body_runtime_zero_write(
    day_root: Optional[Path | str],
) -> Optional[dict[str, Any]]:
    """Read acute body runtime.json via resolve_instance_paths (100% zero-write).

    Surfaces ONLY genuinely notable acute somatic states for TA (high fatigue,
    hunger, thirst, toilet urgency, thermal chill/overheat, or active sleep_like
    recovery). Calm/normal defaults produce ``has_notable_acute=False`` and
    ``notable_cues=[]`` so the Calm Gate stays silent.
    """
    if day_root is None:
        return None
    root_path = Path(day_root)
    candidates: list[Path] = []
    try:
        from components.world.scripts import instance_paths as _ip
        candidates.append(_ip.resolve_instance_paths(root_path).body_runtime_json)
    except Exception:
        try:
            import instance_paths as _ip  # type: ignore[no-redef]
            candidates.append(_ip.resolve_instance_paths(root_path).body_runtime_json)
        except Exception:
            pass
    for fallback in (
        root_path / "one-day" / "world-home" / "data" / "body" / "runtime.json",
        root_path / "world-home" / "data" / "body" / "runtime.json",
        root_path / "data" / "body" / "runtime.json",
    ):
        if fallback not in candidates:
            candidates.append(fallback)

    doc: Optional[dict[str, Any]] = None
    for cand in candidates:
        loaded = _read_json_readonly(cand)
        if isinstance(loaded, Mapping):
            doc = dict(loaded)
            break
    if doc is None:
        return None

    somatic = doc.get("somatic") if isinstance(doc.get("somatic"), Mapping) else {}
    lived_needs = doc.get("lived_needs") if isinstance(doc.get("lived_needs"), Mapping) else {}
    levels = lived_needs.get("levels") if isinstance(lived_needs.get("levels"), Mapping) else {}
    recovery = doc.get("recovery") if isinstance(doc.get("recovery"), Mapping) else {}

    fatigue = _body_num_or_none(somatic.get("fatigue"))
    warmth = _body_num_or_none(somatic.get("warmth"))
    hunger = _body_num_or_none(levels.get("hunger"))
    thirst = _body_num_or_none(levels.get("thirst"))
    toilet = _body_num_or_none(levels.get("toilet"))

    rec_state = str(recovery.get("state") or "inactive").strip().lower()
    rec_mode = str(recovery.get("mode") or "").strip().lower()
    sleep_like_active = rec_state == "active" and rec_mode == "sleep_like"

    notable_cues: list[str] = []
    if sleep_like_active:
        notable_cues.append("正处在深睡休养中")
    if fatigue is not None and fatigue >= 0.65:
        notable_cues.append("眼下颇为困倦疲惫" if fatigue >= 0.80 else "有些疲惫犯困")
    if hunger is not None and hunger >= 0.65:
        notable_cues.append("肚子已经很饿" if hunger >= 0.85 else "有点饿了")
    if thirst is not None and thirst >= 0.65:
        notable_cues.append("嗓子发干很想喝水" if thirst >= 0.85 else "有些口渴")
    if toilet is not None and toilet >= 0.75:
        notable_cues.append("有点想去趟洗手间")
    if warmth is not None and warmth <= 0.35:
        notable_cues.append("身上偏凉手脚发冷" if warmth <= 0.22 else "觉得有点凉")
    elif warmth is not None and warmth >= 0.75:
        notable_cues.append("身上发闷偏热")

    return {
        "present": True,
        "has_notable_acute": bool(notable_cues),
        "notable_cues": notable_cues,
        "fatigue": round(fatigue, 4) if fatigue is not None else None,
        "warmth": round(warmth, 4) if warmth is not None else None,
        "hunger": round(hunger, 4) if hunger is not None else None,
        "thirst": round(thirst, 4) if thirst is not None else None,
        "toilet": round(toilet, 4) if toilet is not None else None,
        "sleep_like_active": sleep_like_active,
        "recovery_state": rec_state,
        "recovery_mode": rec_mode,
    }


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
    body_runtime: Optional[Mapping[str, Any]] = None,
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
            gdry = {"wet": "淋湿", "damp": "微潮", "dry": "干爽"}.get(str(g.get("dryness") or ""), "")
            gact = "穿着" if g.get("state") == "wear" else "挂起晾着"
            garment_desc = f"{gact}{gdry}外衣「{glbl}」"
    elif isinstance(somatic_disk, Mapping):
        win_state = _one_line((somatic_disk.get("window") or {}).get("state"), 20)
        zone = _one_line((somatic_disk.get("local_position") or {}).get("zone"), 20)
        g = somatic_disk.get("garment") if isinstance(somatic_disk.get("garment"), Mapping) else {}
        if g.get("state") in ("wear", "hang") and (g.get("label") or g.get("garment_id")):
            glbl = _one_line(g.get("label") or g.get("garment_id"), 24)
            gdry = {"wet": "淋湿", "damp": "微潮", "dry": "干爽"}.get(str(g.get("dryness") or ""), "")
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

    if isinstance(body_runtime, Mapping) and body_runtime.get("has_notable_acute"):
        acute_cues = [
            _one_line(c, 24)
            for c in (body_runtime.get("notable_cues") or [])
            if isinstance(c, str) and c.strip()
        ]
        if acute_cues:
            parts.append("当下躯体体感:" + "、".join(acute_cues[:4]))

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
            # Only a *witnessed* miss may surface; a bare ``impression_stale``
            # is the God-view fact that something moved unseen and must never
            # leak into her context before discovery.
            if it.get("not_at_last_seen_place") is True:
                stale_notes.append(f"{lbl}(不在原印象位置，未知新位置)")
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


#: Mirrors of ``components/phone/emotional_tension_view.py`` expiry windows.  A
#: disk fallback reads the raw committed state, so the bridge itself must apply
#: the same TTLs instead of re-surfacing expired short moods / favors / notes.
_ET_SHORT_SPACE_SECONDS = 2 * 3600
_ET_FAVOR_EXPIRE_SECONDS = 2 * 3600
_ET_MISSING_KEEP_SECONDS = 3 * 86400
_ET_DEAD_STATUSES = frozenset({
    "expired", "resolved", "closed", "faded", "cancelled", "canceled",
    "done", "ended", "withdrawn", "revoked",
})


def _et_row_is_dead(
    row: Mapping[str, Any],
    now_dt: datetime,
    *,
    ttl_seconds: Optional[int] = None,
    since_key: str = "declared_at",
) -> bool:
    """True when a raw/projected row must not re-enter lived context."""

    if row.get("active_now") is False:
        return True
    status = str(row.get("status") or "").strip().lower()
    if status in _ET_DEAD_STATUSES:
        return True
    expires_dt = _parse_iso(row.get("expires_at"))
    if expires_dt is not None and now_dt >= expires_dt:
        return True
    if ttl_seconds is not None:
        since_dt = _parse_iso(row.get(since_key))
        if since_dt is not None and (now_dt - since_dt).total_seconds() >= ttl_seconds:
            return True
    return False


def _et_space_rows(
    quarrel_doc: Mapping[str, Any], now_dt: datetime
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    """Return ``(all_rows, live_rows)`` for spaces, supporting both shapes."""

    raw = quarrel_doc.get("active_spaces") or quarrel_doc.get("spaces")
    if not isinstance(raw, (list, Mapping)):
        raw = quarrel_doc.get("spaces_by_incident")
    if isinstance(raw, Mapping):
        rows = [row for row in raw.values() if isinstance(row, Mapping)]
    elif isinstance(raw, list):
        rows = [row for row in raw if isinstance(row, Mapping)]
    else:
        rows = []
    live = [
        row
        for row in rows
        if not _et_row_is_dead(
            row,
            now_dt,
            ttl_seconds=(
                _ET_SHORT_SPACE_SECONDS
                if str(row.get("space_style") or "").upper() == "SHORT"
                else None
            ),
            since_key="declared_at",
        )
    ]
    return rows, live


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

        all_spaces, live_spaces = _et_space_rows(q, now_dt)

        def _space_cue(row: Mapping[str, Any]) -> str:
            style = _one_line(
                row.get("space_style") or row.get("style") or "UNTIL_SLEEP", 16
            )
            return f"已开口要独处空间({style}，仍正常简短回话)"

        if q.get("in_space") is True:
            if live_spaces:
                cues.append(_space_cue(live_spaces[0]))
            elif not all_spaces:
                # Legacy shape with no space rows at all: keep the honest cue.
                cues.append("已开口要独处空间(仍正常简短回话)")
            # Every declared space has expired -> stay silent (no resurrection).
        elif live_spaces:
            cues.append(_space_cue(live_spaces[0]))

        lm = tension_doc.get("low_mood") if isinstance(tension_doc.get("low_mood"), Mapping) else {}
        if lm.get("in_low_mood_spell") is True:
            cues.append("正处轻微无因低落窗口(有点闷，与伴侣无关，不必强行哄好)")

        m = (
            tension_doc.get("missing_partner")
            if isinstance(tension_doc.get("missing_partner"), Mapping)
            else (tension_doc.get("missing_domain") if isinstance(tension_doc.get("missing_domain"), Mapping) else {})
        )
        quiet_space_active = bool(
            (q.get("in_space") is True and not all_spaces)
            or any(
                str(sp.get("style") or sp.get("space_style") or "").upper() == "QUIET"
                for sp in live_spaces
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
            live_notes = [
                note
                for note in notes
                if isinstance(note, Mapping)
                and note.get("summary")
                and not _et_row_is_dead(
                    note,
                    now_dt,
                    ttl_seconds=_ET_MISSING_KEEP_SECONDS,
                    since_key="created_at",
                )
            ]
            if live_notes:
                # Newest valid note first: an expired/short mood never resurrects,
                # and raw disk order (newest-first) is not trusted blindly.
                live_notes.sort(
                    key=lambda note: str(note.get("created_at") or ""), reverse=True
                )
                n0 = live_notes[0]
                cues.append(f"想念留话:「{_one_line(n0['summary'], 26)}」")

        ask = (
            tension_doc.get("asking_partner")
            if isinstance(tension_doc.get("asking_partner"), Mapping)
            else (tension_doc.get("asking_domain") if isinstance(tension_doc.get("asking_domain"), Mapping) else {})
        )
        fav = ask.get("open_favor")
        if (
            isinstance(fav, Mapping)
            and str(fav.get("status") or "OPEN").upper() == "OPEN"
            and not _et_row_is_dead(
                fav,
                now_dt,
                ttl_seconds=_ET_FAVOR_EXPIRE_SECONDS,
                since_key="asked_at",
            )
        ):
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
        "（成年门未开或双向同意许可未激活；场外聊边界或偏好不会自动开启亲密场景）。"
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
            + ("/有第三人在场!" if third_p else "/无第三人")
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
            + ("/有第三人在场!" if third_p else "/无第三人")
            + ")"
        )
        raw_cats = rec.get("categories") or rec.get("agreed_categories") or []
        if isinstance(raw_cats, list):
            menu_cats = [_one_line(c, 24) for c in raw_cats if _one_line(c, 24)]

    if not gate_open or permit_state != "ACTIVE":
        state_zh = {"PAUSED": "已暂停", "AFTERCARE": "事后安抚期"}.get(permit_state, "")
        extra_state = f"；当前场景状态={state_zh}" if state_zh else ""
        return (
            "- 36B成年亲密边界：当前处于日常非露骨边界"
            f"（成年门未开或双向同意许可未激活{extra_state}；场外聊边界不自动开启场景）。"
        )

    if effective_tier not in ("FADE_OUT", "SUGGESTIVE", "EXPLICIT"):
        effective_tier = "FADE_OUT"
    cats_text = "、".join(menu_cats[:6]) if menu_cats else "以当前双向许可限定范围为准"
    return (
        f"- 36B成年亲密边界（双向许可激活）：生效呈现尺度={effective_tier}；"
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

    # 4. v02_recall_projection real fuzzy RANGE/GIST cues & tombstone boundary
    if isinstance(recall_doc, Mapping):
        cc = recall_doc.get("clarity_counts") if isinstance(recall_doc.get("clarity_counts"), Mapping) else {}
        fuzzy_snippets: list[str] = []
        raw_items_pool: list[Mapping[str, Any]] = []
        for pool_key in ("direct_recall_items", "surfaceable_memories", "items"):
            raw_pool = recall_doc.get(pool_key)
            if isinstance(raw_pool, list):
                raw_items_pool.extend(r for r in raw_pool if isinstance(r, Mapping))
                if raw_items_pool:
                    break
        if not raw_items_pool and isinstance(recall_doc.get("memories"), Mapping):
            raw_items_pool.extend(
                v for v in recall_doc["memories"].values() if isinstance(v, Mapping)
            )
        for item in raw_items_pool:
            if (
                item.get("deleted")
                or item.get("tombstoned")
                or item.get("governance_blocked")
                or item.get("active_fade")
                or str(item.get("decay_mode") or "").strip().upper() in ("ACTIVE_FADE", "GOVERNANCE_TOMBSTONE")
            ):
                continue
            lvl = str(item.get("clarity") or item.get("clarity_level") or item.get("level") or "").strip().upper()
            if lvl not in ("RANGE", "GIST"):
                continue
            snippet = _one_line(
                item.get("projected_text") or item.get("gist_summary") or item.get("topic"),
                38,
            )
            if snippet and snippet not in fuzzy_snippets:
                fuzzy_snippets.append(snippet)
            if len(fuzzy_snippets) >= 2:
                break
        if fuzzy_snippets:
            cues.append(
                "久远日常记忆（大概印象，不伪造逐字原话）：" + "；".join(fuzzy_snippets[:2])
            )
        elif int(cc.get("GIST") or 0) > 0 or int(cc.get("RANGE") or 0) > 0:
            cues.append("久远日常记忆以大概印象与区间回忆为准，不伪造逐字原话")
        ts_cnt = int(cc.get("TOMBSTONED") or recall_doc.get("tombstone_count") or 0)
        if ts_cnt == 0:
            raw_ts = recall_doc.get("tombstones") or recall_doc.get("tombstone_ledger")
            if isinstance(raw_ts, (Mapping, list)):
                ts_cnt = len(raw_ts)
        if ts_cnt > 0:
            cues.append(f"遗忘墓碑水位({ts_cnt}条已撤回/抹除)：严禁脑补或重提已墓碑化事项")

    # 5. §10.E 错过时刻事后补材料待重接提示（仅 MATERIAL_SUPPLIED）
    if isinstance(common_ground_doc, Mapping):
        raw_missed = common_ground_doc.get("missed_moment_reconnections")
        missed_iter = (
            list(raw_missed.values())
            if isinstance(raw_missed, Mapping)
            else (raw_missed if isinstance(raw_missed, list) else [])
        )
        for mm in missed_iter:
            if not isinstance(mm, Mapping):
                continue
            if str(mm.get("status") or "").strip().upper() == "MATERIAL_SUPPLIED":
                m_lbl = _one_line(mm.get("topic") or mm.get("summary_cn") or mm.get("moment_id"), 22)
                if m_lbl:
                    cues.append(f"错过时刻补材料待接续({m_lbl})：补见仍保留“当时不在场”的真实感")
                    break

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
    raw_missed = doc.get("missed_moment_reconnections")
    missed_iter = (
        list(raw_missed.values())
        if isinstance(raw_missed, Mapping)
        else (raw_missed if isinstance(raw_missed, list) else [])
    )
    safe_missed = [
        {
            k: v
            for k, v in mm.items()
            if k not in ("private_note", "private_body", "subject_private_note")
        }
        for mm in missed_iter
        if isinstance(mm, Mapping) and mm.get("shared") is not False
    ]
    return {
        "schema": doc.get("schema"),
        "perspective": "partner",
        "items": safe_items,
        "pending_partner_questions": safe_questions,
        "missed_moment_reconnections": safe_missed,
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
    skip_desire_texts: Optional[Sequence[str]] = None,
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
    skipped_duplicate = False
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

        raw_label = (
            row.get("summary_zh")
            or row.get("title_zh")
            or row.get("title")
            or row.get("label")
            or row.get("summary")
            or row.get("desire_text")
            or row.get("wish")
            or row.get("description")
            or row.get("text")
        )
        if _desire_matches_surfaced(str(raw_label or ""), skip_desire_texts):
            skipped_duplicate = True
            continue
        label = _one_line_clean(raw_label, 28)
        if not label:
            continue
        dom_zh = _DESIRE_DOMAIN_ZH.get(domain, "")
        item_txt = f"{label}({dom_zh})" if dom_zh else label
        if item_txt not in picked:
            picked.append(item_txt)
        if len(picked) >= 3:
            break

    if not picked and not skipped_duplicate:
        raw_fallback = desires_doc.get("partner_safe_summary") or desires_doc.get("active_summary")
        if not _desire_matches_surfaced(str(raw_fallback or ""), skip_desire_texts):
            fallback_summary = _one_line_clean(raw_fallback, 54)
            if fallback_summary:
                picked.append(fallback_summary)

    if not picked:
        return None
    return _truncate_section(
        "- 当下日常小心愿：" + "、".join(picked) + "（自然心意·可顺势提及或放下）",
        140,
    )


def _format_high_priority_interactive(
    *,
    v03_ecology: Optional[Mapping[str, Any]],
    v03_social: Optional[Mapping[str, Any]],
    v04_ep: Optional[Mapping[str, Any]],
    window_keepsakes: Optional[Mapping[str, Any]] = None,
    include_private: bool = True,
    now_dt: Optional[datetime] = None,
) -> Optional[str]:
    """Tier-3 interactive moments: active temporary_game, pending partner_stipend_escrow, recovery_supports, pending item gift, matured slow-letter draft."""
    moment = now_dt or datetime.now(tz=timezone.utc)
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

    # 4. GAP-05 pending non-expired partner item gift (zero burden, accept or decline freely)
    if isinstance(v03_ecology, Mapping):
        econ_g = v03_ecology.get("economy") if isinstance(v03_ecology.get("economy"), Mapping) else v03_ecology
        raw_gifts = econ_g.get("item_gift_escrow") if "item_gift_escrow" in econ_g else v03_ecology.get("item_gift_escrow")
        gift_rows: list[tuple[str, str, Mapping[str, Any]]] = []
        if isinstance(raw_gifts, Mapping):
            for gid, g_row in raw_gifts.items():
                if isinstance(g_row, Mapping):
                    gift_rows.append((str(g_row.get("offered_at") or ""), str(g_row.get("gift_id") or gid or ""), g_row))
        elif isinstance(raw_gifts, list):
            for g_row in raw_gifts:
                if isinstance(g_row, Mapping):
                    gift_rows.append((str(g_row.get("offered_at") or ""), str(g_row.get("gift_id") or ""), g_row))
        gift_rows.sort(key=lambda item: (item[0], item[1]), reverse=True)
        for _offered_at, _gid, g_row in gift_rows:
            g_status = str(g_row.get("effective_status") or g_row.get("status") or "").strip().upper()
            if g_status != "OFFERED":
                continue
            exp_dt = _parse_iso(g_row.get("expires_at"))
            if exp_dt is not None and moment >= exp_dt:
                continue
            g_lbl = _one_line(g_row.get("item_label_cn") or g_row.get("label_cn") or g_row.get("title") or "小礼物", 24)
            if g_lbl:
                cues.append(f"待决小礼物：「{g_lbl}」（可收可拒零负担）")
                break

    # 5. Ch.10 matured (>=72h) subject-authored slow-letter draft (NEVER inspect partner cooling drafts!)
    if (
        include_private
        and isinstance(window_keepsakes, Mapping)
        and window_keepsakes.get("perspective") != "partner"
    ):
        raw_drafts = (
            window_keepsakes.get("slow_letter_drafts")
            if isinstance(window_keepsakes.get("slow_letter_drafts"), list)
            else window_keepsakes.get("own_slow_letter_drafts")
        )
        if isinstance(raw_drafts, list):
            matured_drafts: list[tuple[str, str, str]] = []
            for dr in raw_drafts:
                if not isinstance(dr, Mapping):
                    continue
                if str(dr.get("author_side") or "").strip().lower() != "subject":
                    continue
                if str(dr.get("status") or "").strip().lower() != "cooling_draft":
                    continue
                cools_dt = _parse_iso(dr.get("cools_until"))
                if cools_dt is None or moment < cools_dt:
                    continue
                d_title = _one_line(dr.get("title") or "慢信", 24)
                if d_title:
                    matured_drafts.append((str(dr.get("cools_until") or ""), str(dr.get("draft_id") or ""), d_title))
            if matured_drafts:
                matured_drafts.sort(key=lambda item: (item[0], item[1]))
                cues.append(f"慢信草稿「{matured_drafts[0][2]}」已静置满72h可投递")

    if not cues:
        return None
    return "- 即时互动与待回应照料：" + "；".join(cues)


#: How long a resolved card-spot suggestion still counts as fresh counter news.
_SHOPKEEPER_CARD_SPOT_FRESH_HOURS = 72


def _short_festival_traces(items: Any) -> str:
    """Compact, honest label for the physical traces a market leaves behind."""
    labels: list[str] = []
    if isinstance(items, list):
        for raw in items:
            text = str(raw or "")
            if "灯笼" in text:
                lbl = "纸灯笼串"
            elif "回收箱" in text:
                lbl = "回收箱"
            else:
                lbl = _one_line(text, 8)
            if lbl and lbl not in labels:
                labels.append(lbl)
            if len(labels) >= 2:
                break
    return "与".join(labels) if labels else "实物留痕"


_V04_WEATHER_ZH: dict[str, str] = {
    "clear": "晴朗",
    "sunny": "晴朗",
    "cloudy": "多云",
    "overcast": "阴天",
    "windy": "有风",
    "breezy_cool": "微风清凉",
    "cold_clear": "干冷晴朗",
    "drizzle": "细雨",
    "rain": "下雨",
    "shower": "阵雨",
    "heavy_rain": "大雨",
    "humid_plum_rain": "梅雨",
    "storm": "风雨",
    "sea_gale": "海风骤雨",
    "thunder_blackout": "雷雨",
    "fog": "大雾",
    "coastal_mist": "海雾",
    "snow": "下雪",
}
_V04_WIND_ZH: dict[str, str] = {
    "calm": "静风",
    "still": "静风",
    "weak": "微风",
    "light": "微风",
    "gentle": "微风",
    "breeze": "微风",
    "medium": "和风",
    "moderate": "和风",
    "strong": "大风",
    "brisk": "大风",
    "gale": "劲风",
    "gusty": "劲风",
}
_V04_GROUND_ZH: dict[str, str] = {
    "dry": "干爽",
    "damp": "微潮",
    "wet": "潮湿",
    "puddle": "有积水",
    "muddy": "泥泞",
    "snowy": "积雪",
    "icy": "结冰",
}
_V04_PLACE_ZH: dict[str, str] = {
    "home": "家中",
    "porch": "门廊",
    "yard": "院子",
    "street_corner": "街角",
    "bookstore": "旧书店",
    "riverside": "河边",
}
_V04_DRYING_STATE_ZH: dict[str, str] = {
    "dry": "已干",
    "dried": "已干",
    "sleeping_dry": "已干",
    "damp": "微潮",
    "slightly_damp": "微潮",
    "wet": "潮湿",
    "soaked": "湿透",
    "drying": "晾晒中",
    "hung": "晾晒中",
    "hanging": "晾晒中",
}
_V04_SHELF_HOLDER_ZH: dict[str, str] = {
    "shared_shelf": "共享架",
    "on_shelf": "共享架",
    "community_shared_shelf": "共享架",
    "shelf": "共享架",
    "available": "共享架",
    "subject": "手边",
    "self": "手边",
    "borrowed_by_subject": "手边",
    "partner": "伴侣处",
    "borrowed_by_partner": "伴侣处",
    "borrowed": "借出中",
    "home": "家中",
    "bookstore": "旧书店",
}
_V04_WEATHER_QUERY_WORDS: tuple[str, ...] = (
    "天气",
    "下雨",
    "下雪",
    "晴",
    "阴天",
    "刮风",
    "风大",
    "冷不冷",
    "热不热",
    "冷吗",
    "热吗",
    "几度",
    "气温",
    "温度",
    "带伞",
    "湿不湿",
    "外面",
    "外头",
    "冷",
    "热",
    "凉",
)


def _translate_v04_ground(raw: str) -> str:
    txt = str(raw or "").strip().lower()
    if not txt:
        return ""
    if ":" in txt:
        place_part, state_part = txt.split(":", 1)
        p_zh = _V04_PLACE_ZH.get(place_part.strip(), _one_line(place_part.strip(), 8))
        s_zh = _V04_GROUND_ZH.get(state_part.strip(), _one_line(state_part.strip(), 8))
        return f"{p_zh}{s_zh}" if p_zh and s_zh else (s_zh or p_zh)
    return _V04_GROUND_ZH.get(txt, _one_line(txt, 14))


def _extract_v04_temp_label(
    env_blk: Mapping[str, Any],
    *,
    now_dt: Optional[datetime],
    allow_seasonal_fallback: bool,
) -> tuple[str, bool]:
    """Return ``(temp_label_cn, is_explicit)`` using pure Chinese '度' (zero unicode.So)."""
    for key in ("temp_c", "temperature_c", "ambient_temp_c"):
        val = env_blk.get(key)
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return f"{int(round(float(val)))}度", True
    eco = env_blk.get("season_ecology")
    if isinstance(eco, Mapping):
        val = eco.get("ambient_temp_base_c")
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return f"{int(round(float(val)))}度", False
    if allow_seasonal_fallback and now_dt is not None:
        try:
            from components.world.scripts import v05_season_solar_pulse as _ssp
        except Exception:  # noqa: BLE001
            _ssp = None  # type: ignore[assignment]
        if _ssp is not None:
            try:
                raw_season = env_blk.get("season") if env_blk.get("updated_at") is not None or env_blk.get("season_explicit") is True else None
                pulse = _ssp.project_season_solar_pulse(
                    now_iso=now_dt,
                    explicit_season=_ssp.normalize_season(raw_season),
                )
                micro = pulse.get("microclimate") if isinstance(pulse.get("microclimate"), Mapping) else {}
                val = micro.get("ambient_temp_base_c")
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    return f"约{int(round(float(val)))}度", False
            except Exception:  # noqa: BLE001
                pass
    return "", False


def _format_v04_neighborhood_and_elastic(
    *,
    v04_nb: Optional[Mapping[str, Any]],
    v04_ep: Optional[Mapping[str, Any]],
    now_dt: Optional[datetime] = None,
    incoming_text: str = "",
    include_private: bool = False,
) -> Optional[str]:
    """Compact spoiler-safe v0.4 neighborhood & elastic-play hints (never leaks prepared_waiting).

    Ch.26: the living shopkeeper only ever surfaces *non-default* salience -- an
    unverified secondhand rumor (attributed, never a confirmed roadblock) or a
    recently resolved card-spot suggestion.  A quiet ``current_task`` rotation by
    itself emits nothing, so calm rooms keep the Wave 8 zero-char silence gate.

    Ch.50 §五: one recently paired page of *mutually shared* annotations renders
    side by side -- never a comprehension/affinity score.  Ch.52 §五: a kept
    ``kept_both_unreplaced`` divergence wakes only when the incoming words name
    its work; ordinary days and unrelated chats stay silent (no daily nagging).
    ``include_private=False`` never reads ``private_two_phrasings``, an
    un-offered surprise's title/content, or an unshared annotation.
    """

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
        incoming_weather_query = bool(incoming_text) and any(
            kw in incoming_text for kw in _V04_WEATHER_QUERY_WORDS
        )

        env_sub: list[str] = []
        is_clear_weather = w_cond in ("clear", "sunny")
        if w_cond and not is_clear_weather and w_cond != "normal":
            env_sub.append(_V04_WEATHER_ZH.get(w_cond, _one_line(w_cond, 12)))
        if g_cond and g_cond not in ("dry", "normal"):
            env_sub.append(f"地面:{_translate_v04_ground(g_cond)}")
        elif env_blk.get("yard_bench_damp") is True:
            env_sub.append("院凳微潮")
        if wind and wind.lower() not in ("calm", "normal", "still", "gentle", "none", "medium", "moderate"):
            wind_zh = _V04_WIND_ZH.get(wind.lower(), wind)
            env_sub.append(f"风:{wind_zh}")
        if temp_feel and temp_feel.lower() not in ("comfortable", "mild", "normal", "none"):
            env_sub.append(f"体感:{temp_feel}")

        temp_lbl, temp_explicit = _extract_v04_temp_label(
            env_blk,
            now_dt=now_dt,
            allow_seasonal_fallback=incoming_weather_query,
        )
        if is_clear_weather and (incoming_weather_query or bool(env_sub) or temp_explicit):
            clear_tok = f"晴朗·{temp_lbl}" if temp_lbl else "晴朗"
            env_sub.insert(0, clear_tok)
        elif not is_clear_weather and env_sub and temp_lbl and (incoming_weather_query or temp_explicit):
            env_sub[0] = f"{env_sub[0]}·{temp_lbl}"

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

        # 2.5 Ch.41 §五: missable street-corner seasonal market.  The market is
        #     never turned into a shared memory: an ended one surfaces as real
        #     physical traces only when ``street_corner`` is a visited place, and
        #     the attendance flag is reported honestly, never assumed.
        festival_src: Optional[Mapping[str, Any]] = None
        pub_life_raw = v04_nb.get("public_life")
        if isinstance(pub_life_raw, Mapping):
            sf_raw = pub_life_raw.get("seasonal_festival")
            if isinstance(sf_raw, Mapping):
                festival_src = sf_raw
        if festival_src is None:
            ft_raw = env_blk.get("festival_traces")
            if isinstance(ft_raw, Mapping):
                festival_src = ft_raw
        if festival_src:
            f_phase = str(festival_src.get("phase") or "").strip().lower()
            attended = festival_src.get("attended_firsthand")
            visited_ids: set[str] = set()
            for src_ids in (
                v04_nb.get("visited_place_ids"),
                jny.get("visited_place_ids"),
                oc.get("visited_place_ids"),
            ):
                if isinstance(src_ids, list):
                    visited_ids.update(str(x) for x in src_ids)
            if f_phase == "active":
                fest_line = "街角今日有季候小集市"
                if attended is True:
                    fest_line += "(已去过)"
                elif attended is False:
                    fest_line += "(还没去)"
                bits.append(fest_line)
            elif f_phase == "residual_traces" and "street_corner" in visited_ids:
                trace_lbl = _short_festival_traces(festival_src.get("residual_traces"))
                if attended is True:
                    suffix = "(已亲历)"
                elif attended is False:
                    suffix = "(未亲历)"
                else:
                    suffix = ""
                bits.append(f"街角集市刚散·只剩{trace_lbl}{suffix}")

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
                        c_st_zh = _V04_DRYING_STATE_ZH.get(c_st, _one_line(c_st, 10))
                        bits.append(f"院里晾晒:{lbl}({c_st_zh})")
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

        plant_notes: list[str] = []
        for pl in plant_candidates:
            hyd_raw = pl.get("hydration")
            stage_str = str(pl.get("stage") or "").strip().lower()
            hyd_str = str(hyd_raw if hyd_raw is not None else (pl.get("state") or pl.get("moisture") or "")).strip().lower()
            low_float = isinstance(hyd_raw, (int, float)) and not isinstance(hyd_raw, bool) and float(hyd_raw) < 30.0
            if stage_str == "wilting_recoverable":
                note = "门廊盆栽偏干发蔫(浇水能缓)"
            elif str(pl.get("new_leaf_note_cn") or "").strip():
                leaf_raw = pl.get("leaf_count")
                leaf_count = leaf_raw if isinstance(leaf_raw, int) and not isinstance(leaf_raw, bool) else 3
                note = f"盆栽刚抽新叶(共{leaf_count}片)"
            elif str(pl.get("seasonal_state") or "").strip().lower() == "winter_dormant":
                note = "冬日盆栽休眠省水"
            elif low_float or hyd_str in ("dry", "wilting", "thirsty", "needs_water", "wilting_recoverable"):
                note = "门廊盆栽偏干缺水"
            else:
                continue
            if note not in plant_notes:
                plant_notes.append(note)
            if len(plant_notes) >= 2:
                break
        if plant_notes:
            bits.append("；".join(plant_notes))

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
                raw_holder = str(si.get("holder") or si.get("status") or si.get("state") or "").strip()
                sholder = _V04_SHELF_HOLDER_ZH.get(raw_holder.lower(), _one_line(raw_holder, 10))
                if slbl:
                    shelf_labels.append(f"{slbl}({sholder})" if sholder else slbl)
        if shelf_labels:
            bits.append("共享架:" + "、".join(shelf_labels))

        # 5. Ch.26 living shopkeeper: only non-default salience surfaces.  A plain
        #    ``current_task`` rotation on a quiet counter stays completely silent.
        bookstore = pub_life.get("bookstore") if isinstance(pub_life.get("bookstore"), Mapping) else {}
        shop_life = (
            bookstore.get("shopkeeper_life")
            if isinstance(bookstore.get("shopkeeper_life"), Mapping)
            else {}
        )
        if shop_life:
            shop_bits: list[str] = []
            for rumor in (shop_life.get("heard_rumors") or [])[-2:]:
                if not isinstance(rumor, Mapping):
                    continue
                # Hearsay only: never claim a roadblock that was not seen firsthand.
                if rumor.get("verified_firsthand") is not False:
                    continue
                claim = _one_line(rumor.get("claim_cn"), 26)
                if not claim:
                    continue
                src_name = _one_line(rumor.get("source_name"), 16)
                if src_name:
                    shop_bits.append(f"听说:{src_name}「{claim}」(待实地核实)")
                else:
                    shop_bits.append(f"听说:「{claim}」(待实地核实)")
            for sug in (shop_life.get("card_spot_suggestions") or [])[-2:]:
                if not isinstance(sug, Mapping):
                    continue
                status = str(sug.get("status") or "").strip().lower()
                if status not in ("adopted", "relocated"):
                    continue
                resolved_dt = _parse_iso(sug.get("resolved_at"))
                if now_dt is not None and resolved_dt is not None:
                    try:
                        stale = (now_dt - resolved_dt).total_seconds() > (
                            _SHOPKEEPER_CARD_SPOT_FRESH_HOURS * 3600
                        )
                    except TypeError:  # naive/aware mismatch: never hide real news
                        stale = False
                    if stale:
                        continue
                card_id = _one_line(sug.get("card_id"), 16)
                if not card_id:
                    continue
                status_cn = "已摆" if status == "adopted" else "店主另挪"
                shop_bits.append(f"书店卡片:{card_id}({status_cn})")
            # The task line is only a side note to real counter news, never a cue itself.
            if shop_bits:
                task_cn = _one_line(shop_life.get("current_task_cn"), 16)
                if task_cn:
                    shop_bits.append(f"店主:{task_cn}")
            if shop_bits:
                bits.append("店主近况:" + "；".join(shop_bits[:3]))

    if isinstance(v04_ep, Mapping):
        # Ch.53 §二: an active fantasy frame is shared play only -- state the physical
        # boundary plainly so the scene never rewrites the real room.
        tg = v04_ep.get("temporary_game") if isinstance(v04_ep.get("temporary_game"), Mapping) else {}
        if tg.get("active") is True:
            fict_role = _one_line(tg.get("fictional_role_cn"), 20)
            if fict_role:
                prop_lbl = _one_line(tg.get("prop_label") or tg.get("game_kind"), 16) or "道具"
                bits.append(f"小游戏假想:{prop_lbl}暂作「{fict_role}」(不改真实屋子)")

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

        # Ch.50 §五: one page both people chose to share, with different attention.
        # Only rows already shared by both sides form a pair; no score, no verdict.
        pairs = v04_ep.get("distinct_annotation_pairs")
        if isinstance(pairs, list):
            for pair in reversed(pairs):
                if not isinstance(pair, Mapping):
                    continue
                page = pair.get("page")
                if isinstance(page, bool) or not isinstance(page, int):
                    continue
                book = _one_line(
                    pair.get("material_title") or pair.get("material_key"), 18
                ) or "那本书"
                theirs = _one_line(pair.get("partner_noticed_detail_cn"), 20)
                mine = _one_line(pair.get("subject_noticed_detail_cn"), 20)
                if not theirs or not mine:
                    continue
                line = (
                    f"同读《{book}》第{page}页:TA留意「{theirs}」"
                    f"/你留意「{mine}」"
                )
                if pair.get("same_spot_resonance") is True:
                    line += "(撞见同一句)"
                bits.append(line)
                break

        # Ch.52 §五: a kept divergence wakes only when the incoming words name its
        # exact work (ref or title).  No daily reminder, no nag on unrelated days.
        incoming = strip_injected_turn_envelope(
            incoming_text if isinstance(incoming_text, str) else ""
        )
        if incoming:
            divergence_rows: list[Mapping[str, Any]] = [
                row
                for row in (v04_ep.get("experiments") or [])
                if isinstance(row, Mapping)
                and str(row.get("status") or "") == "kept_both_unreplaced"
            ]
            if not divergence_rows:
                ubd = v04_ep.get("unresolved_bound_divergences")
                if isinstance(ubd, Mapping) and isinstance(ubd.get("rows"), list):
                    divergence_rows = [
                        row for row in ubd["rows"] if isinstance(row, Mapping)
                    ]
            for exp in divergence_rows:
                work_ref = str(exp.get("target_work_ref") or "").strip()
                work_title = _one_line(exp.get("title") or exp.get("work_title"), 24)
                if not (
                    (work_ref and work_ref in incoming)
                    or (work_title and work_title in incoming)
                ):
                    continue
                memo = _one_line(
                    exp.get("divergence_memo_cn") or exp.get("outcome"), 32
                )
                if not memo:
                    continue
                label = work_title or work_ref
                bits.append(f"保留分歧[{label}]:「{memo}」")
                break

    if not bits:
        return None
    return "- 街区与弹性玩心：" + "；".join(bits[:10])


def _format_v04_project_reentry_micro(
    *,
    v04_ep: Optional[Mapping[str, Any]],
    root: Optional[Path],
    include_private: bool,
    now_dt: datetime,
    blocked_ids: Optional[set[str]] = None,
    barrier: Optional[Mapping[str, Any]] = None,
) -> Optional[str]:
    """Ch.45 §三/§六: room + body first, optional active project, scoped failures only.

    Strictly zero-write and bounded.  A resting / shelved project never appears
    here.  When nothing in this wave is live, returns ``None`` so the Wave 8
    calm-silence gate keeps the context at 0 extra chars.
    """

    if not include_private:
        return None
    if isinstance(barrier, Mapping) and barrier.get("fail_closed"):
        return None
    eff_blocked = set(blocked_ids or ())

    bits: list[str] = []

    room_label = ""
    comfort_note = ""
    if root is not None:
        matter: Optional[Mapping[str, Any]] = None
        for rel in ("world/v08_matter_space.json", "world/v08_universal_matter.json"):
            doc = _read_json_readonly(root / rel)
            if isinstance(doc, Mapping):
                matter = _filter_mapping_by_barrier(doc, eff_blocked, barrier=barrier)
                break
        if isinstance(matter, Mapping):
            raw_objects = matter.get("matter_objects")
            if not isinstance(raw_objects, (Mapping, list)):
                raw_objects = matter.get("objects")
            rows = (
                list(raw_objects.values())
                if isinstance(raw_objects, Mapping)
                else (raw_objects if isinstance(raw_objects, list) else [])
            )
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                if row.get("consumed") is True or row.get("discarded") is True:
                    continue
                if _entry_hits_forget_barrier(row, eff_blocked, barrier=barrier):
                    continue
                parent = str(row.get("parent_id") or row.get("zone_id") or "")
                if not any(tok in parent.lower() for tok in ("desk", "table", "counter", "sill", "桌", "台")):
                    continue
                label = _one_line(
                    row.get("label_cn") or row.get("label") or row.get("object_id"), 16
                )
                if label:
                    room_label = f"{label}还留在{_one_line(parent, 12) or '原处'}上"
                    break

        somatic = _read_json_readonly(root / "phone" / "somatic_comfort.json")
        if isinstance(somatic, Mapping):
            somatic = _filter_mapping_by_barrier(somatic, eff_blocked, barrier=barrier)
        if isinstance(somatic, Mapping):
            position = (
                somatic.get("local_position")
                if isinstance(somatic.get("local_position"), Mapping)
                else {}
            )
            zone = str(position.get("zone") or somatic.get("local_zone") or "").strip().lower()
            window = somatic.get("window") if isinstance(somatic.get("window"), Mapping) else {}
            window_state = str(window.get("state") or somatic.get("window_state") or "").strip().lower()
            comfort_parts: list[str] = []
            if zone == "window_side":
                comfort_parts.append("在窗边")
            elif zone == "desk_side":
                comfort_parts.append("在桌边")
            if window_state in ("open", "ajar"):
                comfort_parts.append("窗扇开着一点" if window_state == "open" else "窗扇半掩")
            if comfort_parts:
                comfort_note = "；".join(comfort_parts)

    if room_label:
        bits.append(f"醒来/被打断先看房间：{room_label}")
    if comfort_note:
        bits.append(f"身体这边：{comfort_note}")
    elif room_label:
        bits.append("先按身体舒服来，不收也不急着接活")

    active_project: Optional[Mapping[str, Any]] = None
    if isinstance(v04_ep, Mapping):
        for row in v04_ep.get("personal_projects") or []:
            if not isinstance(row, Mapping):
                continue
            if str(row.get("status") or "active").strip().lower() != "active":
                continue
            if str(row.get("purpose_cn") or "").strip():
                active_project = row
                break
    if active_project is not None:
        purpose = _one_line(active_project.get("purpose_cn"), 22)
        next_step = _one_line(active_project.get("next_step_cn"), 20)
        bits.append(
            f"手上项目:「{purpose}」" + (f"，想起就做一点：{next_step}" if next_step else "，做不做都行")
        )

    if root is not None:
        cooldowns = _read_json_readonly(root / "life" / "v02_v03_intent_cooldowns.json")
        if isinstance(cooldowns, Mapping) and isinstance(cooldowns.get("cooldowns"), Mapping):
            for row in cooldowns["cooldowns"].values():
                if not isinstance(row, Mapping):
                    continue
                expires = _parse_iso(row.get("expires_at"))
                if expires is None or now_dt >= expires:
                    continue
                category = str(row.get("category") or "")
                haystack = f"{category} {row.get('reason_code') or ''}".lower()
                if any(tok in haystack for tok in ("read", "page", "book", "阅读", "书页")):
                    bits.append("这次阅读读取未成功，不等于书不存在")
                else:
                    bits.append("这一步没做通，不代表手头别的事都不行")
                break

    if not bits:
        return None
    return "- 项目与回到当下：" + "；".join(bits[:5])


def _read_v02_honest_signals_zero_write(
    day_root: Optional[Path],
    *,
    include_private: bool,
    incoming_text: str = "",
) -> dict[str, Any]:
    """Read-only zero-write scan of Forget barrier, DIVERGED .md, UNVALIDATED_AFTER_MIGRATION skills, and scrap matches."""
    out: dict[str, Any] = {
        "blocked_ids": set(),
        "barrier_active": False,
        "diverged_docs": [],
        "unvalidated_skills": [],
        "matched_scraps": [],
        "latest_self_claim": None,
    }
    if day_root is None:
        return out

    # 1. Forget barrier & DIVERGED representations (delegated to unified RLF + V-C barrier)
    from components.life.scripts import v02_representation_lease_forget as _rlf_mod

    barrier = _rlf_mod.load_unified_forget_barrier_readonly(day_root)
    raw_rlf = barrier.get("raw_rlf") if isinstance(barrier.get("raw_rlf"), Mapping) else {}
    blocked_ids: set[str] = set(barrier.get("blocked_ids") or ())
    if barrier.get("barrier_active"):
        out["barrier_active"] = True
    out["forget_barrier"] = barrier

    if isinstance(raw_rlf, Mapping):
        arts = raw_rlf.get("artifacts") if isinstance(raw_rlf.get("artifacts"), Mapping) else {}
        reps = raw_rlf.get("representations") if isinstance(raw_rlf.get("representations"), Mapping) else {}
        for aid, binding in sorted(reps.items()):
            if not isinstance(binding, Mapping):
                continue
            if _rlf_mod.is_entity_forget_blocked({"artifact_id": str(aid)}, barrier):
                continue
            art = arts.get(aid) if isinstance(arts.get(aid), Mapping) else {}
            is_sh = art.get("visibility") == "shared" or art.get("shared") is True
            if not include_private and not is_sh:
                continue
            health = str(binding.get("health") or "")
            if health != "DIVERGED":
                try:
                    _rc, rep_info = _rlf_mod.inspect_representation(
                        day_root, artifact_id=str(aid), has_subject_capability=True
                    )
                    if isinstance(rep_info, Mapping):
                        health = str(rep_info.get("representation_health") or health)
                except Exception:
                    pass
            if health == "DIVERGED":
                rel_p = str(binding.get("workspace_rel_path") or "")
                lbl = _one_line(art.get("title") or (Path(rel_p).name if rel_p else aid), 24)
                if lbl:
                    out["diverged_docs"].append(lbl)

    out["blocked_ids"] = blocked_ids

    # 2. UNVALIDATED_AFTER_MIGRATION skills & F-076 period self_claims in autobiography_skill_provenance
    asp_path = day_root / "state" / "v02_autobiography_skill_provenance.json"
    asp_doc = _read_json_readonly(asp_path)
    if isinstance(asp_doc, Mapping):
        sk_blk = asp_doc.get("skills") if isinstance(asp_doc.get("skills"), Mapping) else {}
        releases = sk_blk.get("releases") if isinstance(sk_blk.get("releases"), Mapping) else {}
        for skey, srec in sorted(releases.items()):
            if not isinstance(srec, Mapping):
                continue
            st = str(srec.get("status") or "").upper()
            if st in ("UNVALIDATED_AFTER_MIGRATION", "SUSPENDED", "SUSPENDED_AFTER_TRIAL_LOSS"):
                mid = _one_line(srec.get("method_id") or skey, 18)
                if mid and mid not in out["unvalidated_skills"]:
                    out["unvalidated_skills"].append(mid)
        auto_blk = asp_doc.get("autobiography") if isinstance(asp_doc.get("autobiography"), Mapping) else {}
        raw_claims = auto_blk.get("self_claims") if isinstance(auto_blk.get("self_claims"), list) else []
        for clm in reversed(raw_claims):
            if not isinstance(clm, Mapping):
                continue
            cid = str(clm.get("claim_id") or "").strip()
            if (cid and cid in blocked_ids) or _rlf_mod.is_entity_forget_blocked(clm, barrier):
                continue
            is_sh_clm = (
                clm.get("shared") is True
                or str(clm.get("visibility") or "").strip().lower() == "shared"
            )
            if not include_private and not is_sh_clm:
                continue
            stmt_txt = _one_line(
                clm.get("statement") or clm.get("stance") or clm.get("claim_key"),
                28,
            )
            if stmt_txt:
                out["latest_self_claim"] = {
                    "period_id": _one_line(clm.get("period_id"), 12),
                    "statement": stmt_txt,
                    "shared": is_sh_clm,
                }
                break

    # 3. Salvaged scrap matching against current turn words (zero-write)
    ls_path = day_root / "state" / "v02_lived_slices_state.json"
    if incoming_text and ls_path.is_file() and not ls_path.is_symlink():
        ls_doc = _read_json_readonly(ls_path)
        if isinstance(ls_doc, Mapping):
            low_in = incoming_text.lower()
            tokens = {
                tok.lower()
                for tok in re.split(r"[\s,，、。;；:：()（）「」<>【】/_-]+", incoming_text)
                if len(tok) >= 2
            }
            for sc in ls_doc.get("salvaged_scraps") or ():
                if not isinstance(sc, Mapping):
                    continue
                sid = str(sc.get("scrap_id") or "").strip()
                if (sid and sid in blocked_ids) or _rlf_mod.is_entity_forget_blocked(sc, barrier):
                    continue
                is_sh = sc.get("shared") is True or sc.get("shared_with_partner") is True
                if not include_private and not is_sh:
                    continue
                tags = [str(t).strip() for t in (sc.get("applicability_tags") or ()) if str(t).strip()]
                if any((t.lower() in tokens) or (len(t) >= 2 and t.lower() in low_in) for t in tags):
                    title = _one_line(sc.get("fragment_title") or sid, 22)
                    if title:
                        out["matched_scraps"].append(title)
                        break

    return out


def _id_hits_forget_barrier(
    cand: str,
    blocked_ids: set[str],
    *,
    barrier: Optional[Mapping[str, Any]] = None,
    rlf_mod: Any = None,
) -> bool:
    s = str(cand or "").strip()
    if not s:
        return False
    probes: list[str] = [s]
    if ":" in s:
        pos = 0
        while len(probes) <= 16:
            idx = s.find(":", pos)
            if idx < 0:
                break
            pos = idx + 1
            tail = s[pos:].strip()
            if len(tail) >= 2 and tail not in probes:
                probes.append(tail)
        last_tail = s.rsplit(":", 1)[1].strip()
        if len(last_tail) >= 2 and last_tail not in probes:
            probes.append(last_tail)
    if blocked_ids:
        for item in probes:
            if item in blocked_ids:
                return True
        if any(len(bid) >= 2 and s.endswith(f":{bid}") for bid in blocked_ids):
            return True
    if barrier:
        eff_rlf = rlf_mod
        if eff_rlf is None:
            from components.life.scripts import v02_representation_lease_forget as eff_rlf
        if eff_rlf.is_entity_forget_blocked(s, barrier):
            return True
    return False


def _entry_hits_forget_barrier(
    item: Mapping[str, Any],
    blocked_ids: set[str],
    *,
    barrier: Optional[Mapping[str, Any]] = None,
) -> bool:
    _rlf_mod = None
    if barrier:
        if barrier.get("fail_closed"):
            return True
        from components.life.scripts import v02_representation_lease_forget as _rlf_mod

        if _rlf_mod.is_entity_forget_blocked(item, barrier):
            return True
    if item.get("hold_active") or item.get("erased"):
        return True
    if str(item.get("status") or "").upper() == "TOMBSTONED":
        return True
    if str(item.get("lifecycle") or "").upper() == "TOMBSTONED":
        return True
    if not blocked_ids and not barrier:
        return False
    for k in (
        "source_id",
        "artifact_id",
        "derivation_id",
        "cue_id",
        "trace_id",
        "item_id",
        "entry_id",
        "memory_id",
        "inquiry_id",
        "plan_id",
        "work_id",
        "scrap_id",
        "script_id",
        "episode_id",
        "claim_id",
        "audio_note_id",
        "practice_id",
        "desire_id",
        "dream_id",
        "notice_id",
        "proposal_id",
        "postcard_id",
        "concern_ref",
        "book_id",
        "project_id",
        "tension_id",
        "encounter_id",
        "outbound_id",
        "event_id",
        "turn_id",
        "focus_ref",
        "episode_key",
        "object_id",
        "thread_id",
        "idiom_id",
        "note_id",
        "favor_id",
        "material_id",
        "habit_id",
        "letter_id",
        "keepsake_id",
        "incident_ref",
        "target_ref",
        "episode_ref",
        "source_ref",
        "world_anchor",
        "origin_ref",
        "ref",
        "id",
    ):
        v = item.get(k)
        if isinstance(v, str) and _id_hits_forget_barrier(
            v, blocked_ids, barrier=barrier, rlf_mod=_rlf_mod
        ):
            return True
    for lk in (
        "root_set",
        "source_refs",
        "provenance_refs",
        "origin_refs",
        "source_ids",
        "episode_ids",
        "durable_claim_ids",
    ):
        seq = item.get(lk)
        if isinstance(seq, Sequence) and not isinstance(seq, (str, bytes)):
            for el in seq:
                if isinstance(el, str):
                    if _id_hits_forget_barrier(el, blocked_ids, barrier=barrier, rlf_mod=_rlf_mod):
                        return True
                elif isinstance(el, Sequence) and not isinstance(el, (str, bytes)):
                    for sub in el:
                        if isinstance(sub, str) and _id_hits_forget_barrier(
                            sub, blocked_ids, barrier=barrier, rlf_mod=_rlf_mod
                        ):
                            return True
                    if len(el) >= 2 and all(isinstance(x, str) and x.strip() for x in el[:2]):
                        joined = f"{el[0].strip()}:{el[1].strip()}"
                        if _id_hits_forget_barrier(
                            joined, blocked_ids, barrier=barrier, rlf_mod=_rlf_mod
                        ):
                            return True
                elif isinstance(el, Mapping) and _entry_hits_forget_barrier(
                    el, blocked_ids, barrier=barrier
                ):
                    return True
    return False


def _filter_mapping_by_barrier(
    doc: Any,
    blocked_ids: set[str],
    *,
    depth: int = 0,
    barrier: Optional[Mapping[str, Any]] = None,
) -> Any:
    if doc is None or depth > 12:
        return doc
    fail_closed = bool(barrier.get("fail_closed")) if isinstance(barrier, Mapping) else False
    if fail_closed:
        if isinstance(doc, Mapping):
            return {}
        if isinstance(doc, list):
            return []
        return doc
    barrier_active = bool(barrier.get("barrier_active")) if isinstance(barrier, Mapping) else False
    if not blocked_ids and not barrier_active:
        return doc
    _rlf_mod = None
    if barrier:
        from components.life.scripts import v02_representation_lease_forget as _rlf_mod
    if isinstance(doc, Mapping):
        if _entry_hits_forget_barrier(doc, blocked_ids, barrier=barrier):
            return {}
        out: dict[str, Any] = {}
        for k, v in doc.items():
            sk = str(k)
            if _id_hits_forget_barrier(sk, blocked_ids, barrier=barrier, rlf_mod=_rlf_mod):
                continue
            if isinstance(v, Mapping) and _entry_hits_forget_barrier(v, blocked_ids, barrier=barrier):
                continue
            out[sk] = _filter_mapping_by_barrier(v, blocked_ids, depth=depth + 1, barrier=barrier)
        return out
    if isinstance(doc, list):
        kept: list[Any] = []
        for elem in doc:
            if isinstance(elem, str) and _id_hits_forget_barrier(
                elem, blocked_ids, barrier=barrier, rlf_mod=_rlf_mod
            ):
                continue
            if isinstance(elem, Mapping) and _entry_hits_forget_barrier(
                elem, blocked_ids, barrier=barrier
            ):
                continue
            kept.append(_filter_mapping_by_barrier(elem, blocked_ids, depth=depth + 1, barrier=barrier))
        return kept
    return doc


_COPRACTICE_SCALE_ZH: dict[str, str] = {
    "light_relay": "轻接力",
    "co_presence": "轻伴同在",
    "async_checkin": "异步小报平安",
    "pause_without_debt": "无债暂歇",
    "paused_no_debt": "无债暂歇",
    "quiet_checkin": "安静小问候",
}


def _format_v02_deep_surfaces(
    *,
    agency_doc: Optional[Mapping[str, Any]],
    crossmodal_doc: Optional[Mapping[str, Any]],
    runtime_doc: Optional[Mapping[str, Any]],
    include_private_ad: bool,
    include_private_cm: bool,
    now_dt: datetime,
    v02_signals: Optional[Mapping[str, Any]] = None,
    honest_ux_doc: Optional[Mapping[str, Any]] = None,
    include_private_hux: bool = True,
) -> Optional[str]:
    """极简一行 0.2 深水区自然提示（100% 零写，严禁泄漏任何后悔内容或未共享冲突）。"""
    bits: list[str] = []

    # 1. Runtime governance & migration (体面封存 / 主动联络节制 / 省电休眠 / 待复验技法)
    if isinstance(runtime_doc, Mapping):
        ret_blk = (
            runtime_doc.get("retirement_status")
            if isinstance(runtime_doc.get("retirement_status"), Mapping)
            else (
                runtime_doc.get("retirement")
                if isinstance(runtime_doc.get("retirement"), Mapping)
                else None
            )
        )
        if isinstance(ret_blk, Mapping):
            ret_st = str(ret_blk.get("status") or "ACTIVE").strip().upper()
            if ret_st and ret_st != "ACTIVE":
                f_note = _one_line(ret_blk.get("farewell_note_cn"), 20)
                if ret_st == "RETIRED_ARCHIVE_SEALED":
                    bits.append(
                        "已温和封存归档(只读保存·暂停后台认知"
                        + (f"·告别语:{f_note}" if f_note else "")
                        + ")"
                    )
                elif ret_st == "PENDING_CONFIRMATION":
                    bits.append(
                        "体面封存待二次确认(记忆只读保留"
                        + (f"·{f_note}" if f_note else "")
                        + ")"
                    )
                else:
                    bits.append(f"归档状态:{ret_st}")

        po_blk = (
            runtime_doc.get("proactive_outbound")
            if isinstance(runtime_doc.get("proactive_outbound"), Mapping)
            else None
        )
        if isinstance(po_blk, Mapping):
            po_enabled = po_blk.get("enabled") is True
            raw_pq = po_blk.get("pending_queue") if isinstance(po_blk.get("pending_queue"), list) else []
            queued_pq = [
                q
                for q in raw_pq
                if isinstance(q, Mapping)
                and str(q.get("status") or "QUEUED").strip().upper() == "QUEUED"
            ]
            if po_enabled or queued_pq:
                sent_cnt = int(po_blk.get("sent_today_count") or 0)
                d_cap = int(po_blk.get("daily_cap") or 2)
                q_start = _one_line(po_blk.get("quiet_start_hm"), 8)
                q_end = _one_line(po_blk.get("quiet_end_hm"), 8)
                po_parts = [
                    "已开启" if po_enabled else "已关闭",
                    f"今日{sent_cnt}/{d_cap}",
                ]
                if queued_pq:
                    po_parts.append(f"待阅{len(queued_pq)}条")
                if q_start and q_end:
                    po_parts.append(f"免打扰{q_start}-{q_end}")
                bits.append("主动联络:" + "·".join(po_parts))

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

        # D01 placed memory cues (visible / non-occluded / non-removed / at spot only)
        raw_placed = agency_doc.get("placed_memory_cues")
        if isinstance(raw_placed, Mapping):
            cue_cand_list = list(raw_placed.get("active_cues") or [])
        elif isinstance(raw_placed, list):
            cue_cand_list = [
                c
                for c in raw_placed
                if isinstance(c, Mapping)
                and c.get("visible") is True
                and not bool(c.get("occluded", False))
                and not bool(c.get("removed", False))
                and not bool(c.get("moved_away", False))
                and bool(str(c.get("spot_id") or "").strip())
            ]
        else:
            cue_cand_list = []
        for pc in cue_cand_list:
            if not isinstance(pc, Mapping):
                continue
            if not include_private_ad and not bool(pc.get("shared", False)):
                continue
            c_txt = _one_line(pc.get("cue_text_cn") or pc.get("cue_id"), 18)
            sp_txt = _one_line(pc.get("spot_id"), 12)
            if c_txt:
                bits.append(f"桌面可见记忆线索({sp_txt or '屋里'}):{c_txt}")
                break

        # Wave A: open inquiries with partner clues, recent belief revisions,
        # recent rule enforcements, and pending partner commitment tradeoffs
        for inq in (agency_doc.get("inquiries") or []):
            if not isinstance(inq, Mapping):
                continue
            if str(inq.get("status") or "").upper() in ("CONCLUDED", "STOPPED"):
                continue
            if not include_private_ad and not bool(inq.get("shared", False)):
                continue
            p_clues = [c for c in (inq.get("partner_clues") or []) if isinstance(c, Mapping)]
            if not p_clues:
                continue
            clue_txt = _one_line(p_clues[-1].get("clue_text"), 20)
            q_txt = _one_line(inq.get("question"), 14)
            if clue_txt:
                bits.append(f"探究「{q_txt or '问题'}」伴侣线索:{clue_txt}")
                break

        for rev in (agency_doc.get("belief_revisions") or []):
            if not isinstance(rev, Mapping):
                continue
            if not include_private_ad and not bool(rev.get("shared", False)):
                continue
            rev_txt = _one_line(rev.get("revised_belief_cn"), 24)
            if rev_txt:
                bits.append(f"观点更新:{rev_txt}")
                break

        for enf in reversed(agency_doc.get("rule_enforcements") or []):
            if not isinstance(enf, Mapping):
                continue
            if not include_private_ad and not bool(enf.get("shared", False)):
                continue
            r_title = _one_line(enf.get("rule_title"), 14)
            c_title = _one_line(enf.get("candidate_title"), 14)
            if r_title:
                bits.append(f"规矩生效:{r_title}延后{c_title or '候选项'}")
                break

        for tr in (agency_doc.get("commitment_tradeoffs") or []):
            if not isinstance(tr, Mapping):
                continue
            if str(tr.get("status") or "").upper() != "PROPOSED_BY_PARTNER":
                continue
            if not include_private_ad and not bool(tr.get("shared", False)):
                continue
            r_lbl = _one_line(tr.get("resp_title") or tr.get("resp_id"), 14)
            val_txt = _one_line(tr.get("new_value_cn") or tr.get("conflict_note_cn"), 20)
            if r_lbl or val_txt:
                bits.append(f"伴侣提议承诺权衡({r_lbl or '承诺'}):{val_txt or '待商量'}")
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

        for note in (crossmodal_doc.get("audio_notes") or []):
            if not isinstance(note, Mapping):
                continue
            is_sh_an = note.get("shared") is True
            if not include_private_cm and not is_sh_an:
                continue
            a_title = _one_line(note.get("title_cn") or note.get("title"), 18)
            if not a_title:
                continue
            tr_status = str(note.get("transcript_status") or "").strip().upper()
            tr_txt = _one_line(note.get("transcript_cn"), 22) if tr_status == "USER_SUPPLIED" else ""
            an_cue = f"声音便笺:{a_title}"
            if tr_txt:
                an_cue += f"「{tr_txt}」"
            if not is_sh_an:
                an_cue += "[未公开]"
            bits.append(an_cue)
            break

    # 4. Honest UX: pending partner rescaled co-practice proposal (F-189)
    if isinstance(honest_ux_doc, Mapping):
        cop_rows: list[Mapping[str, Any]] = []
        raw_cop_list = honest_ux_doc.get("rescaled_copractices")
        if isinstance(raw_cop_list, list):
            cop_rows.extend(r for r in raw_cop_list if isinstance(r, Mapping))
        raw_cop_map = honest_ux_doc.get("proposed_rescaled_copractices")
        if isinstance(raw_cop_map, Mapping):
            cop_rows.extend(r for _k, r in sorted(raw_cop_map.items()) if isinstance(r, Mapping))
        for crow in cop_rows:
            if str(crow.get("status") or "").strip().upper() != "PROPOSED_BY_PARTNER":
                continue
            if crow.get("subject_confirmed") is True:
                continue
            is_sh_cop = crow.get("shared") is True or crow.get("shared_with_partner") is True
            if not include_private_hux and not is_sh_cop:
                continue
            c_title = _one_line(crow.get("title_cn") or crow.get("title") or crow.get("practice_id"), 18)
            if not c_title:
                continue
            raw_sm = str(crow.get("scale_mode") or "").strip().lower()
            sm_zh = _COPRACTICE_SCALE_ZH.get(raw_sm, _one_line(raw_sm, 12))
            c_note = _one_line(crow.get("note_cn"), 18)
            c_sum = f"{c_title}({sm_zh})" if sm_zh else c_title
            if c_note:
                c_sum += f"·{c_note}"
            bits.append(f"伴侣提议缩减共同练习:{c_sum}")
            break

    if isinstance(v02_signals, Mapping):
        extra_recheck = [
            _one_line(s, 18)
            for s in (v02_signals.get("unvalidated_skills") or ())[:2]
            if _one_line(s, 18)
        ]
        if extra_recheck and not any(b.startswith("待复验技法:") for b in bits):
            bits.append("待复验技法:" + "/".join(extra_recheck))
        diverged_docs = [
            _one_line(d, 22)
            for d in (v02_signals.get("diverged_docs") or ())[:2]
            if _one_line(d, 22)
        ]
        if diverged_docs:
            bits.append("工作区文档外部有改动(未覆盖):" + "、".join(diverged_docs))
        matched_scraps = [
            _one_line(sc, 22)
            for sc in (v02_signals.get("matched_scraps") or ())[:1]
            if _one_line(sc, 22)
        ]
        if matched_scraps:
            bits.append(f"可参考旧边角:「{matched_scraps[0]}」")

    if not bits:
        return None
    return _truncate_section("- 0.2深水区感知：" + "；".join(bits[:10]), 380)


def _format_v02_skill_drift_and_ecology(
    *,
    skill_book_doc: Optional[Mapping[str, Any]],
    drift_doc: Optional[Mapping[str, Any]],
    ecology_doc: Optional[Mapping[str, Any]],
    include_private_rm: bool,
    include_private_eco: bool,
    v02_signals: Optional[Mapping[str, Any]] = None,
) -> Optional[str]:
    """Compact zero-write cue line for Skill Book, Personality Drift, Self-Claims, and Wave B1 Ecology."""
    bits: list[str] = []

    # 0. Period self-claim (F-076) from autobiography or drift state
    latest_claim: Optional[Mapping[str, Any]] = None
    if isinstance(v02_signals, Mapping) and isinstance(v02_signals.get("latest_self_claim"), Mapping):
        latest_claim = v02_signals["latest_self_claim"]
    if latest_claim is None:
        fallback_claims: list[Any] = []
        if isinstance(drift_doc, Mapping) and isinstance(drift_doc.get("self_claims"), list):
            fallback_claims = list(drift_doc["self_claims"])
        elif isinstance(skill_book_doc, Mapping):
            auto_sec = skill_book_doc.get("autobiography")
            if isinstance(auto_sec, Mapping) and isinstance(auto_sec.get("self_claims"), list):
                fallback_claims = list(auto_sec["self_claims"])
        for clm in reversed(fallback_claims):
            if not isinstance(clm, Mapping):
                continue
            is_sh_c = (
                clm.get("shared") is True
                or str(clm.get("visibility") or "").strip().lower() == "shared"
            )
            if not include_private_rm and not is_sh_c:
                continue
            stmt = _one_line(clm.get("statement") or clm.get("stance") or clm.get("claim_key"), 28)
            if stmt:
                latest_claim = {
                    "period_id": _one_line(clm.get("period_id"), 12),
                    "statement": stmt,
                    "shared": is_sh_c,
                }
                break
    if isinstance(latest_claim, Mapping):
        is_sh_c = bool(latest_claim.get("shared", False))
        if include_private_rm or is_sh_c:
            c_stmt = _one_line(latest_claim.get("statement"), 24)
            c_per = _one_line(latest_claim.get("period_id"), 12)
            if c_stmt:
                c_lbl = f"阶段自述({c_per}):{c_stmt}" if c_per else f"阶段自述:{c_stmt}"
                if not is_sh_c:
                    c_lbl += "[仅己见]"
                bits.append(c_lbl)

    # 1. Personal Skill Book (partner recheck requests, PRACTICED_MASTERY skills, pending patches)
    if isinstance(skill_book_doc, Mapping):
        raw_skills_sec = (
            skill_book_doc.get("skills")
            if isinstance(skill_book_doc.get("skills"), Mapping)
            else None
        )
        recheck_reqs = (
            skill_book_doc.get("recheck_requests")
            if isinstance(skill_book_doc.get("recheck_requests"), list)
            else (
                raw_skills_sec.get("recheck_requests")
                if raw_skills_sec and isinstance(raw_skills_sec.get("recheck_requests"), list)
                else []
            )
        )
        queued_methods: list[str] = []
        for req in reversed(recheck_reqs):
            if not isinstance(req, Mapping):
                continue
            if str(req.get("status") or "").upper() != "QUEUED":
                continue
            mid = _one_line(req.get("method_id"), 18)
            if mid and mid not in queued_methods:
                queued_methods.append(mid)
                if len(queued_methods) >= 2:
                    break
        if queued_methods:
            bits.append("伴侣请复验技法:" + "/".join(queued_methods))

        entries_seq: list[Any]
        if isinstance(skill_book_doc.get("entries"), list):
            entries_seq = list(skill_book_doc.get("entries") or [])
        elif raw_skills_sec and isinstance(raw_skills_sec.get("releases"), Mapping):
            entries_seq = list(raw_skills_sec["releases"].values())
        else:
            entries_seq = []
        mastery_labels: list[str] = []
        for ent in entries_seq:
            if not isinstance(ent, Mapping):
                continue
            if str(ent.get("attribution") or "").upper() != "PRACTICED_MASTERY":
                continue
            if str(ent.get("status") or "").upper() in ("RETIRED", "SUSPENDED"):
                continue
            lbl = _one_line(ent.get("title_cn") or ent.get("method_id"), 16)
            if lbl and lbl not in mastery_labels:
                mastery_labels.append(lbl)
                if len(mastery_labels) >= 2:
                    break
        if mastery_labels:
            bits.append("已练成技法:" + "、".join(mastery_labels))

        method_patches = (
            skill_book_doc.get("method_patches")
            if isinstance(skill_book_doc.get("method_patches"), list)
            else (
                raw_skills_sec.get("method_patches")
                if raw_skills_sec and isinstance(raw_skills_sec.get("method_patches"), list)
                else []
            )
        )
        for patch in reversed(method_patches):
            if not isinstance(patch, Mapping):
                continue
            if str(patch.get("retest_status") or "").upper() != "PENDING_RETEST":
                continue
            if not include_private_rm and patch.get("shared") is False:
                continue
            p_mid = _one_line(patch.get("method_id") or patch.get("patch_id"), 18)
            if p_mid:
                bits.append(f"待复测技法补丁:{p_mid}")
                break

    # 2. Personality Drift & Model Recheck
    if isinstance(drift_doc, Mapping):
        for pack in reversed(drift_doc.get("recheck_packs") or []):
            if not isinstance(pack, Mapping):
                continue
            if str(pack.get("status") or "").upper() != "OPEN":
                continue
            frm = _one_line(pack.get("from_model_id"), 12)
            tom = _one_line(pack.get("to_model_id"), 12)
            label = f"{frm}->{tom}" if (frm or tom) else _one_line(pack.get("recheck_id"), 14)
            if label:
                bits.append(f"换模型偏好复核待答:{label}")
                break

        for sh in reversed(drift_doc.get("standard_shifts") or []):
            if not isinstance(sh, Mapping):
                continue
            if str(sh.get("axis") or "").lower() != "partner_observation":
                continue
            if not include_private_rm and sh.get("shared") is False:
                continue
            dom = _one_line(sh.get("domain"), 12)
            obs_txt = _one_line(sh.get("new_stance_cn") or sh.get("observation_cn"), 20)
            if obs_txt:
                bits.append(f"伴侣留意品味变化({dom or '日常'}):{obs_txt}")
                break

        mat_status = str(drift_doc.get("maturity_status") or "").upper()
        if mat_status in ("GENTLE_SHIFT", "NOTABLE_SHIFT"):
            mat_lbl = _one_line(drift_doc.get("maturity_label_cn") or mat_status, 18)
            bits.append(f"近期性情变化:{mat_lbl}")

    # 3. Wave B1 Mid-scale Ecology (absence observations, opportunities, chains, partner sparks)
    if isinstance(ecology_doc, Mapping):
        abs_items: list[str] = []
        for obs in (ecology_doc.get("absence_observations") or []):
            if not isinstance(obs, Mapping):
                continue
            if not include_private_eco and obs.get("shared") is False:
                continue
            title = _one_line(obs.get("title_cn") or obs.get("item_id"), 14)
            if not title:
                continue
            kind = str(obs.get("kind") or "").upper()
            obs_place = _one_line(obs.get("observed_place_cn"), 10)
            exp_place = _one_line(obs.get("expected_place_cn"), 10)
            if kind == "MOVED_OBJECT" and obs_place:
                detail = f"挪到{obs_place}"
            elif kind == "SHOP_CLOSED_UNEXPECTED":
                detail = "暂未开门"
            elif exp_place:
                detail = f"原在{exp_place}未见"
            else:
                detail = "有变动"
            p_note = _one_line(obs.get("partner_note_cn"), 18) if obs.get("acknowledged_by_partner") else ""
            entry_txt = f"{title}({detail}" + (f"·伴侣注:{p_note}" if p_note else "") + ")"
            abs_items.append(entry_txt)
            if len(abs_items) >= 2:
                break
        if abs_items:
            bits.append("物件变动:" + "、".join(abs_items))

        for sp in (ecology_doc.get("partner_ecology_sparks") or []):
            if not isinstance(sp, Mapping):
                continue
            if not include_private_eco and sp.get("shared") is False:
                continue
            sp_txt = _one_line(sp.get("spark_text_cn") or sp.get("theme_cn"), 22)
            if sp_txt:
                bits.append(f"伴侣活动灵感:{sp_txt}")
                break

        for opp in (ecology_doc.get("autobiography_opportunities") or []):
            if not isinstance(opp, Mapping):
                continue
            if not include_private_eco and not bool(opp.get("shared", False)):
                continue
            idea = _one_line(opp.get("idea_title_cn"), 20)
            if idea:
                bits.append(f"旧事新契机:{idea}")
                break

        for ch in (ecology_doc.get("ecology_chains") or []):
            if not isinstance(ch, Mapping):
                continue
            if not include_private_eco and ch.get("shared") is False:
                continue
            theme = _one_line(ch.get("theme_cn"), 14)
            step = _one_line(ch.get("latest_step_kind"), 10)
            if theme:
                bits.append(f"活动延续链:{theme}({step or '进行中'})")
                break

    if not bits:
        return None
    return _truncate_section("- 0.2成长与生态：" + "；".join(bits[:9]), 360)


def _format_v02_world_lineage(
    *,
    world_lineage_doc: Optional[Mapping[str, Any]],
    include_private: bool,
) -> Optional[str]:
    """Compact zero-write cue line for Wave B2 Layered News, Carrier Adaptation, and Meaning Notes."""
    if not isinstance(world_lineage_doc, Mapping):
        return None
    bits: list[str] = []

    # 1. Carrier Capability Adaptation (only when non-desktop or restricted capabilities)
    carrier = world_lineage_doc.get("carrier_adaptation")
    if isinstance(carrier, Mapping):
        carrier_kind = str(carrier.get("carrier_kind") or "desktop_gui").strip()
        carrier_label = _one_line(carrier.get("carrier_label_cn") or carrier_kind, 14)
        raw_adaps = carrier.get("active_adaptations") if isinstance(carrier.get("active_adaptations"), list) else []
        restricted_codes: list[str] = []
        first_adj = ""
        for ad in raw_adaps:
            if not isinstance(ad, Mapping):
                continue
            code = str(ad.get("code") or "").strip().upper()
            if not code or code == "FULL_DESKTOP_CARRIER":
                continue
            restricted_codes.append(code)
            if not first_adj:
                first_adj = _one_line(ad.get("habit_adjustment_cn"), 24)
        is_non_desktop = bool(carrier.get("synced")) and carrier_kind != "desktop_gui"
        if restricted_codes or is_non_desktop:
            code_part = "/".join(restricted_codes[:3]) if restricted_codes else carrier_kind
            adj_part = f":{first_adj}" if first_adj else ""
            bits.append(f"载体适配({carrier_label}·{code_part}){adj_part}")

    # 2. Layered News Anti-Contamination (supports both HTTP dict shape & raw disk list shape)
    raw_layered = world_lineage_doc.get("layered_news")
    local_items: list[Mapping[str, Any]] = []
    ext_items: list[Mapping[str, Any]] = []
    hearsay_items: list[Mapping[str, Any]] = []
    if isinstance(raw_layered, Mapping):
        local_items = [x for x in (raw_layered.get("local_world_news") or []) if isinstance(x, Mapping)]
        ext_items = [x for x in (raw_layered.get("external_real_facts") or []) if isinstance(x, Mapping)]
        hearsay_items = [x for x in (raw_layered.get("unverified_hearsay") or []) if isinstance(x, Mapping)]
    elif isinstance(raw_layered, list):
        for item in raw_layered:
            if not isinstance(item, Mapping):
                continue
            layer = str(item.get("layer") or "").strip().upper()
            if layer == "LOCAL_WORLD_NEWS":
                local_items.append(item)
            elif layer == "EXTERNAL_REAL_FACT":
                ext_items.append(item)
            elif layer == "UNVERIFIED_HEARSAY":
                hearsay_items.append(item)

    def _pick_latest_headline(rows: Sequence[Mapping[str, Any]]) -> str:
        for row in reversed(rows):
            if not include_private and row.get("shared") is False:
                continue
            hl = _one_line(row.get("headline_cn") or row.get("title_cn") or row.get("summary_cn"), 22)
            if hl:
                return hl
        return ""

    local_hl = _pick_latest_headline(local_items)
    if local_hl:
        bits.append(f"[小镇虚构动态]{local_hl}")
    ext_hl = _pick_latest_headline(ext_items)
    if ext_hl:
        bits.append(f"[外部现实信息·独立于小镇勿混淆]{ext_hl}")
    hearsay_hl = _pick_latest_headline(hearsay_items)
    if hearsay_hl:
        bits.append(f"[口头听闻·未核证]{hearsay_hl}")

    # 3. Partner Meaning Notes & Meaning Versions
    for mn in reversed(world_lineage_doc.get("partner_meaning_notes") or []):
        if not isinstance(mn, Mapping):
            continue
        if not include_private and mn.get("shared") is False:
            continue
        st = _one_line(mn.get("subject_title_cn"), 12)
        nt = _one_line(mn.get("note_cn"), 20)
        if st or nt:
            bits.append(f"伴侣意义便笺({st or '旧物'}):{nt}")
            break

    for mv in reversed(world_lineage_doc.get("meaning_versions") or []):
        if not isinstance(mv, Mapping):
            continue
        if not include_private and mv.get("shared") is False:
            continue
        st = _one_line(mv.get("subject_title_cn"), 12)
        cur = _one_line(mv.get("current_meaning_cn"), 20)
        if st or cur:
            bits.append(f"意义演变({st or '旧物'}):{cur}")
            break

    if not bits:
        return None
    return _truncate_section("- 0.2信息分层与载体：" + "；".join(bits[:6]), 340)


_V05_SEASON_SOLAR_CUE_WORDS: tuple[str, ...] = (
    "节气",
    "时令",
    "四季",
    "换季",
    "天黑",
    "天亮",
    "日照",
    "日出",
    "日落",
    "冷暖",
    "天色",
    "春天",
    "春日",
    "夏天",
    "夏日",
    "秋天",
    "秋日",
    "冬天",
    "冬日",
    "小寒",
    "大寒",
    "立春",
    "雨水",
    "惊蛰",
    "春分",
    "清明",
    "谷雨",
    "立夏",
    "小满",
    "芒种",
    "夏至",
    "小暑",
    "大暑",
    "立秋",
    "处暑",
    "白露",
    "秋分",
    "寒露",
    "霜降",
    "立冬",
    "小雪",
    "大雪",
    "冬至",
)


def _format_v05_season_solar_cue(
    *,
    now_dt: datetime,
    v04_nb: Optional[Mapping[str, Any]],
    town_doc: Optional[Mapping[str, Any]],
    clean_incoming: str,
) -> str:
    """Return a <=60-char seasonal/solar-term cue strictly behind the Calm Gate."""
    try:
        from components.world.scripts import v05_season_solar_pulse as _ssp
    except Exception:  # noqa: BLE001
        try:
            import v05_season_solar_pulse as _ssp  # type: ignore[no-redef]
        except Exception:  # noqa: BLE001
            return ""

    explicit_season: Optional[str] = None
    if isinstance(v04_nb, Mapping):
        env_nb = v04_nb.get("environment") if isinstance(v04_nb.get("environment"), Mapping) else {}
        rev_nb = v04_nb.get("revision")
        nb_persisted = (
            (isinstance(rev_nb, int) and not isinstance(rev_nb, bool) and rev_nb > 0)
            or env_nb.get("updated_at") is not None
        )
        if nb_persisted:
            norm_nb = _ssp.normalize_season(env_nb.get("season") or v04_nb.get("season"))
            if norm_nb is not None:
                explicit_season = norm_nb

    town_active = isinstance(town_doc, Mapping) and bool(town_doc.get("state_exists"))
    if town_active and isinstance(town_doc, Mapping):
        wc = town_doc.get("world_context") if isinstance(town_doc.get("world_context"), Mapping) else {}
        norm_town = _ssp.normalize_season(wc.get("season"))
        if norm_town is not None:
            explicit_season = norm_town

    pulse = _ssp.project_season_solar_pulse(
        now_iso=now_dt,
        explicit_season=explicit_season,
    )
    solar_term = pulse.get("solar_term") if isinstance(pulse.get("solar_term"), Mapping) else {}
    is_term_day = bool(solar_term.get("is_term_day"))
    mentions_season = bool(clean_incoming) and _incoming_touches(
        clean_incoming, _V05_SEASON_SOLAR_CUE_WORDS
    )
    if not (is_term_day or town_active or mentions_season):
        return ""
    return _one_line(pulse.get("compact_context_cue_cn"), 60)


_V05_HOME_ZONE_ZH: dict[str, str] = {
    "desk": "书桌",
    "window_sill": "窗台",
    "bedside": "床头",
    "hearth_table": "茶桌",
    "entryway_shelf": "玄关架",
}


def _extract_v05_margin_note_text(entry: Any) -> str:
    if isinstance(entry, str):
        return _one_line_clean(entry, 28)
    if isinstance(entry, Mapping):
        return _one_line_clean(
            entry.get("note_cn")
            or entry.get("margin_note_cn")
            or entry.get("text")
            or entry.get("note"),
            28,
        )
    return ""


def _format_v05_living_town_and_spark(
    town_doc: Optional[Mapping[str, Any]],
    spark_doc: Optional[Mapping[str, Any]],
    home_craft_doc: Optional[Mapping[str, Any]] = None,
    *,
    include_private: bool = True,
    season_cue: str = "",
    now_dt: Optional[datetime] = None,
    skip_desire_texts: Optional[Sequence[str]] = None,
) -> Optional[str]:
    """Format compact 0.5 living-town (dual map/scent/bookbox), home-craft & inner-spark cues."""
    bits: list[str] = []
    clean_season_cue = _one_line(season_cue, 60)
    if clean_season_cue:
        bits.append(clean_season_cue)
    if isinstance(town_doc, Mapping) and town_doc.get("state_exists"):
        pp = town_doc.get("partner_presence") if isinstance(town_doc.get("partner_presence"), Mapping) else {}
        p_status = str(pp.get("status") or "home").strip()
        p_loc = _one_line(pp.get("location_name_cn") or pp.get("location_id"), 16)
        if p_status == "walking_together":
            bits.append(f"正与伴侣同行({p_loc or '镇上'})")
        elif p_status == "in_town" and p_loc:
            bits.append(f"伴侣此刻在{p_loc}")
        reunion = town_doc.get("umbrella_reunion") if isinstance(town_doc.get("umbrella_reunion"), Mapping) else {}
        if reunion.get("triggered"):
            bits.append("雨天半路撞伞汇合")
        spec = town_doc.get("rare_spectacle") if isinstance(town_doc.get("rare_spectacle"), Mapping) else {}
        if spec.get("title_cn"):
            bits.append(f"今日奇景:{_one_line(spec.get('title_cn'), 14)}")
        kit = town_doc.get("alley_kitten") if isinstance(town_doc.get("alley_kitten"), Mapping) else {}
        if int(kit.get("feeding_count") or 0) > 0:
            k_name = _one_line(kit.get("nickname") or kit.get("stage_label_cn"), 12)
            if k_name:
                bits.append(f"巷口猫({k_name})")

        # Ch. 97: Current spot's live scent & sound snippet
        scent_map = town_doc.get("sound_scent_map")
        if isinstance(scent_map, list) and scent_map:
            tp = town_doc.get("ta_presence") if isinstance(town_doc.get("ta_presence"), Mapping) else {}
            cur_spot_id = str(tp.get("location_id") or "").strip()
            if not cur_spot_id and p_status in ("walking_together", "in_town"):
                cur_spot_id = str(pp.get("location_id") or "").strip()
            if not cur_spot_id:
                cur_spot_id = "home"
            spot_entry: Optional[Mapping[str, Any]] = None
            fallback_entry: Optional[Mapping[str, Any]] = None
            for row in scent_map:
                if not isinstance(row, Mapping):
                    continue
                if fallback_entry is None and (
                    row.get("scent_cn") or row.get("sound_cn") or row.get("tactile_air_cn")
                ):
                    fallback_entry = row
                if str(row.get("spot_id") or "").strip() == cur_spot_id:
                    spot_entry = row
                    break
            chosen_spot = spot_entry or fallback_entry
            if isinstance(chosen_spot, Mapping):
                spot_name = _one_line(chosen_spot.get("name_cn"), 10)
                scent_s = _one_line(chosen_spot.get("scent_cn"), 16)
                sound_s = _one_line(chosen_spot.get("sound_cn"), 16)
                air_s = _one_line(chosen_spot.get("tactile_air_cn"), 14)
                sensory_bits = [x for x in (scent_s, sound_s) if x]
                if not sensory_bits and air_s:
                    sensory_bits.append(air_s)
                if sensory_bits:
                    spot_prefix = f"{spot_name}·" if spot_name else ""
                    bits.append(f"此刻街气:{spot_prefix}{'，'.join(sensory_bits)}")

        # Ch. 101: Latest drifting bookbox note when non-empty
        bookbox = town_doc.get("drifting_bookbox")
        if isinstance(bookbox, list) and bookbox:
            best_book_rank: tuple[str, int] = ("", -1)
            best_book_cue = ""
            for idx, row in enumerate(bookbox):
                if not isinstance(row, Mapping):
                    continue
                if not include_private and row.get("shared") is False:
                    continue
                b_title = _one_line(row.get("book_title"), 14)
                if not b_title:
                    continue
                latest_margin = ""
                latest_margin_ts = ""
                m_notes = row.get("margin_notes")
                if isinstance(m_notes, list):
                    for mn in reversed(m_notes):
                        cand_txt = _extract_v05_margin_note_text(mn)
                        if cand_txt:
                            latest_margin = cand_txt
                            if isinstance(mn, Mapping):
                                latest_margin_ts = str(mn.get("created_at") or mn.get("at") or "").strip()
                            break
                slip_txt = _one_line_clean(row.get("slip_note_cn"), 28)
                note_txt = latest_margin or slip_txt
                if not note_txt:
                    continue
                row_ts = latest_margin_ts or str(row.get("updated_at") or row.get("created_at") or "").strip()
                rank = (row_ts, idx)
                if rank >= best_book_rank:
                    best_book_rank = rank
                    best_book_cue = f"漂流书箱《{b_title}》:{note_txt}"
            if best_book_cue:
                bits.append(best_book_cue)

    if isinstance(spark_doc, Mapping) and spark_doc.get("state_exists"):
        desires = spark_doc.get("desires") if isinstance(spark_doc.get("desires"), Mapping) else {}
        desire_rows = desires.get("items") if isinstance(desires.get("items"), list) else (desires.get("active") or [])
        active_desires = [
            d for d in desire_rows
            if isinstance(d, Mapping)
            and str(d.get("status") or "active") == "active"
            and (include_private or d.get("shared") is not False)
        ]
        if active_desires:
            chosen_desire = None
            for cand_d in reversed(active_desires):
                cand_txt = str(cand_d.get("text") or "").strip()
                if cand_txt and not _desire_matches_surfaced(cand_txt, skip_desire_texts):
                    chosen_desire = cand_d
                    break
            if chosen_desire is not None:
                bits.append(f"眼下念想:{_one_line_clean(chosen_desire.get('text'), 28)}")
        sw = spark_doc.get("sleep_wake_state") if isinstance(spark_doc.get("sleep_wake_state"), Mapping) else {}
        md = sw.get("morning_dream") if isinstance(sw.get("morning_dream"), Mapping) else None
        if md and md.get("status") != "faded_by_noon" and md.get("fragment_cn"):
            if include_private or md.get("shared_with_partner"):
                bits.append(f"晨梦余韵:{_one_line(md.get('fragment_cn'), 24)}")
        wf = spark_doc.get("window_frost") if isinstance(spark_doc.get("window_frost"), Mapping) else None
        if wf and not wf.get("melted") and wf.get("text"):
            bits.append(f"雾窗字迹:{_one_line(wf.get('text'), 16)}")
        for bk in (spark_doc.get("foreshadow_books") or []):
            if not isinstance(bk, Mapping):
                continue
            rev = [n for n in (bk.get("revealed_notes") or []) if isinstance(n, Mapping)]
            if rev:
                latest_n = rev[-1]
                bits.append(f"翻到书页折条:{_one_line(latest_n.get('note_text'), 18)}")
                break

    if isinstance(home_craft_doc, Mapping) and home_craft_doc.get("state_exists") is not False:
        hc_view: Mapping[str, Any] = home_craft_doc
        if (
            isinstance(home_craft_doc.get("data"), Mapping)
            and "home_arrangement" not in home_craft_doc
            and "moving_carton" not in home_craft_doc
            and "parallel_postcards" not in home_craft_doc
            and "crumpled_draft_signal" not in home_craft_doc
        ):
            hc_view = home_craft_doc["data"]
        if hc_view.get("state_exists") is not False:
            # Ch. 98 + 103: Open home-arrangement proposal
            arr_sec = hc_view.get("home_arrangement") if isinstance(hc_view.get("home_arrangement"), Mapping) else {}
            proposals = arr_sec.get("proposals")
            if isinstance(proposals, list):
                open_props = [
                    p for p in proposals
                    if isinstance(p, Mapping)
                    and str(p.get("status") or "").strip().upper() == "OPEN"
                    and (include_private or p.get("shared") is not False)
                ]
                if open_props:
                    p0 = open_props[-1]
                    obj_name = _one_line(p0.get("object_name_cn") or p0.get("object_id"), 12)
                    raw_tz = str(p0.get("target_zone") or "").strip()
                    tz_zh = _V05_HOME_ZONE_ZH.get(raw_tz, _one_line(raw_tz, 10))
                    reason = _one_line(p0.get("reason_cn"), 16)
                    if obj_name and tz_zh:
                        prop_txt = f"{obj_name}挪到{tz_zh}"
                        if reason:
                            prop_txt += f"({reason})"
                        bits.append(f"布置商量:{prop_txt}")
                    elif obj_name or reason:
                        bits.append(f"布置商量:{obj_name or reason}")

            # Ch. 106: Unpacked moving-carton item count
            mc_sec = hc_view.get("moving_carton") if isinstance(hc_view.get("moving_carton"), Mapping) else {}
            cartons = mc_sec.get("cartons")
            if isinstance(cartons, list):
                unpacked_remaining = 0
                for carton in cartons:
                    if not isinstance(carton, Mapping):
                        continue
                    if not include_private and carton.get("shared") is False:
                        continue
                    if str(carton.get("status") or "").strip().upper() == "ALL_SETTLED":
                        continue
                    c_items = carton.get("items")
                    if isinstance(c_items, list):
                        for it in c_items:
                            if isinstance(it, Mapping) and not bool(it.get("unpacked")):
                                unpacked_remaining += 1
                if unpacked_remaining > 0:
                    bits.append(f"玄关搬家纸箱尚余{unpacked_remaining}件待拆")

            # Ch. 112: Latest received parallel postcard
            pc_sec = hc_view.get("parallel_postcards") if isinstance(hc_view.get("parallel_postcards"), Mapping) else {}
            rec_list = pc_sec.get("received")
            if isinstance(rec_list, list):
                rec_rows = [
                    r for r in rec_list
                    if isinstance(r, Mapping)
                    and (include_private or r.get("shared") is not False)
                ]
                if rec_rows:
                    pc0 = rec_rows[-1]
                    pc_town = _one_line(pc0.get("town_name_cn"), 10)
                    pc_term = _one_line(pc0.get("solar_term_name_cn"), 6)
                    pc_greet = _one_line(pc0.get("greeting_cn"), 18)
                    pc_sender = _one_line(pc0.get("sender_label_cn"), 10)
                    origin = pc_town or pc_sender
                    if origin and pc_term:
                        origin_tag = f"{origin}({pc_term})"
                    else:
                        origin_tag = origin or pc_term
                    if origin_tag and pc_greet:
                        bits.append(f"收到平行小镇明信片:{origin_tag}「{pc_greet}」")
                    elif pc_greet:
                        bits.append(f"收到平行小镇明信片:「{pc_greet}」")
                    elif origin_tag:
                        bits.append(f"收到平行小镇明信片:{origin_tag}")

            # Ch. 110 #2: Zero-text crumpled draft signal (same UTC+8 day & <= 7200s)
            cds = hc_view.get("crumpled_draft_signal")
            if isinstance(cds, Mapping) and (include_private or cds.get("shared") is not False):
                sig_date = str(cds.get("date") or "").strip()
                if sig_date:
                    ref_dt: Optional[datetime] = None
                    ref_src: Any = now_dt if now_dt is not None else hc_view.get("observed_at")
                    try:
                        from components.world.scripts import v05_season_solar_pulse as _ssp
                        ref_dt = _ssp.parse_iso_to_default_tz(ref_src)
                    except Exception:  # noqa: BLE001
                        ref_dt = None
                    if ref_dt is None:
                        from datetime import timedelta as _td
                        _tz8 = timezone(_td(hours=8))
                        if isinstance(ref_src, datetime):
                            ref_dt = (
                                ref_src.astimezone(_tz8)
                                if ref_src.tzinfo is not None
                                else ref_src.replace(tzinfo=_tz8)
                            )
                        else:
                            parsed_ref = _parse_iso(ref_src) if isinstance(ref_src, str) else None
                            ref_dt = (
                                parsed_ref.astimezone(_tz8)
                                if parsed_ref is not None
                                else datetime.now(_tz8)
                            )
                    cur_date_str = ref_dt.strftime("%Y-%m-%d")
                    if sig_date == cur_date_str:
                        within_window = True
                        raw_ts = cds.get("last_crumpled_at")
                        if isinstance(raw_ts, str) and raw_ts.strip():
                            ts_norm = raw_ts.strip()
                            if ts_norm.endswith(("Z", "z")):
                                ts_norm = ts_norm[:-1] + "+00:00"
                            sig_dt: Optional[datetime] = None
                            try:
                                parsed_sig = datetime.fromisoformat(ts_norm)
                                sig_dt = (
                                    parsed_sig
                                    if parsed_sig.tzinfo is not None
                                    else parsed_sig.replace(tzinfo=ref_dt.tzinfo)
                                )
                            except ValueError:
                                sig_dt = None
                            if sig_dt is not None:
                                age_seconds = (ref_dt - sig_dt).total_seconds()
                                if not (0.0 <= age_seconds <= 7200.0):
                                    within_window = False
                        if within_window:
                            dk = str(cds.get("disposal_kind") or "wastebasket").strip().lower()
                            if dk == "winter_hearth":
                                bits.append("壁炉刚化掉一团揉碎的废纸(对方没留字,顺口温和宽慰半句即可)")
                            else:
                                bits.append("桌角废纸篓刚多了一团揉掉的废稿(对方没留字,顺口温和宽慰半句即可)")

    if not bits:
        return None
    return _truncate_section("- 0.5小镇与心火线索：" + "；".join(bits[:10]), 380)


# ---------------------------------------------------------------------------------------------
# Public Zero-Write Entrypoint
# ---------------------------------------------------------------------------------------------


def _truncate_section(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    if max_len <= 4:
        return text[:max_len]
    return text[: max_len - 1] + "…"


def _v08_matter_craft_cue(
    space: Optional[Mapping[str, Any]],
    *,
    include_private: bool,
) -> str:
    """One short phrase about a half-finished craft the real room already holds.

    Reads only what ``read_matter_space_view`` already projects; it never writes and
    never creates a file.  Under ``include_private=False`` an object that is itself
    private, or that sits in a private zone, is simply invisible -- a partner
    projection can never leak it through here.
    """

    if not isinstance(space, Mapping):
        return ""
    zones = {
        str(row.get("zone_id")): row
        for row in (space.get("zones") or [])
        if isinstance(row, Mapping)
    }
    objects = [row for row in (space.get("objects") or []) if isinstance(row, Mapping)]

    def visible(obj: Mapping[str, Any]) -> bool:
        if obj.get("active") is False:
            return False
        if include_private:
            return True
        if str(obj.get("visibility") or "shared") == "private":
            return False
        parent = zones.get(str(obj.get("parent_id") or ""))
        return not (parent is not None and str(parent.get("visibility") or "shared") == "private")

    def label(obj: Mapping[str, Any]) -> str:
        return str(obj.get("label_cn") or obj.get("object_id") or "手边物件")[:16]

    def units(value: Any) -> int:
        return value if isinstance(value, int) and not isinstance(value, bool) else 0

    # At most one cue, most specific first.  A room can carry several half-finished
    # things; one phrase is enough to say "there is an open thread here" without a list.
    for obj in objects:
        if visible(obj) and str(obj.get("fit_status") or "") == "oversized_unfitted":
            return "有件%s穿着不合身、还没改" % label(obj)
    for obj in objects:
        if (
            visible(obj)
            and units(obj.get("material_units")) >= 2
            and str(obj.get("parent_id") or "") == "home_desk"
        ):
            return "桌上%s攒着好几份材料，可以拆开或拼起来" % label(obj)
    for obj in objects:
        if visible(obj) and obj.get("transcribed_source"):
            return "有张从别处抄来的底稿"
    for obj in objects:
        if visible(obj) and str(obj.get("wear_state") or "") == "patched":
            return "有件补过的%s还穿着" % label(obj)
    return ""


def _v08_matter_loan_cue(
    space: Optional[Mapping[str, Any]],
    *,
    include_private: bool,
) -> str:
    """One short, neutral phrase about a borrowed thing still at hand.

    Reads only what ``read_matter_space_view`` already projects; it never writes and
    never creates a file.  A loan is a plain physical fact -- "this is not mine, take
    it back" -- never a debt or guilt signal.  Under ``include_private=False`` an
    object that is itself private, or that sits in a private zone, is invisible.
    """

    if not isinstance(space, Mapping):
        return ""
    zones = {
        str(row.get("zone_id")): row
        for row in (space.get("zones") or [])
        if isinstance(row, Mapping)
    }
    for obj in (space.get("objects") or []):
        if not isinstance(obj, Mapping):
            continue
        if obj.get("active") is False:
            continue
        if not include_private:
            if str(obj.get("visibility") or "shared") == "private":
                continue
            parent = zones.get(str(obj.get("parent_id") or ""))
            if parent is not None and str(parent.get("visibility") or "shared") == "private":
                continue
        loan = obj.get("loan_meta")
        if not isinstance(loan, Mapping):
            continue
        if str(loan.get("loan_status") or "") != "borrowed":
            continue
        label = str(obj.get("label_cn") or obj.get("object_id") or "借来的物件")[:16]
        return "手边有借来的「%s」待归还" % label
    return ""


def _v08_matter_scaffold_or_absence_cue(
    space: Optional[Mapping[str, Any]],
    *,
    include_private: bool,
) -> str:
    """At most one calm phrase about a familiar spot now empty or a ready workspace.

    Reads only what ``read_matter_space_view`` already projects; it never writes and
    never creates a file.  Two strict gates apply:

    * an *absence* phrase is shown only for a cue genuinely witnessed as empty
      (``epistemic_state == "witnessed_empty"``, never blame) -- an occluded or
      unobserved spot emits nothing, and a temporary unfamiliar object has no
      background expectation to violate at all;
    * a *scaffold* phrase is shown only for a real ``ready_at_hand`` surface; it is a
      factual ease note derived live from the physical objects, never a phase score.

    Under ``include_private=False`` a private cue/surface is simply invisible -- a
    partner projection can never leak it through here.
    """

    if not isinstance(space, Mapping):
        return ""

    def _visible(row: Any) -> bool:
        if not isinstance(row, Mapping):
            return False
        if include_private:
            return True
        if str(row.get("visibility") or "shared") == "private":
            return False
        return row.get("private") is not True

    # 1) A familiar object that used to live here is now seen to be away.
    for row in (space.get("familiar_absence_cues") or []):
        if not _visible(row):
            continue
        if str(row.get("epistemic_state") or "") != "witnessed_empty":
            continue
        if row.get("zero_blame") is False:
            continue
        phrase = str(row.get("cue_cn") or "").strip()
        if not phrase:
            label = str(row.get("label_cn") or "").strip()
            zone = str(
                row.get("expected_zone_label_cn") or row.get("zone_label_cn") or ""
            ).strip()
            if label and zone:
                phrase = "平时搁在%s的「%s」这会儿空着" % (zone, label)
            elif label:
                phrase = "「%s」这会儿不在常放的位置" % label
        if phrase:
            return phrase[:40]

    # 2) A real surface left at hand, so starting the next act is friction-free.
    for row in (space.get("workspace_scaffolds") or []):
        if not _visible(row):
            continue
        if str(row.get("startup_ease") or "") != "ready_at_hand":
            continue
        if str(row.get("ready_domain") or "") not in (
            "craft_or_writing",
            "warm_tea_or_cooking",
        ):
            continue
        phrase = str(row.get("summary_cn") or "").strip()
        if phrase:
            return phrase[:40]
    return ""


def _format_microzone_salience_snippet(
    microzone: Optional[Mapping[str, Any]],
    *,
    include_private: bool,
) -> str:
    """One short phrase about a microzone condition that is *actually* worth mentioning.

    This is the strict Background-Silence / Change-Salience gate from v3.0 Ch.33 §五 and
    v0.4 Ch.41 §六: a calm, comfortable indoor microzone is background noise and emits
    **zero characters** -- no sunny/cozy chatter ever reaches the context or the model.
    A phrase appears only when the room genuinely changed how it feels to sit there:

    * hard light at the active spot (``light_quality`` ``glare`` / ``dim``),
    * ``rain_splash`` / ``chilly_draft``,
    * a non-empty ``aroma_cue_cn`` (8A only emits this for real warm food/drink),
    * a preserved work breakpoint -- and *only* when the caller may see private rows.

    The helper is a pure read of the projected mapping; it never writes and never
    creates a file, so a missing state file simply yields ``""``.
    """

    if not isinstance(microzone, Mapping):
        return ""
    # Accept either the ecology mapping directly or a wrapper that nests it.
    inner = microzone.get("microzone_ecology")
    if isinstance(inner, Mapping):
        microzone = inner

    light = str(microzone.get("light_quality") or "").strip().lower()
    draft = str(microzone.get("draft_state") or "").strip().lower()
    aroma = _one_line(microzone.get("aroma_cue_cn"), 24)
    comfort = str(
        microzone.get("spot_comfort")
        or microzone.get("comfort_level")
        or microzone.get("comfort_state")
        or ""
    ).strip().lower()
    calm = str(
        microzone.get("spot_mood")
        or microzone.get("ambience")
        or microzone.get("mood")
        or ""
    ).strip().lower()
    quiet = microzone.get("is_quiet_background") is True

    continuity = microzone.get("continuity_preserved") is True
    breakpoint_cn = _one_line(
        microzone.get("preserved_breakpoint_cn") or microzone.get("breakpoint_cn"), 30
    )
    shared = str(microzone.get("visibility") or "shared").strip().lower() == "shared"
    allow_breakpoint = bool(continuity and breakpoint_cn and (include_private or shared))

    has_light = light in ("glare", "dim")
    has_draft = draft in ("rain_splash", "chilly_draft")

    # Background-silence: a quiet, comfortable, calm microzone with no real cue adds
    # nothing.  (The signal checks below already cover this; the explicit guard keeps
    # the intent obvious and stops any future "cozy weather" chatter from creeping in.)
    if quiet and not aroma and not has_light and not has_draft and not allow_breakpoint:
        comfortable = comfort.startswith("comfort") or "舒适" in comfort
        peaceful = calm.startswith("calm") or "静" in calm
        if comfortable and peaceful:
            return ""

    if not (has_light or has_draft or aroma or allow_breakpoint):
        return ""

    bits: list[str] = []
    if light == "glare":
        bits.append("窗边光线刺眼")
    elif light == "dim":
        bits.append("屋里光线有点暗")
    if draft == "rain_splash":
        bits.append("窗边溅进雨点")
    elif draft == "chilly_draft":
        bits.append("窗边有股冷风")
    if aroma:
        bits.append("近旁飘着" + aroma)
    if allow_breakpoint:
        # Only ever reaches a partner projection when the row is explicitly shared.
        bits.append("手头那段%s还没收尾" % breakpoint_cn)
    if not bits:
        return ""
    return "；".join(bits)[:90]


#: Pre-miss wording a God view would use to hint "something was moved while you
#: were not looking".  Such a hint is never injected into lived context before
#: the subject actually reaches the spot: only a real ``not_at_last_seen_place``
#: miss may be mentioned (and even then the new location is never named).
_PRE_MISS_STALE_HINT_TOKENS = (
    "被挪过",
    "还没重新看一眼",
    "还没重新看",
    "不在原印象位置",
    "印象偏差",
)


def _matter_hint_is_pre_miss_stale(text: str) -> bool:
    return any(token in text for token in _PRE_MISS_STALE_HINT_TOKENS)


_V08_ZONE_LABEL_ZH = {
    "window_side": "窗边",
    "desk_side": "书桌旁",
    "bed_side": "床边",
    "home_desk": "书桌",
    "dining_table": "餐桌",
    "kitchen_counter": "灶台边",
    "home_bed": "床边",
    "home_staging_corner": "角落",
    "bookshelf": "书架",
}


def _v08_object_visible_for_actor(
    obj: Mapping[str, Any],
    zones: Mapping[str, Mapping[str, Any]],
    *,
    include_private: bool,
) -> bool:
    """Partner-safe visibility: a private object or private zone is simply absent."""

    if obj.get("active") is False:
        return False
    if include_private:
        return True
    if str(obj.get("visibility") or "shared") == "private":
        return False
    parent = zones.get(str(obj.get("parent_id") or ""))
    return not (
        parent is not None and str(parent.get("visibility") or "shared") == "private"
    )


def _v08_zone_nickname_cue(
    space: Optional[Mapping[str, Any]],
    *,
    include_private: bool,
) -> str:
    """Ch.16 §3 #13: her own corner vocabulary, never leaked before it is shared.

    ``include_private=False`` drops every ``shared_with_partner != True`` row --
    there is no hidden count and no placeholder.  A shared name rides after the
    real zone label as ``(也叫「雨棚」)``; an unshared name only reaches her own
    context (``include_private=True``) and carries an explicit reminder that the
    partner does not know it yet.
    """

    if not isinstance(space, Mapping):
        return ""
    rows = space.get("zone_nicknames")
    if not isinstance(rows, list):
        return ""
    zone_label = {
        str(row.get("zone_id")): _one_line(row.get("label_cn"), 12)
        for row in (space.get("zones") or [])
        if isinstance(row, Mapping) and row.get("zone_id")
    }
    parts: list[str] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        name = _one_line(row.get("nickname_cn"), 12)
        if not name:
            continue
        shared = row.get("shared_with_partner") is True
        if not shared and not include_private:
            continue
        zid = str(row.get("zone_id") or "")
        label = zone_label.get(zid) or _V08_ZONE_LABEL_ZH.get(zid) or zid or "这里"
        if shared:
            parts.append(f"{label}(也叫「{name}」)")
        else:
            parts.append(f"{label}(私下叫「{name}」[私人称呼·伴侣未懂])")
        if len(parts) >= 2:
            break
    return "、".join(parts)


def _v08_matter_scalding_cue(
    space: Optional[Mapping[str, Any]],
    *,
    now_dt: Optional[datetime],
    include_private: bool,
) -> str:
    """A just-poured drink still too hot is one honest physical fact.

    Emitted only while the objective ``scalding_until_iso`` deadline is in the
    future; once the real clock passes it nothing is rendered.  It reports a
    wait, never that the drink was consumed.
    """

    if now_dt is None or not isinstance(space, Mapping):
        return ""
    zones = {
        str(row.get("zone_id")): row
        for row in (space.get("zones") or [])
        if isinstance(row, Mapping)
    }
    for obj in (space.get("objects") or []):
        if not isinstance(obj, Mapping):
            continue
        if str(obj.get("item_kind") or "") != "drink":
            continue
        if not _v08_object_visible_for_actor(obj, zones, include_private=include_private):
            continue
        deadline = _parse_iso(obj.get("scalding_until_iso"))
        if deadline is not None and now_dt < deadline:
            return "手边热饮还烫嘴(晾几分钟)"
    return ""


def _v08_matter_bookmark_cue(
    space: Optional[Mapping[str, Any]],
    *,
    include_private: bool,
) -> str:
    """Ch.16 §3 #14: a page mark is only ever a position, never comprehension."""

    if not isinstance(space, Mapping):
        return ""
    zones = {
        str(row.get("zone_id")): row
        for row in (space.get("zones") or [])
        if isinstance(row, Mapping)
    }
    for obj in (space.get("objects") or []):
        if not isinstance(obj, Mapping):
            continue
        if not _v08_object_visible_for_actor(obj, zones, include_private=include_private):
            continue
        page: Any = None
        assoc = obj.get("subject_association")
        if isinstance(assoc, Mapping):
            page = assoc.get("bookmark_page")
        if page is None and isinstance(obj.get("reading_mark"), Mapping):
            page = obj["reading_mark"].get("bookmark_page")
        if isinstance(page, int) and not isinstance(page, bool) and page > 0:
            return f"书签在第{page}页"
    return ""


_V08_STAGING_CORNER_ZONE_ID = "home_staging_corner"


def _v08_staging_count(value: Any) -> Optional[int]:
    """Read a plain non-negative slot count; booleans and fractions are never counts."""

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _v08_matter_staging_corner_cue(
    space: Optional[Mapping[str, Any]],
    *,
    include_private: bool,
) -> str:
    """One very light note when the drop-off corner gets crowded -- never a chore list.

    Reads only the actor's own projection: the whitelisted ``staging_corner`` block
    from the desktop layer when present, otherwise the ``zones`` / ``objects`` view.
    A partner projection can never infer a private item from the corner's size, since
    only objects it may see are counted.  Zero or one ordinary thing is a calm corner
    and emits exactly ``""`` so the quiet-silence gate and the 1800-char ceiling hold.
    """

    if not isinstance(space, Mapping):
        return ""
    zones = [row for row in (space.get("zones") or []) if isinstance(row, Mapping)]
    zone = next(
        (
            row
            for row in zones
            if str(row.get("zone_id") or "") == _V08_STAGING_CORNER_ZONE_ID
        ),
        None,
    )
    if (
        zone is not None
        and not include_private
        and str(zone.get("visibility") or "shared") == "private"
    ):
        return ""
    corner = space.get("staging_corner")
    capacity: Optional[int] = None
    occupied: Optional[int] = None
    is_full = False
    if isinstance(corner, Mapping):
        if zone is None and str(corner.get("zone_id") or "") != _V08_STAGING_CORNER_ZONE_ID:
            return ""
        if not include_private and (
            str(corner.get("visibility") or "shared") == "private"
            or corner.get("private") is True
        ):
            return ""
        raw_capacity = _v08_staging_count(corner.get("capacity_slots"))
        if raw_capacity is not None and 1 <= raw_capacity <= 12:
            capacity = raw_capacity
        occupied = _v08_staging_count(corner.get("occupied_slots"))
        is_full = corner.get("is_full") is True
    if occupied is None and zone is not None:
        zone_map = {str(zone.get("zone_id")): zone}
        occupied = sum(
            1
            for obj in (space.get("objects") or [])
            if isinstance(obj, Mapping)
            and str(obj.get("parent_id") or "") == _V08_STAGING_CORNER_ZONE_ID
            and _v08_object_visible_for_actor(
                obj, zone_map, include_private=include_private
            )
        )
    if capacity is None and zone is not None:
        raw_capacity = _v08_staging_count(zone.get("capacity_slots"))
        if raw_capacity is not None and 1 <= raw_capacity <= 12:
            capacity = raw_capacity
    if occupied is None or occupied < 0:
        return ""
    # 0 或 1 件平常东西就是平静的角落：严格零字符，绝不打破静默门禁。
    if occupied <= 1:
        return ""
    if capacity is not None:
        occupied = min(occupied, capacity)
        if is_full or occupied >= capacity:
            return "暂放角放满了(%d/%d)" % (occupied, capacity)
        return "暂放角放着%d/%d件" % (occupied, capacity)
    if is_full:
        return "暂放角放满了"
    return "暂放角放着%d件" % occupied


def _format_v08_living_traces_micro(
    doc: Optional[Mapping[str, Any]],
    *,
    include_private: bool,
    now_dt: Optional[datetime] = None,
    matter_space: Optional[Mapping[str, Any]] = None,
) -> str:
    """One bounded line about the room's current traces and saved routines.

    Private rows are either not present at all (partner/HTTP projection) or, when a
    verified subject capability allowed them through, carry the explicit
    "do not blurt this out" tag.  Never longer than ~150 characters.

    ``matter_space`` lets the caller pass a freshly read zero-write room
    projection (``world/v08_matter_space.json``) even when the stored living-traces
    summary carries none; ``now_dt`` drives the honest scalding deadline.  A calm
    room with no real cue contributes exactly ``""``.
    """

    doc = doc if isinstance(doc, Mapping) else {}
    space = (
        matter_space
        if isinstance(matter_space, Mapping)
        else (doc.get("matter_space") if isinstance(doc.get("matter_space"), Mapping) else None)
    )
    traces: list[Mapping[str, Any]] = [
        row
        for row in (doc.get("active_traces") or doc.get("traces") or [])
        if isinstance(row, Mapping)
    ]
    if not include_private:
        traces = [row for row in traces if str(row.get("visibility") or "shared") == "shared"]
    routines: list[Mapping[str, Any]] = [
        row for row in (doc.get("routines") or []) if isinstance(row, Mapping)
    ]
    if not include_private:
        routines = [row for row in routines if str(row.get("visibility") or "shared") == "shared"]

    parts: list[str] = []
    for row in traces[:2]:
        author = "伴侣刚留下" if str(row.get("author_side") or "") == "partner" else "自己留的"
        private_tag = (
            "[未向伴侣公开·勿顺嘴说漏] "
            if include_private and str(row.get("visibility") or "shared") == "private"
            else ""
        )
        remaining = row.get("remaining_minutes")
        if str(row.get("fade_mode") or "timed") == "until_touched":
            fade = "碰到才退"
        elif isinstance(remaining, int) and remaining > 0:
            fade = "%d分钟后自然淡去" % remaining
        else:
            fade = ""
        where = str(row.get("object_label_cn") or row.get("object_id") or "手边物件")[:24]
        sticker = str(row.get("state_sticker_cn") or "")[:40]
        parts.append("%s%s：%s（%s%s）" % (
            private_tag, where, sticker, author, ("，" + fade) if fade else ""
        ))
    # Microzone salience gate: only a genuinely changed room (glare / dim / rain splash
    # / cold draft / real aroma / a preserved breakpoint the caller may see) contributes
    # a phrase.  A quiet, calm, comfortable microzone adds *zero* characters.
    microzone_snippet = _format_microzone_salience_snippet(
        doc.get("microzone_ecology") or doc.get("microzone"),
        include_private=include_private,
    )
    if microzone_snippet:
        parts.append(microzone_snippet)
    # Ch.21 §3 small honestities first: a still-scalding drink, her own corner
    # name (public or privately-tagged), and an honest page bookmark.  Folded in
    # ahead of the longer routine/matter lists so the 160-char cap keeps them.
    scalding_cue = _v08_matter_scalding_cue(
        space, now_dt=now_dt, include_private=include_private
    )
    if scalding_cue:
        parts.append(scalding_cue)
    nickname_cue = _v08_zone_nickname_cue(space, include_private=include_private)
    if nickname_cue:
        parts.append(nickname_cue)
    bookmark_cue = _v08_matter_bookmark_cue(space, include_private=include_private)
    if bookmark_cue:
        parts.append(bookmark_cue)
    # One optional half-finished-craft cue, folded in right after the traces so the
    # 160-char cap cannot silently drop it behind a long routine/matter list.
    craft_cue = _v08_matter_craft_cue(
        space, include_private=include_private
    )
    if craft_cue:
        parts.append(craft_cue)
    # One optional neutral loan cue: a borrowed thing still at hand is a physical
    # fact to return, never a debt/guilt score.  Folded in before the longer routine
    # and matter lists so the 160-char cap cannot silently drop it.
    loan_cue = _v08_matter_loan_cue(
        space, include_private=include_private
    )
    if loan_cue:
        parts.append(loan_cue)
    # One calm absence / ready-workspace phrase, folded in before routines so the
    # 160-char cap cannot silently drop it behind a long routine/matter list.
    scaffold_cue = _v08_matter_scaffold_or_absence_cue(
        space, include_private=include_private
    )
    if scaffold_cue:
        parts.append(scaffold_cue)
    # Ch.25 drop-off corner: two or more visible things is a light physical fact,
    # never a debt/guilt score; zero or one stays exactly silent.
    staging_corner_cue = _v08_matter_staging_corner_cue(
        space, include_private=include_private
    )
    if staging_corner_cue:
        parts.append(staging_corner_cue)
    if routines:
        parts.append("顺手小套路：" + "、".join(
            str(row.get("title_cn") or "")[:20] for row in routines[:2] if row.get("title_cn")
        ))
    matter_lines: list[str] = []
    for hint in (doc.get("matter_hints") or []):
        if isinstance(hint, Mapping):
            text = str(hint.get("text_cn") or hint.get("text") or "").strip()
            hidden = bool(hint.get("private"))
        else:
            text = str(hint or "").strip()
            hidden = False
        if not text:
            continue
        if _matter_hint_is_pre_miss_stale(text):
            # God-view stale hint: never leak before the subject discovers it.
            continue
        if not include_private and (hidden or "私人" in text or "抽屉" in text):
            continue
        if include_private and hidden:
            text = "[未向伴侣公开·勿顺嘴说漏] " + text
        matter_lines.append(text[:48])
        if len(matter_lines) >= 2:
            break
    if matter_lines:
        parts.append("手边：" + "；".join(matter_lines))
    if not parts:
        return ""
    return ("- 屋里动态：" + "；".join(parts))[:160]


def _format_v08_composable_body_micro(
    doc: Optional[Mapping[str, Any]],
    *,
    include_private: bool,
) -> str:
    """One concise private body-care cue, never longer than 120 characters.

    Only rendered when the subject view is permitted (``include_private``) and a real
    body pack is enabled *and* actively expressing a current feeling / need / garment
    state (``menstrual_cycle.current_feeling_cn`` or
    ``nocturnal_emission.pending_garment_care``).  A disabled or quiet pack emits
    ``""`` so no context token is ever wasted.
    """

    if not include_private or not isinstance(doc, Mapping):
        return ""

    def _pack(name: str) -> Mapping[str, Any]:
        direct = doc.get(name)
        if isinstance(direct, Mapping):
            return direct
        # A projection carries ``body_packs``; the raw on-disk state carries ``packs``.
        for container in ("body_packs", "packs"):
            packs = doc.get(container)
            if isinstance(packs, Mapping) and isinstance(packs.get(name), Mapping):
                return packs[name]
        return {}

    def _enabled(pack: Mapping[str, Any]) -> bool:
        return bool(pack) and pack.get("enabled") is not False

    parts: list[str] = []
    cycle = _pack("menstrual_cycle")
    feeling = ""
    if _enabled(cycle):
        feeling = _one_line(
            cycle.get("current_feeling_cn") or cycle.get("feeling_cn"), 40
        )
    if feeling:
        parts.append("当下体感：" + feeling)

    nocturnal = _pack("nocturnal_emission")
    pending = nocturnal.get("pending_garment_care") is True if nocturnal else False
    if not pending and doc.get("pending_garment_care") is True:
        pending = True
    if pending:
        label = _one_line(
            nocturnal.get("garment_label_cn")
            or nocturnal.get("garment_note_cn")
            or doc.get("garment_note_cn"),
            30,
        )
        parts.append("衣物留意：" + (label or "夜里沾到的衣物先搁着，得空再处理"))

    if not parts:
        return ""
    return _truncate_section("- 身体照料：" + "；".join(parts), 120)


# ---------------------------------------------------------------------------------------------
# v0.8 emergent echoes: shared idioms / wake-on-cue future questions / reinterpreted memories
# / divergent notices.  Pure read-only projection -- the incoming turn text is only ever a
# substring matcher, never a trigger for persistence or side effects.
# ---------------------------------------------------------------------------------------------

#: Cue words that let a section borrow priority when the partner's own words touch it.
_V08_TRACE_CUE_WORDS = (
    "冰箱", "灶", "锅", "杯", "热", "冷", "吃", "喝", "放", "挪", "贴纸", "做饭", "水", "收",
)
_V08_ENC_CUE_WORDS = ("书", "页", "卡片", "读", "递", "听说", "邻", "铺")
_V08_BODY_CUE_WORDS = ("冷", "热", "累", "睡", "病", "疼", "发烧", "饿", "渴", "穿", "身体")
_V08_ANCHOR_CUE_WORDS = ("约", "衣服", "出门", "重逢", "习惯")
_V05_TOWN_CUE_WORDS = (
    "镇上", "小镇", "街上", "天气", "下雨", "刮风", "风大", "晴天", "阴天",
    "街坊", "邻居", "茶馆", "书店", "摊子", "集市", "心愿", "梦",
)
_V08_MEMORY_CUE_WORDS = (
    "记忆", "想起", "记得", "以前", "从前", "那时", "当初", "旧事", "回忆",
    "说法", "话", "写法", "作品", "稿",
)


def _incoming_touches(text: str, needles: tuple[str, ...]) -> bool:
    """True when any non-empty cue word appears in the (already stripped) incoming text."""

    if not text:
        return False
    return any(needle and needle in text for needle in needles)


#: The partner's own words that mark a real errand-in-progress (v0.4 Ch.48 §九 Condition B).
_URGENCY_MARKERS = (
    "急事", "赶紧", "马上", "来不及", "出事了", "现在就要", "十万火急",
)
#: Concise exception cue: the usual "wrap up first / reply later" habit yields this turn only.
#: Phrased partner-neutrally so it never hard-codes a name or pronoun.
_URGENCY_EXCEPTION_CUE = (
    "对方提及急事：平时先收尾或稍后回的习惯本轮让位，先直接回应眼下事情"
)
#: Preference scopes/words describing a deferred-reply habit learned through v04 elastic play.
_DEFERRED_REPLY_PREF_SCOPES = frozenset({
    "partner_explanation", "reply", "deferred_reply", "conversation",
})
_DEFERRED_REPLY_PREF_WORDS = ("收尾", "稍后回", "再聊", "晚点回", "先忙", "忙的时候")


def _incoming_shows_urgency(clean_incoming: str) -> bool:
    """True when the partner's own (already stripped) words carry a clear urgency marker."""

    return _incoming_touches(clean_incoming, _URGENCY_MARKERS)


def _has_deferred_reply_preference(v04_ep: Optional[Mapping[str, Any]]) -> bool:
    """True when a v04 elastic-play scoped preference records a deferred-reply habit."""

    if not isinstance(v04_ep, Mapping):
        return False
    raw = v04_ep.get("scoped_preferences")
    if isinstance(raw, Mapping):
        prefs = list(raw.values())
    elif isinstance(raw, (list, tuple)):
        prefs = list(raw)
    else:
        return False
    for row in prefs:
        if not isinstance(row, Mapping):
            continue
        if str(row.get("status") or "active").strip().lower() in ("revoked", "expired"):
            continue
        scope = str(row.get("scope") or "").strip().lower()
        text = _one_line(
            row.get("instruction") or row.get("preference") or row.get("style") or row.get("mode"),
            60,
        )
        if scope in _DEFERRED_REPLY_PREF_SCOPES or any(
            word in text for word in _DEFERRED_REPLY_PREF_WORDS
        ):
            return True
    return False


def _v08_idiom_is_active(row: Mapping[str, Any]) -> bool:
    """A shared idiom stays wakeable until it is explicitly faded or retired."""

    if row.get("faded") is True:
        return False
    status = str(row.get("status") or "").strip().lower()
    return status not in ("faded", "retired", "archived")


def _craft_genealogy_tokens(text: str) -> list[str]:
    """Bounded phrase tokens for craft-genealogy recall (no word-segmenter needed).

    Chinese sentences have no spaces, so short sub-phrases and sliding 4/3/2-char
    windows are emitted; ASCII words keep their whole form when long enough.
    """

    out: list[str] = []
    for chunk in re.split(r"[\s,，、。;；:：()（）「」<>【】/]+", str(text or "")):
        chunk = chunk.strip().lower()
        if not chunk:
            continue
        if chunk.isascii():
            if len(chunk) >= 2:
                out.append(chunk)
            continue
        if len(chunk) <= 12:
            out.append(chunk)
        for size in (4, 3, 2):
            if len(chunk) < size:
                continue
            for start in range(0, len(chunk) - size + 1):
                out.append(chunk[start : start + size])
    return out


def _v08_craft_genealogy_tip(
    doc: Mapping[str, Any],
    *,
    clean_incoming: str,
    include_private: bool,
) -> str:
    """At most one applicable craft-genealogy tip, woken only by the present words.

    The private manuscript/draft reference (``source_work_ref``) is *never* rendered
    into the turn context -- not even under a subject view.  A worked method whose
    ``mismatch_boundary_cn`` fires against the current turn is surfaced as an honest
    caution instead of being mechanically replayed.  Under ``include_private=False``
    only rows explicitly marked ``shared_tip_only`` are considered.
    """

    rows = [row for row in (doc.get("craft_genealogy") or []) if isinstance(row, Mapping)]
    for row in rows:
        if str(row.get("outcome") or "worked").strip().lower() != "worked":
            continue
        if not include_private and row.get("shared_tip_only") is not True:
            continue
        problem = _one_line(row.get("problem_pattern_cn"), 60)
        operation = _one_line(row.get("operation_cn"), 60)
        tip = _one_line(row.get("shareable_tip_cn"), 60)
        domain = _one_line(row.get("domain_tag"), 24)
        tokens = _craft_genealogy_tokens(" ".join((problem, operation, tip, domain)))
        if not any(tok in clean_incoming for tok in tokens):
            continue
        boundary = _one_line(row.get("mismatch_boundary_cn"), 60)
        boundary_tokens = _craft_genealogy_tokens(boundary)
        mismatch = bool(boundary_tokens) and any(
            tok in clean_incoming for tok in boundary_tokens
        )
        if not mismatch and boundary:
            # A short partner phrase (e.g. "赶工交接") can itself sit inside the
            # stored boundary sentence; treat that as an honest boundary hit too.
            mismatch = any(
                tok in boundary
                for tok in _craft_genealogy_tokens(clean_incoming)
                if len(tok) >= 2
            )
        if mismatch:
            return "旧技法「%s」此刻未必合用：%s" % (
                (problem or operation or "这段")[:12],
                boundary[:24],
            )
        shown = tip or operation
        if not shown:
            continue
        domain_zh = {
            "craft_or_writing": "手作或写作",
            "warm_tea_or_cooking": "煮茶或做饭",
        }.get(domain, "")
        head = "旧技法可借" + ("（%s）" % domain_zh if domain_zh else "")
        return "%s：%s" % (head, shown[:44])
    return ""


def _v08_partial_reinterpretation_note(row: Mapping[str, Any]) -> str:
    """Concise partial-reinterpretation tail for one ``memory_reinterpretations`` row.

    Ch.23: a later reading may keep the old reservation beside a freshly noticed
    facet instead of flipping the whole episode.  Only the two caller-supplied
    fields are ever rendered; when neither is present the caller keeps the exact
    legacy line format.  A page is appended only when ``exact_page_known`` is
    literally ``True`` -- an unknown page never yields a guessed "第x页".
    """

    bits: list[str] = []
    reservation = _one_line(row.get("preserved_reservation_cn"), 36)
    if reservation:
        bits.append("留着原先介意的一处：%s" % reservation)
    facet = _one_line(row.get("appreciation_facet_cn"), 36)
    if facet:
        bits.append("新觉得有意思的一处：%s" % facet)
    if row.get("exact_page_known") is True:
        page = _one_line(row.get("exact_page_ref"), 24)
        digits = re.sub(r"\D", "", page)
        if digits:
            bits.append("翻到第%s页" % digits)
    if not bits:
        return ""
    return "（" + "；".join(bits) + "）"


def _format_v08_emergent_echoes_micro(
    doc: Optional[Mapping[str, Any]],
    *,
    incoming_text: str,
    include_private: bool,
    now_dt: datetime,
    v04_ep: Optional[Mapping[str, Any]] = None,
) -> str:
    """One bounded line of emergent echoes, woken only by the partner's own present words.

    Zero nagging: a future-self question or a reinterpreted memory is surfaced only when a
    keyword/anchor of it actually appears in ``incoming_text``.  Private rows are withheld
    entirely under a restricted view.  ``now_dt`` is the reference clock for a lingering
    thread's ``expires_at`` (a thread never auto-renews: once the clock reaches its expiry it
    simply stops being injected, while staying stored).  ``v04_ep`` is the ``v04_elastic_play``
    projection, read only to detect a learned deferred-reply habit.  Never touches disk or turn
    state.
    """

    if now_dt is None:
        now_dt = datetime.now(tz=timezone.utc)
    if not isinstance(doc, Mapping):
        return ""

    clean_incoming = strip_injected_turn_envelope(
        incoming_text if isinstance(incoming_text, str) else ""
    )
    parts: list[str] = []

    # 1) Shared idioms: render only the idiom whose exact phrase the partner just used.
    #    Mutual-consensus framing is reserved for peer-``confirmed`` rows.  A merely
    #    ``proposed`` row is still readable, but only as a one-sided proposal with
    #    explicit proposer attribution -- never as "our shared phrase" (and it earns
    #    zero mutual-consensus boost in ``_situation_boost``).
    idioms = [row for row in (doc.get("shared_idioms") or []) if isinstance(row, Mapping)]
    for row in idioms:
        if not _v08_idiom_is_active(row):
            continue
        phrase = _one_line(row.get("phrase"), 24)
        if not phrase or phrase not in clean_incoming:
            continue
        meaning = _one_line(row.get("meaning"), 40)
        origin = _one_line(row.get("origin"), 20)
        status = str(row.get("status") or "").strip().lower()
        if status == "proposed":
            proposer = str(row.get("proposed_by") or "").strip().lower()
            by_zh = (
                "由自己提出"
                if proposer == "subject"
                else ("由对方提出" if proposer == "partner" else "由一方提出")
            )
            parts.append(
                "说法提议中「%s」（%s，尚未双方确认）：%s（不必当成双方默契）"
                % (phrase, by_zh, meaning or "…")
            )
        else:
            # ``confirmed`` (or a legacy row that predates the status field) is a
            # genuine mutual tacit consensus.
            tail = (
                "（曾起于：%s；若忙或不方便，不必勉强照演）" % origin
                if origin
                else "（若忙或不方便，不必勉强照演）"
            )
            parts.append("默契说法「%s」：%s%s" % (phrase, meaning, tail))
        break

    # 2) Future-self questions: only the open question whose keyword/anchor matches today.
    open_questions = [
        row
        for row in (doc.get("future_self_questions") or [])
        if isinstance(row, Mapping) and str(row.get("status") or "open").strip().lower() == "open"
    ]
    if not include_private:
        open_questions = [
            row for row in open_questions if str(row.get("visibility") or "private") == "shared"
        ]
    for row in open_questions:
        question = _one_line(row.get("question_cn") or row.get("question"), 36)
        if not question:
            continue
        keywords = row.get("keywords") or row.get("trigger_keywords") or []
        keyword_hit = any(
            isinstance(word, str) and word and word in clean_incoming for word in keywords
        )
        anchor = _one_line(row.get("anchor_ref"), 40)
        anchor_hit = bool(anchor) and anchor in clean_incoming
        if not (keyword_hit or anchor_hit):
            continue
        private_tag = (
            "[未向伴侣公开·勿顺嘴说漏] "
            if include_private and str(row.get("visibility") or "private") != "shared"
            else ""
        )
        parts.append("%s留给未来的问题被触发：%s" % (private_tag, question))
        break

    # 3) Reinterpreted memories: wake on an episode/fact match; otherwise at most the single
    #    most recent visible reinterpretation, and only when the turn itself touches memories.
    reinterpretations = [
        row for row in (doc.get("memory_reinterpretations") or []) if isinstance(row, Mapping)
    ]
    if not include_private:
        reinterpretations = [
            row for row in reinterpretations if row.get("shared_with_partner") is True
        ]
    matched_memory = False
    for row in reinterpretations:
        title = _one_line(row.get("episode_title_cn"), 18)
        ref = _one_line(row.get("episode_ref"), 40)
        fact = _one_line(row.get("original_fact_cn"), 24)
        if not any(value and value in clean_incoming for value in (title, ref, fact)):
            continue
        view = _one_line(
            row.get("view_cn") or row.get("reinterpretation_cn") or row.get("current_view_cn"), 32
        )
        parts.append(
            "旧事新解：%s（原事：%s -> 今看：%s）%s"
            % (
                title or ref or "旧事",
                fact or "…",
                view or "…",
                _v08_partial_reinterpretation_note(row),
            )
        )
        matched_memory = True
        break
    if not matched_memory and reinterpretations and _incoming_touches(
        clean_incoming, _V08_MEMORY_CUE_WORDS
    ):
        row = reinterpretations[-1]
        title = _one_line(row.get("episode_title_cn"), 18)
        fact = _one_line(row.get("original_fact_cn"), 24)
        view = _one_line(
            row.get("view_cn") or row.get("reinterpretation_cn") or row.get("current_view_cn"), 32
        )
        parts.append(
            "旧事新解：%s（原事：%s -> 今看：%s）%s"
            % (
                title or "旧事",
                fact or "…",
                view or "…",
                _v08_partial_reinterpretation_note(row),
            )
        )

    # 4) Divergent notices: both sides' readings of one thing, only when today touches it.
    notices = [row for row in (doc.get("divergent_notices") or []) if isinstance(row, Mapping)]
    for row in notices:
        title = _one_line(row.get("target_title_cn"), 18)
        ref = _one_line(row.get("target_ref"), 40)
        if not ((title and title in clean_incoming) or (ref and ref in clean_incoming)):
            continue
        subject_notice = _one_line(row.get("subject_notice_cn"), 30)
        partner_notice = _one_line(row.get("partner_notice_cn"), 30)
        # Divergent notices are per-actor readings, never agreed objective truth.
        # A unilateral row keeps explicit attribution instead of being flattened
        # into a single "the truth is..." statement.
        parts.append(
            "各自看法：%s（各是一方视角，不是共识；自己：%s；对方：%s）"
            % (
                title or ref or "这件事",
                subject_notice or "还没有自己的明确看法",
                partner_notice or "对方还没有明确看法",
            )
        )
        break

    # 5) Day directions: a direction is only a leaning for the day -- never an accomplished
    #    fact.  Ambient (not keyword-gated); private directions are withheld entirely under a
    #    restricted view.  Placed first so the bounded line never truncates it away.
    directions = [
        row for row in (doc.get("day_directions") or []) if isinstance(row, Mapping)
    ]
    live_directions = [
        row
        for row in directions
        if str(row.get("status") or "direction_only").strip().lower()
        not in ("faded", "completed")
        and row.get("executed_in_world") is not True
        and (include_private or str(row.get("visibility") or "shared") == "shared")
    ]
    if live_directions:
        note = _one_line(live_directions[-1].get("horizon_note_cn"), 46)
        if note:
            parts.insert(0, "今日方向（尚未发生，非既成经历）：" + note)

    # 6) Autobiographical craft genealogy: an old worked method is only ever recalled
    #    when the partner's present words touch it; a boundary hit becomes a caution,
    #    and ``source_work_ref`` is never rendered into the context.
    craft_tip = _v08_craft_genealogy_tip(
        doc, clean_incoming=clean_incoming, include_private=include_private
    )
    if craft_tip:
        parts.append(craft_tip)

    # 7) Lingering threads: one calm, self-fading thread of the day.  Strictly non-resolved,
    #    private rows only when the caller may see private rows, and never auto-renewed --
    #    once ``now_dt`` reaches ``expires_at`` it simply stops being injected (still stored).
    lingering_rows = [
        row for row in (doc.get("lingering_threads") or []) if isinstance(row, Mapping)
    ]
    live_threads: list[Mapping[str, Any]] = []
    for row in lingering_rows:
        if str(row.get("status") or "open").strip().lower() in (
            "resolved", "closed", "done", "faded", "archived",
        ):
            continue
        # An explicit ``active_now is False`` or an already-expired row is dead
        # history: it stays on disk but must never re-enter lived context.  The
        # comparison uses the passed/committed clock, never a fresh OS clock.
        if row.get("active_now") is False:
            continue
        if not (include_private or str(row.get("visibility") or "shared") == "shared"):
            continue
        expires_dt = _parse_iso(row.get("expires_at"))
        if expires_dt is not None and now_dt >= expires_dt:
            continue
        if _one_line(row.get("summary_cn") or row.get("summary"), 40):
            live_threads.append(row)
    if live_threads:
        summary = _one_line(
            live_threads[-1].get("summary_cn") or live_threads[-1].get("summary"), 40
        )
        parts.append("挂着的一缕余绪：" + summary)

    # 7b) Personal rituals (Ch.16 §三 #18 / §四 #7): one compact, zero-pressure cue.
    #     Only rows the projection already marked ``shared`` are ever read -- under a
    #     restricted view a private practice never leaks even its title or count.
    #     ``faded_quietly`` / omitted practices stay at exactly 0 characters.
    ritual_rows = [
        row
        for row in (doc.get("personal_rituals") or [])
        if isinstance(row, Mapping) and str(row.get("visibility") or "") == "shared"
    ]
    technique_row: Optional[Mapping[str, Any]] = None
    habit_row: Optional[Mapping[str, Any]] = None
    for row in ritual_rows:
        status = str(row.get("status") or "").strip().lower()
        if status == "technique_retained" and technique_row is None:
            technique_row = row
        elif status in ("reused_variant", "milestone_option") and habit_row is None:
            habit_row = row
    if technique_row is not None:
        title = _one_line(technique_row.get("title_cn"), 24)
        if title:
            parts.append("小习惯淡了·做法仍在:「%s」" % title)
    elif habit_row is not None:
        title = _one_line(habit_row.get("title_cn"), 24)
        if title:
            parts.append("手边顺手惯例:「%s」" % title)

    # 8) Urgency exception: the partner names a real errand-in-progress.  This overrides the
    #    usual "wrap up first / reply later" habit for this turn only -- and only when there is
    #    in fact a deferred-reply preference or a live direction/thread to override.  Placed
    #    first so the bounded line never truncates the exception away.
    if _incoming_shows_urgency(clean_incoming) and (
        _has_deferred_reply_preference(v04_ep) or live_directions or live_threads
    ):
        parts.insert(0, _URGENCY_EXCEPTION_CUE)

    if not parts:
        return ""
    return _truncate_section("- 心里回响：" + "；".join(parts), 180)


_V09_AGENCY_WORLD_CUE_WORDS: tuple[str, ...] = (
    "选择",
    "挑",
    "决定",
    "主见",
    "理由",
    "顾虑",
    "想法",
    "劝",
    "说服",
    "立场",
    "预订",
    "预约",
    "到货",
    "到店",
    "取书",
    "旧书铺",
    "书店",
    "书铺",
    "街坊",
    "约定",
    "市集",
    "饼干盒",
    "铁盒",
    "杯子",
    "标签",
    "贴纸",
    "分类",
    "整理",
    "收纳",
    "票根",
    "小盒",
)

_V09_VENUE_LABEL_ZH: dict[str, str] = {
    "bookstore": "旧书铺",
    "old_bookshop": "旧书铺",
    "bookshop": "旧书铺",
    "cafe": "茶点烘焙铺",
    "bakery_corner": "茶点烘焙铺",
    "bakery": "茶点烘焙铺",
    "tea_shop": "茶点烘焙铺",
    "home_goods_store": "杂货邮亭",
    "convenience_store": "杂货邮亭",
    "post_office": "杂货邮亭",
    "craft_corner": "手作工坊",
    "porch_market": "露台市集",
}

_V09_COMMITMENT_STATE_ZH: dict[str, str] = {
    "ORDERED": "已预订待到货",
    "ARRIVED_HOLDING": "已到店留存",
    "DELAYED_RESCHEDULED": "到货延期",
    "SCHEDULED": "已约好时间",
    "ACTIVE_WINDOW": "正值约定时间窗",
    "RESCHEDULED_INDOOR": "已改室内碰面",
    "CANCELLED_BY_WEATHER": "因雨取消",
    "CANCELLED_WITH_NOTICE": "已留言改期",
}


def _read_v09_living_agency_and_world_zero_write(
    root_path: Optional[Path],
    *,
    include_private: bool,
    now_iso: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Read v0.9 stance/objects/commitments state strictly zero-write (0 dirs, 0 files, 0 locks)."""
    if root_path is None:
        return None
    try:
        base_root = Path(root_path)
        if base_root.is_symlink() or not base_root.is_dir():
            return None
    except OSError:
        return None

    resolved_root = base_root
    try:
        from components.world.scripts import instance_paths as _ip
        resolved_root = _ip.resolve_instance_paths(base_root).day_root
    except Exception:
        try:
            import instance_paths as _ip  # type: ignore[no-redef]
            resolved_root = _ip.resolve_instance_paths(base_root).day_root
        except Exception:
            resolved_root = base_root

    stance_file = resolved_root / "life" / "v09_true_stance_state.json"
    tax_file = resolved_root / "world" / "personal_objects_taxonomy.json"
    cmt_file = resolved_root / "world" / "npc_commitments_state.json"

    def _is_reg(p: Path) -> bool:
        try:
            return p.is_file() and not p.is_symlink()
        except OSError:
            return False

    has_stance = _is_reg(stance_file)
    has_tax = _is_reg(tax_file)
    has_cmt = _is_reg(cmt_file)
    if not (has_stance or has_tax or has_cmt):
        return None

    try:
        from components.life.scripts import v02_representation_lease_forget as _rlf
    except Exception:
        try:
            import v02_representation_lease_forget as _rlf  # type: ignore[no-redef]
        except Exception:
            _rlf = None  # type: ignore[assignment]
    if _rlf is not None:
        try:
            barrier = _rlf.load_unified_forget_barrier_readonly(resolved_root)
            if isinstance(barrier, Mapping) and barrier.get("fail_closed"):
                return None
        except Exception:
            return None

    actor_side = "subject" if include_private else "partner"
    combined: dict[str, Any] = {
        "perspective": actor_side,
        "blind_choices": [],
        "growth_stances": [],
        "repurposed_objects": [],
        "custom_categories": [],
        "categorized_items": [],
        "commitments": [],
        "active_commitments": [],
        "venue_notices": [],
        "observed_venue_notices": [],
    }

    if has_stance:
        try:
            try:
                from components.life.scripts import v09_true_stance_engine as _tse
            except ImportError:
                import v09_true_stance_engine as _tse  # type: ignore[no-redef]
            st_doc = _tse.read_true_stance_zero_write(
                resolved_root, actor_side=actor_side, now_iso=now_iso
            )
            if isinstance(st_doc, Mapping) and not st_doc.get("forget_barrier_fail_closed"):
                combined["blind_choices"] = list(st_doc.get("blind_choices") or [])
                combined["growth_stances"] = list(st_doc.get("growth_stances") or [])
        except Exception:
            pass

    if has_tax:
        try:
            try:
                from components.world.scripts import personal_objects_taxonomy as _pot
            except ImportError:
                import personal_objects_taxonomy as _pot  # type: ignore[no-redef]
            tx_doc = _pot.read_personal_objects_taxonomy_zero_write(
                resolved_root, actor_side=actor_side, now_iso=now_iso
            )
            if isinstance(tx_doc, Mapping) and not tx_doc.get("forget_barrier_fail_closed"):
                combined["repurposed_objects"] = list(tx_doc.get("repurposed_objects") or [])
                combined["custom_categories"] = list(tx_doc.get("custom_categories") or [])
                combined["categorized_items"] = list(tx_doc.get("categorized_items") or [])
        except Exception:
            pass

    if has_cmt:
        try:
            try:
                from components.world.scripts import world_commitments_engine as _wce
            except ImportError:
                import world_commitments_engine as _wce  # type: ignore[no-redef]
            cm_doc = _wce.read_world_commitments_zero_write(
                resolved_root, actor_side=actor_side, now_iso=now_iso
            )
            if isinstance(cm_doc, Mapping) and not cm_doc.get("forget_barrier_fail_closed"):
                combined["commitments"] = list(cm_doc.get("commitments") or [])
                combined["active_commitments"] = list(cm_doc.get("active_commitments") or [])
                combined["venue_notices"] = list(cm_doc.get("venue_notices") or [])
                combined["observed_venue_notices"] = list(cm_doc.get("observed_venue_notices") or [])
        except Exception:
            pass

    if not any(
        combined[k]
        for k in (
            "blind_choices",
            "growth_stances",
            "repurposed_objects",
            "custom_categories",
            "active_commitments",
            "observed_venue_notices",
        )
    ):
        return None
    return combined


def _format_v09_living_agency_micro(
    doc: Optional[Mapping[str, Any]],
    *,
    pronoun: str = "TA",
    include_private: bool = False,
) -> str:
    """Format a bounded ``<= 160`` char v0.9 stance/choice/commitment/object cue (calm -> ``""``)."""
    if not isinstance(doc, Mapping) or not doc:
        return ""
    if doc.get("forget_barrier_fail_closed"):
        return ""

    pron = _one_line(pronoun, 16) or "TA"
    segments: list[str] = []

    # 1. Blind choice / retained reservations
    raw_choices = [
        row for row in (doc.get("blind_choices") or ()) if isinstance(row, Mapping)
    ]
    choice_seg = ""
    for bc in raw_choices:
        status = str(bc.get("status") or "").upper()
        topic = _one_line_clean(bc.get("topic_cn"), 16)
        if not topic:
            continue
        retained = [
            _one_line_clean(r.get("text_cn"), 14)
            for r in (bc.get("retained_reservations") or ())
            if isinstance(r, Mapping) and _one_line_clean(r.get("text_cn"), 14)
        ]
        if status == "RESOLVED" and retained:
            res_joined = "、".join(retained[:2])
            if str(bc.get("resolution_mode") or "") == "ACCEPT_CHOICE_KEEP_REASONS":
                choice_seg = f"共同选择：〈{topic}〉已照伴侣选定执行，但{pron}仍保留原顾虑（{res_joined}）"
            else:
                choice_seg = f"共同选择：〈{topic}〉{pron}仍保留顾虑（{res_joined}）"
            break
    if not choice_seg:
        for bc in raw_choices:
            status = str(bc.get("status") or "").upper()
            topic = _one_line_clean(bc.get("topic_cn"), 16)
            if not topic or status == "RESOLVED":
                continue
            if status in ("OPEN_AWAITING_BOTH", "OPEN_PARTNER_COMMITTED"):
                choice_seg = f"共同选择：〈{topic}〉正等{pron}先独自选定再揭晓"
            elif status == "OPEN_SUBJECT_COMMITTED":
                choice_seg = f"共同选择：〈{topic}〉{pron}已封存选择待揭晓"
            elif status in ("REVEALED", "DISCUSSED"):
                choice_seg = f"共同选择：〈{topic}〉已揭晓理由待商议"
            if choice_seg:
                break
    if choice_seg:
        segments.append(choice_seg)

    # 2. Growth-from-Blank stances (respecting visibility)
    raw_stances = [
        row
        for row in (doc.get("growth_stances") or ())
        if isinstance(row, Mapping)
        and (include_private or bool(row.get("shared_with_partner")))
    ]
    formed_cue = ""
    unformed_cue = ""
    for st in raw_stances:
        topic = _one_line_clean(st.get("topic_cn"), 14)
        if not topic:
            continue
        st_status = str(st.get("status") or "").upper()
        tier = str(st.get("provenance_tier") or "").lower()
        summary = _one_line_clean(st.get("stance_summary_cn"), 18)
        if st_status in ("FORMED", "REVISED") and not formed_cue and summary:
            prov_label = (
                "亲身试过"
                if tier == "firsthand_lived"
                else ("读过后" if tier == "read_knowledge" else "")
            )
            formed_cue = f"〈{topic}〉{pron}{prov_label}{summary}"
        elif st_status == "UNFORMED" and not unformed_cue:
            unformed_cue = f"〈{topic}〉这件事{pron}还没想过"
        elif st_status == "TENTATIVE" and not formed_cue and summary:
            formed_cue = f"〈{topic}〉{pron}暂觉得{summary}"
    stance_bits = [b for b in (formed_cue, unformed_cue) if b]
    if stance_bits:
        segments.append("主见留痕：" + "；".join(stance_bits[:2]))

    # 3. Active commitments / repurposed objects / custom categories
    world_bits: list[str] = []
    raw_cmts = [
        row
        for row in (doc.get("active_commitments") or ())
        if isinstance(row, Mapping) and not row.get("is_terminal")
    ]
    arrived_cmt = next(
        (
            c
            for c in raw_cmts
            if str(c.get("kind") or "") == "BOOK_OR_ITEM_RESERVATION"
            and str(c.get("state") or "") == "ARRIVED_HOLDING"
        ),
        None,
    )
    if arrived_cmt is not None:
        venue_raw = str(arrived_cmt.get("venue_id") or "")
        venue_zh = _V09_VENUE_LABEL_ZH.get(venue_raw, _one_line_clean(venue_raw, 12) or "街坊铺子")
        c_title = _one_line_clean(arrived_cmt.get("title_cn"), 14)
        world_bits.append(f"{venue_zh}预订「{c_title}」已到店留存")
    elif raw_cmts:
        c0 = raw_cmts[0]
        c_title = _one_line_clean(c0.get("title_cn"), 14)
        c_state = _V09_COMMITMENT_STATE_ZH.get(str(c0.get("state") or ""), "")
        if c_title:
            world_bits.append(f"「{c_title}」{c_state}" if c_state else f"「{c_title}」")
    else:
        obs_notices = [
            _one_line_clean(n.get("text_cn") or n.get("notice_cn"), 22)
            for n in (doc.get("observed_venue_notices") or ())
            if isinstance(n, Mapping)
            and _one_line_clean(n.get("text_cn") or n.get("notice_cn"), 20)
        ]
        if obs_notices:
            world_bits.append(obs_notices[-1])

    raw_objs = [
        row
        for row in (doc.get("repurposed_objects") or ())
        if isinstance(row, Mapping)
        and (include_private or str(row.get("visibility") or "shared") == "shared")
    ]
    for obj in raw_objs:
        custom_lbl = _one_line_clean(obj.get("custom_label_cn"), 12)
        base_name = _one_line_clean(obj.get("base_name_cn") or obj.get("object_kind"), 10)
        stored = obj.get("stored_items") if isinstance(obj.get("stored_items"), list) else []
        if custom_lbl and stored:
            world_bits.append(f"{base_name}贴了「{custom_lbl}」收着{len(stored)}件小物")
            break
        if custom_lbl:
            world_bits.append(f"{base_name}贴了「{custom_lbl}」")
            break
        if stored:
            world_bits.append(f"{base_name}收着{len(stored)}件小物")
            break

    if len(world_bits) < 2:
        raw_cats = [
            row
            for row in (doc.get("custom_categories") or ())
            if isinstance(row, Mapping)
            and not row.get("archived")
            and (include_private or str(row.get("visibility") or "shared") == "shared")
        ]
        if raw_cats:
            cat0 = raw_cats[0]
            cat_title = _one_line_clean(cat0.get("title_cn"), 12)
            if cat_title:
                world_bits.append(f"自建分类「{cat_title}」")

    if world_bits:
        segments.append("街坊约定：" + "；".join(world_bits[:2]))

    if not segments:
        return ""
    return _truncate_section("- " + "；".join(segments), 160)


#: Historical order of the middle sections, before any situational priority is applied.
_MIDDLE_SECTION_ORDER = (
    "autonomy_line",
    "interactive_line",
    "epistemic_micro_line",
    "desires_line",
    "v03_line",
    "v04_line",
    "v04_project_line",
    "v02_deep_line",
    "v02_skill_drift_ecology_line",
    "v02_world_lineage_line",
    "v08_traces_line",
    "v08_echoes_line",
    "v03_enc_line",
    "v08_body_line",
    "v09_agency_world_line",
    "v05_town_spark_line",
    "desk_line",
    "sensory_line",
    "anchor_line",
)

#: Ch.47 §七: low-salience flavor/mood hints.  They sort strictly *after* every
#: commitment/boundary/interactive line so they are the first to evict at the
#: ``MAX_LIVED_CONTEXT_CHARS`` ceiling.
_FLAVOR_SECTION_NAMES = frozenset({"desk_line", "sensory_line", "anchor_line"})


def _situation_boost(name: str, text: str, clean_incoming: str) -> int:
    """Deterministic 0..3 priority boost for one middle section under today's words."""

    if not clean_incoming:
        return 0
    if name == "v08_echoes_line":
        if _URGENCY_EXCEPTION_CUE in text:
            return 3
        if "默契说法" in text or "留给未来的问题被触发" in text:
            return 3
        if text and _incoming_touches(clean_incoming, _V08_MEMORY_CUE_WORDS):
            return 2
        return 0
    if name == "v08_traces_line":
        return 2 if _incoming_touches(clean_incoming, _V08_TRACE_CUE_WORDS) else 0
    if name == "v09_agency_world_line":
        return 2 if _incoming_touches(clean_incoming, _V09_AGENCY_WORLD_CUE_WORDS) else 0
    if name == "v03_enc_line":
        return 2 if _incoming_touches(clean_incoming, _V08_ENC_CUE_WORDS) else 0
    if name in ("sensory_line", "v03_line"):
        return 2 if _incoming_touches(clean_incoming, _V08_BODY_CUE_WORDS) else 0
    if name == "anchor_line":
        return 2 if _incoming_touches(clean_incoming, _V08_ANCHOR_CUE_WORDS) else 0
    if name == "v05_town_spark_line":
        return (
            2
            if _incoming_touches(
                clean_incoming, _V05_SEASON_SOLAR_CUE_WORDS + _V05_TOWN_CUE_WORDS
            )
            else 0
        )
    return 0


def _rank_middle_sections_by_situation(
    sections: list[tuple[str, str]],
    *,
    clean_incoming: str,
) -> list[tuple[str, str]]:
    """Order non-core middle sections by situational relevance, stable on ties.

    ``autonomy_line`` is pinned first so an active activity or refusal never drops below
    a situational echo; every other section keeps its historical index unless a cue boosts it.
    With empty ``clean_incoming`` every boost is 0 and the exact historical order is returned.
    """

    pinned = [item for item in sections if item[0] == "autonomy_line"]
    rest = [item for item in sections if item[0] != "autonomy_line"]
    order_index = {name: idx for idx, name in enumerate(_MIDDLE_SECTION_ORDER)}
    # Tier 0 = commitments / boundaries / informative lines; tier 1 = living-town/seasonal cue;
    # tier 2 = ambient/sensory flavor. When a situational cue yields ``boost >= 2``, the section
    # is promoted to ``effective_tier = 0`` so relevant sensory/town cues are never starved behind
    # unboosted Tier 0 lines. Commitment-bearing ``interactive_line`` retains priority ahead of
    # ``boost == 2`` promotions, while ``boost >= 3`` (urgency / confirmed idiom) still ranks #1
    # right after ``autonomy_line``.
    decorated: list[tuple[int, int, int, int, str, str]] = []
    for idx, (name, text) in enumerate(rest):
        boost = _situation_boost(name, text, clean_incoming)
        if name in _FLAVOR_SECTION_NAMES:
            tier = 2
        elif name == "v05_town_spark_line":
            tier = 1
        else:
            tier = 0
        effective_tier = 0 if boost >= 2 else tier
        effective_boost = max(boost, 2) if (clean_incoming and name == "interactive_line") else boost
        decorated.append(
            (
                effective_tier,
                -effective_boost,
                order_index.get(name, len(_MIDDLE_SECTION_ORDER) + idx),
                idx,
                name,
                text,
            )
        )
    decorated.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
    ranked = [(name, text) for _t, _b, _o, _i, name, text in decorated]
    return pinned + ranked


def _active_nonintimate_concern_label(
    desires_doc: Optional[Mapping[str, Any]],
    *,
    day_root: Optional[Path],
    now_iso: Optional[str],
    blocked_ids: Optional[set[str]] = None,
    barrier: Optional[Mapping[str, Any]] = None,
) -> str:
    """One non-intimate active concern label, after source-loss and forget-barrier filtering.

    Prefers the zero-write lifecycle projection (which already removed concerns
    whose structured origin vanished) and falls back to the raw stored shapes.
    Intimacy concerns are *always* skipped here -- this line may surface on an
    ordinary channel.
    """

    if isinstance(barrier, Mapping) and barrier.get("fail_closed"):
        return ""
    eff_blocked = set(blocked_ids or ())
    rows: list[Mapping[str, Any]] = []
    if day_root is not None:
        try:
            try:
                from components.life.scripts import everyday_desire_lifecycle as _edl  # type: ignore[import-not-found]
            except ImportError:
                import everyday_desire_lifecycle as _edl  # type: ignore[no-redef]
            proj = _edl.project_everyday_desires(day_root, now_iso=now_iso)
            rows.extend(
                row for row in proj.get("retained_concerns") or [] if isinstance(row, Mapping)
            )
            rows.extend(
                row for row in proj.get("turn_only_concerns") or [] if isinstance(row, Mapping)
            )
        except Exception:
            rows = []
    if not rows and isinstance(desires_doc, Mapping):
        for key in ("retained_concerns", "turn_only_buffer", "active_desires", "desires", "items"):
            raw = desires_doc.get(key)
            if isinstance(raw, Mapping):
                rows.extend(v for v in raw.values() if isinstance(v, Mapping))
            elif isinstance(raw, list):
                rows.extend(v for v in raw if isinstance(v, Mapping))

    for row in rows:
        if (eff_blocked or (isinstance(barrier, Mapping) and barrier.get("barrier_active"))) and _entry_hits_forget_barrier(
            row, eff_blocked, barrier=barrier
        ):
            continue
        if str(row.get("domain") or "").strip().lower() == "intimacy":
            continue
        status = str(row.get("status") or row.get("state") or "ACTIVE").strip().upper()
        if status not in ("ACTIVE", "LIVE", "RETAINED", "TURN_ONLY"):
            continue
        label = _one_line_clean(
            row.get("expression")
            or row.get("summary_zh")
            or row.get("title_zh")
            or row.get("title")
            or row.get("label")
            or row.get("summary"),
            34,
        )
        if label:
            return label
    return ""


def _format_compact_current_situation(
    *,
    v04_nb: Optional[Mapping[str, Any]] = None,
    v04_ep: Optional[Mapping[str, Any]] = None,
    autonomy_doc: Optional[Mapping[str, Any]] = None,
    tension_doc: Optional[Mapping[str, Any]] = None,
    desires_doc: Optional[Mapping[str, Any]] = None,
    include_private: bool = False,
    day_root: Optional[Path] = None,
    now_iso: Optional[str] = None,
    now_dt: Optional[datetime] = None,
    body_runtime: Optional[Mapping[str, Any]] = None,
    blocked_ids: Optional[set[str]] = None,
    barrier: Optional[Mapping[str, Any]] = None,
) -> str:
    """Ch.47 §三: ONE compact, zero-write "current situation" line (<= 160 chars).

    Only genuine active anchors qualify -- mid-route transit, an active/paused
    project, an active refusal/commitment, an open relational knot, or a
    non-intimate active concern that survived source-loss and forget-barrier
    filtering.  When everything is calm and default this returns ``""`` so the
    Wave 8 zero-character calm-silence gate is preserved.
    """

    eff_barrier: Mapping[str, Any] = barrier if isinstance(barrier, Mapping) else {}
    if not eff_barrier and day_root is not None:
        try:
            from components.life.scripts import v02_representation_lease_forget as _rlf_mod
            loaded_b = _rlf_mod.load_unified_forget_barrier_readonly(day_root)
            if isinstance(loaded_b, Mapping):
                eff_barrier = loaded_b
        except Exception:
            eff_barrier = {}
    if eff_barrier.get("fail_closed"):
        return ""
    eff_blocked: set[str] = set(blocked_ids or ())
    if isinstance(eff_barrier.get("blocked_ids"), (set, frozenset, list, tuple)):
        eff_blocked.update(str(x).strip() for x in eff_barrier["blocked_ids"] if str(x).strip())
    if eff_blocked or eff_barrier.get("barrier_active"):
        v04_nb = _filter_mapping_by_barrier(v04_nb, eff_blocked, barrier=eff_barrier)
        v04_ep = _filter_mapping_by_barrier(v04_ep, eff_blocked, barrier=eff_barrier)
        autonomy_doc = _filter_mapping_by_barrier(autonomy_doc, eff_blocked, barrier=eff_barrier)
        tension_doc = _filter_mapping_by_barrier(tension_doc, eff_blocked, barrier=eff_barrier)
        desires_doc = _filter_mapping_by_barrier(desires_doc, eff_blocked, barrier=eff_barrier)

    moment = now_dt or _parse_iso(now_iso) or datetime.now(tz=timezone.utc)
    bits: list[str] = []

    if isinstance(v04_nb, Mapping):
        oc = v04_nb.get("outing_continuity") if isinstance(v04_nb.get("outing_continuity"), Mapping) else {}
        jny = v04_nb.get("journey") if isinstance(v04_nb.get("journey"), Mapping) else {}
        state_txt = str(
            oc.get("state") or oc.get("status") or jny.get("status") or jny.get("state") or ""
        ).strip().lower()
        target = _one_line(
            oc.get("target_place_id")
            or jny.get("target_place_id")
            or jny.get("to_place")
            or jny.get("destination")
            or jny.get("to"),
            18,
        )
        paused_node = _one_line(oc.get("paused_at_node") or jny.get("paused_at_node"), 18)
        here = _one_line(jny.get("current_place_id") or jny.get("from_place"), 18)
        if bool(oc.get("is_turning_back") or jny.get("is_turning_back")):
            bits.append("正在从外面折返")
        elif paused_node or state_txt == "paused_at_node":
            bits.append(f"正停在{paused_node or here or '中途'}，还没定下一步")
        elif target and state_txt in (
            "in_transit",
            "active",
            "walking",
            "en_route",
            "moving",
        ):
            bits.append(f"正在往{target}去，路上还没到")
        elif target and oc.get("target_place_id"):
            bits.append(f"正要去{target}")

    if include_private and isinstance(v04_ep, Mapping):
        for row in v04_ep.get("personal_projects") or []:
            if not isinstance(row, Mapping):
                continue
            if str(row.get("status") or "active").strip().lower() != "active":
                continue
            purpose = _one_line(row.get("purpose_cn"), 24)
            if not purpose:
                continue
            next_step = _one_line(row.get("next_step_cn"), 20)
            bits.append(f"手头在做「{purpose}」" + (f"，下一步可{next_step}" if next_step else ""))
            break

    if include_private and isinstance(autonomy_doc, Mapping):
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
            else (ref_block.get("refusal_locks") if isinstance(ref_block.get("refusal_locks"), list) else [])
        )
        for lock in locks:
            if not isinstance(lock, Mapping):
                continue
            expires = _parse_iso(lock.get("expires_at"))
            if expires is not None and moment >= expires:
                continue
            topic = _one_line(lock.get("topic_label") or lock.get("topic_key"), 22)
            if topic:
                bits.append(f"对「{topic}」还守着拒绝立场")
                break

    if include_private and isinstance(tension_doc, Mapping):
        quarrel = (
            tension_doc.get("quarrel")
            if isinstance(tension_doc.get("quarrel"), Mapping)
            else (
                tension_doc.get("quarrel_and_repair")
                if isinstance(tension_doc.get("quarrel_and_repair"), Mapping)
                else (tension_doc.get("quarrel_domain") if isinstance(tension_doc.get("quarrel_domain"), Mapping) else {})
            )
        )
        ruptures = quarrel.get("ruptures") if isinstance(quarrel, Mapping) else None
        rows = list(ruptures.values()) if isinstance(ruptures, Mapping) else (ruptures if isinstance(ruptures, list) else [])
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            if str(row.get("status") or "") in ("RESOLVED", "LET_GO", "CLOSED", ""):
                continue
            scope = _one_line(row.get("description") or row.get("scope") or row.get("summary"), 22)
            bits.append("关系里有个疙瘩还没翻篇" + (f"（{scope}）" if scope else ""))
            break

    concern_label = _active_nonintimate_concern_label(
        desires_doc,
        day_root=day_root,
        now_iso=now_iso,
        blocked_ids=eff_blocked,
        barrier=eff_barrier,
    )
    if concern_label:
        bits.append(f"心里还惦记着{concern_label}")

    eff_body = body_runtime
    if eff_body is None and day_root is not None:
        eff_body = _read_body_runtime_zero_write(day_root)
    if isinstance(eff_body, Mapping) and eff_body.get("has_notable_acute"):
        acute_cues = [
            _one_line(c, 22)
            for c in (eff_body.get("notable_cues") or [])
            if isinstance(c, str) and c.strip()
        ]
        if acute_cues:
            bits.append("体感：" + "、".join(acute_cues[:3]))

    if not bits:
        return ""
    return _truncate_section("当前处境：" + "；".join(bits[:3]), 160)


def _format_v03_epistemic_encounters_micro(
    doc: Optional[Mapping[str, Any]],
    *,
    include_private: bool,
) -> str:
    """One bounded line about finite-glance encounters, dropped material and hearsay.

    Never quotes a ``WITHDRAWN`` material, never mentions an unshared encounter when
    ``include_private`` is false, and stays within 140 characters.  The dropped material
    is mentioned gently -- never as an unread debt or a prompt to hurry.
    """

    if not isinstance(doc, Mapping):
        return ""

    parts: list[str] = []

    encounters = [row for row in (doc.get("encounters") or []) if isinstance(row, Mapping)]
    if not include_private:
        encounters = [row for row in encounters if row.get("shared") is not False]
    if encounters:
        row = encounters[-1]
        title = _one_line(row.get("target_title_cn"), 18)
        seen_pages = row.get("seen_pages") or []
        total_pages = row.get("total_pages")
        desc = f"《{title}》" if title else ""
        if isinstance(total_pages, int) and total_pages > 0:
            desc += f"只看过{len(seen_pages)}/{total_pages}页"
        stage = str(row.get("stage") or "")
        if stage == "FIRST_GLANCE":
            desc += "，其余页还没看"
        elif stage == "QUESTION_DRIVEN_REVISIT":
            question = _one_line(row.get("active_question_cn"), 18)
            detail = _one_line(row.get("noticed_detail_cn"), 20)
            desc += "，带着问题重看" + (f"({question})" if question else "")
            desc += (f"，注意到{detail}" if detail else "")
        elif stage == "NUANCED_RECOGNITION":
            kept = _one_line(row.get("partial_disagreement_kept"), 22)
            desc += "，有具体评价" + (f"，仍保留异议:{kept}" if kept else "")
        if desc:
            parts.append("有限视野:" + desc)

    materials = [
        row
        for row in (doc.get("shared_materials") or [])
        if isinstance(row, Mapping) and str(row.get("status") or "") != "WITHDRAWN"
    ]
    if materials:
        unopened = [row for row in materials if str(row.get("status") or "") == "RECEIVED_UNOPENED"]
        if unopened:
            title = _one_line(unopened[-1].get("title_cn"), 20)
            parts.append(f"伴侣递来《{title}》，没催着读")
        else:
            row = materials[-1]
            title = _one_line(row.get("title_cn"), 20)
            status = str(row.get("status") or "")
            if status == "USED_IN_WORK":
                parts.append(f"《{title}》已用进作品里")
            elif status == "OPENED_PARTIAL":
                parts.append(f"《{title}》只翻了几页")

    npc_rows = [
        row
        for row in (doc.get("npc_entries") or doc.get("npc_errands_and_hearsay") or [])
        if isinstance(row, Mapping)
    ]
    if npc_rows:
        row = npc_rows[-1]
        label = _one_line(row.get("npc_label_cn"), 14)
        summary = _one_line(row.get("summary_cn"), 28)
        if str(row.get("kind") or "") == "hearsay":
            source = _one_line(row.get("hearsay_source_cn"), 16)
            if row.get("verified_in_person") is True:
                source_tag = "亲见"
            elif source:
                source_tag = f"听说自{source}"
            else:
                source_tag = "听说"
            parts.append(f"{label}的事({source_tag}):{summary}")
        else:
            parts.append(f"{label}手头事:{summary}")

    # Concern-driven attention lens (v3.0 Ch.23 / Ch.30): what she is more likely to notice
    # right now given her open questions / directions.  Subject-only ambient context -- never a
    # debt and never a prompt to hurry.  Placed first so the bounded line keeps it.
    if include_private:
        cues = doc.get("attention_cues")
        if isinstance(cues, list):
            lens: list[str] = []
            for cue in cues[:2]:
                if not isinstance(cue, Mapping):
                    continue
                title = _one_line(cue.get("title_cn"), 20)
                concern = _one_line(cue.get("matched_concern_cn"), 16)
                if not title:
                    continue
                lens.append(f"{title}(因{concern})" if concern else title)
            if lens:
                parts.insert(0, "眼下更留意的：" + "、".join(lens))

    if not parts:
        return ""
    return _truncate_section("- 有限视野与街坊听说：" + "；".join(parts[:3]), 140)


def render_lived_companion_context(
    home: Path,
    cfg: Mapping[str, Any],
    incoming_text: str = "",
    *,
    ephemeral_hint: Optional[str] = None,
    now_iso: Optional[str] = None,
    _diag: Optional[dict[str, Any]] = None,
) -> str:
    """Render bounded (<= 1800 chars), zero-write lived context (plus one-shot turn hint if present).

    ``now_iso`` is the caller's committed/simulated clock.  Every TTL check
    (spaces, kept notes, favors, lingering threads) compares against it; the OS
    clock is only a fallback when no committed timestamp is supplied.

    ``_diag`` is an internal, read-only diagnostics sink used by
    :func:`measure_context_budget_breakdown`; it never changes the returned text.
    """

    try:
        home_path = Path(home)
        if not isinstance(cfg, Mapping):
            return ""

        deadline = time.monotonic() + TOTAL_TIMEOUT_BUDGET_S
        now_dt = _parse_iso(now_iso) or datetime.now(tz=timezone.utc)
        # Read-only pattern matcher only: the partner's genuine words steer section priority and
        # wake matching echoes, but never touch disk or turn state (strict zero-write).
        clean_incoming = strip_injected_turn_envelope(
            incoming_text if isinstance(incoming_text, str) else ""
        )

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
            "v08_emergent_echoes",
            "v08_composable_body",
            "window_keepsakes",
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
                        ("v02_honest_ux", "/api/phone/v02/honest-ux"),
                        ("v02_recall_projection", "/api/phone/v02/recall-projection"),
                        ("v02_common_ground", "/api/phone/v02/common-ground"),
                        ("v02_ecology_growth", "/api/phone/v02/ecology-growth"),
                        ("v02_world_lineage", "/api/phone/v02/world-lineage"),
                        ("v08_living_traces", "/api/phone/v08/living-traces"),
                        ("v08_emergent_echoes", "/api/phone/v08/emergent-echoes"),
                        ("v03_encounters", "/api/phone/v03/encounters"),
                        ("v08_composable_body", "/api/phone/v08/composable-body"),
                        ("v09_living_agency", "/api/phone/v09/living-agency"),
                        ("v09_world_commitments", "/api/phone/v09/world-commitments"),
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
                        *(
                            (("window_keepsakes", "/api/phone/home/letters"),)
                            if bool(cap)
                            else ()
                        ),
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
                ("v02_honest_ux", "v02_honest_ux_state.json"),
                ("v02_common_ground", "v02_common_ground.json"),
                ("v02_recall_projection", "v02_recall_projection.json"),
                ("v08_emergent_echoes", "v08_emergent_echoes.json"),
                ("everyday_desires", "everyday_desires.json"),
                ("window_keepsakes", "window_keepsakes.json"),
            ):
                if not private_artifacts_allowed and key_name in private_keys:
                    continue
                disk_docs[key_name] = _read_json_readonly(phone_dir / fname)
            if disk_docs.get("v02_runtime_migration") is None:
                disk_docs["v02_runtime_migration"] = _read_json_readonly(
                    phone_dir.parent / "state" / "v02_runtime_governance_migration.json"
                )
            if private_artifacts_allowed and disk_docs.get("v02_crossmodal") is None:
                disk_docs["v02_crossmodal"] = _read_json_readonly(
                    phone_dir.parent / "state" / "v02_crossmodal_perception.json"
                )
            if private_artifacts_allowed and disk_docs.get("v02_crossmodal") is not None:
                # Re-verify album source liveness via zero-write crossmodal projection when available
                raw_cm_disk = disk_docs.get("v02_crossmodal")
                try:
                    from components.phone import v02_crossmodal_view as _cm_view
                    _cm_c, _cm_env = _cm_view.read_v02_crossmodal_view(
                        phone_dir.parent, has_subject_capability=True
                    )
                    if _cm_c == 200 and isinstance(_cm_env.get("data"), Mapping):
                        merged_cm = dict(_cm_env["data"])
                        if (
                            ("audio_notes" not in merged_cm or not merged_cm.get("state_exists"))
                            and isinstance(raw_cm_disk, Mapping)
                            and isinstance(raw_cm_disk.get("audio_notes"), list)
                        ):
                            merged_cm["audio_notes"] = list(raw_cm_disk["audio_notes"])
                        disk_docs["v02_crossmodal"] = merged_cm
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
        elif explicit_root_exists:
            for root_cand in _resolve_day_root_candidates(home_path, cfg):
                if disk_docs.get("v02_runtime_migration") is None:
                    loaded_rm = _read_json_readonly(
                        root_cand / "state" / "v02_runtime_governance_migration.json"
                    )
                    if loaded_rm is not None:
                        disk_docs["v02_runtime_migration"] = loaded_rm
                if disk_docs.get("v02_honest_ux") is None:
                    loaded_hux = _read_json_readonly(root_cand / "phone" / "v02_honest_ux_state.json")
                    if loaded_hux is not None:
                        disk_docs["v02_honest_ux"] = loaded_hux
                if private_artifacts_allowed:
                    if disk_docs.get("v02_crossmodal") is None:
                        loaded_cm = _read_json_readonly(
                            root_cand / "state" / "v02_crossmodal_perception.json"
                        )
                        if loaded_cm is not None:
                            disk_docs["v02_crossmodal"] = loaded_cm
                    if disk_docs.get("v02_agency_depth") is None:
                        loaded_ad = _read_json_readonly(
                            root_cand / "state" / "v02_agency_depth_state.json"
                        )
                        if loaded_ad is not None:
                            disk_docs["v02_agency_depth"] = loaded_ad
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
            if disk_docs.get("v08_living_traces") is None:
                microzone_state_present = (
                    (root_cand / "world" / "v08_microzone_state.json").is_file()
                    or (root_cand / "world" / "v04_neighborhood_state.json").is_file()
                )
                try:
                    from components.phone.v08_living_traces_view import (
                        summarize_living_traces_for_context as _v08_summarize,
                    )
                    if (
                        (root_cand / "phone" / "v08_living_traces.json").is_file()
                        or (root_cand / "world" / "v08_matter_space.json").is_file()
                    ):
                        disk_docs["v08_living_traces"] = _v08_summarize(
                            root_cand, include_private=private_artifacts_allowed
                        )
                except Exception:
                    pass
                # Microzone ecology: loaded read-only and merged onto the projection so a
                # state file that exists without a living-traces projection still surfaces
                # a genuinely salient cue (and stays invisible when quiet/comfortable/calm).
                # Zero writes regardless of whether Worker 8A's engine has landed.
                if microzone_state_present:
                    _mz_view = _read_microzone_ecology_view_zero_write(
                        root_cand, include_private=private_artifacts_allowed
                    )
                    if isinstance(_mz_view, Mapping):
                        _base = disk_docs.get("v08_living_traces")
                        if _base is None:
                            disk_docs["v08_living_traces"] = {}
                            _base = disk_docs["v08_living_traces"]
                        _merged = dict(_base) if isinstance(_base, Mapping) else {}
                        _merged.setdefault(
                            "perspective",
                            "subject" if private_artifacts_allowed else "partner",
                        )
                        _merged["microzone_ecology"] = dict(_mz_view)
                        disk_docs["v08_living_traces"] = _merged
            if disk_docs.get("v03_encounters") is None:
                try:
                    from components.phone.v03_encounters_view import (
                        summarize_encounters_for_context as _v03e_summarize,
                    )
                    if (root_cand / "phone" / "v03_encounters.json").is_file():
                        disk_docs["v03_encounters"] = _v03e_summarize(
                            root_cand, include_private=private_artifacts_allowed
                        )
                except Exception:
                    pass
            if disk_docs.get("v08_emergent_echoes") is None and private_artifacts_allowed:
                # v0.8 emergent echoes: read-only, private-artifact gated; degrade to "" when
                # Worker 2A's writer has not produced the file yet.
                disk_docs["v08_emergent_echoes"] = _read_json_readonly(
                    root_cand / "phone" / "v08_emergent_echoes.json"
                )
            if disk_docs.get("window_keepsakes") is None and private_artifacts_allowed:
                disk_docs["window_keepsakes"] = _read_json_readonly(
                    root_cand / "phone" / "window_keepsakes.json"
                )
            if disk_docs.get("v08_composable_body") is None and private_artifacts_allowed:
                # v0.8 composable body: prefer the engine's subject projection (it reads the
                # <day_root>/body/ pack 100% zero-write and computes the cycle "feeling"
                # field), then fall back to a plain read of the same pack file.  A missing
                # file simply contributes no cue.
                body_doc: Optional[dict[str, Any]] = None
                try:
                    from components.world.scripts import (
                        v08_composable_body as _cb_engine,
                    )
                    projection = _cb_engine.read_composable_body_zero_write(
                        root_cand, has_subject_capability=True
                    )
                    if (
                        isinstance(projection, Mapping)
                        and projection.get("perspective") == "subject"
                    ):
                        body_doc = dict(projection)
                except Exception:  # noqa: BLE001 - absence falls back to the raw pack read
                    body_doc = None
                if body_doc is None:
                    body_doc = _read_json_readonly(
                        root_cand / "body" / "v08_composable_body_pack.json"
                    )
                disk_docs["v08_composable_body"] = body_doc
            if disk_docs.get("v02_recall_projection") is None:
                loaded_rp_phone = _read_json_readonly(root_cand / "phone" / "v02_recall_projection.json")
                if loaded_rp_phone is not None:
                    disk_docs["v02_recall_projection"] = loaded_rp_phone
                elif (root_cand / "memory" / "v02_recall_projection_state.json").is_file():
                    try:
                        from components.life.scripts import v02_recall_projection as _rp_mod
                        rp_cap = bool(private_artifacts_allowed and (not http_alive or bool(cap)))
                        disk_docs["v02_recall_projection"] = _rp_mod.read_v02_recall_projection_view(
                            root_cand,
                            now_iso=now_iso,
                            has_subject_capability=rp_cap,
                        )
                    except Exception:
                        pass
            if disk_docs.get("v02_ecology_growth") is None:
                eco_state_file = root_cand / "state" / "v02_midscale_ecology.json"
                asp_state_file = root_cand / "state" / "v02_autobiography_skill_provenance.json"
                has_asp_episodes = False
                if not eco_state_file.is_file() and asp_state_file.is_file():
                    raw_asp = _read_json_readonly(asp_state_file)
                    if isinstance(raw_asp, Mapping):
                        auto_sec = raw_asp.get("autobiography")
                        if isinstance(auto_sec, Mapping) and bool(auto_sec.get("episodes")):
                            has_asp_episodes = True
                if eco_state_file.is_file() or has_asp_episodes:
                    eco_cap = bool(private_artifacts_allowed and (not http_alive or bool(cap)))
                    loaded_eco: Optional[dict[str, Any]] = None
                    try:
                        from components.life.scripts import v02_midscale_ecology_engine as _eco_mod
                        loaded_eco = _eco_mod.read_midscale_ecology_view(
                            root_cand,
                            has_subject_capability=eco_cap,
                            now_iso=now_iso,
                        )
                    except Exception:
                        loaded_eco = None
                    if loaded_eco is None and eco_state_file.is_file():
                        loaded_eco = _read_json_readonly(eco_state_file)
                    if loaded_eco is not None:
                        disk_docs["v02_ecology_growth"] = loaded_eco
            if disk_docs.get("v02_world_lineage") is None:
                wl_state_file = root_cand / "state" / "v02_world_news_carrier_lineage.json"
                if wl_state_file.is_file():
                    wl_cap = bool(private_artifacts_allowed and (not http_alive or bool(cap)))
                    loaded_wl: Optional[dict[str, Any]] = None
                    try:
                        from components.life.scripts import v02_world_news_carrier_lineage as _wl_mod
                        loaded_wl = _wl_mod.read_world_news_carrier_lineage_view(
                            root_cand,
                            has_subject_capability=wl_cap,
                            now_iso=now_iso,
                        )
                    except Exception:
                        loaded_wl = None
                    if loaded_wl is None and wl_cap:
                        loaded_wl = _read_json_readonly(wl_state_file)
                    if loaded_wl is not None:
                        disk_docs["v02_world_lineage"] = loaded_wl
            if disk_docs.get("v02_personality_drift") is None:
                drift_state_file = root_cand / "state" / "v02_personality_drift_recheck.json"
                if drift_state_file.is_file():
                    dr_cap = bool(private_artifacts_allowed and (not http_alive or bool(cap)))
                    loaded_dr: Optional[dict[str, Any]] = None
                    try:
                        from components.life.scripts import v02_personality_drift_and_model_recheck as _dr_mod
                        loaded_dr = _dr_mod.read_personality_drift_and_recheck_view(
                            root_cand,
                            has_subject_capability=dr_cap,
                            now_iso=now_iso,
                        )
                    except Exception:
                        loaded_dr = None
                    if loaded_dr is None and dr_cap:
                        loaded_dr = _read_json_readonly(drift_state_file)
                    if loaded_dr is not None:
                        disk_docs["v02_personality_drift"] = loaded_dr
            if disk_docs.get("v02_skill_book") is None:
                asp_state_file = root_cand / "state" / "v02_autobiography_skill_provenance.json"
                if asp_state_file.is_file():
                    sb_cap = bool(private_artifacts_allowed and (not http_alive or bool(cap)))
                    loaded_sb: Optional[dict[str, Any]] = None
                    try:
                        from components.life.scripts import v02_autobiography_skill_provenance as _asp_mod
                        loaded_sb = _asp_mod.project_personal_skill_book_zero_write(
                            root_cand,
                            has_subject_capability=sb_cap,
                            now_iso=now_iso,
                        )
                    except Exception:
                        loaded_sb = None
                    if loaded_sb is None and sb_cap:
                        loaded_sb = _read_json_readonly(asp_state_file)
                    if loaded_sb is not None:
                        disk_docs["v02_skill_book"] = loaded_sb
            v05_now_iso = now_iso or now_dt.isoformat()
            if disk_docs.get("v05_living_town") is None:
                lt_state_file = root_cand / "world" / "v05_living_town.json"
                if not lt_state_file.is_symlink() and lt_state_file.is_file():
                    lt_snap: Optional[dict[str, Any]] = None
                    try:
                        from components.world.scripts import v05_living_town_engine as _lt_mod
                        lt_snap = _lt_mod.read_living_town_snapshot(
                            root_cand, now_iso=v05_now_iso
                        )
                    except Exception:
                        lt_snap = None
                    raw_lt = _read_json_readonly(lt_state_file)
                    if isinstance(lt_snap, Mapping):
                        merged_lt = dict(lt_snap)
                        if isinstance(raw_lt, Mapping):
                            for k_add in ("sound_scent_map", "drifting_bookbox", "bulletin_followups"):
                                if k_add not in merged_lt and k_add in raw_lt:
                                    merged_lt[k_add] = raw_lt[k_add]
                        disk_docs["v05_living_town"] = merged_lt
                    elif isinstance(raw_lt, Mapping) and bool(private_artifacts_allowed and (not http_alive or bool(cap))):
                        raw_lt_copy = dict(raw_lt)
                        raw_lt_copy.setdefault("state_exists", True)
                        disk_docs["v05_living_town"] = raw_lt_copy
            if disk_docs.get("v05_home_craft") is None:
                hc_state_file = root_cand / "world" / "v05_home_craft.json"
                if not hc_state_file.is_symlink() and hc_state_file.is_file():
                    loaded_hc: Optional[dict[str, Any]] = None
                    try:
                        from components.world.scripts import v05_home_craft_engine as _hc_mod
                        reader_hc = (
                            getattr(_hc_mod, "read_home_craft_snapshot", None)
                            or getattr(_hc_mod, "read_v05_home_craft_snapshot", None)
                            or getattr(_hc_mod, "project_v05_home_craft_state", None)
                        )
                        if callable(reader_hc):
                            proj_hc = reader_hc(root_cand, now_iso=v05_now_iso)
                            if isinstance(proj_hc, Mapping):
                                loaded_hc = dict(proj_hc)
                    except Exception:
                        loaded_hc = None
                    if loaded_hc is None and bool(private_artifacts_allowed and (not http_alive or bool(cap))):
                        loaded_hc = _read_json_readonly(hc_state_file)
                    if isinstance(loaded_hc, Mapping):
                        hc_dict = dict(loaded_hc)
                        if (
                            isinstance(hc_dict.get("data"), Mapping)
                            and "home_arrangement" not in hc_dict
                            and "moving_carton" not in hc_dict
                            and "parallel_postcards" not in hc_dict
                            and "crumpled_draft_signal" not in hc_dict
                        ):
                            hc_dict = dict(hc_dict["data"])
                        hc_dict.setdefault("state_exists", True)
                        disk_docs["v05_home_craft"] = hc_dict
            if disk_docs.get("v05_inner_spark") is None:
                is_state_file = root_cand / "life" / "v05_inner_spark.json"
                if is_state_file.is_file():
                    is_cap = bool(private_artifacts_allowed and (not http_alive or bool(cap)))
                    try:
                        from components.life.scripts import v05_dreams_desires_foreshadow as _is_mod
                        disk_docs["v05_inner_spark"] = _is_mod.project_v05_inner_spark_state(
                            root_cand, has_subject_capability=is_cap, now_iso=v05_now_iso
                        )
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
        v02_hux_doc = http_docs.get("v02_honest_ux") or disk_docs.get("v02_honest_ux")

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
        hux_inc_priv = bool(
            private_artifacts_allowed
            and (not isinstance(v02_hux_doc, Mapping) or v02_hux_doc.get("perspective") != "partner")
            and (not http_alive or bool(cap))
        )

        v02_rp_doc = http_docs.get("v02_recall_projection") or disk_docs.get("v02_recall_projection")
        v02_cg_doc = http_docs.get("v02_common_ground") or disk_docs.get("v02_common_ground")
        v02_eco_doc = http_docs.get("v02_ecology_growth") or disk_docs.get("v02_ecology_growth")
        v02_wl_doc = http_docs.get("v02_world_lineage") or disk_docs.get("v02_world_lineage")
        v02_skill_book_doc = (
            v02_rm_doc.get("personal_skill_book")
            if isinstance(v02_rm_doc, Mapping) and isinstance(v02_rm_doc.get("personal_skill_book"), Mapping)
            else None
        ) or disk_docs.get("v02_skill_book")
        v02_drift_doc = (
            v02_rm_doc.get("personality_drift_and_recheck")
            if isinstance(v02_rm_doc, Mapping) and isinstance(v02_rm_doc.get("personality_drift_and_recheck"), Mapping)
            else None
        ) or disk_docs.get("v02_personality_drift")
        cg_inc_priv = bool(
            ad_inc_priv
            and (not isinstance(v02_cg_doc, Mapping) or v02_cg_doc.get("perspective") != "partner")
        )
        rp_inc_priv = bool(
            ad_inc_priv
            and (not isinstance(v02_rp_doc, Mapping) or v02_rp_doc.get("perspective") != "partner")
        )
        rm_inc_priv = bool(
            private_artifacts_allowed
            and (not isinstance(v02_rm_doc, Mapping) or v02_rm_doc.get("perspective") != "partner")
            and (not http_alive or bool(cap))
        )
        eco_inc_priv = bool(
            private_artifacts_allowed
            and (not isinstance(v02_eco_doc, Mapping) or v02_eco_doc.get("perspective") != "partner")
            and (not http_alive or bool(cap))
        )
        wl_inc_priv = bool(
            private_artifacts_allowed
            and (not isinstance(v02_wl_doc, Mapping) or v02_wl_doc.get("perspective") != "partner")
            and (not http_alive or bool(cap))
        )

        micro_day_root: Optional[Path] = None
        if phone_dir is not None:
            micro_day_root = phone_dir.parent
        else:
            _root_candidates = _resolve_day_root_candidates(home_path, cfg)
            if _root_candidates:
                micro_day_root = _root_candidates[0]

        v02_signals = _read_v02_honest_signals_zero_write(
            micro_day_root,
            include_private=private_artifacts_allowed,
            incoming_text=clean_incoming,
        )
        _barrier_ids: set[str] = set(v02_signals.get("blocked_ids") or ())
        _barrier_obj: Mapping[str, Any] = (
            v02_signals.get("forget_barrier")
            if isinstance(v02_signals.get("forget_barrier"), Mapping)
            else {}
        )
        _barrier_enforced = bool(_barrier_ids or _barrier_obj.get("barrier_active") or _barrier_obj.get("fail_closed"))

        def _apply_barrier(doc: Any) -> Any:
            if not _barrier_enforced or doc is None:
                return doc
            return _filter_mapping_by_barrier(doc, _barrier_ids, barrier=_barrier_obj)

        _rp_ts_count = 0
        if isinstance(v02_rp_doc, Mapping) and not _barrier_obj.get("fail_closed"):
            _raw_cc = v02_rp_doc.get("clarity_counts") if isinstance(v02_rp_doc.get("clarity_counts"), Mapping) else {}
            _raw_ts_a = v02_rp_doc.get("tombstones")
            _raw_ts_b = v02_rp_doc.get("tombstone_ledger")
            _rp_ts_count = max(
                len(_raw_ts_a) if isinstance(_raw_ts_a, (Mapping, list)) else 0,
                len(_raw_ts_b) if isinstance(_raw_ts_b, (Mapping, list)) else 0,
                int(v02_rp_doc.get("tombstone_count") or 0) if isinstance(v02_rp_doc.get("tombstone_count"), (int, float)) and not isinstance(v02_rp_doc.get("tombstone_count"), bool) else 0,
                int(_raw_cc.get("TOMBSTONED") or 0) if isinstance(_raw_cc.get("TOMBSTONED"), (int, float)) and not isinstance(_raw_cc.get("TOMBSTONED"), bool) else 0,
            )

        # Filter ALL 18 source documents through the unified forget barrier before any formatting.
        autonomy_doc = _apply_barrier(http_docs.get("autonomy_rhythm") or disk_docs.get("autonomy_rhythm"))
        tension_doc = _apply_barrier(http_docs.get("emotional_tension") or disk_docs.get("emotional_tension"))
        safe_intimacy = _apply_barrier(safe_intimacy)
        v03_clock_doc = _apply_barrier(http_docs.get("v03_clock") or disk_docs.get("v03_clock"))
        life_dwell_doc = _apply_barrier(disk_docs.get("life_dwell"))
        v03_eco_doc = _apply_barrier(v03_eco_doc)
        v03_soc_doc = _apply_barrier(v03_soc_doc)
        v04_nb_doc = _apply_barrier(v04_nb_doc)
        v04_ep_doc = _apply_barrier(v04_ep_doc)
        desires_doc = _apply_barrier(http_docs.get("everyday_desires") or disk_docs.get("everyday_desires"))
        window_keepsakes_doc = _apply_barrier(http_docs.get("window_keepsakes") or disk_docs.get("window_keepsakes"))
        v02_ad_doc = _apply_barrier(v02_ad_doc)
        v02_cm_doc = _apply_barrier(v02_cm_doc)
        v02_rm_doc = _apply_barrier(v02_rm_doc)
        v02_hux_doc = _apply_barrier(v02_hux_doc)
        v02_rp_doc = _apply_barrier(v02_rp_doc)
        if _rp_ts_count > 0 and isinstance(v02_rp_doc, dict) and not _barrier_obj.get("fail_closed"):
            v02_rp_doc["tombstone_count"] = max(int(v02_rp_doc.get("tombstone_count") or 0), _rp_ts_count)
        v02_cg_doc = _apply_barrier(v02_cg_doc)
        v02_eco_doc = _apply_barrier(v02_eco_doc)
        v02_wl_doc = _apply_barrier(v02_wl_doc)
        v02_skill_book_doc = _apply_barrier(v02_skill_book_doc)
        v02_drift_doc = _apply_barrier(v02_drift_doc)
        v08_traces_doc = _apply_barrier(http_docs.get("v08_living_traces") or disk_docs.get("v08_living_traces"))
        v03_enc_doc = _apply_barrier(http_docs.get("v03_encounters") or disk_docs.get("v03_encounters"))
        v08_echoes_doc = _apply_barrier(http_docs.get("v08_emergent_echoes") or disk_docs.get("v08_emergent_echoes"))
        v08_body_doc = _apply_barrier(http_docs.get("v08_composable_body") or disk_docs.get("v08_composable_body"))
        desk_http_doc = _apply_barrier(http_docs.get("desk_presence"))
        stance_doc = _apply_barrier(http_docs.get("stance_containment") or disk_docs.get("stance_containment"))
        body_sensory_doc = _apply_barrier(http_docs.get("body_sensory"))
        body_somatic_doc = _apply_barrier(http_docs.get("body_somatic"))
        body_gw_doc = _apply_barrier(http_docs.get("body_garment_window"))
        body_care_doc = _apply_barrier(http_docs.get("body_care"))
        somatic_disk_doc = _apply_barrier(disk_docs.get("somatic"))
        sensory_disk_doc = _apply_barrier(disk_docs.get("sensory_cards"))
        wardrobe_doc = _apply_barrier(http_docs.get("wardrobe") or disk_docs.get("wardrobe"))
        reunion_doc = _apply_barrier(http_docs.get("reunion_anchor") or disk_docs.get("reunion_anchor"))
        habits_doc = _apply_barrier(http_docs.get("longterm_habits") or disk_docs.get("longterm_habits"))
        plans_doc = _apply_barrier(http_docs.get("tentative_plans") or disk_docs.get("tentative_plans"))
        v05_town_doc = _apply_barrier(http_docs.get("v05_living_town") or disk_docs.get("v05_living_town"))
        v05_spark_doc = _apply_barrier(http_docs.get("v05_inner_spark") or disk_docs.get("v05_inner_spark"))
        v05_home_craft_doc = _apply_barrier(http_docs.get("v05_home_craft") or disk_docs.get("v05_home_craft"))

        autonomy_line = _format_autonomy_and_tension(
            autonomy_doc=autonomy_doc,
            tension_doc=tension_doc,
            intimacy_doc=safe_intimacy,
            now_dt=now_dt,
        )
        wk_inc_priv = bool(
            private_artifacts_allowed
            and (not http_alive or bool(cap))
        )
        interactive_line = _format_high_priority_interactive(
            v03_ecology=v03_eco_doc,
            v03_social=v03_soc_doc,
            v04_ep=v04_ep_doc,
            window_keepsakes=window_keepsakes_doc if wk_inc_priv else None,
            include_private=wk_inc_priv,
            now_dt=now_dt,
        )
        epistemic_micro_line = _format_v02_v03_epistemic_and_clock_micro(
            v03_clock=v03_clock_doc,
            life_dwell=life_dwell_doc,
            v03_ecology=v03_eco_doc,
            autonomy_doc=autonomy_doc,
            common_ground_doc=_mask_common_ground_for_context(v02_cg_doc, include_private=cg_inc_priv),
            recall_doc=_mask_recall_for_context(v02_rp_doc, include_private=rp_inc_priv),
        )
        priv_with_cap = bool(
            private_artifacts_allowed
            and (not http_alive or bool(cap))
        )
        body_runtime_doc = _read_body_runtime_zero_write(micro_day_root)
        compact_situation_line = _format_compact_current_situation(
            v04_nb=v04_nb_doc,
            v04_ep=v04_ep_doc,
            autonomy_doc=autonomy_doc,
            tension_doc=tension_doc,
            desires_doc=desires_doc,
            include_private=priv_with_cap,
            day_root=micro_day_root,
            now_iso=now_iso,
            now_dt=now_dt,
            body_runtime=body_runtime_doc,
            blocked_ids=_barrier_ids,
            barrier=_barrier_obj,
        )
        surfaced_desire_texts: list[str] = []
        if compact_situation_line and "心里还惦记着" in compact_situation_line:
            after_mark = compact_situation_line.split("心里还惦记着", 1)[1]
            concern_seg = after_mark.split("；", 1)[0].strip()
            if concern_seg:
                surfaced_desire_texts.append(concern_seg)

        desires_line = _format_everyday_desires_micro(
            desires_doc,
            now_dt=now_dt,
            skip_desire_texts=surfaced_desire_texts or None,
        )
        spark_skip_desires: list[str] = list(surfaced_desire_texts)
        if desires_line and "当下日常小心愿：" in desires_line:
            d_body = desires_line.split("当下日常小心愿：", 1)[1].split("（自然心意", 1)[0]
            for seg in d_body.split("、"):
                clean_seg = re.sub(r"\([^()]*\)\s*$", "", seg).strip()
                if clean_seg:
                    spark_skip_desires.append(clean_seg)
        v03_line = _format_v03_illness_economy_hands(
            v03_clock=v03_clock_doc,
            v03_ecology=v03_eco_doc,
            v03_social=v03_soc_doc,
            stance_doc=stance_doc,
            intimacy_doc=safe_intimacy,
            tension_doc=tension_doc,
        )
        v04_line = _format_v04_neighborhood_and_elastic(
            v04_nb=v04_nb_doc,
            v04_ep=v04_ep_doc,
            now_dt=now_dt,
            incoming_text=clean_incoming,
            include_private=priv_with_cap,
        )
        v04_project_line = _format_v04_project_reentry_micro(
            v04_ep=v04_ep_doc,
            root=micro_day_root,
            include_private=priv_with_cap,
            now_dt=now_dt,
            blocked_ids=_barrier_ids,
            barrier=_barrier_obj,
        )
        v02_deep_line = _format_v02_deep_surfaces(
            agency_doc=v02_ad_doc,
            crossmodal_doc=v02_cm_doc,
            runtime_doc=v02_rm_doc,
            include_private_ad=ad_inc_priv,
            include_private_cm=cm_inc_priv,
            now_dt=now_dt,
            v02_signals=v02_signals,
            honest_ux_doc=v02_hux_doc,
            include_private_hux=hux_inc_priv,
        )
        v02_skill_drift_ecology_line = _format_v02_skill_drift_and_ecology(
            skill_book_doc=v02_skill_book_doc,
            drift_doc=v02_drift_doc,
            ecology_doc=v02_eco_doc,
            include_private_rm=rm_inc_priv,
            include_private_eco=eco_inc_priv,
            v02_signals=v02_signals,
        )
        v02_world_lineage_line = _format_v02_world_lineage(
            world_lineage_doc=v02_wl_doc,
            include_private=wl_inc_priv,
        )
        v08_inc_priv = bool(
            private_artifacts_allowed
            and (not isinstance(v08_traces_doc, Mapping) or v08_traces_doc.get("perspective") != "partner")
            and (not http_alive or bool(cap))
        )
        v08_matter_space: Optional[Mapping[str, Any]] = None
        if isinstance(v08_traces_doc, Mapping) and isinstance(
            v08_traces_doc.get("matter_space"), Mapping
        ):
            v08_matter_space = v08_traces_doc["matter_space"]
        elif micro_day_root is not None:
            # Disk fallback: the stored summary omits ``matter_space``, so read the
            # real room read-only (partner door when private rows are not allowed).
            v08_matter_space = _apply_barrier(
                _read_matter_space_projection_zero_write(
                    micro_day_root,
                    include_private=v08_inc_priv,
                    now_iso=now_iso,
                )
            )
        v08_traces_line = _format_v08_living_traces_micro(
            v08_traces_doc,
            include_private=v08_inc_priv,
            now_dt=now_dt,
            matter_space=v08_matter_space,
        )
        v03_enc_inc_priv = bool(
            private_artifacts_allowed
            and (not isinstance(v03_enc_doc, Mapping) or v03_enc_doc.get("perspective") != "partner")
            and (not http_alive or bool(cap))
        )
        v03_enc_line = _format_v03_epistemic_encounters_micro(
            v03_enc_doc, include_private=v03_enc_inc_priv
        )
        v08_echo_inc_priv = bool(
            private_artifacts_allowed
            and (not isinstance(v08_echoes_doc, Mapping) or v08_echoes_doc.get("perspective") != "partner")
            and (not http_alive or bool(cap))
        )
        v08_echoes_line = _format_v08_emergent_echoes_micro(
            v08_echoes_doc,
            incoming_text=clean_incoming,
            include_private=v08_echo_inc_priv,
            now_dt=now_dt,
            v04_ep=v04_ep_doc,
        )
        v08_body_inc_priv = bool(
            private_artifacts_allowed
            and (not isinstance(v08_body_doc, Mapping) or v08_body_doc.get("perspective") != "partner")
            and (not http_alive or bool(cap))
        )
        v08_body_line = _format_v08_composable_body_micro(
            v08_body_doc, include_private=v08_body_inc_priv
        )
        v09_inc_priv = bool(
            private_artifacts_allowed
            and (not http_alive or bool(cap))
        )
        v09_http_la = http_docs.get("v09_living_agency")
        v09_http_wc = http_docs.get("v09_world_commitments")
        v09_raw_doc: Optional[dict[str, Any]] = None
        if isinstance(v09_http_la, Mapping) or isinstance(v09_http_wc, Mapping):
            v09_raw_doc = {
                "blind_choices": list((v09_http_la or {}).get("blind_choices") or []),
                "growth_stances": list((v09_http_la or {}).get("growth_stances") or []),
                "repurposed_objects": list((v09_http_la or {}).get("repurposed_objects") or []),
                "custom_categories": list((v09_http_la or {}).get("custom_categories") or []),
                "categorized_items": list((v09_http_la or {}).get("categorized_items") or []),
                "commitments": list((v09_http_wc or {}).get("commitments") or []),
                "active_commitments": list((v09_http_wc or {}).get("active_commitments") or []),
                "venue_notices": list((v09_http_wc or {}).get("venue_notices") or []),
                "observed_venue_notices": list((v09_http_wc or {}).get("observed_venue_notices") or []),
            }
        elif micro_day_root is not None:
            v09_raw_doc = _read_v09_living_agency_and_world_zero_write(
                micro_day_root,
                include_private=v09_inc_priv,
                now_iso=now_iso,
            )
        v09_agency_world_doc = _apply_barrier(v09_raw_doc)
        v09_agency_world_line = _format_v09_living_agency_micro(
            v09_agency_world_doc,
            pronoun=pronouns,
            include_private=v09_inc_priv,
        )
        desk_line = _format_desk_and_containment(
            desk_http=desk_http_doc,
            stance_doc=stance_doc,
            v04_ep=v04_ep_doc,
            now_dt=now_dt,
        )
        sensory_line = _format_sensory_somatic(
            sensory_http=body_sensory_doc,
            somatic_http=body_somatic_doc,
            gw_http=body_gw_doc,
            care_http=body_care_doc,
            somatic_disk=somatic_disk_doc,
            sensory_disk=sensory_disk_doc,
            now_dt=now_dt,
            body_runtime=body_runtime_doc,
        )
        anchor_line = _format_v02_micro_anchors(
            wardrobe_doc=wardrobe_doc,
            reunion_doc=reunion_doc,
            habits_doc=habits_doc,
            plans_doc=plans_doc,
        )
        v05_season_cue = _format_v05_season_solar_cue(
            now_dt=now_dt,
            v04_nb=v04_nb_doc,
            town_doc=v05_town_doc,
            clean_incoming=clean_incoming,
        )
        v05_town_spark_line = _format_v05_living_town_and_spark(
            v05_town_doc,
            v05_spark_doc,
            home_craft_doc=v05_home_craft_doc,
            include_private=priv_with_cap,
            season_cue=v05_season_cue,
            now_dt=now_dt,
            skip_desire_texts=spark_skip_desires or None,
        )
        intimacy_line = _format_adult_intimacy_boundary(safe_intimacy)

        core_head: list[str] = []
        if hint_block:
            core_head.append(_truncate_section(hint_block, 450))
        core_head.extend([header, identity_line])
        if compact_situation_line:
            core_head.append(compact_situation_line)
        core_tail = [intimacy_line]

        raw_middle = [
            ("autonomy_line", autonomy_line),
            ("interactive_line", interactive_line),
            ("epistemic_micro_line", epistemic_micro_line),
            ("desires_line", desires_line),
            ("v03_line", v03_line),
            ("v04_line", v04_line),
            ("v04_project_line", v04_project_line),
            ("v02_deep_line", v02_deep_line),
            ("v02_skill_drift_ecology_line", v02_skill_drift_ecology_line),
            ("v02_world_lineage_line", v02_world_lineage_line),
            ("v05_town_spark_line", v05_town_spark_line),
            ("v08_traces_line", v08_traces_line),
            ("v08_echoes_line", v08_echoes_line),
            ("v03_enc_line", v03_enc_line),
            ("v08_body_line", v08_body_line),
            ("v09_agency_world_line", v09_agency_world_line),
            ("desk_line", desk_line),
            ("sensory_line", sensory_line),
            ("anchor_line", anchor_line),
        ]
        middle_sections = [
            (name, _truncate_section(text, 420)) for name, text in raw_middle if text
        ]
        ranked_middle = _rank_middle_sections_by_situation(
            middle_sections, clean_incoming=clean_incoming
        )

        kept_middle: list[str] = []
        kept_middle_names: list[str] = []
        per_section_chars: dict[str, int] = {}
        reserved_len = sum(len(x) for x in core_head + core_tail) + len(core_head) + len(core_tail) + 2
        total_raw_len = reserved_len + sum(len(line) + 1 for _name, line in ranked_middle)
        over_budget = total_raw_len > MAX_LIVED_CONTEXT_CHARS
        used_len = reserved_len
        skipped_middle: list[tuple[str, str]] = []
        for name, line in ranked_middle:
            sec_boost = _situation_boost(name, line, clean_incoming)
            if over_budget:
                sec_cap = 320 if sec_boost >= 2 else 220
                eff_line = _truncate_section(line, sec_cap)
            else:
                eff_line = line
            if used_len + len(eff_line) + 1 <= MAX_LIVED_CONTEXT_CHARS:
                kept_middle.append(eff_line)
                kept_middle_names.append(name)
                per_section_chars[name] = len(eff_line)
                used_len += len(eff_line) + 1
            else:
                remaining_chars = MAX_LIVED_CONTEXT_CHARS - used_len - 1
                if (name == "autonomy_line" or sec_boost >= 2) and remaining_chars >= 28:
                    clipped = _truncate_section(eff_line, remaining_chars)
                    kept_middle.append(clipped)
                    kept_middle_names.append(name)
                    per_section_chars[name] = len(clipped)
                    used_len += len(clipped) + 1
                else:
                    skipped_middle.append((name, eff_line))
                continue

        if skipped_middle:
            remaining_chars = MAX_LIVED_CONTEXT_CHARS - used_len - 1
            if remaining_chars >= 28:
                s_name, s_line = skipped_middle[0]
                clipped = _truncate_section(s_line, remaining_chars)
                kept_middle.append(clipped)
                kept_middle_names.append(s_name)
                per_section_chars[s_name] = len(clipped)
                used_len += len(clipped) + 1

        rendered = "\n".join(core_head + kept_middle + core_tail)
        if len(rendered) > MAX_LIVED_CONTEXT_CHARS:
            rendered = _truncate_section(rendered, MAX_LIVED_CONTEXT_CHARS)

        if _diag is not None:
            core_diag: dict[str, int] = {}
            if hint_block:
                core_diag["ephemeral_hint"] = len(core_head[0])
            core_diag["header"] = len(header)
            core_diag["identity_line"] = len(identity_line)
            if compact_situation_line:
                core_diag["compact_situation_line"] = len(compact_situation_line)
            core_diag["intimacy_line"] = len(intimacy_line)
            core_diag.update(per_section_chars)
            _diag["per_section_chars"] = core_diag
            _diag["kept_section_names"] = list(core_diag.keys()) + [
                n for n in kept_middle_names if n not in core_diag
            ]
            _diag["evicted_section_names"] = [
                name for name, _text in ranked_middle if name not in kept_middle_names
            ]
            _diag["compact_situation_line"] = compact_situation_line
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
    now_iso: Optional[str] = None,
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
        now_iso=now_iso,
    )


def measure_context_budget_breakdown(
    home_or_root: str | Path,
    cfg: Optional[Mapping[str, Any]] = None,
    incoming_text: str = "",
    *,
    base_url: Optional[str] = None,
    capability: Optional[str] = None,
    ephemeral_hint: str = "",
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """100% zero-write diagnostic of one lived-context render (Ch.47 §三/§七).

    Never consumes the one-shot ``ephemeral_turn_hint.json`` (``ephemeral_hint``
    defaults to the empty string, so the unlink path is skipped) and never writes
    any state.  Returns the rendered text plus its budget accounting.
    """

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

    diag: dict[str, Any] = {}
    rendered = render_lived_companion_context(
        root_path,
        eff_cfg,
        incoming_text,
        ephemeral_hint=str(ephemeral_hint or ""),
        now_iso=now_iso,
        _diag=diag,
    )
    total_chars = len(rendered)
    return {
        "rendered_text": rendered,
        "total_chars": total_chars,
        "estimated_tokens": (total_chars + 2) // 3,
        "max_chars": MAX_LIVED_CONTEXT_CHARS,
        "within_budget": total_chars <= MAX_LIVED_CONTEXT_CHARS,
        "calm_silent": total_chars == 0,
        "kept_section_names": list(diag.get("kept_section_names") or []),
        "evicted_section_names": list(diag.get("evicted_section_names") or []),
        "per_section_chars": dict(diag.get("per_section_chars") or {}),
    }


BASE_TOOLS: tuple[str, ...] = ("look_around", "touch_or_use", "jot_note", "step_to")

SCENE_EXTRA_TOOLS: dict[str, tuple[str, ...]] = {
    "indoor_desk": ("read_material", "edit_work_draft", "compare_versions", "craft_assemble"),
    "outdoor_street": ("store_order", "talk_shopkeeper", "borrow_or_return", "take_light_sketch"),
    "in_room_copresence": ("sync_shared_page", "offer_tea_or_cushion", "propose_tentative_plan", "play_ten_min_game"),
    "private_intimate": ("confirm_boundary_or_pace", "stop_immediately"),
    "workbench_collab": ("inspect_workspace_file", "apply_minimal_patch", "run_local_check"),
}


def visible_tools_for_scene(scene: str) -> tuple[str, ...]:
    """第 62 / 63 章：按当前场景收窄可见工具，严格不超过 8 个。"""
    if scene not in SCENE_EXTRA_TOOLS:
        raise ValueError(f"UNKNOWN_SCENE:{scene}")
    if scene == "private_intimate":
        return SCENE_EXTRA_TOOLS["private_intimate"]
    if scene == "workbench_collab":
        return ("look_around",) + SCENE_EXTRA_TOOLS["workbench_collab"]
    return BASE_TOOLS + SCENE_EXTRA_TOOLS[scene]


def build_cache_friendly_prompt_blocks(
    *,
    soul_core: str,
    base_tools_schema: str,
    scene_tools_schema: str,
    dynamic_three_line_situation: str,
) -> list[tuple[str, str]]:
    """第 94 章：固定前缀最前，每轮动态处境严格置尾，保障提示词前缀缓存命中率。"""
    return [
        ("prefix_static_soul", soul_core),
        ("prefix_static_base_tools", base_tools_schema),
        ("mid_scene_tools", scene_tools_schema),
        ("tail_dynamic_situation", dynamic_three_line_situation),
    ]


def estimate_monthly_rmb_cost(
    *,
    autonomous_calls_per_day: int = 12,
    chat_turns_per_day: int = 30,
    cache_hit_ratio: float = 0.75,
) -> float:
    """第 94 章：按平价口径核算 30 天人民币缓存成本。"""
    in_price_miss = 2.0 / 1_000_000
    in_price_hit = 0.5 / 1_000_000
    out_price = 8.0 / 1_000_000
    eff_in_price = cache_hit_ratio * in_price_hit + (1.0 - cache_hit_ratio) * in_price_miss
    daily_auto_in = autonomous_calls_per_day * 1100
    daily_auto_out = autonomous_calls_per_day * 160
    daily_chat_in = chat_turns_per_day * 1800
    daily_chat_out = chat_turns_per_day * 180
    daily_rmb = (daily_auto_in + daily_chat_in) * eff_in_price + (daily_auto_out + daily_chat_out) * out_price
    return round(daily_rmb * 30, 2)


from dataclasses import dataclass, field


def classify_channel_mode(session_key_or_mode: Optional[str]) -> str:
    """第 83 / 85 章：根据会话键或前端渠道标识区分远程消息 vs 同屋/工作台在场。"""
    key = str(session_key_or_mode or "").strip().lower()
    if any(tok in key for tok in ("weixin", "qqbot", "telegram", "wechat", "remote")):
        return "REMOTE_PHONE_MESSAGE"
    return "IN_ROOM_TOGETHER"


@dataclass
class RuntimeSessionBridge:
    """第 80 / 82 / 85 章：工作台切换书签暂存、目录防火墙、超时软着陆与拒绝立场锁隔离。"""

    foreground_locked: bool = False
    active_book_title: str = "《海堤风物记》"
    active_book_page: int = 4
    parked_bookmark: Optional[tuple[str, int]] = None
    workbench_mode: bool = False
    deferred_hours_active: bool = False
    refused_firm_topics: set[str] = field(default_factory=set)

    def handle_model_timeout(self) -> dict[str, Any]:
        """单次请求超时软着陆：释放占用锁、零盲目重试、原地看窗外 5 分钟。"""
        self.foreground_locked = False
        return {
            "status": "IDLE_GLANCE_WINDOW",
            "duration_minutes": 5,
            "llm_retries": 0,
            "foreground_locked": self.foreground_locked,
        }

    def enter_workbench(self) -> dict[str, Any]:
        """切入并肩工作台：夹好书签、豁免闲聊延迟，但绝不解除日常拒绝锁（第 85 章接缝二）。"""
        self.parked_bookmark = (self.active_book_title, self.active_book_page)
        self.workbench_mode = True
        return {
            "parked_bookmark": self.parked_bookmark,
            "bypass_idle_deferral": True,
            "private_drafts_exposed": False,
        }

    def exit_workbench(self, clean_summary_cn: str) -> dict[str, Any]:
        if "/" in clean_summary_cn or "\\" in clean_summary_cn or "SECRET" in clean_summary_cn.upper():
            raise ValueError("SENSITIVE_WORKSPACE_PATH_OR_SECRET_FORBIDDEN")
        self.workbench_mode = False
        resumed = self.parked_bookmark
        self.parked_bookmark = None
        return {"resumed_bookmark": resumed, "logged_summary_cn": clean_summary_cn}

    def check_request_allowed(
        self,
        topic_id: str,
        channel: str,
        requires_in_room: bool = False,
    ) -> dict[str, Any]:
        if topic_id in self.refused_firm_topics:
            return {"allowed": False, "reason": "REFUSED_FIRM_NON_CAPITULATION"}
        if classify_channel_mode(channel) == "REMOTE_PHONE_MESSAGE" and requires_in_room:
            return {"allowed": False, "reason": "REQUIRES_IN_ROOM_PRESENCE"}
        if self.deferred_hours_active and not self.workbench_mode:
            return {"allowed": False, "reason": "DEFERRED_IN_PHONE_BOX"}
        return {"allowed": True, "reason": "OK"}


__all__ = [
    "BASE_TOOLS",
    "MAX_LIVED_CONTEXT_CHARS",
    "RuntimeSessionBridge",
    "SCENE_EXTRA_TOOLS",
    "TOTAL_TIMEOUT_BUDGET_S",
    "build_cache_friendly_prompt_blocks",
    "build_lived_turn_context",
    "classify_channel_mode",
    "consume_ephemeral_turn_hint",
    "estimate_monthly_rmb_cost",
    "measure_context_budget_breakdown",
    "render_lived_companion_context",
    "strip_injected_turn_envelope",
    "visible_tools_for_scene",
]
