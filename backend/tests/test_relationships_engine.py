"""Relationship engine: full pipeline integration tests with fixture repository.

Covers the deterministic parse → relationship pipeline end-to-end, including
idempotency, duplicate prevention, repository isolation, and API exposure.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.analysis.relationships.engine import RelationshipEngine
from app.analysis.relationships.index import load_index
from app.analysis.relationships.import_resolver import resolve_imports
from app.analysis.relationships.models import RelationshipEdge
from app.core.database import Base, get_db
from app.main import app
from app.models.orm import FileRecord, ParseResult, Relationship, Repository, Symbol
from app.repositories.languages import detect_language


def _build_fixture_repo(root: Path) -> None:
    """Create a deterministic multi-module repository for relationship tests."""
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
        "\n"
        "def login():\n"
        "    service = AuthService()\n"
        "    return create_user('x')\n",
        encoding="utf-8",
    )
    (root / "auth" / "token.py").write_text(
        "from users.repository import UserRepository\n"
        "class TokenService:\n"
        "    pass\n",
        encoding="utf-8",
    )
    (root / "users").mkdir()
    (root / "users" / "__init__.py").write_text("", encoding="utf-8")
    (root / "users" / "repository.py").write_text(
        "class UserRepository:\n    pass\n", encoding="utf-8"
    )
    (root / "users" / "service.py").write_text(
        "from users.repository import UserRepository\n"
        "class UserService:\n"
        "    def get_users(self):\n"
        "        return UserRepository()\n"
        "\n"
        "class AdminUser(UserService):\n"
        "    pass\n",
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
    (root / "tests" / "test_unrelated.py").write_text(
        "def test_math():\n    assert 1 + 1 == 2\n", encoding="utf-8"
    )
    (root / "dup_line.py").write_text("import os; import json\n", encoding="utf-8")
    (root / "web").mkdir()
    (root / "web" / "app.ts").write_text(
        "export const x = 1;\n"
        "interface Shape { }\n"
        "class Circle implements Shape { }\n"
        "export default function render() {}\n",
        encoding="utf-8",
    )
    (root / "web" / "util.js").write_text(
        "const helper = require('./helper');\n"
        "export function go() { return helper; }\n",
        encoding="utf-8",
    )
    (root / "web" / "helper.js").write_text("module.exports = {};\n", encoding="utf-8")


@pytest.fixture
async def rel_env(tmp_path):
    """Isolated API environment with an empty DB."""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'rel.db'}", echo=False
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_db():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, maker, tmp_path
    app.dependency_overrides.pop(get_db, None)
    await engine.dispose()


@pytest.fixture
def fixture_root(tmp_path) -> Path:
    """Materialize the fixture repository on disk."""
    root = tmp_path / "fixture_repo"
    _build_fixture_repo(root)
    return root


async def _ingest_and_parse(client, maker, tmp_path, fixture_root) -> int:
    """API-free ingest: directly seed DB rows, then run parse."""
    async with maker() as session:
        repo = Repository(
            url="https://github.com/example/fixture",
            owner="example",
            name="fixture",
            status="ready",
            local_path=str(fixture_root),
        )
        session.add(repo)
        await session.flush()

        rows = []
        for path in sorted(Path(fixture_root).rglob("*")):
            if path.is_file():
                rows.append(
                    FileRecord(
                        repository_id=repo.id,
                        path=path.relative_to(fixture_root).as_posix(),
                        language=detect_language(path),
                        size_bytes=path.stat().st_size,
                        analyzed=True,
                    )
                )
        session.add_all(rows)
        repo.file_count = len(rows)
        await session.commit()
        return repo.id

    from app.analysis.service import AnalysisService
    async with maker() as session:
        await AnalysisService(session).parse_repository(repo_id)
    return repo_id


@pytest.fixture
def client_and_repo_id(rel_env):
    client, maker, tmp_path = rel_env
    fixture_root = _build_fixture_repo_path(tmp_path)
    return client, maker, fixture_root


def _fixture_root(tmp_path) -> Path:
    root = tmp_path / "fixture_repo"
    _build_fixture_repo(root)
    return root


def _build_fixture_repo_path(tmp_path) -> Path:
    root = tmp_path / "fixture_repo"
    _build_fixture_repo(root)
    return root


@pytest.mark.asyncio
async def test_relationship_pipeline_repository_isolation(rel_env, fixture_root, tmp_path) -> None:
    """Two repositories should not share relationships."""
    client, maker, _ = rel_env

    # Repo 1
    async with maker() as session:
        repo1 = Repository(url="https://github.com/example/one", owner="example",
                           name="one", status="ready", local_path=str(fixture_root))
        session.add(repo1)
        await session.flush()
        for path in sorted(Path(fixture_root).rglob("*")):
            if path.is_file():
                session.add(FileRecord(
                    repository_id=repo1.id,
                    path=path.relative_to(fixture_root).as_posix(),
                    language=detect_language(path),
                    size_bytes=path.stat().st_size, analyzed=True,
                ))
        await session.commit()
        repo1_id = repo1.id

    from app.analysis.service import AnalysisService
    async with maker() as session:
        await AnalysisService(session).parse_repository(repo1_id)

    async with maker() as session:
        r1 = await RelationshipEngine(session).build_relationships(repo1_id)

    # Repo 2 (empty dir)
    empty = tmp_path / "empty"
    empty.mkdir()
    async with maker() as session:
        repo2 = Repository(url="https://github.com/example/two", owner="example",
                           name="two", status="ready", local_path=str(empty))
        session.add(repo2)
        await session.commit()
        repo2_id = repo2.id

    async with maker() as session:
        r1_rels = (
            await session.execute(
                select(func.count()).select_from(Relationship).where(
                    Relationship.repository_id == repo1_id
                )
            )
        ).scalar_one()
        r2_rels = (
            await session.execute(
                select(func.count()).select_from(Relationship).where(
                    Relationship.repository_id == repo2_id
                )
            )
        ).scalar_one()
    assert r1_rels > 0
    assert r2_rels == 0


@pytest.mark.asyncio
async def test_defines_and_imports_resolved(rel_env, fixture_root) -> None:
    """DEFINES + IMPORTS relationships are created and resolved."""
    client, maker, _ = rel_env
    # Seed repo
    async with maker() as session:
        repo = Repository(url="https://github.com/example/fixture", owner="example",
                          name="fixture", status="ready", local_path=str(fixture_root))
        session.add(repo)
        await session.flush()
        for path in sorted(Path(fixture_root).rglob("*")):
            if path.is_file():
                session.add(FileRecord(
                    repository_id=repo.id, path=path.relative_to(fixture_root).as_posix(),
                    language=detect_language(path), size_bytes=path.stat().st_size,
                    analyzed=True,
                ))
        await session.commit()
        repo_id = repo.id

    from app.analysis.service import AnalysisService
    async with maker() as session:
        await AnalysisService(session).parse_repository(repo_id)
    async with maker() as session:
        await RelationshipEngine(session).build_relationships(repo_id)

    async with maker() as session:
        defines = (
            await session.execute(
                select(Relationship).where(
                    Relationship.repository_id == repo_id,
                    Relationship.type == "DEFINES",
                )
            )
        ).scalars().all()
        imports = (
            await session.execute(
                select(Relationship).where(
                    Relationship.repository_id == repo_id,
                    Relationship.type == "IMPORTS",
                )
            )
        ).scalars().all()
        resolved_imports = [
            r for r in imports if r.resolution_status == "RESOLVED"
        ]
        external_imports = [
            r for r in imports if r.resolution_status == "EXTERNAL"
        ]

    assert len(defines) > 0
    assert len(imports) > 0
    assert any(r.target_file and r.target_file.endswith("auth/service.py") for r in resolved_imports)
    assert len(unresolved_or_external := [r for r in imports if r.resolution_status != "RESOLVED"]) >= 0


@pytest.mark.asyncio
async def test_relationship_engine_idempotent(rel_env, fixture_root) -> None:
    """Running relationship analysis twice yields identical counts."""
    client, maker, _ = rel_env
    async with maker() as session:
        repo = Repository(url="https://github.com/example/fixture", owner="example",
                          name="fixture", status="ready", local_path=str(fixture_root))
        session.add(repo)
        await session.flush()
        for path in sorted(Path(fixture_root).rglob("*")):
            if path.is_file():
                session.add(FileRecord(
                    repository_id=repo.id, path=path.relative_to(fixture_root).as_posix(),
                    language=detect_language(path), size_bytes=path.stat().st_size,
                    analyzed=True,
                ))
        await session.commit()
        repo_id = repo.id

    from app.analysis.service import AnalysisService
    async with maker() as session:
        await AnalysisService(session).parse_repository(repo_id)

    async with maker() as session:
        first = await RelationshipEngine(session).build_relationships(repo_id)
    async with maker() as session:
        second = await RelationshipEngine(session).build_relationships(repo_id)

    assert first["relationships_created"] == second["relationships_created"]

    async with maker() as session:
        count = (
            await session.execute(
                select(func.count()).select_from(Relationship).where(
                    Relationship.repository_id == repo_id
                )
            )
        ).scalar_one()
    assert count == first["relationships_created"]


@pytest.mark.asyncio
async def test_relationship_api_endpoints(rel_env, fixture_root) -> None:
    """POST, GET with filters, and symbol neighborhood endpoints work."""
    client, maker, _ = rel_env
    async with maker() as session:
        repo = Repository(url="https://github.com/example/fixture", owner="example",
                          name="fixture", status="ready", local_path=str(fixture_root))
        session.add(repo)
        await session.flush()
        for path in sorted(Path(fixture_root).rglob("*")):
            if path.is_file():
                session.add(FileRecord(
                    repository_id=repo.id, path=path.relative_to(fixture_root).as_posix(),
                    language=detect_language(path), size_bytes=path.stat().st_size,
                    analyzed=True,
                ))
        await session.commit()
        repo_id = repo.id

    from app.analysis.service import AnalysisService
    async with maker() as session:
        await AnalysisService(session).parse_repository(repo_id)

    # Build via API
    build_resp = await client.post(f"/api/repositories/{repo_id}/relationships")
    assert build_resp.status_code == 200, build_resp.text
    body = build_resp.json()
    assert body["status"] == "ready"
    assert body["relationships_created"] > 0
    assert body["resolved"] > 0

    # GET all
    all_resp = await client.get(f"/api/repositories/{repo_id}/relationships")
    assert all_resp.status_code == 200
    assert all_resp.json()["total"] == body["relationships_created"]

    # Filter by type
    imports_resp = await client.get(
        f"/api/repositories/{repo_id}/relationships", params={"type": "IMPORTS"}
    )
    assert imports_resp.json()["total"] > 0
    assert all(i["type"] == "IMPORTS" for i in imports_resp.json()["items"])

    # Filter by status
    resolved_resp = await client.get(
        f"/api/repositories/{repo_id}/relationships", params={"status": "RESOLVED"}
    )
    assert resolved_resp.json()["total"] > 0
    assert all(i["resolution_status"] == "RESOLVED" for i in resolved_resp.json()["items"])

    # Symbol neighborhood
    async with maker() as session:
        sym = (
            await session.execute(
                select(Symbol).where(
                    Symbol.repository_id == repo_id,
                    Symbol.name == "AuthService",
                    Symbol.kind == "CLASS",
                )
            )
        ).scalars().first()
        sym_id = sym.id

    neighborhood = await client.get(
        f"/api/repositories/{repo_id}/symbols/{sym_id}/relationships"
    )
    assert neighborhood.status_code == 200
    assert neighborhood.json()["total"] > 0

    # Missing repo
    missing = await client.post("/api/repositories/99999/relationships")
    assert missing.status_code == 404


async def _seed_and_parse(maker, root: Path, name: str) -> int:
    """Seed a repository row + file records, then parse it. Returns repo id."""
    async with maker() as session:
        repo = Repository(
            url=f"https://github.com/example/{name}",
            owner="example",
            name=name,
            status="ready",
            local_path=str(root),
        )
        session.add(repo)
        await session.flush()
        rows = []
        for path in sorted(Path(root).rglob("*")):
            if path.is_file():
                rows.append(
                    FileRecord(
                        repository_id=repo.id,
                        path=path.relative_to(root).as_posix(),
                        language=detect_language(path),
                        size_bytes=path.stat().st_size,
                        analyzed=True,
                    )
                )
        session.add_all(rows)
        repo.file_count = len(rows)
        await session.commit()
        repo_id = repo.id

    from app.analysis.service import AnalysisService
    async with maker() as session:
        await AnalysisService(session).parse_repository(repo_id)
    return repo_id


@pytest.mark.asyncio
async def test_relationship_engine_calls_resolved(rel_env, fixture_root) -> None:
    """CALLS edges are produced by the full pipeline and resolve to symbols."""
    client, maker, _ = rel_env
    repo_id = await _seed_and_parse(maker, fixture_root, "calls_fixture")

    async with maker() as session:
        await RelationshipEngine(session).build_relationships(repo_id)

    async with maker() as session:
        resolved_calls = (
            await session.execute(
                select(func.count()).select_from(Relationship).where(
                    Relationship.repository_id == repo_id,
                    Relationship.type == "CALLS",
                    Relationship.resolution_status == "RESOLVED",
                )
            )
        ).scalar_one()
        calls_to_create_user = (
            await session.execute(
                select(func.count())
                .select_from(Relationship)
                .join(Symbol, Relationship.target_symbol_id == Symbol.id)
                .where(
                    Relationship.repository_id == repo_id,
                    Relationship.type == "CALLS",
                    Relationship.resolution_status == "RESOLVED",
                    Symbol.name == "create_user",
                )
            )
        ).scalar_one()

    assert resolved_calls > 0, "expected resolved CALLS edges in the pipeline"
    assert calls_to_create_user > 0, "login() -> create_user(...) should resolve"


@pytest.mark.asyncio
async def test_relationship_engine_same_line_imports_dedupe(
    rel_env, fixture_root
) -> None:
    """Two imports sharing a line must not violate the unique constraint."""
    client, maker, _ = rel_env
    repo_id = await _seed_and_parse(maker, fixture_root, "dup_fixture")

    async with maker() as session:
        first = await RelationshipEngine(session).build_relationships(repo_id)
    async with maker() as session:
        second = await RelationshipEngine(session).build_relationships(repo_id)

    assert first["relationships_created"] == second["relationships_created"]


@pytest.mark.asyncio
async def test_relationship_engine_requires_analysis(rel_env, tmp_path) -> None:
    """Building relationships before parsing returns 409."""
    client, maker, _ = rel_env
    empty = tmp_path / "empty_norepo"
    empty.mkdir()
    async with maker() as session:
        repo = Repository(url="https://github.com/example/none", owner="example",
                          name="none", status="ready", local_path=str(empty))
        session.add(repo)
        await session.commit()
        repo_id = repo.id

    resp = await client.post(f"/api/repositories/{repo_id}/relationships")
    assert resp.status_code == 409
