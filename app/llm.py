"""Optional LLM client with a deterministic, no-key fallback.

MandatePay works without any model credentials: the planner and verifier have
careful rule-based implementations.  When an LLM is configured (OpenAI,
Anthropic, Ollama or any OpenAI-compatible endpoint) it is used for:

* extracting the mandate from natural language,
* drafting milestones and acceptance criteria,
* the qualitative part of verification.

Machine-checkable criteria are always evaluated by the rules engine; the model
can never override a hard failure.  That keeps the demo honest and safe.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from .config import Settings

log = logging.getLogger("mandatepay.llm")

_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _extract_json(text: str) -> dict | None:
    if not text:
        return None
    match = _JSON_FENCE.search(text)
    candidate = match.group(1) if match else text
    candidate = candidate.strip()
    try:
        value = json.loads(candidate)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start >= 0 and end > start:
            try:
                value = json.loads(candidate[start : end + 1])
                return value if isinstance(value, dict) else None
            except json.JSONDecodeError:
                return None
    return None


class LLMClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.last_error: str | None = None

    @property
    def enabled(self) -> bool:
        return self.settings.llm_enabled

    @property
    def provider(self) -> str:
        return self.settings.llm_provider_resolved

    @property
    def model(self) -> str:
        return self.settings.llm_model

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "provider": self.provider,
            "model": self.model or None,
            "last_error": self.last_error,
        }

    async def json(self, system: str, user: str, *, max_tokens: int = 1400) -> dict | None:
        if not self.enabled:
            return None
        try:
            if self.provider == "anthropic":
                return await self._anthropic(system, user, max_tokens)
            return await self._openai(system, user, max_tokens)
        except Exception as exc:  # noqa: BLE001 - the app must never die because a model failed
            self.last_error = f"{type(exc).__name__}: {exc}"
            log.warning("LLM call failed, falling back to rules: %s", self.last_error)
            return None

    async def _openai(self, system: str, user: str, max_tokens: int) -> dict | None:
        base = self.settings.llm_base_url_resolved
        payload: dict[str, Any] = {
            "model": self.settings.llm_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
            "max_tokens": max_tokens,
        }
        headers = {"Content-Type": "application/json"}
        if self.settings.llm_api_key:
            headers["Authorization"] = f"Bearer {self.settings.llm_api_key}"
        async with httpx.AsyncClient(timeout=self.settings.llm_timeout_seconds) as client:
            response = await client.post(
                f"{base}/chat/completions",
                json={**payload, "response_format": {"type": "json_object"}},
                headers=headers,
            )
            if response.status_code >= 400:
                # Some compatible servers do not implement response_format.
                response = await client.post(f"{base}/chat/completions", json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
        text = data["choices"][0]["message"]["content"]
        return _extract_json(text)

    async def _anthropic(self, system: str, user: str, max_tokens: int) -> dict | None:
        base = self.settings.llm_base_url_resolved
        headers = {
            "x-api-key": self.settings.llm_api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": self.settings.llm_model,
            "max_tokens": max_tokens,
            "temperature": 0.2,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        async with httpx.AsyncClient(timeout=self.settings.llm_timeout_seconds) as client:
            response = await client.post(f"{base}/v1/messages", json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
        parts = [block.get("text", "") for block in data.get("content", []) if block.get("type") == "text"]
        return _extract_json("\n".join(parts))
