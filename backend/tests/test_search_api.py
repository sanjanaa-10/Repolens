"""Search API + service tests for Phase 4.

Covers symbol, file, and text search modes; deterministic ranking;
input validation; repository isolation; and pagination.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.analysis.relationships.engine import RelationshipEngine
from app.core.database import Base, get_db
from app.main import app
from app.models.orm import FileRecord, Repository, Symbol
from app.repositories.languages import detect_language


def _build_search_repo(root: Path) -> None:
    """A small repository with symbol/file/text searchable content."""
    (root / "auth").mkdir(parents=True)
    (root / "auth" / "__init__.py").write_text("", encoding="utf-8")
    (root / "auth" / "service.py").write_text(
        "class AuthService:\n"
        "    def validate_token(self, token):\n"
        "        return 'valid'\n"
        "\n"
        "def create_user(name):\n"
        "    return name\n",
        encoding="utf-8",
    )
    (root / "auth" / "controller.py").write_text(
        "from auth.service import AuthService, create_user\n"
        "\n"
        "def login():\n"
        "    svc = AuthService()\n"
        "    return create_user('x')\n",
        encoding="utf-8",
    )
    (root / "users").mkdir()
    (root / "users" / "__init__.py").write_text("", encoding="utf-8")
    (root / "users" / "repository.py").write_text(
        "class UserRepository:\n    pass\n", encoding="utf-8"
    )
    (root / "tests").mkdir()
    (root / "tests" / "test_auth.py").write_text(
        "from auth.service import AuthService\n"
        "def test_auth():\n"
        "    svc = AuthService()\n"
        "    return svc.validate_token('tok')\n",
        encoding="utf-8",
    )


@pytest.fixture
async def search_env(tmp_path):
    """Isolated API environment for search tests."""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'search.db'}", echo=False
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_db():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, maker, tmp_path
    app.dependency_overrides.pop(get_db, None)
    await engine.dispose()


async def _seed_and_parse(maker, root: Path, name: str) -> int:
    """Seed a repository row + files, parse it, build relationships. Returns repo id."""
    from app.analysis.service import AnalysisService

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
        for path in sorted(Path(root).rglob("*")):
            if path.is_file():
                session.add(
                    FileRecord(
                        repository_id=repo.id,
                        path=path.relative_to(root).as_posix(),
                        language=detect_language(path),
                        size_bytes=path.stat().st_size,
                        analyzed=True,
                    )
                )
        await session.commit()
        repo_id = repo.id

    async with maker() as session:
        await AnalysisService(session).parse_repository(repo_id)
    return repo_id


@pytest.mark.asyncio
async def test_symbol_search_exact_and_ranked(search_env, tmp_path) -> None:
    """Symbol search returns the exact name first with correct metadata."""
    client, maker, _ = search_env
    root = tmp_path / "search_fixture"
    _build_search_repo(root)
    repo_id = await _seed_and_parse(maker, root, "search")

    resp = await client.get(
        f"/api/repositories/{repo_id}/search",
        params={"q": "AuthService", "type": "symbol"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["type"] == "symbol"
    assert body["total"] > 0
    # Exact name CLASS definition should rank above the same-named import alias
    first = body["results"][0]
    assert first["name"] == "AuthService"
    assert first["kind"] == "CLASS"
    assert "auth/service.py" in first["file_path"]
    assert first["line_start"] > 0


@pytest.mark.asyncio
async def test_symbol_search_case_insensitive_prefix(search_env, tmp_path) -> None:
    """Symbol search is case-insensitive and prefix-aware."""
    client, maker, _ = search_env
    root = tmp_path / "search_fixture"
    _build_search_repo(root)
    repo_id = await _seed_and_parse(maker, root, "search")

    # Lowercase query still finds the class
    resp = await client.get(
        f"/api/repositories/{repo_id}/search",
        params={"q": "authservice", "type": "symbol"},
    )
    assert resp.status_code == 200
    assert resp.json()["total"] > 0
    names = {r["name"] for r in resp.json()["results"]}
    assert "AuthService" in names


@pytest.mark.asyncio
async def test_file_search_matches_paths(search_env, tmp_path) -> None:
    """File search matches repository-relative paths."""
    client, maker, _ = search_env
    root = tmp_path / "search_fixture"
    _build_search_repo(root)
    repo_id = await _seed_and_parse(maker, root, "search")

    resp = await client.get(
        f"/api/repositories/{repo_id}/search",
        params={"q": "service", "type": "file"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "file"
    paths = {r["path"] for r in body["results"]}
    assert any("service" in p for p in paths)


@pytest.mark.asyncio
async def test_file_search_exact_path_ranks_first(search_env, tmp_path) -> None:
    """Exact path matches rank ahead of substrings."""
    client, maker, _ = search_env
    root = tmp_path / "search_fixture"
    _build_search_repo(root)
    repo_id = await _seed_and_parse(maker, root, "search")

    resp = await client.get(
        f"/api/repositories/{repo_id}/search",
        params={"q": "auth/service.py", "type": "file"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["results"]) > 0
    assert body["results"][0]["path"] == "auth/service.py"


@pytest.mark.asyncio
async def test_text_search_finds_line_snippets(search_env, tmp_path) -> None:
    """Text search returns line-level snippets with line numbers."""
    client, maker, _ = search_env
    root = tmp_path / "search_fixture"
    _build_search_repo(root)
    repo_id = await _seed_and_parse(maker, root, "search")

    resp = await client.get(
        f"/api/repositories/{repo_id}/search",
        params={"q": "validate_token", "type": "text"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "text"
    assert len(body["results"]) > 0
    hit = body["results"][0]
    assert "line_number" in hit and hit["line_number"] > 0
    assert "validate_token" in hit["snippet"]


@pytest.mark.asyncio
async def test_search_invalid_query_rejected(search_env, tmp_path) -> None:
    """Empty, wildcard, and over-long queries are rejected with 400."""
    client, maker, _ = search_env
    root = tmp_path / "search_fixture"
    _build_search_repo(root)
    repo_id = await _seed_and_parse(maker, root, "search")

    resp = await client.get(
        f"/api/repositories/{repo_id}/search", params={"q": "   ", "type": "symbol"}
    )
    assert resp.status_code == 400

    resp = await client.get(
        f"/api/repositories/{repo_id}/search", params={"q": "*", "type": "symbol"}
    )
    assert resp.status_code == 400

    resp = await client.get(
        f"/api/repositories/{repo_id}/search",
        params={"q": "x" * 500, "type": "symbol"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_search_invalid_type_rejected(search_env, tmp_path) -> None:
    """Unknown search types are rejected."""
    client, maker, _ = search_env
    root = tmp_path / "search_fixture"
    _build_search_repo(root)
    repo_id = await _seed_and_parse(maker, root, "search")

    resp = await client.get(
        f"/api/repositories/{repo_id}/search",
        params={"q": "AuthService", "type": "bogus"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_search_isolation_between_repos(search_env, tmp_path) -> None:
    """Search results from one repo should not leak into another."""
    client, maker, _ = search_env
    root = tmp_path / "search_fixture"
    _build_search_repo(root)
    repo_id = await _seed_and_parse(maker, root, "search")

    # A repo with no matching symbols
    empty = tmp_path / "empty_repo"
    empty.mkdir()
    empty_repo_id = await _seed_and_parse(maker, empty, "empty")

    resp = await client.get(
        f"/api/repositories/{repo_id}/search",
        params={"q": "AuthService", "type": "symbol"},
    )
    assert resp.json()["total"] > 0

    resp2 = await client.get(
        f"/api/repositories/{empty_repo_id}/search",
        params={"q": "AuthService", "type": "symbol"},
    )
    assert resp2.json()["total"] == 0


@pytest.mark.asyncio
async def test_search_missing_repo_404(search_env, tmp_path) -> None:
    """Searching an unknown repository returns 404."""
    client, maker, _ = search_env
    resp = await client.get(
        "/api/repositories/99999/search", params={"q": "x", "type": "symbol"}
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_symbol_search_pagination(search_env, tmp_path) -> None:
    """limit/offset support on symbol search."""
    client, maker, _ = search_env
    root = tmp_path / "search_fixture"
    _build_search_repo(root)
    repo_id = await _seed_and_parse(maker, root, "search")

    resp_all = await client.get(
        f"/api/repositories/{repo_id}/search",
        params={"q": "a", "type": "symbol", "limit": 100},
    )
    total = resp_all.json()["total"]

    resp_one = await client.get(
        f"/api/repositories/{repo_id}/search",
        params={"q": "a", "type": "symbol", "limit": 1},
    )
    assert len(resp_one.json()["results"]) == 1

    resp_offset = await client.get(
        f"/api/repositories/{repo_id}/search",
        params={"q": "a", "type": "symbol", "limit": 1, "offset": 1},
    )
    assert len(resp_offset.json()["results"]) == 1


@pytest.mark.asyncio
async def test_text_search_case_sensitive(search_env, tmp_path) -> None:
    """case_sensitive flag changes text search behavior."""
    client, maker, _ = search_env
    root = tmp_path / "search_fixture"
    _build_search_repo(root)
    repo_id = await _seed_and_parse(maker, root, "search")

    # lowercase 'authservice' won't match text case-sensitively
    resp_sensitive = await client.get(
        f"/api/repositories/{repo_id}/search",
        params={"q": "authservice", "type": "text", "case_sensitive": True},
    )
    assert resp_sensitive.json()["total"] == 0

    resp_insensitive = await client.get(
        f"/api/repositories/{repo_id}/search",
        params={"q": "authservice", "type": "text", "case_sensitive": False},
    )
    assert resp_insensitive.json()["total"] > 0
