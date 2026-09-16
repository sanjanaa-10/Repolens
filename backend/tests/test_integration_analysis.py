"""Integration test: clone + full parse of a real public repository.

Requires network access; excluded from default runs by the ``integration``
marker. Verifies the whole Phase 2 pipeline against real-world source.
"""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.analysis.service import AnalysisService
from app.config import get_settings
from app.core.database import Base
from app.models.orm import FileRecord, Repository
from app.repositories.acquisition import acquire_repository
from app.repositories.discovery import discover_files


@pytest.mark.integration
@pytest.mark.asyncio
async def test_parse_real_itsdangerous(tmp_path) -> None:
    settings = get_settings()
    workspace = tmp_path / "repo"
    branch, commit = acquire_repository("pallets", "itsdangerous", workspace)
    assert branch and commit

    result = discover_files(
        workspace,
        max_file_size_bytes=settings.max_file_size_bytes,
        max_files=settings.max_files_per_repo,
        max_repo_bytes=settings.max_repo_size_bytes,
    )
    assert result.files

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'analysis.db'}", echo=False
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as session:
        repo = Repository(
            url="https://github.com/pallets/itsdangerous",
            owner="pallets",
            name="itsdangerous",
            status="ready",
            commit_sha=commit,
            file_count=len(result.files),
            local_path=str(workspace),
        )
        session.add(repo)
        await session.flush()
        session.add_all(
            FileRecord(
                repository_id=repo.id,
                path=entry.relative_path,
                language=entry.language,
                line_count=entry.line_count,
                size_bytes=entry.size_bytes,
                analyzed=entry.analyzable,
            )
            for entry in result.files
        )
        await session.commit()

        summary = await AnalysisService(session).parse_repository(repo.id)

        assert summary["analyzed"] is True
        assert summary["files_processed"] == repo.file_count
        assert summary["symbol_count"] > 200
        assert summary["import_count"] > 100
        assert summary["statuses"]["parsed"] >= 10
        assert summary["statuses"]["unsupported"] > 0
        assert summary["statuses"]["failed"] == 0

    await engine.dispose()