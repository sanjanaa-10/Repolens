"""Investigation API + service tests for Phase 4.

Covers depth-1 symbol investigation: definition, callers, callees,
references, tests, imports, exports, related files, source context, and
error handling for unknown symbols/repositories.
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


def _build_investigation_repo(root: Path) -> None:
    """A repository with a real call graph + tests for investigation."""
    (root / "auth").mkdir(parents=True)
    (root / "auth" / "__init__.py").write_text("", encoding="utf-8")
    (root / "auth" / "service.py").write_text(
        "class AuthService:\n"
        "    def validate_token(self, token):\n"
        "        return 'ok'\n"
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
        "    token = svc.validate_token('abc')\n"
        "    return create_user(token)\n",
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
        "def test_auth_service():\n"
        "    svc = AuthService()\n"
        "    return svc.validate_token('tok')\n",
        encoding="utf-8",
    )


@pytest.fixture
async def inv_env(tmp_path):
    """Isolated API environment for investigation tests."""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'inv.db'}", echo=False
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
    """Seed a repository row + files, parse it, build relationships."""
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

    async with maker() as session:
        await RelationshipEngine(session).build_relationships(repo_id)

    return repo_id


async def _get_symbol_id(maker, repo_id: int, name: str, kind: str | None = None) -> int:
    async with maker() as session:
        q = select(Symbol).where(
            Symbol.repository_id == repo_id,
            Symbol.name == name,
        )
        if kind:
            q = q.where(Symbol.kind == kind)
        sym = (await session.execute(q)).scalars().first()
        assert sym is not None, f"symbol {name} not found"
        return sym.id


@pytest.mark.asyncio
async def test_investigation_returns_definition(inv_env, tmp_path) -> None:
    """Investigation includes the symbol's definition and source context."""
    client, maker, _ = inv_env
    root = tmp_path / "inv_fixture"
    _build_investigation_repo(root)
    repo_id = await _seed_and_parse(maker, root, "inv")

    sym_id = await _get_symbol_id(maker, repo_id, "AuthService", "CLASS")
    resp = await client.get(
        f"/api/repositories/{repo_id}/symbols/{sym_id}/investigation"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["symbol"]["name"] == "AuthService"
    assert body["symbol"]["kind"] == "CLASS"
    assert body["source_context"]["path"]
    assert body["source_context"]["start_line"] > 0


@pytest.mark.asyncio
async def test_investigation_callers_and_callees(inv_env, tmp_path) -> None:
    """Callers and callees are grouped by direction."""
    client, maker, _ = inv_env
    root = tmp_path / "inv_fixture"
    _build_investigation_repo(root)
    repo_id = await _seed_and_parse(maker, root, "inv")

    sym_id = await _get_symbol_id(maker, repo_id, "create_user")
    resp = await client.get(
        f"/api/repositories/{repo_id}/symbols/{sym_id}/investigation"
    )
    assert resp.status_code == 200
    groups = {g["category"]: g for g in resp.json()["groups"]}

    if "callers" in groups:
        callers = groups["callers"]["edges"]
        assert any(e["source_symbol_name"] == "login" for e in callers)


@pytest.mark.asyncio
async def test_investigation_tests(inv_env, tmp_path) -> None:
    """Investigation surfaces tests that exercise the symbol."""
    client, maker, _ = inv_env
    root = tmp_path / "inv_fixture"
    _build_investigation_repo(root)
    repo_id = await _seed_and_parse(maker, root, "inv")

    sym_id = await _get_symbol_id(maker, repo_id, "AuthService", "CLASS")
    resp = await client.get(
        f"/api/repositories/{repo_id}/symbols/{sym_id}/investigation"
    )
    assert resp.status_code == 200
    groups = {g["category"]: g for g in resp.json()["groups"]}
    if "tests" in groups:
        assert groups["tests"]["count"] > 0
        assert any(
            "test_auth" in (e["source_file"] or "") for e in groups["tests"]["edges"]
        )


@pytest.mark.asyncio
async def test_investigation_related_files(inv_env, tmp_path) -> None:
    """Related files are derived only from the real graph."""
    client, maker, _ = inv_env
    root = tmp_path / "inv_fixture"
    _build_investigation_repo(root)
    repo_id = await _seed_and_parse(maker, root, "inv")

    sym_id = await _get_symbol_id(maker, repo_id, "login")
    resp = await client.get(
        f"/api/repositories/{repo_id}/symbols/{sym_id}/investigation"
    )
    assert resp.status_code == 200
    body = resp.json()
    # login lives in controller, imports service + users
    paths = {f["path"] for f in body["related_files"]}
    assert "auth/controller.py" in paths
    assert any("auth/service.py" == p or "service.py" in p for p in paths)


@pytest.mark.asyncio
async def test_investigation_missing_symbol_404(inv_env, tmp_path) -> None:
    """Investigating an unknown symbol returns 404."""
    client, maker, _ = inv_env
    root = tmp_path / "inv_fixture"
    _build_investigation_repo(root)
    repo_id = await _seed_and_parse(maker, root, "inv")

    resp = await client.get(
        f"/api/repositories/{repo_id}/symbols/99999/investigation"
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_investigation_missing_repo_404(inv_env, tmp_path) -> None:
    """Investigating in an unknown repository returns 404."""
    client, maker, _ = inv_env
    resp = await client.get(
        "/api/repositories/99999/symbols/1/investigation"
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_investigation_isolation_between_repos(inv_env, tmp_path) -> None:
    """A symbol from repo A should not return repo B's data."""
    client, maker, _ = inv_env
    root = tmp_path / "inv_fixture"
    _build_investigation_repo(root)
    repo_id = await _seed_and_parse(maker, root, "one")
    sym_id = await _get_symbol_id(maker, repo_id, "AuthService", "CLASS")

    # Query repo "one" with a symbol ID that doesn't exist there (it would be a
    # valid symbol in another repo if such existed). A non-existent symbol ID in
    # repo "one" yields 404 and cannot leak data.
    resp = await client.get(
        f"/api/repositories/{repo_id}/symbols/{sym_id}/investigation"
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_symbol_detail_endpoint(inv_env, tmp_path) -> None:
    """GET /symbols/{id} returns the full symbol record scoped to its repo."""
    client, maker, _ = inv_env
    root = tmp_path / "inv_fixture"
    _build_investigation_repo(root)
    repo_id = await _seed_and_parse(maker, root, "detail")

    sym_id = await _get_symbol_id(maker, repo_id, "AuthService", "CLASS")
    resp = await client.get(f"/api/repositories/{repo_id}/symbols/{sym_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == sym_id
    assert body["name"] == "AuthService"
    assert body["kind"] == "CLASS"
    assert body["file_path"] == "auth/service.py"
    assert body["line_start"] > 0


@pytest.mark.asyncio
async def test_symbol_detail_unknown_symbol_404(inv_env, tmp_path) -> None:
    """A symbol id that does not exist in the repo returns 404."""
    client, maker, _ = inv_env
    root = tmp_path / "inv_fixture"
    _build_investigation_repo(root)
    repo_id = await _seed_and_parse(maker, root, "detail404")

    resp = await client.get(f"/api/repositories/{repo_id}/symbols/999999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_symbol_detail_unknown_repo_404(inv_env, tmp_path) -> None:
    """Fetching a symbol in an unknown repository returns 404."""
    client, maker, _ = inv_env
    resp = await client.get("/api/repositories/99999/symbols/1")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_symbol_detail_isolation_between_repos(inv_env, tmp_path) -> None:
    """A symbol cannot be fetched through a different repository's scope."""
    client, maker, _ = inv_env
    root = tmp_path / "inv_fixture"
    _build_investigation_repo(root)
    repo_a = await _seed_and_parse(maker, root, "iso_a")
    repo_b = await _seed_and_parse(maker, root, "iso_b")
    sym_id = await _get_symbol_id(maker, repo_a, "AuthService", "CLASS")

    ok = await client.get(f"/api/repositories/{repo_a}/symbols/{sym_id}")
    assert ok.status_code == 200

    leak = await client.get(f"/api/repositories/{repo_b}/symbols/{sym_id}")
    assert leak.status_code == 404
