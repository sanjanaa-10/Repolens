"""API tests for repository ingestion.

Acquisition (git clone) is monkeypatched so these tests do not require
network access; they exercise URL validation, the ingestion pipeline, DB
storage, and the API contract end-to-end. Network-backed cloning is covered
separately by the integration suite.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from httpx import AsyncClient


@pytest.fixture
def fake_remote(sample_repo, monkeypatch):
    """Replace git clone with a local copy of the fixture repository."""
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
        return "main", "0123456789abcdef0123456789abcdef01234567"

    monkeypatch.setattr(
        "app.repositories.acquisition.acquire_repository", fake_acquire
    )
    return fixture_root


@pytest.mark.asyncio
async def test_ingest_valid_repository(client, fake_remote) -> None:
    resp = await client.post(
        "/api/repositories", json={"url": "https://github.com/fastapi/fastapi"}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["owner"] == "fastapi"
    assert body["name"] == "fastapi"
    assert body["status"] == "ready"
    assert body["branch"] == "main"
    assert body["commit_sha"] == "0123456789abcdef0123456789abcdef01234567"
    assert body["file_count"] > 0
    assert "Python" in body["languages"]
    assert body["url"] == "https://github.com/fastapi/fastapi"

    repo_id = body["id"]

    # Repository appears in the list endpoint.
    listing = await client.get("/api/repositories")
    assert any(r["id"] == repo_id for r in listing.json())

    # It can be fetched by id.
    detail = await client.get(f"/api/repositories/{repo_id}")
    assert detail.status_code == 200
    assert detail.json()["name"] == "fastapi"

    # And deleted, cleaning up its workspace.
    deleted = await client.delete(f"/api/repositories/{repo_id}")
    assert deleted.status_code == 204
    detail_after = await client.get(f"/api/repositories/{repo_id}")
    assert detail_after.status_code == 404


@pytest.mark.asyncio
async def test_ingest_invalid_url_rejected(client) -> None:
    resp = await client.post(
        "/api/repositories", json={"url": "http://localhost:8000/repo"}
    )
    assert resp.status_code == 400
    assert "GitHub" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_ingest_duplicate_url_returns_existing(client, fake_remote) -> None:
    first = await client.post(
        "/api/repositories", json={"url": "https://github.com/psf/requests"}
    )
    assert first.status_code == 201
    second = await client.post(
        "/api/repositories", json={"url": "https://github.com/psf/requests.git"}
    )
    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"]


@pytest.mark.asyncio
async def test_ingest_access_failure_controlled(client, monkeypatch) -> None:
    from app.repositories.acquisition import RepositoryAccessError

    def boom(owner, name, destination):
        raise RepositoryAccessError()

    monkeypatch.setattr(
        "app.repositories.acquisition.acquire_repository", boom
    )
    resp = await client.post(
        "/api/repositories", json={"url": "https://github.com/org/whatever"}
    )
    assert resp.status_code == 502
    body = resp.json()
    assert "detail" in body
    assert "traceback" not in str(body).lower()


@pytest.mark.asyncio
async def test_ingest_unsupported_repository(client, monkeypatch, tmp_path) -> None:
    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    (empty_root / "README.md").write_text("no code here\n", encoding="utf-8")

    def fake_acquire(owner, name, destination: Path) -> tuple[str, str]:
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copy2(empty_root / "README.md", destination / "README.md")
        return "main", "a" * 40

    monkeypatch.setattr(
        "app.repositories.acquisition.acquire_repository", fake_acquire
    )
    resp = await client.post(
        "/api/repositories", json={"url": "https://github.com/org/docs-only"}
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_missing_url_body_is_422(client) -> None:
    resp = await client.post("/api/repositories", json={})
    assert resp.status_code == 422