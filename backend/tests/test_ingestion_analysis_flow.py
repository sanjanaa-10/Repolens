"""Ingestion must make relationship analysis available in the normal flow.

Covers the "Analyze Repository -> Index -> Extract symbols -> Build
relationships -> Ready" contract: a freshly ingested repository is only reported
``ready`` once symbols and relationships exist, and a legacy ready repository
that is missing relationships is completed when it is analyzed again.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.analysis.relationships.engine import RelationshipEngine
from app.core.database import Base, enable_foreign_keys, get_db
from app.main import app
from app.models.orm import FileRecord, Relationship, Repository
from app.repositories.languages import detect_language


def _build_python_repo(root: Path) -> Path:
    """A small Python repository with resolvable imports and calls."""
    (root / "auth").mkdir(parents=True)
    (root / "auth" / "__init__.py").write_text("", encoding="utf-8")
    (root / "auth" / "service.py").write_text(
        "class AuthService:\n"
        "    def validate_token(self, token):\n"
        "        return token\n"
        "\n"
        "def create_user(name):\n"
        "    return name\n",
        encoding="utf-8",
    )
    (root / "auth" / "controller.py").write_text(
        "from auth.service import AuthService, create_user\n"
        "import fastapi\n"
        "\n"
        "def login():\n"
        "    service = AuthService()\n"
        "    return create_user('x')\n",
        encoding="utf-8",
    )
    (root / "tests").mkdir()
    (root / "tests" / "test_auth.py").write_text(
        "from auth.service import AuthService\n"
        "def test_auth_service():\n"
        "    svc = AuthService()\n"
        "    return svc.validate_token('tok')\n",
        encoding="utf-8",
    )
    return root


def _copy_tree(src: Path, dst: Path) -> None:
    for entry in src.iterdir():
        target = dst / entry.name
        if entry.is_dir():
            shutil.copytree(entry, target, dirs_exist_ok=True)
        else:
            shutil.copy2(entry, target)


@pytest.fixture
async def api_env(tmp_path):
    """Isolated API environment with an empty DB (same pattern as the
    relationship-engine suite)."""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'flow.db'}", echo=False
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_db():
        async with maker() as session:
            await enable_foreign_keys(session)
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, maker, tmp_path
    app.dependency_overrides.pop(get_db, None)
    await engine.dispose()


@pytest.mark.asyncio
async def test_fresh_ingestion_runs_parse_and_relationships_before_ready(
    api_env, monkeypatch
) -> None:
    """A new repository is only 'ready' after symbols and relationships exist."""
    client, maker, tmp_path = api_env
    remote = _build_python_repo(tmp_path / "remote")

    def fake_acquire(owner, name, destination: Path):
        destination.mkdir(parents=True, exist_ok=True)
        _copy_tree(remote, destination)
        return "main", "0123456789abcdef0123456789abcdef01234567"

    monkeypatch.setattr(
        "app.repositories.acquisition.acquire_repository", fake_acquire
    )

    statuses_during_relationships: list[str] = []
    real_build = RelationshipEngine.build_relationships

    async def recording_build(self, repository_id: int):
        repo = await self.db.get(Repository, repository_id)
        statuses_during_relationships.append(repo.status)
        return await real_build(self, repository_id)

    monkeypatch.setattr(
        "app.services.ingestion_service.RelationshipEngine.build_relationships",
        recording_build,
    )

    resp = await client.post(
        "/api/repositories", json={"url": "https://github.com/example/flow"}
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "ready"
    assert body["relationship_count"] > 0

    # The relationship graph was built BEFORE the repository was marked ready.
    assert statuses_during_relationships == ["indexing"]

    rid = body["id"]

    # Relationship API servers the same graph the overview advertises.
    rel = await client.get(f"/api/repositories/{rid}/relationships")
    assert rel.status_code == 200
    assert rel.json()["total"] == body["relationship_count"]

    async with maker() as session:
        repo = await session.get(Repository, rid)
        assert repo is not None
        assert repo.analyzed is True
        assert repo.symbol_count > 0
        assert repo.relationship_count == body["relationship_count"]
        stored = (
            await session.execute(
                select(func.count()).select_from(Relationship).where(
                    Relationship.repository_id == rid
                )
            )
        ).scalar_one()
        assert stored == body["relationship_count"]


@pytest.mark.asyncio
async def test_reanalyze_existing_ready_repo_builds_missing_relationships(
    api_env, monkeypatch, tmp_path
) -> None:
    """A legacy detected 'ready' repo missing relationships is completed when
    the user analyzes it again (the psf/requests scenario)."""
    client, maker, tmp_path = api_env
    local = _build_python_repo(tmp_path / "existing")

    # Seed the exact legacy state: ready + parsed, but no relationships.
    async with maker() as session:
        repo = Repository(
            url="https://github.com/example/legacy",
            owner="example",
            name="legacy",
            status="ready",
            local_path=str(local),
        )
        session.add(repo)
        await session.flush()
        for path in sorted(local.rglob("*")):
            if path.is_file():
                session.add(
                    FileRecord(
                        repository_id=repo.id,
                        path=path.relative_to(local).as_posix(),
                        language=detect_language(path),
                        size_bytes=path.stat().st_size,
                        analyzed=True,
                    )
                )
        await session.commit()
        repo_id = repo.id

    from app.analysis.service import AnalysisService

    async with maker() as session:
        await AnalysisService(session).parse_repository(repo_id)

    async with maker() as session:
        repo = await session.get(Repository, repo_id)
        assert repo.analyzed is True
        assert repo.relationship_count == 0

    # Analyzing the same URL again returns the existing repository but now
    # completes the missing relationship graph.
    resp = await client.post(
        "/api/repositories", json={"url": "https://github.com/example/legacy"}
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["id"] == repo_id
    assert body["relationship_count"] > 0

    async with maker() as session:
        count = (
            await session.execute(
                select(func.count()).select_from(Relationship).where(
                    Relationship.repository_id == repo_id
                )
            )
        ).scalar_one()
        assert count == body["relationship_count"]


@pytest.mark.asyncio
async def test_reanalyze_complete_repo_is_a_fast_nop(api_env, monkeypatch, tmp_path) -> None:
    """An already-complete repository stays untouched on re-analysis."""
    client, maker, tmp_path = api_env
    local = _build_python_repo(tmp_path / "complete")

    async with maker() as session:
        repo = Repository(
            url="https://github.com/example/complete",
            owner="example",
            name="complete",
            status="ready",
            local_path=str(local),
        )
        session.add(repo)
        await session.flush()
        for path in sorted(local.rglob("*")):
            if path.is_file():
                session.add(
                    FileRecord(
                        repository_id=repo.id,
                        path=path.relative_to(local).as_posix(),
                        language=detect_language(path),
                        size_bytes=path.stat().st_size,
                        analyzed=True,
                    )
                )
        await session.commit()
        repo_id = repo.id

    from app.analysis.service import AnalysisService

    async with maker() as session:
        await AnalysisService(session).parse_repository(repo_id)
    async with maker() as session:
        await RelationshipEngine(session).build_relationships(repo_id)

    async with maker() as session:
        repo = await session.get(Repository, repo_id)
        before_symbols = repo.symbol_count
        before_relationships = repo.relationship_count

    resp = await client.post(
        "/api/repositories", json={"url": "https://github.com/example/complete"}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] == repo_id
    assert body["relationship_count"] == before_relationships

    async with maker() as session:
        repo = await session.get(Repository, repo_id)
        assert repo.symbol_count == before_symbols
        assert repo.relationship_count == before_relationships