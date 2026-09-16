"""Lens LLM provider abstraction (Phase 8).

Only one provider is implemented (OpenAI-compatible ``/chat/completions``,
which covers OpenAI and most local/compatible gateways). More providers can be
added behind the same ``LLMProvider.complete`` seam. ``get_provider`` returns
``None`` when no API key is configured, which is how the app stays fully
functional without any LLM dependency.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod

import httpx

from app.config import Settings

logger = logging.getLogger("repolens.lens")

DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"

SUPPORTED_PROVIDERS = {"openai", "openai-compatible", "openai_compatible"}

# Hard ceiling on a provider response body. A malicious or broken provider must
# never be able to force RepoLens into unbounded memory by streaming a gigantic
# reply claiming success. The content-length header is checked first so the
# body is never read at all when the provider declares an oversized payload.
MAX_LENS_RESPONSE_BYTES = 1024 * 1024  # 1 MiB


class LensError(Exception):
    """Base class for Lens failures."""


class LensProviderError(LensError):
    """The provider could not be reached or returned an error."""


class LensTimeoutError(LensError):
    """The provider call exceeded the timeout."""


class LLMProvider(ABC):
    """Interface every Lens backing model must implement."""

    name: str
    model: str

    @abstractmethod
    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        """Return the raw model reply text for a single turn."""


class OpenAICompatibleProvider(LLMProvider):
    def __init__(
        self,
        *,
        name: str = "openai-compatible",
        base_url: str = DEFAULT_OPENAI_BASE_URL,
        api_key: str,
        model: str,
        timeout: float = 20.0,
        max_tokens: int = 700,
    ) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
            "max_tokens": self.max_tokens,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(url, headers=headers, json=body)
        except httpx.TimeoutException as exc:
            logger.warning("lens provider timed out after %.1fs", self.timeout)
            raise LensTimeoutError("LLM request timed out") from exc
        except httpx.HTTPError as exc:
            logger.warning("lens provider transport error: %s", exc)
            raise LensProviderError(f"LLM transport error: {exc}") from exc

        content_length = response.headers.get("content-length")
        if content_length and content_length.isdigit():
            if int(content_length) > MAX_LENS_RESPONSE_BYTES:
                raise LensProviderError(
                    "LLM response is larger than the safety ceiling"
                )

        if response.status_code >= 400:
            # Never echo the body: it may contain provider/API details.
            logger.warning("lens provider returned HTTP %s", response.status_code)
            raise LensProviderError(f"LLM provider returned HTTP {response.status_code}")

        if len(response.content) > MAX_LENS_RESPONSE_BYTES:
            raise LensProviderError("LLM response is larger than the safety ceiling")

        try:
            data = response.json()
        except ValueError as exc:
            raise LensProviderError("LLM returned non-JSON response") from exc
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LensProviderError("LLM response missing message content") from exc
        if not isinstance(content, str) or not content.strip():
            raise LensProviderError("LLM returned empty message content")
        return content


def get_provider(settings: Settings) -> LLMProvider | None:
    """Build the configured provider, or ``None`` when Lens is unavailable.

    Lens is unavailable without both a provider name and an API key, or when
    the provider name is not supported. Unknown providers degrade gracefully
    (log a warning, return None) rather than failing the deterministic core.
    """
    provider = (settings.ai_provider or "").strip().lower()
    api_key = (settings.ai_api_key or "").strip()
    if not provider or not api_key:
        return None
    if provider not in SUPPORTED_PROVIDERS:
        logger.warning("unsupported ai_provider %r; Lens is unavailable", provider)
        return None
    base_url = settings.ai_base_url or DEFAULT_OPENAI_BASE_URL
    return OpenAICompatibleProvider(
        name=provider,
        base_url=base_url,
        api_key=api_key,
        model=settings.ai_model,
        timeout=settings.lens_timeout_seconds,
    )