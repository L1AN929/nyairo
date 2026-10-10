"""v0.2 B4: a small OpenAI-compatible transport for the configured provider.

Why this exists
---------------
``AgencyModelClient`` already supports an injected ``transport`` ("when a host transport is
injected it IS the provider path", ``agency_model_client.py:180``).  Its own raw-HTTP path sends
``Python-urllib/3.x`` as the user agent, which the instance's gateway front (Cloudflare) rejects
with error 1010.  Rather than edit the frozen client, this supplies the transport seam it already
has: same endpoint, same auth, same request shape, plus a normal user agent.

It reads nothing from the model and holds no credentials of its own: the caller passes the
already-resolved ``base_url``/``api_key`` from the profile.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Optional

#: A plain, honest user agent.  Not a disguise -- just not the default library string that the
#: front end blocks.
USER_AGENT = "nyairo-life/0.2 (+https://nyairo.com)"

DEFAULT_PURPOSE = "agency_cognition"


class TransportResult:
    """The shape ``AgencyModelClient`` and the adapters expect back."""

    def __init__(self, *, text: str, latency_ms: int, status: int,
                 usage: Optional[dict[str, Any]] = None, model: str = "") -> None:
        self.text = text
        self.latency_ms = latency_ms
        self.status = status
        self.usage = dict(usage or {})
        self.input_tokens = self.usage.get("prompt_tokens")
        self.output_tokens = self.usage.get("completion_tokens")
        self.total_tokens = self.usage.get("total_tokens")
        self.reported_cost_usd = None
        self.usage_source = "provider" if self.usage else None
        self.model = model


class OpenAICompatibleTransport:
    """Minimal chat-completions transport with an explicit user agent."""

    def __init__(self, *, base_url: str, api_key: str, model: str,
                 provider: str = "custom", timeout_s: float = 30.0,
                 purpose: str = DEFAULT_PURPOSE, temperature: float = 0.0,
                 request_json_object: bool = False) -> None:
        if not base_url or not api_key or not model:
            raise ValueError("base_url, api_key and model are all required")
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.model = model
        self.provider = provider
        self.timeout_s = float(timeout_s)
        self.purpose = purpose
        self.temperature = float(temperature)
        self.request_json_object = bool(request_json_object)
        self.backend = "openai_compatible_http"
        self.call_count = 0

    def describe(self) -> dict[str, Any]:
        return {"backend": self.backend, "provider": self.provider, "model": self.model,
                "base_url": self.base_url, "api_key_present": bool(self._api_key),
                "user_agent": USER_AGENT, "calls": self.call_count}

    def complete(self, *, system_prompt: str, user_prompt: str,
                 timeout_s: Optional[float] = None,
                 max_output_tokens: Optional[int] = None) -> TransportResult:
        from agency_model_client import (AgencyModelClientError, PROVIDER_AUTH_ERROR,
                                         PROVIDER_MALFORMED_RESPONSE, PROVIDER_RATE_LIMITED,
                                         PROVIDER_SERVER_ERROR, TRANSPORT_CONNECTION,
                                         TRANSPORT_TIMEOUT)

        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system_prompt},
                         {"role": "user", "content": user_prompt}],
            "max_tokens": int(max_output_tokens or 512),
            "temperature": self.temperature,
            "stream": False,
        }
        if self.request_json_object:
            body["response_format"] = {"type": "json_object"}
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=payload, method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self._api_key}",
                     "User-Agent": USER_AGENT,
                     "Accept": "application/json",
                     "X-Chiyo-Purpose": self.purpose},
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request,
                                        timeout=float(timeout_s or self.timeout_s)) as response:
                status = int(getattr(response, "status", 200))
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:200]
            except Exception:  # pragma: no cover - body already consumed
                detail = ""
            if status in (401, 403):
                raise AgencyModelClientError(
                    PROVIDER_AUTH_ERROR, f"http {status} {detail}") from exc
            if status == 429:
                raise AgencyModelClientError(
                    PROVIDER_RATE_LIMITED, f"http {status} {detail}") from exc
            if status >= 500:
                raise AgencyModelClientError(
                    PROVIDER_SERVER_ERROR, f"http {status} {detail}") from exc
            raise AgencyModelClientError(
                PROVIDER_MALFORMED_RESPONSE, f"http {status} {detail}") from exc
        except TimeoutError as exc:
            raise AgencyModelClientError(TRANSPORT_TIMEOUT, str(exc)) from exc
        except urllib.error.URLError as exc:
            raise AgencyModelClientError(TRANSPORT_CONNECTION, str(exc)) from exc

        latency_ms = int((time.monotonic() - started) * 1000)
        self.call_count += 1
        try:
            parsed = json.loads(raw)
            choices = parsed.get("choices") or []
            text = str((choices[0].get("message") or {}).get("content") or "") if choices else ""
        except (ValueError, AttributeError, IndexError) as exc:
            raise AgencyModelClientError(
                PROVIDER_MALFORMED_RESPONSE, f"unparsable body: {raw[:200]}") from exc
        if not text.strip():
            raise AgencyModelClientError(PROVIDER_MALFORMED_RESPONSE, "empty completion")
        return TransportResult(text=text, latency_ms=latency_ms, status=status,
                               usage=parsed.get("usage"), model=str(parsed.get("model") or ""))


__all__ = ["OpenAICompatibleTransport", "TransportResult", "USER_AGENT"]
