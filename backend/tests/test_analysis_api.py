"""API tests for the Phase 2 parse/analysis endpoints (no network required)."""
from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base, get_db
from app.main import app
from app.models.orm import Repository


@pytest.fixture
async def api_env(tmp_path):
    """API client + session maker over one isolated SQLite database."""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'api.db'}", echo=False
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
        yield client, maker
    app.dependency_overrides.pop(get_db, None)
    await engine.dispose()


@pytest.fixture
def fake_remote(sample_repo, monkeypatch):
    """Copy the fixture repository into thought fake clones (no git/network)."""
    import os
    import shutil

    fixture_root = Path(sample_repo())

    def fake_acquire(owner, name, destination: Path) -> tuple[str, str]:
        destination.mkdir(parents=True, exist_ok=True)
        for entry in os.listdir(fixture_root):
            src = fixture_root / entry
            dst = destination / entry
            if src.is_dir():
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dst)
        return "main", "a" * 40

    monkeypatch.setattr(
        "app.repositories.acquisition.acquire_repository", fake_acquire
    )
    return fixture_root


async def _ingest(client) -> int:
    resp = await client.post(
        "/api/repositories", json={"url": "https://github.com/example/sample"}
    )
    assert resp.status_code == 201
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_parse_endpoint_returns_counts(api_env, fake_remote) -> None:
    client, _ = api_env
    repo_id = await _ingest(client)

    resp = await client.post(f"/api/repositories/{repo_id}/parse")
    assert resp.status_code == 200
    body = resp.json()
    assert body["analyzed"] is True
    assert body["symbol_count"] > 0
    assert body["import_count"] > 0
    assert body["parser_versions"]["python"].startswith("tree-sitter-python")
    assert body["statuses"]["unsupported"] >= 0
    assert body["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_parse_missing_repository_404(api_env) -> None:
    client, _ = api_env
    for method, path in (
        ("post", "/api/repositories/4242/parse"),
        ("get", "/api/repositories/4242/analysis"),
        ("get", "/api/repositories/4242/symbols"),
        ("get", "/api/repositories/4242/imports"),
        ("get", "/api/repositories/4242/exports"),
    ):
        resp = await getattr(client, method)(path)
        assert resp.status_code == 404, (method, path)


@pytest.mark.asyncio
async def test_parse_not_ready_409(api_env) -> None:
    client, maker = api_env
    async with maker() as session:
        session.add(
            Repository(
                url="https://github.com/example/pending",
                owner="example",
                name="pending",
                status="indexing",
                local_path=None,
            )
        )
        await session.commit()

    resp = await client.post("/api/repositories/1/parse")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_analysis_summary_endpoint(api_env, fake_remote) -> None:
    client, _ = api_env
    repo_id = await _ingest(client)
    await client.post(f"/api/repositories/{repo_id}/parse")

    resp = await client.get(f"/api/repositories/{repo_id}/analysis")
    assert resp.status_code == 200
    body = resp.json()
    assert body["analyzed"] is True
    assert body["file_count"] == body["files_processed"]


@pytest.mark.asyncio
async def test_symbol_filters(api_env, fake_remote) -> None:
    client, _ = api_env
    repo_id = await _ingest(client)
    await client.post(f"/api/repositories/{repo_id}/parse")

    all_syms = (await client.get(f"/api/repositories/{repo_id}/symbols")).json()
    assert all_syms["total"] > 0

    functions = (
        await client.get(f"/api/repositories/{repo_id}/symbols?kind=function")
    ).json()
    assert functions["total"] > 0
    assert all(item["kind"] == "FUNCTION" for item in functions["items"])

    auth = (
        await client.get(f"/api/repositories/{repo_id}/symbols?file=auth%2F")
    ).json()
    assert auth["total"] > 0
    assert all("auth/" in item["file_path"] for item in auth["items"])

    py = (
        await client.get(f"/api/repositories/{repo_id}/symbols?language=python")
    ).json()
    assert py["total"] > 0
    assert all(item["language"] == "python" for item in py["items"])


@pytest.mark.asyncio
async def test_imports_and_exports_endpoints(api_env, fake_remote) -> None:
    client, _ = api_env
    repo_id = await _ingest(client)
    await client.post(f"/api/repositories/{repo_id}/parse")

    imports = (await client.get(f"/api/repositories/{repo_id}/imports")).json()
    assert isinstance(imports, list)
    assert imports[0]["file_path"].startswith("web/") or imports[0]["file_path"].startswith("auth/") or imports[0]["file_path"].startswith("tests/")

    exports = (await client.get(f"/api/repositories/{repo_id}/exports")).json()
    assert len(exports) >= 2  # App.tsx export, app.ts export const x


@pytest.mark.asyncio
async def test_content_endpoint_and_escapes(api_env, fake_remote) -> None:
    client, _ = api_env
    repo_id = await _ingest(client)
    await client.post(f"/api/repositories/{repo_id}/parse")

    content = (
        await client.get(
            f"/api/repositories/{repo_id}/content", params={"path": "auth/service.py"}
        )
    ).json()
    assert content["language"] == "Python"
    assert "AuthService" in content["content"]

    for evil in ("../secrets.txt", "/etc/passwd", "auth/../../etc/passwd"):
        resp = await client.get(
            f"/api/repositories/{repo_id}/content", params={"path": evil}
        )
        assert resp.status_code == 400, evil

    missing = await client.get(
        f"/api/repositories/{repo_id}/content", params={"path": "nope.py"}
    )
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_reparse_keeps_counts_stable(api_env, fake_remote) -> None:
    client, _ = api_env
    repo_id = await _ingest(client)
    first = (await client.post(f"/api/repositories/{repo_id}/parse")).json()
    second = (await client.post(f"/api/repositories/{repo_id}/parse")).json()
    assert second["symbol_count"] == first["symbol_count"]
    assert second["files_processed"] == first["files_processed"]