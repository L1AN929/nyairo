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

LPC0B-R3: a thinking provider's answer is not always in ``content``
-------------------------------------------------------------------
The configured ``cn:deepseek-v4.1-flash`` is a reasoning model: its thinking tokens are counted
*inside* ``usage.completion_tokens`` (``completion_tokens_details.reasoning_tokens``), so they are
charged against the same ``max_tokens`` as the answer.  Two facts follow, and this transport
enforces both:

* a response whose ``finish_reason`` is ``"length"`` was cut off by the cap -- whether it came
  back with ``content: ""`` or with a partial (even accidentally parseable) body.  That is a
  *truncated* answer, and it is reported as ``PROVIDER_OUTPUT_TRUNCATED`` -- never as
  ``PROVIDER_MALFORMED_RESPONSE``, which the cognition adapter turns into ``INVALID_JSON`` and
  which therefore blamed the model for a cap it was given.  The error carries shape-only
  metadata (finish reason, character counts and integer token counts) so the failure path can
  record why it happened without persisting any text;
* ``message.reasoning_content`` is a derivation, not an answer.  It is measured (length only) for
  telemetry and then dropped: it is never ``text``, never parsed, never a decision.

W05R-R1c: the finite send phase and the response wait are separate
------------------------------------------------------------------
The pause confirmation has to be coordinated with the **actual send**, not with a check that
happened earlier: a request must not start after the operator's ``pause`` returned.  That is only
possible where the request is really written out, so this transport exposes the two halves:

* :meth:`begin_send` -- connect + headers + body (the finite *send phase*).  When a pause guard
  is configured, :meth:`complete` holds the guard's shared confirmation lock across exactly this
  call: the lock is the permit to send, and the bit is re-read while it is held;
* :meth:`finish_send` -- read and parse the response (the *response wait*).  It always runs with
  **no** pause/shared/control lock held, so a slow model can never block the operator's pause.

