"""Lens AI explanation layer (Phase 8).

Lens is an optional, provider-backed explanation layer strictly on top of
RepoLens' deterministic analysis. It never creates or decides findings: the
server builds bounded, numbered, verified evidence from the same data shown in
the UI, and Lens only explains it. The app works fully without a provider key.
"""

from app.ai.client import (
    LensError,
    LensProviderError,
    LensTimeoutError,
    LLMProvider,
    OpenAICompatibleProvider,
    get_provider,
)
from app.ai.context import (
    LensContext,
    build_change_context,
    build_impact_context,
    build_review_context,
    build_unresolved_context,
)
from app.ai.prompts import InvalidLensResponse, parse_explanation
from app.ai.service import (
    LensProviderFailure,
    LensService,
    LensUnavailableError,
)

__all__ = [
    "LensContext",
    "LensError",
    "LensProviderError",
    "LensProviderFailure",
    "LensService",
    "LensTimeoutError",
    "LensUnavailableError",
    "LLMProvider",
    "OpenAICompatibleProvider",
    "InvalidLensResponse",
    "build_change_context",
    "build_impact_context",
    "build_review_context",
    "build_unresolved_context",
    "get_provider",
    "parse_explanation",
]