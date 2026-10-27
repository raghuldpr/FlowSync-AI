"""Minimal OpenAI-compatible chat-completions client (xAI, Gemini's OpenAI endpoint, OpenRouter, local servers).

Errors carry only a coarse `kind`; response bodies, headers and the API key are never put in exceptions or logs.
No automatic retries: a timed-out request may have had side effects on the provider's side, and the
deterministic fallback is the recovery path.
"""
from typing import Optional
from urllib.parse import urlparse

import httpx


class LLMError(Exception):
    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind      # not_configured | timeout | unreachable | http_error | invalid_response


class LLMClient:
    def __init__(self, settings, transport: Optional[httpx.BaseTransport] = None):
        self.base_url = settings.llm_base_url.rstrip("/")
        self.model = settings.llm_model
        self._key = settings.llm_api_key
        self.timeout = settings.llm_timeout_s
        self.max_tokens = settings.llm_max_tokens
        self.enabled = bool(settings.llm_enabled and self.base_url and self.model and self._key)
        self.requested = bool(settings.llm_enabled)
        self._transport = transport
        self.forced_down = False          # failure drill: behave as if the provider were unreachable

    def status(self) -> dict:
        host = urlparse(self.base_url).hostname if self.base_url else None
        return {"requested": self.requested, "enabled": self.enabled, "model": self.model or None, "provider_host": host}

    def chat(self, messages: list, tools: Optional[list] = None, timeout: Optional[float] = None) -> dict:
        """Returns the first choice's message dict. Raises LLMError."""
        if self.forced_down:
            raise LLMError("unreachable")
        if not self.enabled:
            raise LLMError("not_configured")
        body = {"model": self.model, "messages": messages, "temperature": 0, "max_tokens": self.max_tokens}
        if tools:
            body["tools"], body["tool_choice"] = tools, "auto"
        try:
            with httpx.Client(timeout=timeout or self.timeout, transport=self._transport) as c:
                r = c.post(self.base_url + "/chat/completions", json=body,
                           headers={"Authorization": "Bearer " + self._key})
        except httpx.TimeoutException:
            raise LLMError("timeout") from None
        except httpx.HTTPError:
            raise LLMError("unreachable") from None
        if r.status_code != 200:
            raise LLMError("http_error")
        try:
            msg = r.json()["choices"][0]["message"]
            if not isinstance(msg, dict):
                raise TypeError
            return msg
        except Exception:
            raise LLMError("invalid_response") from None
