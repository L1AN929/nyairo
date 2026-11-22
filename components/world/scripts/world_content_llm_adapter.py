#!/usr/bin/env python3
"""External LLM Adapter for Scoped World Content Generation (NyAiro v0.9).

Bridges ``components/world/scripts/world_content_generator.py`` with the
already-configured external OpenAI-compatible LLM provider while preserving
strict zero-write config inspection, secret redaction, WSL bridge IP
auto-healing, and real token accounting in ``life_request_budget.py``.

Config resolution priority (100% zero-write, zero network I/O):
1. Environment variables: ``NYA02_MODEL_BASE_URL`` + ``NYA02_MODEL_NAME`` +
   ``NYA02_MODEL_API_KEY`` (plus optional ``NYA02_MODEL_PROVIDER``).
2. ``<NYAIRO_RC1_PROFILE or /root/.nyairo-rc1>/config.yaml``
   (``model.base_url``, ``model.default``, ``model.api_key``, ``model.provider``,
   with optional ``.env`` / ``custom_providers`` expansion).
3. ``<HERMES_HOME or /root/.hermes>`` (``config.yaml`` + ``.env``
   ``OPENCODE_GO_BASE_URL`` / ``OPENCODE_GO_API_KEY`` or provider block).

Stale WSL bridge IP auto-resolver:
If ``base_url`` targets a private ``172.16.0.0/12`` ``.1`` gateway host (for
example ``http://172.23.128.1:3425/v1``) and ``ip route show default`` reports
a different live WSL gateway IP (for example ``172.22.128.1``), the effective
URL is rewritten in memory with ``stale_bridge_detected=True`` and
``live_gateway_host=...`` without touching any file on disk.
"""
from __future__ import annotations

import copy
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import unicodedata
import urllib.parse
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Optional

