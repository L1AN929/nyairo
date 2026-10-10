"""v0.2 B4/E10: reuse the model provider the instance is *already* configured with.

Why this module exists
----------------------
``agency_model_client.load_provider_config`` (``agency_model_client.py:108``) expects an older
profile shape: a top-level ``provider:`` scalar plus a ``<provider>:`` block carrying
``base_url``/``key_env``.  The profile this instance actually runs on has moved to the current
shape::

    model:
      default: <model>
      provider: <name>
      base_url: <url>
      api_key: <key>
    custom_providers:
      <name>:
        base_url: <url>
        key_env: <ENV_NAME>
        model: <model>

so the frozen resolver finds nothing and the client reports ``CONFIG_MISSING``.  Rather than
rewrite a frozen module, this adds one resolver that understands the current shape and falls back
to the frozen one.

What it never does
------------------
* It hardcodes no endpoint, no model name and no key.  Everything comes from the profile the
  operator already configured.
* It never logs or returns a key in a reportable field: ``api_key`` is present in the returned
  mapping (the client needs it) but ``describe_model_config`` exposes only ``api_key_present``.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Optional

_LIFE_SCRIPTS = str(Path(__file__).resolve().parent)
if _LIFE_SCRIPTS not in sys.path:
    sys.path.insert(0, _LIFE_SCRIPTS)

from agency_model_client import load_provider_config  # noqa: E402


def _read_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def _block(text: str, name: str, indent: int = 0) -> Optional[str]:
    """The indented body of ``name:`` at the given indent level."""
    pattern = (rf"^{' ' * indent}{re.escape(name)}:\s*$(.*?)(?=^{' ' * indent}\S|\Z)")
    match = re.search(pattern, text, re.MULTILINE | re.DOTALL)
    return match.group(1) if match else None


def _scalar(text: str, key: str, indent: int = 2) -> Optional[str]:
    match = re.search(rf"^{' ' * indent}{re.escape(key)}:\s*(\S.*?)\s*$", text, re.MULTILINE)
    if not match:
        return None
    value = match.group(1).strip()
    return value or None


def _expand(value: Optional[str], env_file: Mapping[str, str]) -> Optional[str]:
    """Resolve ``${VAR}`` references.  The profile keeps the real key in its env file and only a
    reference in config.yaml, so taking the reference literally authenticates as nothing."""
    if not value:
        return value
    text = str(value).strip()
    match = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", text)
    if match:
        name = match.group(1)
        return os.environ.get(name) or env_file.get(name) or None
    return text


def load_configured_model_config(*, hermes_home: Optional[str] = None) -> dict[str, Any]:
    """Resolve the configured provider into the mapping ``AgencyModelClient`` expects."""
    home = Path(hermes_home or os.environ.get("HERMES_HOME") or "./data/persona")
    frozen = dict(load_provider_config(hermes_home=str(home)))
    if frozen.get("api_key") and frozen.get("base_url") and frozen.get("model"):
        frozen["resolved_by"] = "frozen_resolver"
        return frozen

    text = ""
    try:
        text = (home / "config.yaml").read_text(encoding="utf-8")
    except OSError:
        text = ""

    provider = _scalar(text, "provider") or frozen.get("provider") or "custom"
    model = _scalar(text, "default") or frozen.get("model")
    base_url = _scalar(text, "base_url")
    api_key = _scalar(text, "api_key")
    key_env = None

    custom = _block(text, "custom_providers", indent=0) or ""
    provider_block = _block(custom, provider, indent=2) or ""
    if provider_block:
        base_url = _scalar(provider_block, "base_url") or base_url
        key_env = _scalar(provider_block, "key_env") or key_env
        model = model or _scalar(provider_block, "model")

    env_file = _read_env_file(home / ".env")
    # config.yaml may hold a ${VAR} reference instead of the key itself; expand before use.
    api_key = _expand(api_key, env_file)
    base_url = _expand(base_url, env_file)
    model = _expand(model, env_file)
    if not api_key and key_env:
        api_key = os.environ.get(key_env) or env_file.get(key_env)
    return {
        "hermes_home": str(home),
        "provider": str(provider),
        "model": str(model) if model else None,
        "base_url": (base_url or "").rstrip("/") or None,
        "key_env": key_env,
        "api_key": api_key,
        "api_key_present": bool(api_key),
        "config_path": str(home / "config.yaml"),
        "resolved_by": "profile_shape_resolver",
    }


def describe_model_config(cfg: Mapping[str, Any]) -> dict[str, Any]:
    """A reportable summary.  Never contains the key itself."""
    return {
        "provider": cfg.get("provider"),
        "model": cfg.get("model"),
        "base_url": cfg.get("base_url"),
        "key_env": cfg.get("key_env"),
        "api_key_present": bool(cfg.get("api_key")),
        "hermes_home": cfg.get("hermes_home"),
        "resolved_by": cfg.get("resolved_by"),
        "usable": bool(cfg.get("api_key") and cfg.get("base_url") and cfg.get("model")),
    }


__all__ = ["describe_model_config", "load_configured_model_config"]
