"""Smoke tests for the RepoLens application shell."""
from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_endpoint(client: AsyncClient) -> None:
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "RepoLens"


@pytest.mark.asyncio
async def test_api_root(client: AsyncClient) -> None:
    resp = await client.get("/api/")
    assert resp.status_code == 200
    body = resp.json()
    assert "docs" in body
    assert body["version"]


@pytest.mark.asyncio
async def test_repositories_empty(client: AsyncClient) -> None:
    resp = await client.get("/api/repositories")
    assert resp.status_code == 200
    assert resp.json() == []