_WORLD_SCRIPTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _WORLD_SCRIPTS_DIR.parents[2]
_LIFE_SCRIPTS_DIR = _REPO_ROOT / "components" / "life" / "scripts"
for _p in (str(_REPO_ROOT), str(_WORLD_SCRIPTS_DIR), str(_LIFE_SCRIPTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from components.life.scripts import agency_model_client as _amc
except ImportError:  # pragma: no cover - flat script import fallback
    import agency_model_client as _amc  # type: ignore[no-redef]

try:
    from components.life.scripts import life_request_budget as _lrb
except ImportError:  # pragma: no cover - flat script import fallback
    import life_request_budget as _lrb  # type: ignore[no-redef]

try:
    from components.life.scripts import profile_model_transport as _pmt
except ImportError:  # pragma: no cover - flat script import fallback
    import profile_model_transport as _pmt  # type: ignore[no-redef]

try:
    from components.world.scripts import world_content_catalog as _catalog
except ImportError:  # pragma: no cover - flat script import fallback
    import world_content_catalog as _catalog  # type: ignore[no-redef]

try:
    from components.world.scripts import world_open_storefront as _storefront
except ImportError:  # pragma: no cover - flat script import fallback
    import world_open_storefront as _storefront  # type: ignore[no-redef]


DEFAULT_RC1_PROFILE_DIR = "/root/.nyairo-rc1"
DEFAULT_HERMES_HOME_DIR = "/root/.hermes"
DEFAULT_GENERATION_MODE = "offline"
DEFAULT_TIMEOUT_S = 35.0
DEFAULT_MAX_TOKENS = 1600
DEFAULT_UTC_DAY_MAX = 96
PURPOSE_WORLD_CONTENT = "world_content_generation"

UNCONFIGURED_DETAIL_CN = "尚未配置外接大模型，请先前往「设置 -> 模型与通道」配置"

SAFE_STATUS_KEYS: tuple[str, ...] = (
    "configured",
    "provider",
    "model_name",
    "endpoint_host",
    "api_key_present",
    "stale_bridge_detected",
    "default_generation_mode",
    "source",
)

_WSL_BRIDGE_NET = ipaddress.IPv4Network("172.16.0.0/12")
_ENV_REF_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")
_DEFAULT_GW_RE = re.compile(r"\bdefault\s+via\s+(\d{1,3}(?:\.\d{1,3}){3})\b")
_CODE_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)\s*```", flags=re.DOTALL)

_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-~+/=]{4,}")
_KEY_TOKEN_RE = re.compile(r"\b(?:sk|pk|rk|wb|oc|opencode)[-_][A-Za-z0-9_\-]{8,}")
_SECRET_KV_RE = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|secret|password|authorization)\s*[:=]\s*[\"']?)([^\s,;\"'\}\]]+)"
)
_URL_USERINFO_RE = re.compile(r"(https?://)([^/\s:@]+):([^/\s@]+)@")

#: Optional normalization from legacy conceptual template names to canonical catalog templates.
_ITEM_TEMPLATE_ALIASES: dict[str, str] = {
    "consumable_drink": "drink_warm",
    "consumable_food": "snack_portable",
    "usable_tool": "desk_vessel",
    "reusable_container": "desk_vessel",
}


class WorldContentLLMError(RuntimeError):
    """Base error for external LLM world content generation (always secret-scrubbed)."""

    def __init__(
        self,
        reason_code: str,
        detail: str = "",
        *,
        status_code: int = 502,
        meta: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self.reason_code = str(reason_code or "LLM_GENERATION_FAILED")
        self.status_code = int(status_code)
        self.detail = redact_secrets(str(detail or self.reason_code))[:240]
        self.meta: dict[str, Any] = _scrub_meta_dict(meta or {})
        super().__init__(self.detail)


class WorldContentLLMUnconfiguredError(WorldContentLLMError):
    """Raised when generation_mode='llm' is requested without a usable model config."""

    def __init__(self, detail: str = UNCONFIGURED_DETAIL_CN) -> None:
        super().__init__(
            "LLM_UNCONFIGURED",
            detail or UNCONFIGURED_DETAIL_CN,
            status_code=503,
        )


class WorldContentLLMGenerationError(WorldContentLLMError):
    """Raised when an external LLM call fails, times out, or emits invalid JSON."""

    def __init__(
        self,
        detail: str = "外接模型生成失败",
        *,
        reason_code: str = "LLM_GENERATION_FAILED",
        meta: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(
            reason_code or "LLM_GENERATION_FAILED",
            detail or "外接模型生成失败",
            status_code=502,
            meta=meta,
        )


def redact_secrets(text: str, extra_secrets: Sequence[str] = ()) -> str:
    """Scrub API keys, bearer tokens, URL credentials, and explicit secrets from text."""
    out = str(text or "")
    for secret in sorted(
        {str(s) for s in extra_secrets if isinstance(s, str) and len(s) >= 4},
        key=len,
        reverse=True,
    ):
        if secret in out:
            out = out.replace(secret, "[REDACTED]")
    out = _URL_USERINFO_RE.sub(r"\1[REDACTED]:[REDACTED]@", out)
    out = _BEARER_RE.sub("Bearer [REDACTED]", out)
    out = _KEY_TOKEN_RE.sub("[REDACTED]", out)
    out = _SECRET_KV_RE.sub(r"\1[REDACTED]", out)
    return out


def _scrub_meta_dict(meta: Mapping[str, Any]) -> dict[str, Any]:
    banned = ("api_key", "secret", "password", "authorization", "cookie", "bearer")
    out: dict[str, Any] = {}
    for k, v in meta.items():
        lk = str(k).strip().lower()
        if any(b in lk for b in banned) and lk != "api_key_present":
            continue
        if "token" in lk and not lk.endswith("_tokens") and lk not in (
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "input_tokens",
            "output_tokens",
            "reasoning_tokens",
        ):
            continue
        if isinstance(v, Mapping):
            out[str(k)] = _scrub_meta_dict(v)
        elif isinstance(v, str):
            out[str(k)] = redact_secrets(v)[:200]
        elif isinstance(v, (int, float, bool)) or v is None:
            out[str(k)] = v
    return out


def _clean_scalar(raw: Any) -> str:
    if raw is None or isinstance(raw, (bool, dict, list, tuple)):
        return ""
    text = str(raw).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        text = text[1:-1].strip()
    return text


def _read_text_zero_write(path: Path) -> Optional[str]:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _read_dotenv_zero_write(path: Path) -> dict[str, str]:
    raw = _read_text_zero_write(path)
    if not raw:
        return {}
    out: dict[str, str] = {}
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, val = stripped.partition("=")
        k = key.strip()
        if k.startswith("export "):
            k = k[7:].strip()
        v = _clean_scalar(val)
        if k and v:
            out[k] = v
    return out


def _fallback_parse_yaml_subset(text: str) -> dict[str, Any]:
    """Minimal indentation-aware parser for model/providers YAML when PyYAML is absent."""
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        line = raw_line.strip()
        if ":" not in line or line.startswith("-"):
            continue
        key_part, _, val_part = line.partition(":")
        key = _clean_scalar(key_part)
        if not key:
            continue
        val_raw = val_part.strip()
        # Strip unquoted inline comment
        if val_raw and val_raw[0] not in ("'", '"') and " #" in val_raw:
            val_raw = val_raw.split(" #", 1)[0].strip()
        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if not val_raw:
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = _clean_scalar(val_raw)
    return root


def _read_yaml_zero_write(path: Path) -> dict[str, Any]:
    text = _read_text_zero_write(path)
    if not text:
        return {}
    try:
        import yaml  # type: ignore[import-untyped]

        parsed = yaml.safe_load(text)
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    except ImportError:
        return _fallback_parse_yaml_subset(text)
    except Exception:
        return _fallback_parse_yaml_subset(text)


def _expand_ref(
    raw: Any,
    env_map: Mapping[str, str],
    dotenv: Mapping[str, str],
) -> str:
    val = _clean_scalar(raw)
    if not val:
        return ""
    match = _ENV_REF_RE.fullmatch(val)
    if match:
        var_name = match.group(1)
        return _clean_scalar(env_map.get(var_name)) or _clean_scalar(dotenv.get(var_name))
    return val


def _sanitize_endpoint_host(url: str) -> str:
    """Return ONLY safe ``host`` or ``host:port`` (never scheme, path, query, or userinfo)."""
    text = str(url or "").strip()
    if not text:
        return ""
    try:
        parts = urllib.parse.urlsplit(text)
    except ValueError:
        return ""
    host = (parts.hostname or "").strip()
    if not host:
        return ""
    try:
        port = parts.port
    except ValueError:
        return host
    scheme = (parts.scheme or "").lower()
    if port is not None and not (
        (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    ):
        return f"{host}:{port}"
    return host


def _is_wsl_bridge_host(host: str) -> bool:
    try:
        ip = ipaddress.IPv4Address(str(host or "").strip())
    except ValueError:
        return False
    return ip in _WSL_BRIDGE_NET and ip.packed[3] == 1


def _detect_gateway_from_proc_net_route() -> Optional[str]:
    proc_route = Path("/proc/net/route")
    raw = _read_text_zero_write(proc_route)
    if not raw:
        return None
    for line in raw.splitlines()[1:]:
        cols = line.split()
        if len(cols) < 3:
            continue
        if cols[1] != "00000000":
            continue
        gw_hex = cols[2]
        if len(gw_hex) != 8:
            continue
        try:
            gw_int = int(gw_hex, 16)
            gw_ip = socket.inet_ntoa(gw_int.to_bytes(4, byteorder="little"))
            ipaddress.IPv4Address(gw_ip)
            return gw_ip
        except (ValueError, OSError):
            continue
    return None


def detect_wsl_default_gateway_ip() -> Optional[str]:
    """Read live WSL default gateway IP (read-only, zero disk writes, zero network I/O)."""
    if os.name != "posix":
        return None
    try:
        res = subprocess.run(
            ["ip", "route", "show", "default"],
            capture_output=True,
            text=True,
            timeout=1.5,
            check=False,
        )
        if res.returncode == 0 and res.stdout:
            match = _DEFAULT_GW_RE.search(res.stdout)
            if match:
                cand = match.group(1).strip()
                ipaddress.IPv4Address(cand)
                return cand
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return _detect_gateway_from_proc_net_route()


def _resolve_wsl_bridge_url(
    base_url: str,
    *,
    gateway_ip_fn: Optional[Callable[[], Optional[str]]] = None,
) -> tuple[str, bool, Optional[str]]:
    """Auto-heal stale WSL 172.16.0.0/12 ``.1`` bridge IP in memory without disk writes."""
    cleaned = str(base_url or "").strip().rstrip("/")
    if not cleaned:
        return "", False, None
    try:
        parts = urllib.parse.urlsplit(cleaned)
    except ValueError:
        return cleaned, False, None
    host = (parts.hostname or "").strip()
    if not host or not _is_wsl_bridge_host(host):
        return cleaned, False, None

    resolver = gateway_ip_fn if callable(gateway_ip_fn) else detect_wsl_default_gateway_ip
    try:
        live_gw = resolver()
    except Exception:
        live_gw = None
    if not live_gw:
        return cleaned, False, None

    live_gw = str(live_gw).strip()
    try:
        ipaddress.IPv4Address(live_gw)
    except ValueError:
        return cleaned, False, None

    if live_gw == host:
        return cleaned, False, live_gw

    # Swap hostname to live_gw while keeping scheme, port, path, and optional userinfo.
    userinfo = ""
    if parts.username is not None:
        userinfo = parts.username
        if parts.password is not None:
            userinfo = f"{userinfo}:{parts.password}"
        userinfo = f"{userinfo}@"
    try:
        port = parts.port
    except ValueError:
        port = None
    new_netloc = f"{userinfo}{live_gw}:{port}" if port is not None else f"{userinfo}{live_gw}"
    rewritten = urllib.parse.urlunsplit(
        (parts.scheme or "http", new_netloc, parts.path, parts.query, parts.fragment)
    ).rstrip("/")
    return rewritten, True, live_gw


def _extract_profile_config(
    profile_dir: Path,
    env_map: Mapping[str, str],
    *,
    allow_opencode_go: bool = False,
) -> dict[str, Any]:
    cfg_doc = _read_yaml_zero_write(profile_dir / "config.yaml")
    dotenv = _read_dotenv_zero_write(profile_dir / ".env")

    raw_model = cfg_doc.get("model")
    model_map = raw_model if isinstance(raw_model, Mapping) else {}

    provider = (
        _expand_ref(model_map.get("provider"), env_map, dotenv)
        or _expand_ref(cfg_doc.get("provider"), env_map, dotenv)
    )
    model_name = (
        _expand_ref(model_map.get("default"), env_map, dotenv)
        or _expand_ref(model_map.get("model"), env_map, dotenv)
        or (_expand_ref(raw_model, env_map, dotenv) if isinstance(raw_model, str) else "")
    )
    base_url = _expand_ref(model_map.get("base_url"), env_map, dotenv)
    api_key = _expand_ref(model_map.get("api_key"), env_map, dotenv)

    # Check custom_providers.<provider> and providers.<provider>
    for container_key in ("custom_providers", "providers"):
        container = cfg_doc.get(container_key)
        if not isinstance(container, Mapping):
            continue
        prov_block = container.get(provider) if provider else None
        if not isinstance(prov_block, Mapping) and len(container) == 1:
            sole_name, sole_val = next(iter(container.items()))
            if isinstance(sole_val, Mapping):
                prov_block = sole_val
                if not provider:
                    provider = _clean_scalar(sole_name)
        if isinstance(prov_block, Mapping):
            base_url = base_url or _expand_ref(prov_block.get("base_url"), env_map, dotenv)
            model_name = model_name or _expand_ref(prov_block.get("model"), env_map, dotenv)
            api_key = api_key or _expand_ref(prov_block.get("api_key"), env_map, dotenv)
            key_env = _clean_scalar(prov_block.get("key_env"))
            if not api_key and key_env:
                api_key = _clean_scalar(env_map.get(key_env)) or _clean_scalar(dotenv.get(key_env))

    # Hermes OPENCODE_GO_* fallback
    if allow_opencode_go:
        oc_base = _clean_scalar(env_map.get("OPENCODE_GO_BASE_URL")) or _clean_scalar(
            dotenv.get("OPENCODE_GO_BASE_URL")
        )
        oc_key = _clean_scalar(env_map.get("OPENCODE_GO_API_KEY")) or _clean_scalar(
            dotenv.get("OPENCODE_GO_API_KEY")
        )
        if oc_base and oc_key and not (base_url and api_key):
            base_url = base_url or oc_base
            api_key = api_key or oc_key
            provider = provider or "opencode-go"
            model_name = model_name or "deepseek-v4-flash"

    return {
        "provider": provider or ("opencode-go" if allow_opencode_go else "custom"),
        "model_name": model_name,
        "base_url": base_url,
        "api_key": api_key,
    }


def _finalize_config(
    *,
    source: str,
    provider: str,
    model_name: str,
    base_url: str,
    api_key: str,
    gateway_ip_fn: Optional[Callable[[], Optional[str]]] = None,
) -> dict[str, Any]:
    effective_url, stale_bridge, live_gw = _resolve_wsl_bridge_url(
        base_url, gateway_ip_fn=gateway_ip_fn
    )
    configured = bool(effective_url and model_name and api_key)
    return {
        "configured": configured,
        "provider": str(provider or ""),
        "model_name": str(model_name or ""),
        "base_url": effective_url,
        "configured_base_url": str(base_url or "").strip().rstrip("/"),
        "endpoint_host": _sanitize_endpoint_host(effective_url),
        "api_key": str(api_key or ""),
        "api_key_present": bool(api_key),
        "stale_bridge_detected": bool(stale_bridge),
        "live_gateway_host": live_gw,
        "default_generation_mode": DEFAULT_GENERATION_MODE,
        "source": str(source or "none"),
    }


def resolve_external_llm_config_zero_write(
    env: Optional[Mapping[str, str]] = None,
    *,
    gateway_ip_fn: Optional[Callable[[], Optional[str]]] = None,
) -> dict[str, Any]:
    """Resolve external OpenAI-compatible LLM config with strict zero writes & zero network I/O.

    Priority:
    1. ``NYA02_MODEL_BASE_URL`` + ``NYA02_MODEL_NAME`` + ``NYA02_MODEL_API_KEY`` (``source="env"``)
    2. ``<NYAIRO_RC1_PROFILE or /root/.nyairo-rc1>/config.yaml`` (``source="rc1_profile"``)
    3. ``<HERMES_HOME or /root/.hermes>`` (``config.yaml`` + ``.env`` ``OPENCODE_GO_*``) (``source="hermes"``)
    4. Unconfigured fallback (``source="none"``)
    """
    env_map: dict[str, str] = dict(os.environ if env is None else env)

    # Priority 1: explicit NYA02_MODEL_* environment variables
    env_base = _clean_scalar(env_map.get("NYA02_MODEL_BASE_URL"))
    env_model = _clean_scalar(env_map.get("NYA02_MODEL_NAME"))
    env_key = _clean_scalar(env_map.get("NYA02_MODEL_API_KEY"))
    env_prov = _clean_scalar(env_map.get("NYA02_MODEL_PROVIDER")) or "custom"
    if env_base and env_model and env_key:
        return _finalize_config(
            source="env",
            provider=env_prov,
            model_name=env_model,
            base_url=env_base,
            api_key=env_key,
            gateway_ip_fn=gateway_ip_fn,
        )

    # Priority 2: NYAIRO_RC1_PROFILE (default /root/.nyairo-rc1)
    rc1_dir = Path(_clean_scalar(env_map.get("NYAIRO_RC1_PROFILE")) or DEFAULT_RC1_PROFILE_DIR)
    rc1_cfg = _extract_profile_config(rc1_dir, env_map, allow_opencode_go=False)
    if rc1_cfg["base_url"] and rc1_cfg["model_name"] and rc1_cfg["api_key"]:
        return _finalize_config(
            source="rc1_profile",
            provider=rc1_cfg["provider"],
            model_name=rc1_cfg["model_name"],
            base_url=rc1_cfg["base_url"],
            api_key=rc1_cfg["api_key"],
            gateway_ip_fn=gateway_ip_fn,
        )

    # Priority 3: HERMES_HOME (default /root/.hermes)
    hermes_dir = Path(_clean_scalar(env_map.get("HERMES_HOME")) or DEFAULT_HERMES_HOME_DIR)
    hermes_cfg = _extract_profile_config(hermes_dir, env_map, allow_opencode_go=True)
    if hermes_cfg["base_url"] and hermes_cfg["model_name"] and hermes_cfg["api_key"]:
        return _finalize_config(
            source="hermes",
            provider=hermes_cfg["provider"],
            model_name=hermes_cfg["model_name"],
            base_url=hermes_cfg["base_url"],
            api_key=hermes_cfg["api_key"],
            gateway_ip_fn=gateway_ip_fn,
        )

    return _finalize_config(
        source="none",
        provider="",
        model_name="",
        base_url="",
        api_key="",
        gateway_ip_fn=gateway_ip_fn,
    )


def read_external_llm_status_zero_write(
    env: Optional[Mapping[str, str]] = None,
    *,
    gateway_ip_fn: Optional[Callable[[], Optional[str]]] = None,
) -> dict[str, Any]:
    """Return ONLY safe non-secret metadata for GET endpoints (0 disk writes, 0 network I/O)."""
    cfg = resolve_external_llm_config_zero_write(env, gateway_ip_fn=gateway_ip_fn)
    return {
        "configured": bool(cfg.get("configured")),
        "provider": str(cfg.get("provider") or ""),
        "model_name": str(cfg.get("model_name") or ""),
        "endpoint_host": str(cfg.get("endpoint_host") or ""),
        "api_key_present": bool(cfg.get("api_key_present")),
        "stale_bridge_detected": bool(cfg.get("stale_bridge_detected")),
        "default_generation_mode": DEFAULT_GENERATION_MODE,
        "source": str(cfg.get("source") or "none"),
    }


def _strip_emojis_and_ctrl(text: str) -> str:
    out: list[str] = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat == "So" or cat.startswith("C"):
            continue
        out.append(ch)
    return " ".join("".join(out).split()).strip()


def _coerce_int(raw: Any, *, lo: int, hi: int, default: Optional[int] = None) -> Optional[int]:
    if isinstance(raw, bool) or raw is None:
        return default
    if isinstance(raw, int):
        return max(lo, min(hi, raw))
    if isinstance(raw, float) and raw.is_integer():
        return max(lo, min(hi, int(raw)))
    if isinstance(raw, str):
        s = raw.strip()
        if re.fullmatch(r"-?\d+", s):
            return max(lo, min(hi, int(s)))
    return default


def extract_proposal_json(raw_text: str) -> dict[str, Any]:
    """Strip markdown code fences if present and parse a strict JSON object."""
    text = str(raw_text or "").strip()
    if not text:
        raise WorldContentLLMGenerationError("外接模型返回了空内容")

    candidates: list[str] = []
    for match in _CODE_FENCE_RE.finditer(text):
        inner = match.group(1).strip()
        if inner:
            candidates.append(inner)
    candidates.append(text)

    for cand in candidates:
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict):
                return obj
        except ValueError:
            pass
        start = cand.find("{")
        end = cand.rfind("}")
        if start >= 0 and end > start:
            try:
                obj = json.loads(cand[start : end + 1])
                if isinstance(obj, dict):
                    return obj
            except ValueError:
                pass

    raise WorldContentLLMGenerationError("外接模型输出未能解析为合法 JSON 对象")


def _normalize_item_candidate(
    raw_item: Any,
    *,
    store_id: str,
    season: str,
) -> Any:
    if not isinstance(raw_item, Mapping):
        return raw_item

    # Never mask or strip forbidden security keys so catalog validation rejects them loudly.
    if _catalog._find_forbidden_key(raw_item) is not None:
        return dict(raw_item)

    item = dict(raw_item)
    raw_tpl = str(item.get("template_id") or "").strip()
    tpl_id = _ITEM_TEMPLATE_ALIASES.get(raw_tpl, raw_tpl)
    item["template_id"] = tpl_id

    # Normalize harmless naming aliases only when canonical keys are missing.
    if "name_cn" not in item and "name" not in item:
        for alt in ("title_cn", "title"):
            if alt in item:
                item["name_cn"] = item.pop(alt)
                break
    if "description_cn" not in item and "description" not in item:
        for alt in ("desc_cn", "summary_cn"):
            if alt in item:
                item["description_cn"] = item.pop(alt)
                break
    if "price" not in item and "custom_price" not in item and "price_jpy" in item:
        item["price"] = item.pop("price_jpy")
    if "stock_initial" not in item and "initial_stock" not in item and "stock" not in item:
        if "quantity" in item:
            item["stock_initial"] = item.pop("quantity")

    if not item.get("store_id"):
        item["store_id"] = store_id
    if not item.get("season") and season:
        item["season"] = season

    template = _catalog.ITEM_CAPABILITY_TEMPLATES.get(tpl_id)
    if template is None:
        return item

    # Sanitize text lengths and strip emojis/newlines so valid creative output passes cleanly.
    for tk, limit in (
        ("name_cn", 40),
        ("name", 40),
        ("description_cn", 120),
        ("description", 120),
        ("aroma_cn", 60),
        ("taste_note_cn", 80),
        ("material_cn", 32),
        ("argument_thesis_cn", 160),
        ("sample_excerpt_cn", 240),
        ("season", 24),
    ):
        if tk in item and isinstance(item[tk], str):
            cleaned = _strip_emojis_and_ctrl(item[tk])[:limit]
            item[tk] = cleaned

    # Keep template category aligned if omitted or mismatched casing.
    if "category" in item:
        item["category"] = str(template["category"])

    # Normalize price_tier and numeric fields.
    raw_tier = str(item.get("price_tier") or "standard").strip().lower()
    if raw_tier not in _storefront.PRICE_TIERS:
        raw_tier = "standard"
    item["price_tier"] = raw_tier

    for pk in ("price", "custom_price"):
        if pk in item and item[pk] is not None:
            val = _coerce_int(item[pk], lo=10, hi=50_000, default=None)
            if val is not None:
                item[pk] = val
    for sk in ("stock_initial", "initial_stock", "stock"):
        if sk in item and item[sk] is not None:
            val = _coerce_int(item[sk], lo=1, hi=999, default=3)
            if val is not None:
                item[sk] = val

    if tpl_id in ("drink_warm", "meal_light"):
        if "portions" in item:
            item["portions"] = _coerce_int(item["portions"], lo=1, hi=4, default=1)
        if "shelf_life_hours" in item:
            item["shelf_life_hours"] = _coerce_int(item["shelf_life_hours"], lo=1, hi=168, default=12)
    elif tpl_id == "snack_portable":
        if "portions" in item:
            item["portions"] = _coerce_int(item["portions"], lo=1, hi=6, default=3)
        if "shelf_life_hours" in item:
            item["shelf_life_hours"] = _coerce_int(item["shelf_life_hours"], lo=1, hi=720, default=72)
        if isinstance(item.get("reusable_container_spec"), Mapping):
            rcs = dict(item["reusable_container_spec"])
            if "label_cn" in rcs and isinstance(rcs["label_cn"], str):
                rcs["label_cn"] = _strip_emojis_and_ctrl(rcs["label_cn"])[:48] or "铁皮收纳盒"
            if "material_cn" in rcs and isinstance(rcs["material_cn"], str):
                rcs["material_cn"] = _strip_emojis_and_ctrl(rcs["material_cn"])[:32]
            if "capacity_slots" in rcs:
                rcs["capacity_slots"] = _coerce_int(rcs["capacity_slots"], lo=1, hi=12, default=4)
            allowed_rcs = {
                "container_kind",
                "label_cn",
                "capacity_slots",
                "drinkware",
                "supported_capabilities",
                "material_cn",
            }
            item["reusable_container_spec"] = {k: v for k, v in rcs.items() if k in allowed_rcs}
    elif tpl_id == "desk_vessel":
        if "capacity_slots" in item:
            item["capacity_slots"] = _coerce_int(item["capacity_slots"], lo=1, hi=6, default=3)
    elif tpl_id == "decor_fixture":
        if "surfaces" in item and isinstance(item["surfaces"], list):
            item["surfaces"] = [
                s for s in item["surfaces"] if isinstance(s, str) and s in _catalog.ALLOWED_FIXTURE_SURFACES
            ]
        if "light_band" in item and isinstance(item["light_band"], str):
            lb = item["light_band"].strip().lower()
            item["light_band"] = lb if lb in ("dim", "moderate", "bright") else None
    elif tpl_id == "reading_booklet":
        if "pages_count" in item:
            item["pages_count"] = _coerce_int(item["pages_count"], lo=1, hi=32, default=16)
        if "topic_tags" in item and isinstance(item["topic_tags"], list):
            item["topic_tags"] = [
                _strip_emojis_and_ctrl(str(t))[:24]
                for t in item["topic_tags"][:6]
                if _strip_emojis_and_ctrl(str(t))
            ]

    # Strip benign non-schema chatter keys only after confirming no forbidden key existed.
    allowed_top = _catalog._COMMON_ITEM_KEYS | frozenset(template.get("optional_fields") or ())
    return {k: v for k, v in item.items() if k in allowed_top}


def _normalize_event_candidate(
    raw_event: Any,
    *,
    store_id: str,
    season: str,
    first_valid_item_id: Optional[str],
) -> Any:
    if not isinstance(raw_event, Mapping):
        return raw_event
    if _catalog._find_forbidden_key(raw_event) is not None:
        return dict(raw_event)

    ev = dict(raw_event)
    tpl_id = str(ev.get("template_id") or "").strip()
    if tpl_id not in _catalog.EVENT_TEMPLATES:
        return ev

    if not ev.get("venue_id") and not ev.get("store_id"):
        ev["venue_id"] = store_id
    if not ev.get("season") and season:
        ev["season"] = season

    for tk, limit in (
        ("title_cn", 60),
        ("description_cn", 160),
        ("reason_notice_cn", 120),
        ("season", 24),
    ):
        if tk in ev and isinstance(ev[tk], str):
            ev[tk] = _strip_emojis_and_ctrl(ev[tk])[:limit]

    if tpl_id == "tea_tasting" and first_valid_item_id:
        feat = str(ev.get("featured_catalog_item_id") or "").strip()
        if not feat or not feat.startswith("shop_item:"):
            ev["featured_catalog_item_id"] = first_valid_item_id
        if "sample_stock" in ev:
            ev["sample_stock"] = _coerce_int(ev["sample_stock"], lo=1, hi=50, default=6)
    elif tpl_id == "porch_market" and first_valid_item_id:
        fids = ev.get("featured_item_ids")
        if not isinstance(fids, list) or not fids or not all(
            isinstance(x, str) and x.startswith("shop_item:") for x in fids
        ):
            ev["featured_item_ids"] = [first_valid_item_id]
    elif tpl_id == "book_swap" and isinstance(ev.get("swap_titles"), list):
        ev["swap_titles"] = [
            _strip_emojis_and_ctrl(str(t))[:48]
            for t in ev["swap_titles"][:8]
            if _strip_emojis_and_ctrl(str(t))
        ]

    return {k: v for k, v in ev.items() if k in _catalog._COMMON_EVENT_KEYS}


def _normalize_usage(raw_usage: Mapping[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        val = raw_usage.get(key)
        if isinstance(val, int) and not isinstance(val, bool) and val >= 0:
            out[key] = int(val)
    if (out.get("total_tokens", 0) <= 0) and (
        out.get("prompt_tokens", 0) > 0 or out.get("completion_tokens", 0) > 0
    ):
        out["total_tokens"] = int(out.get("prompt_tokens", 0)) + int(out.get("completion_tokens", 0))
    details = raw_usage.get("completion_tokens_details")
    if isinstance(details, Mapping):
        rt = details.get("reasoning_tokens")
        if isinstance(rt, int) and not isinstance(rt, bool) and rt >= 0:
            out["reasoning_tokens"] = int(rt)
    return out


def build_world_content_prompts(clean_ctx: Mapping[str, Any]) -> tuple[str, str]:
    """Build concise Chinese system & user prompts strictly from public world context."""
    allowed_tpls = list(clean_ctx.get("allowed_templates") or ())
    if not allowed_tpls:
        allowed_tpls = list(_catalog.ITEM_CAPABILITY_TEMPLATES.keys())

    system_prompt = (
        "你是街区小店的真实货架上新企划员，只依据公开的店铺、季节与天气信息生成 1 到 2 件接地气、有质感、具象克制的新商品"
        "（可选附带 0 或 1 条店铺本地小活动）。禁止使用表情符号，禁止编造私人隐私或强制偏好。\n"
        "必须只输出一个严格合法的 JSON 对象，结构为 {\"items\": [...], \"events\": [...]}，不要附加解释文字。\n"
        "商品必须使用以下 6 种合法 template_id 之一（优先从 allowed_templates 中选择）并仅使用对应字段：\n"
        "1. drink_warm（热饮）: template_id, name_cn(<=20字), description_cn(<=60字), price_tier(budget|standard|hearty), price(220-1200整数), stock_initial(2-6整数), portions(1-4), aroma_cn, taste_note_cn\n"
        "2. meal_light（轻食）: template_id, name_cn, description_cn, price_tier, price(380-1500整数), stock_initial(2-5整数), portions(1-4), taste_note_cn\n"
        "3. snack_portable（便携点心，可选带可复用空盒）: template_id, name_cn, description_cn, price_tier, price(280-1200整数), stock_initial(2-6整数), portions(1-6), taste_note_cn, reusable_container_spec(可选:{container_kind,label_cn,capacity_slots,drinkware,supported_capabilities,material_cn})\n"
        "4. desk_vessel（案头器皿/杯具）: template_id, name_cn, description_cn, price_tier, price(480-2400整数), stock_initial(2-5整数), capacity_slots(1-6), drinkware(true/false), material_cn\n"
        "5. decor_fixture（居家陈设/灯具）: template_id, name_cn, description_cn, price_tier, price(800-4800整数), stock_initial(1-3整数), surfaces([]或[\"desk_surface\"]), light_band(null|\"dim\"|\"moderate\"|\"bright\"), material_cn\n"
        "6. reading_booklet（小开本读物册子）: template_id, name_cn, description_cn, price_tier, price(320-1400整数), stock_initial(2-5整数), pages_count(8-32), topic_tags(2-4短词数组), argument_thesis_cn, sample_excerpt_cn\n"
        "可选活动 events 默认可为 []；若生成 1 条，仅限 4 种合法 template_id：book_swap / tea_tasting / early_closing / porch_market。\n"
        "禁止与 existing_item_names 重名。"
    )

    public_payload = {
        "world_id": clean_ctx.get("world_id", "shiomi_city"),
        "theme": clean_ctx.get("theme", "coastal"),
        "season": clean_ctx.get("season", "autumn"),
        "solar_term": clean_ctx.get("solar_term", ""),
        "weather": clean_ctx.get("weather", "clear"),
        "store_id": clean_ctx.get("store_id", ""),
        "store_name": clean_ctx.get("store_name", ""),
        "allowed_templates": allowed_tpls,
        "existing_item_names": list(clean_ctx.get("existing_item_names") or ())[:24],
    }
    user_prompt = (
        "请根据下面这家店的公开信息，生成 1 到 2 件贴合当季与店铺气质的新上货商品，直接输出 JSON：\n"
        + json.dumps(public_payload, ensure_ascii=False)
    )
    return system_prompt, user_prompt


class WorldContentLLMProposalCallable:
    """Callable proposal adapter passed as ``llm_proposal_fn`` to ``world_content_generator``."""

    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = 0.4,
    ) -> None:
        self._config = dict(config)
        self.timeout_s = float(timeout_s)
        self.max_tokens = int(max_tokens)
        self.temperature = float(temperature)
        self.call_count = 0
        self.last_metadata: dict[str, Any] = {}
        self.status: dict[str, Any] = {
            "configured": bool(self._config.get("configured")),
            "provider": str(self._config.get("provider") or ""),
            "model_name": str(self._config.get("model_name") or ""),
            "endpoint_host": str(self._config.get("endpoint_host") or ""),
            "api_key_present": bool(self._config.get("api_key_present")),
            "stale_bridge_detected": bool(self._config.get("stale_bridge_detected")),
            "default_generation_mode": DEFAULT_GENERATION_MODE,
            "source": str(self._config.get("source") or "none"),
        }

    def __call__(self, clean_ctx: Mapping[str, Any]) -> dict[str, Any]:
        api_key = str(self._config.get("api_key") or "")
        base_url = str(self._config.get("base_url") or "")
        model_name = str(self._config.get("model_name") or "")
        provider = str(self._config.get("provider") or "custom")
        if not (base_url and model_name and api_key):
            raise WorldContentLLMUnconfiguredError()

        system_prompt, user_prompt = build_world_content_prompts(clean_ctx)
        transport = _pmt.OpenAICompatibleTransport(
            base_url=base_url,
            api_key=api_key,
            model=model_name,
            provider=provider,
            timeout_s=self.timeout_s,
            purpose=PURPOSE_WORLD_CONTENT,
            temperature=self.temperature,
            request_json_object=False,
        )

        self.call_count += 1
        try:
            t_res = transport.complete(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                timeout_s=self.timeout_s,
                max_output_tokens=self.max_tokens,
            )
        except _amc.AgencyModelClientError as exc:
            safe_msg = redact_secrets(str(exc), [api_key])
            raise WorldContentLLMGenerationError(
                f"外接模型调用失败（{exc.failure_class}）：{safe_msg}",
                meta={
                    "failure_class": exc.failure_class,
                    "http_status": exc.status,
                    "endpoint_host": self.status["endpoint_host"],
                    "provider": provider,
                    "model_name": model_name,
                    "shape": dict(exc.metadata or {}),
                },
            ) from None
        except Exception as exc:
            safe_msg = redact_secrets(f"{type(exc).__name__}: {exc}", [api_key])
            raise WorldContentLLMGenerationError(
                f"外接模型请求异常：{safe_msg}",
                meta={
                    "endpoint_host": self.status["endpoint_host"],
                    "provider": provider,
                    "model_name": model_name,
                },
            ) from None

        usage = _normalize_usage(t_res.usage or {})
        effective_model = str(t_res.model or model_name)
        meta = {
            "provider": provider,
            "model_name": effective_model,
            "endpoint_host": self.status["endpoint_host"],
            "stale_bridge_detected": self.status["stale_bridge_detected"],
            "source": self.status["source"],
            "usage": copy.deepcopy(usage),
            "latency_ms": int(t_res.latency_ms),
            "finish_reason": t_res.finish_reason,
            "content_chars": int(t_res.content_chars),
            "reasoning_chars": int(t_res.reasoning_chars),
        }
        self.last_metadata = copy.deepcopy(meta)

        try:
            parsed = extract_proposal_json(t_res.text)
        except WorldContentLLMGenerationError as exc:
            raise WorldContentLLMGenerationError(
                redact_secrets(exc.detail, [api_key]),
                meta={
                    "endpoint_host": self.status["endpoint_host"],
                    "provider": provider,
                    "model_name": effective_model,
                    "finish_reason": t_res.finish_reason,
                    "content_chars": t_res.content_chars,
                    "reasoning_chars": t_res.reasoning_chars,
                },
            ) from None

        raw_items = parsed.get("items")
        raw_events = parsed.get("events")
        if raw_items is None and raw_events is None:
            raise WorldContentLLMGenerationError("外接模型 JSON 缺少 items 字段")
        if raw_items is not None and (not isinstance(raw_items, list) or len(raw_items) == 0):
            if not isinstance(raw_events, list) or len(raw_events) == 0:
                raise WorldContentLLMGenerationError("外接模型返回了空的商品列表")

        store_id = str(clean_ctx.get("store_id") or "").strip()
        season = str(clean_ctx.get("season") or "").strip().lower()

        norm_items = [
            _normalize_item_candidate(row, store_id=store_id, season=season)
            for row in (raw_items if isinstance(raw_items, list) else [])[:4]
        ]

        first_valid_id: Optional[str] = None
        for cand in norm_items:
            ok_it, valid_it, _err = _catalog.validate_candidate_item(
                cand, allowed_store_ids=(store_id,) if store_id else None
            )
            if ok_it and valid_it is not None:
                first_valid_id = str(valid_it.get("catalog_item_id") or "")
                break

        norm_events = [
            _normalize_event_candidate(
                ev,
                store_id=store_id,
                season=season,
                first_valid_item_id=first_valid_id,
            )
            for ev in (raw_events if isinstance(raw_events, list) else [])[:2]
        ]

        return {
            "items": norm_items,
            "events": norm_events,
            "usage": usage,
            "model": effective_model,
            "provider": provider,
            "latency_ms": int(t_res.latency_ms),
            "finish_reason": t_res.finish_reason,
        }


def build_live_world_content_llm_proposal_fn(
    env: Optional[Mapping[str, str]] = None,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float = 0.4,
    gateway_ip_fn: Optional[Callable[[], Optional[str]]] = None,
) -> WorldContentLLMProposalCallable:
    """Build a secret-safe live proposal callable for ``world_content_generator``."""
    cfg = resolve_external_llm_config_zero_write(env, gateway_ip_fn=gateway_ip_fn)
    if not cfg.get("configured"):
        raise WorldContentLLMUnconfiguredError()
    return WorldContentLLMProposalCallable(
        cfg,
        timeout_s=timeout_s,
        max_tokens=max_tokens,
        temperature=temperature,
    )


def open_content_request_budget(
    life_state_root: Path | str,
    *,
    env: Optional[Mapping[str, str]] = None,
) -> Any:
    """Open the canonical ``LifeRequestBudget`` for world content generation."""
    env_map = os.environ if env is None else env
    day_max = DEFAULT_UTC_DAY_MAX
    raw_day = _clean_scalar(env_map.get("NYA02_REQUEST_UTC_DAY_MAX"))
    if raw_day.isdigit() and int(raw_day) > 0:
        day_max = int(raw_day)
    token_cap = int(getattr(_lrb, "DEFAULT_DAILY_TOKEN_BUDGET", 50_000))
    raw_tok = _clean_scalar(env_map.get("NYA02_DAILY_TOKEN_BUDGET"))
    if raw_tok.isdigit() and int(raw_tok) > 0:
        token_cap = int(raw_tok)
    return _lrb.LifeRequestBudget(
        Path(life_state_root),
        utc_day_max=day_max,
        daily_token_budget=token_cap,
    )


def record_actual_usage_in_request_budget(
    budget: Any,
    reservation: Optional[Mapping[str, Any]],
    usage: Optional[Mapping[str, Any]],
    *,
    model_name: str = "",
    provider: str = "",
) -> dict[str, Any]:
    """Attach real provider token usage to an existing ``LifeRequestBudget`` reservation.

    Preserves ``life_request_budget.py``'s no-refund iron law: ``day_tokens[day]``
    never decreases; if actual ``total_tokens`` exceeds ``est_tokens``, the
    positive overage is honestly added to ``day_tokens[day]``.
    """
    if budget is None or not isinstance(reservation, Mapping) or not isinstance(usage, Mapping):
        return {"recorded": False}
    clean_usage = _normalize_usage(usage)
    if not clean_usage:
        return {"recorded": False}

    res_entry = reservation.get("reservation")
    res_token = str(res_entry.get("token") or "") if isinstance(res_entry, Mapping) else ""
    day = str(reservation.get("business_day") or reservation.get("utc_day") or "")
    est_tokens = int(res_entry.get("est_tokens") or 0) if isinstance(res_entry, Mapping) else 0
    actual_total = int(clean_usage.get("total_tokens") or 0)

    try:
        with budget._thread_lock, _lrb._exclusive(budget.state_root):
            data = budget._load_locked()
            tokens = list(data.get("tokens") or [])
            matched_row: Optional[dict[str, Any]] = None
            for row in reversed(tokens):
                if isinstance(row, dict) and res_token and row.get("token") == res_token:
                    matched_row = row
                    break

            if matched_row is not None:
                prev_charged = max(
                    est_tokens,
                    int(matched_row.get("charged_tokens") or 0),
                    int(matched_row.get("actual_tokens") or 0),
                )
            else:
                prev_charged = est_tokens

            delta = actual_total - prev_charged
            if day and delta > 0:
                day_tokens = dict(data.get("day_tokens") or {})
                day_tokens[day] = int(day_tokens.get(day) or 0) + delta
                data["day_tokens"] = day_tokens

            matched = matched_row is not None
            if matched_row is not None:
                matched_row["actual_tokens"] = actual_total
                matched_row["charged_tokens"] = max(prev_charged, actual_total)
                if "prompt_tokens" in clean_usage:
                    matched_row["prompt_tokens"] = int(clean_usage["prompt_tokens"])
                if "completion_tokens" in clean_usage:
                    matched_row["completion_tokens"] = int(clean_usage["completion_tokens"])
                if "reasoning_tokens" in clean_usage:
                    matched_row["reasoning_tokens"] = int(clean_usage["reasoning_tokens"])
                if model_name:
                    matched_row["model"] = str(model_name)[:120]
                if provider:
                    matched_row["provider"] = str(provider)[:64]
            data["tokens"] = tokens
            budget._save_locked(data)
            return {
                "recorded": True,
                "matched_reservation": matched,
                "actual_tokens": actual_total,
                "day_tokens": int((data.get("day_tokens") or {}).get(day) or 0) if day else None,
            }
    except Exception:
        return {"recorded": False}


__all__ = [
    "DEFAULT_GENERATION_MODE",
    "DEFAULT_HERMES_HOME_DIR",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_RC1_PROFILE_DIR",
    "DEFAULT_TIMEOUT_S",
    "PURPOSE_WORLD_CONTENT",
    "SAFE_STATUS_KEYS",
    "UNCONFIGURED_DETAIL_CN",
    "WorldContentLLMError",
    "WorldContentLLMGenerationError",
    "WorldContentLLMProposalCallable",
    "WorldContentLLMUnconfiguredError",
    "build_live_world_content_llm_proposal_fn",
    "build_world_content_prompts",
    "detect_wsl_default_gateway_ip",
    "extract_proposal_json",
    "open_content_request_budget",
    "read_external_llm_status_zero_write",
    "record_actual_usage_in_request_budget",
    "redact_secrets",
    "resolve_external_llm_config_zero_write",
]
