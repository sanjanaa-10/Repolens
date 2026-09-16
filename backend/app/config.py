"""Application configuration for RepoLens.

Settings are loaded from environment variables (prefixed REPOLENS_) or a
local .env file. Every value has a sane local-development default.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Anchor all paths to the backend directory so nothing depends on the CWD of
# the server process (which can vary by launch method and platform).
BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Runtime configuration for the RepoLens backend."""

    app_name: str = "RepoLens"
    version: str = "0.1.0"
    debug: bool = True

    # Where cloned repositories and the SQLite database live.
    repo_storage_dir: Path = BACKEND_DIR / "data" / "repositories"
    db_path: Path = BACKEND_DIR / "data" / "repolens.db"

    # Isolation / security limits applied to untrusted repository input.
    clone_timeout_seconds: int = 120
    max_file_size_bytes: int = 512 * 1024  # 512 KB per source file
    max_files_per_repo: int = 10000
    max_repo_size_bytes: int = 200 * 1024 * 1024  # 200 MB clone cap

    # Optional AI layer (Phase 8 Lens). If unset, the deterministic core
    # still works and Lens reports itself as unavailable.
    ai_provider: str | None = None
    ai_api_key: str | None = None
    ai_model: str = "gpt-4o-mini"
    ai_base_url: str | None = None  # OpenAI-compatible base URL override

    # Lens explanation bounds. These keep the deterministic evidence context
    # small and predictable; they never change what RepoLens finds.
    lens_timeout_seconds: int = 20
    lens_max_context_chars: int = 12_000
    lens_max_source_snippet_chars: int = 800
    lens_max_impact_paths_in_context: int = 6
    lens_prompt_version: str = "1"

    # Host header allowlist (TrustedHostMiddleware). Deployments behind a
    # reverse proxy must set REPOLENS_TRUSTED_HOSTS to their public hostnames.
    trusted_hosts: list[str] = ["localhost", "127.0.0.1", "test"]

    model_config = SettingsConfigDict(
        env_prefix="REPOLENS_",
        env_file=BACKEND_DIR / ".env",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.repo_storage_dir.mkdir(parents=True, exist_ok=True)
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    return settings
