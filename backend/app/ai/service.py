"""Lens service: orchestrates context -> provider -> validated response (Phase 8).

Design principles:
- The server builds all evidence; the LLM only explains and selects from it.
- Every call records metadata-only audit rows (no prompts, snippets, responses,
  or keys).
- No provider configured  -> ``LensUnavailableError`` (route returns ``503``).
- Provider/response failure  -> ``LensProviderFailure`` (route returns ``502``).
"""
from __future__ import annotations

import logging
import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import (
    LensError,
    LensProviderError,
    LensTimeoutError,
    LLMProvider,
    get_provider,
)
from app.ai.context import (
    LensContext,
    build_change_context,
    build_impact_context,
    build_review_context,
    build_unresolved_context,
)
from app.ai.prompts import (
    SYSTEM_PROMPT,
    InvalidLensResponse,
    build_user_prompt,
    parse_explanation,
)
from app.config import Settings, get_settings
from app.models.orm import LensAudit, Repository
from app.models.schemas import LensEvidenceInfo, LensKind, LensResponse

logger = logging.getLogger("repolens.lens")

# Second-layer response caps (mirror the prompt; enforced again here).
MAX_SELECTED_EVIDENCE = 6


class LensUnavailableError(RuntimeError):
    """No usable LLM provider is configured."""


class LensProviderFailure(RuntimeError):
    """The provider call or its response failed.

    ``kind`` is a stable machine-readable reason for the audit tray:
    provider_error | timeout | invalid_response
    """

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


class LensService:
    def __init__(self, db: AsyncSession, settings: Settings | None = None) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self.prompt_version = self.settings.lens_prompt_version
        self.provider: LLMProvider | None = get_provider(self.settings)

    # --- public ---------------------------------------------------------------

    async def explain(
        self, *, repository: Repository, context: LensContext
    ) -> LensResponse:
        provider = self.provider
        kind = str(context.kind)
        if provider is None:
            await self._audit(
                repository.id,
                kind,
                response_status="no_provider",
                error_kind=None,
                context=context,
                latency_ms=0,
            )
            raise LensUnavailableError(
                "Lens is unavailable because no LLM provider is configured."
            )

        payload = context.render(self.settings)
        user_prompt = build_user_prompt(kind, context.goal, payload)
        start = time.perf_counter()
        try:
            raw = await provider.complete(SYSTEM_PROMPT, user_prompt)
            parsed = parse_explanation(raw)
            error_kind = None
            response_status = "ok"
        except LensTimeoutError as exc:
            latency_ms = int((time.perf_counter() - start) * 1000)
            await self._audit(
                repository.id, kind, "timeout", "timeout", context, latency_ms
            )
            raise LensProviderFailure(
                "timeout", "The LLM request timed out."
            ) from exc
        except LensProviderError as exc:
            latency_ms = int((time.perf_counter() - start) * 1000)
            await self._audit(
                repository.id, kind, "provider_error", "provider_error", context, latency_ms
            )
            raise LensProviderFailure(
                "provider_error", "The LLM provider could not be reached."
            ) from exc
        except InvalidLensResponse as exc:
            latency_ms = int((time.perf_counter() - start) * 1000)
            await self._audit(
                repository.id, kind, "invalid_response", "invalid_response", context, latency_ms
            )
            raise LensProviderFailure(
                "invalid_response", "The LLM returned an unusable response."
            ) from exc
        except LensError as exc:  # defensive; subclass bugs must not crash routes
            latency_ms = int((time.perf_counter() - start) * 1000)
            await self._audit(
                repository.id, kind, "provider_error", "provider_error", context, latency_ms
            )
            raise LensProviderFailure("provider_error", str(exc)) from exc

        selected = self._select_evidence(context.evidence, parsed["evidence_indices"])
        if not selected:
            latency_ms = int((time.perf_counter() - start) * 1000)
            await self._audit(
                repository.id, kind, "invalid_response", "invalid_response", context, latency_ms
            )
            raise LensProviderFailure(
                "invalid_response",
                "The LLM selected no valid evidence from the context.",
            )

        latency_ms = int((time.perf_counter() - start) * 1000)
        await self._audit(
            repository.id, kind, response_status, error_kind, context, latency_ms
        )
        return LensResponse(
            kind=LensKind(context.kind),
            provider=provider.name,
            model=provider.model,
            prompt_version=self.prompt_version,
            summary=parsed["summary"],
            evidence=selected,
            uncertainty=parsed["uncertainty"],
            suggested_checks=parsed["suggested_checks"],
        )

    # --- internal -------------------------------------------------------------

    @staticmethod
    def _select_evidence(
        evidence: list[LensEvidenceInfo], indices: list[int]
    ) -> list[LensEvidenceInfo]:
        by_index = {ev.index: ev for ev in evidence}
        selected: list[LensEvidenceInfo] = []
        for index in indices:
            if isinstance(index, bool):
                continue
            ev = by_index.get(index)
            if ev is not None and len(selected) < MAX_SELECTED_EVIDENCE:
                selected.append(ev)
        return selected

    async def _audit(
        self,
        repository_id: int,
        kind: str,
        response_status: str,
        error_kind: str | None,
        context: LensContext,
        latency_ms: int,
    ) -> None:
        provider = self.provider
        from app.models.orm import LensAudit

        self.db.add(
            LensAudit(
                repository_id=repository_id,
                kind=kind,
                provider=provider.name if provider else "none",
                model=provider.model if provider else "none",
                prompt_version=self.prompt_version,
                context_hash=context.digest(self.settings),
                response_status=response_status,
                error_kind=error_kind,
                latency_ms=latency_ms,
            )
        )
        await self.db.commit()