``send_split_capable`` states this as a capability, so a caller that was asked to coordinate a
pause refuses to send through a transport that does not provide it instead of pretending.
"""
from __future__ import annotations

import http.client
import json
import socket
import time
import urllib.parse
from typing import Any, Mapping, Optional

#: A plain, honest user agent.  Not a disguise -- just not the default library string that the
#: front end blocks.
USER_AGENT = "nyairo-life/0.2 (+https://nyairo.com)"

DEFAULT_PURPOSE = "agency_cognition"


def _shape_metadata(*, finish_reason: Any, content_chars: int, reasoning_chars: int,
                    usage: Mapping[str, Any]) -> dict[str, Any]:
    """Shape-only facts about a response that could not be used.

    Counts (characters and integer token counts) and the provider's finish reason, nothing
    else.  A count that is missing or not a non-negative integer is *omitted*, never written
    as ``0``: "the provider reported nothing" and "the provider reported zero" are different
    facts, and the diagnosis depends on keeping them apart.  The response body, the model's
    content and its reasoning text can never enter this mapping.
    """

    out: dict[str, Any] = {
        "finish_reason": finish_reason if isinstance(finish_reason, str) else None,
        "content_chars": int(content_chars),
        "reasoning_chars": int(reasoning_chars),
    }
    for name, source in (("input_tokens", "prompt_tokens"),
                         ("output_tokens", "completion_tokens"),
                         ("total_tokens", "total_tokens")):
        value = usage.get(source)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            out[name] = value
    details = usage.get("completion_tokens_details")
    if isinstance(details, Mapping):
        value = details.get("reasoning_tokens")
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            out["reasoning_tokens"] = value
    return out


class TransportResult:
    """The shape ``AgencyModelClient`` and the adapters expect back."""

    def __init__(self, *, text: str, latency_ms: int, status: int,
                 usage: Optional[dict[str, Any]] = None, model: str = "",
                 finish_reason: Optional[str] = None, reasoning_chars: int = 0,
                 content_chars: int = 0) -> None:
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
        #: LPC0B-R3: provider metadata about the *shape* of the answer -- never its text.
        #: ``reasoning_chars`` is only ever a length: a thinking model's derivation is not an
        #: answer and must never be promoted into ``text``.
        self.finish_reason = finish_reason
        self.reasoning_present = bool(reasoning_chars)
        self.reasoning_chars = int(reasoning_chars or 0)
        self.content_chars = int(content_chars or 0)


class _PendingSend:
    """A request whose finite send phase is written out; its response is not read yet."""

    def __init__(self, *, connection: Any, started: float, url: str) -> None:
        self.connection = connection
        self.started = started
        self.url = url


class OpenAICompatibleTransport:
    """Minimal chat-completions transport with an explicit user agent."""

    #: W05R-R1c: connect + write-out is a separate, finite phase from the response wait, so a
    #: pause guard can hold the confirmation lock across the send only.
    send_split_capable = True

    def __init__(self, *, base_url: str, api_key: str, model: str,
                 provider: str = "custom", timeout_s: float = 30.0,
                 purpose: str = DEFAULT_PURPOSE, temperature: float = 0.0,
                 request_json_object: bool = False, pause_guard: Any = None) -> None:
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
        #: The pause request-send guard (``life_pause.RequestSendGuard``) or ``None``.  Only this
        #: transport holds it, and only across ``begin_send``.
        self._pause_guard = pause_guard
        self.backend = "openai_compatible_http"
        self.call_count = 0

    def describe(self) -> dict[str, Any]:
        return {"backend": self.backend, "provider": self.provider, "model": self.model,
                "base_url": self.base_url, "api_key_present": bool(self._api_key),
                "user_agent": USER_AGENT, "calls": self.call_count,
                "send_split_capable": self.send_split_capable,
                "pause_guard": self._pause_guard is not None}

    # -- the two halves of one request -------------------------------------------------

    def _target(self) -> tuple[str, str, int, str]:
        parts = urllib.parse.urlsplit("%s/chat/completions" % self.base_url)
        scheme = parts.scheme or "https"
        host = parts.hostname or ""
        if not host:
            raise ValueError("base_url has no host")
        port = parts.port or (443 if scheme == "https" else 80)
        path = parts.path or "/"
        if parts.query:
            path = "%s?%s" % (path, parts.query)
        return scheme, host, int(port), path

    def _build_payload(self, system_prompt: str, user_prompt: str,
                       max_output_tokens: Optional[int]) -> bytes:
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
        return json.dumps(body, ensure_ascii=False).encode("utf-8")

    def begin_send(self, *, system_prompt: str, user_prompt: str,
                   timeout_s: Optional[float] = None,
                   max_output_tokens: Optional[int] = None) -> _PendingSend:
        """Write the request out.  Finite: connect, headers, body.  No response is read."""

        from agency_model_client import (AgencyModelClientError, TRANSPORT_CONNECTION,
                                         TRANSPORT_TIMEOUT)

        payload = self._build_payload(system_prompt, user_prompt, max_output_tokens)
        scheme, host, port, path = self._target()
        timeout = float(timeout_s or self.timeout_s)
        connection = (http.client.HTTPSConnection(host, port, timeout=timeout)
                      if scheme == "https"
                      else http.client.HTTPConnection(host, port, timeout=timeout))
        started = time.monotonic()
        try:
            connection.connect()
            connection.putrequest("POST", path)
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Content-Length", str(len(payload)))
            connection.putheader("Authorization", "Bearer %s" % self._api_key)
            connection.putheader("User-Agent", USER_AGENT)
            connection.putheader("Accept", "application/json")
            connection.putheader("X-Chiyo-Purpose", self.purpose)
            connection.endheaders()
            connection.send(payload)
        except (socket.timeout, TimeoutError) as exc:
            connection.close()
            raise AgencyModelClientError(TRANSPORT_TIMEOUT, str(exc)) from exc
        except (OSError, http.client.HTTPException) as exc:
            connection.close()
            raise AgencyModelClientError(TRANSPORT_CONNECTION, str(exc)) from exc
        return _PendingSend(connection=connection, started=started,
                            url="%s://%s:%d%s" % (scheme, host, port, path))

    def finish_send(self, pending: _PendingSend) -> TransportResult:
        """Read and parse the response.  Never runs under a pause/shared/control lock."""

        from agency_model_client import (AgencyModelClientError, PROVIDER_AUTH_ERROR,
                                         PROVIDER_EMPTY_CONTENT, PROVIDER_MALFORMED_RESPONSE,
                                         PROVIDER_OUTPUT_TRUNCATED, PROVIDER_RATE_LIMITED,
                                         PROVIDER_REASONING_ONLY, PROVIDER_REQUEST_REJECTED,
                                         PROVIDER_SERVER_ERROR, TRANSPORT_CONNECTION,
                                         TRANSPORT_TIMEOUT)

        connection = pending.connection
        try:
            response = connection.getresponse()
            status = int(getattr(response, "status", 200) or 200)
            raw = response.read().decode("utf-8", errors="replace")
        except (socket.timeout, TimeoutError) as exc:
            raise AgencyModelClientError(TRANSPORT_TIMEOUT, str(exc)) from exc
        except (OSError, http.client.HTTPException) as exc:
            raise AgencyModelClientError(TRANSPORT_CONNECTION, str(exc)) from exc
        finally:
            try:
                connection.close()
            except Exception:  # pragma: no cover - closing a broken socket
                pass

        if status >= 400:
            detail = raw[:200]
            # 2026-10-24: the HTTP status was already observed here and then dropped, so the
            # adapter's ``response_status`` field was always null and a 4xx refusal could not be
            # told apart (400 vs 404 vs 422) after the fact.  Carry the number only -- the body
            # stays in the exception text and is never persisted by the adapter.
            if status in (401, 403):
                raise AgencyModelClientError(PROVIDER_AUTH_ERROR,
                                             "http %d %s" % (status, detail), status=status)
            if status == 429:
                raise AgencyModelClientError(PROVIDER_RATE_LIMITED,
                                             "http %d %s" % (status, detail), status=status)
            if status >= 500:
                raise AgencyModelClientError(PROVIDER_SERVER_ERROR,
                                             "http %d %s" % (status, detail), status=status)
            # LPC0B-R3: a refused *request* (bad parameter, unsupported response_format, wrong
            # body) is not a malformed *model response*; reporting it as one is how a payload
            # problem used to masquerade as "the model emitted bad JSON".
            raise AgencyModelClientError(PROVIDER_REQUEST_REJECTED,
                                         "http %d %s" % (status, detail), status=status)

        latency_ms = int((time.monotonic() - pending.started) * 1000)
        self.call_count += 1
        try:
            parsed = json.loads(raw)
            choices = parsed.get("choices") or []
            choice = choices[0] if choices else {}
            message = (choice.get("message") or {}) if isinstance(choice, Mapping) else {}
            content = message.get("content")
            reasoning = message.get("reasoning_content")
            finish_reason = choice.get("finish_reason") if isinstance(choice, Mapping) else None
            usage = parsed.get("usage") if isinstance(parsed.get("usage"), Mapping) else {}
        except (ValueError, AttributeError, IndexError, TypeError) as exc:
            raise AgencyModelClientError(
                PROVIDER_MALFORMED_RESPONSE, "unparsable body: %s" % raw[:200]) from exc
        if not isinstance(content, str):
            content = "" if content is None else json.dumps(content, ensure_ascii=False)
        # LPC0B-R3: ``reasoning_content`` is a thinking model's derivation.  It is measured for
        # telemetry and then dropped: it is never text, never parsed, never a decision.  Reading
        # a decision out of it would be exactly the free-text parse the order forbids.
        reasoning_text = reasoning if isinstance(reasoning, str) else ""
        content_chars = len(content.strip())
        reasoning_chars = len(reasoning_text.strip())
        finish = finish_reason if isinstance(finish_reason, str) else None
        shape = ("finish_reason=%s content_chars=%d reasoning_chars=%d"
                 % (finish, content_chars, reasoning_chars))
        # Shape-only metadata travels with the error so the failure path can record *why* the
        # call was unusable without persisting any text or inventing a missing count.
        metadata = _shape_metadata(finish_reason=finish, content_chars=content_chars,
                                   reasoning_chars=reasoning_chars, usage=usage)
        if finish == "length":
            # The token cap ran out: a real, billed call whose answer was cut off -- not bad
            # JSON.  This holds even when the truncated text happens to parse as JSON; a
            # length finish is never promoted into a decision.
            raise AgencyModelClientError(PROVIDER_OUTPUT_TRUNCATED, shape, metadata=metadata)
        if not content_chars:
            if reasoning_chars:
                raise AgencyModelClientError(PROVIDER_REASONING_ONLY, shape, metadata=metadata)
            raise AgencyModelClientError(PROVIDER_EMPTY_CONTENT, shape, metadata=metadata)
        return TransportResult(text=content, latency_ms=latency_ms, status=status,
                               usage=usage, model=str(parsed.get("model") or ""),
                               finish_reason=finish, reasoning_chars=reasoning_chars,
                               content_chars=content_chars)

    def complete(self, *, system_prompt: str, user_prompt: str,
                 timeout_s: Optional[float] = None,
                 max_output_tokens: Optional[int] = None) -> TransportResult:
        """One request: hold the pause confirmation lock across the write-out, then wait.

        With a pause guard configured, the shared confirmation lock is held across
        ``begin_send`` only -- a ``pause`` confirmed before this call can therefore never be
        overtaken, and a ``pause`` confirmed while the response is pending never waits for the
        model.  Without a guard the behaviour is exactly the previous one.
        """
        guard = self._pause_guard
        if guard is None:
            pending = self.begin_send(system_prompt=system_prompt, user_prompt=user_prompt,
                                      timeout_s=timeout_s, max_output_tokens=max_output_tokens)
        else:
            with guard.sending("model"):
                pending = self.begin_send(system_prompt=system_prompt, user_prompt=user_prompt,
                                          timeout_s=timeout_s,
                                          max_output_tokens=max_output_tokens)
        return self.finish_send(pending)


__all__ = ["OpenAICompatibleTransport", "TransportResult", "USER_AGENT"]
