"""Integration test: parse a real small repository through the full API pipeline.

Uses a real git clone of pallets/itsdangerous (small, pure-Python) to verify
the complete ingest → parse → query pipeline works end-to-end against real code.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.core.database import Base, get_db
from app.main import app
from pathlib import Path


REPO_URL = "https://github.com/pallets/itsdangerous"


@pytest.fixture
async def real_api_env(tmp_path):
    """API client + session over an isolated DB (no fixture monkeypatching)."""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'integ.db'}", echo=False
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


@pytest.mark.asyncio
@pytest.mark.network
async def test_parse_itsdangerous_end_to_end(real_api_env) -> None:
    """Ingest + parse itsdangerous, then verify counts and data shape."""
    client, _, storage_dir = real_api_env

    settings = get_settings()
    original_storage = settings.repo_storage_dir
    settings.repo_storage_dir = storage_dir
    try:
        resp = await client.post(
            "/api/repositories", json={"url": REPO_URL}
        )
        assert resp.status_code == 201, resp.text
        repo_id = resp.json()["id"]

        parse_resp = await client.post(f"/api/repositories/{repo_id}/parse")
        assert parse_resp.status_code == 200, parse_resp.text
        parse = parse_resp.json()

        assert parse["analyzed"] is True
        assert parse["symbol_count"] > 30
        assert parse["import_count"] > 5
        assert parse["files_processed"] > 10
        assert parse["duration_ms"] >= 0
        assert parse["parser_versions"]["python"].startswith("tree-sitter-python")

        statuses = parse["statuses"]
        assert statuses["parsed"] > 10
        assert statuses["unsupported"] >= 0
        assert statuses["syntax_error"] == 0

        analysis = (
            await client.get(f"/api/repositories/{repo_id}/analysis")
        ).json()
        assert analysis["analyzed"] is True
        assert analysis["file_count"] == parse["files_processed"]
        assert analysis["symbol_count"] == parse["symbol_count"]

        symbols = (
            await client.get(f"/api/repositories/{repo_id}/symbols")
        ).json()
        assert symbols["total"] == parse["symbol_count"]
        assert len(symbols["items"]) > 0
        for s in symbols["items"]:
            assert "kind" in s
            assert "file_path" in s
            assert "qualified_name" in s

        py_filter = (
            await client.get(
                f"/api/repositories/{repo_id}/symbols?language=python"
            )
        ).json()
        assert py_filter["total"] > 0
        assert all(s["language"] == "python" for s in py_filter["items"])

        cls_filter = (
            await client.get(
                f"/api/repositories/{repo_id}/symbols?kind=class"
            )
        ).json()
        assert cls_filter["total"] > 0
        assert all(s["kind"] == "CLASS" for s in cls_filter["items"])

        imports = (
            await client.get(f"/api/repositories/{repo_id}/imports")
        ).json()
        assert len(imports) > 5
        for imp in imports:
            assert "file_path" in imp
            assert "source" in imp

        exports = (
            await client.get(f"/api/repositories/{repo_id}/exports")
        ).json()
        assert len(exports) >= 0

        first_file = symbols["items"][0]["file_path"]
        content = (
            await client.get(
                f"/api/repositories/{repo_id}/content",
                params={"path": first_file},
            )
        ).json()
        assert "content" in content
        assert len(content["content"]) > 0
    finally:
        settings.repo_storage_dir = original_storage


@pytest.mark.asyncio
async def test_parse_endpoint_returns_parser_versions(
    real_api_env, sample_repo, monkeypatch
) -> None:
    """Verify parser_versions is populated even with a small fake repo."""
    import os
    import shutil

    client, _, storage_dir = real_api_env

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

    settings = get_settings()
    original_storage = settings.repo_storage_dir
    settings.repo_storage_dir = storage_dir
    try:
        resp = await client.post(
            "/api/repositories",
            json={"url": "https://github.com/example/sample"},
        )
        assert resp.status_code == 201, resp.text
        repo_id = resp.json()["id"]
        parse = (await client.post(f"/api/repositories/{repo_id}/parse")).json()
        versions = parse["parser_versions"]
        assert "python" in versions
        assert "javascript" in versions
        assert "typescript" in versions
    finally:
        settings.repo_storage_dir = original_storage
