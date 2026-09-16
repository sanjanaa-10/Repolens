"""Shared pytest fixtures for RepoLens backend tests."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.core.database import Base, enable_foreign_keys, get_db
from app.main import app


@pytest.fixture
def repo_root(tmp_path):
    """A deterministic multi-file repository tree for relationship tests."""
    from pathlib import Path

    root = tmp_path / "rel_repo"

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
        "    svc = AuthService()\n"
        "    return create_user('x')\n",
        encoding="utf-8",
    )
    (root / "users").mkdir()
    (root / "users" / "__init__.py").write_text("", encoding="utf-8")
    (root / "users" / "repository.py").write_text(
        "class UserRepository:\n    pass\n", encoding="utf-8"
    )

    return root


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path):
    """Redirect storage + DB settings to a per-test directory.

    The ingestion service writes cloned workspaces into
    ``settings.repo_storage_dir``, so without this every test (even with a
    mocked ``acquire_repository``) would leak ``owner__repo__token``
    directories into the real ``backend/data/repositories`` tree.
    """
    settings = get_settings()
    original_storage = settings.repo_storage_dir
    original_db = settings.db_path
    settings.repo_storage_dir = tmp_path / "repos"
    settings.db_path = tmp_path / "test.db"
    yield
    settings.repo_storage_dir = original_storage
    settings.db_path = original_db


@pytest.fixture
def sample_repo(tmp_path):
    """A small fake repository tree used by discovery/filtering tests."""
    def build() -> str:
        root = tmp_path / "sample_repo"
        root.mkdir()

        (root / "auth").mkdir()
        (root / "auth" / "controller.py").write_text("def login():\n    pass\n", encoding="utf-8")
        (root / "auth" / "service.py").write_text(
            "import os\nfrom auth.controller import login\n\nclass AuthService:\n    pass\n",
            encoding="utf-8",
        )

        (root / "web").mkdir()
        (root / "web" / "app.ts").write_text("export const x = 1;\n", encoding="utf-8")
        (root / "web" / "App.tsx").write_text(
            'import { x } from "./app";\n\nexport function App() {}\n',
            encoding="utf-8",
        )

        (root / "tests").mkdir()
        (root / "tests" / "test_auth.py").write_text("def test_login(): pass\n", encoding="utf-8")

        (root / "node_modules").mkdir()
        (root / "node_modules" / "lodash").mkdir()
        (root / "node_modules" / "lodash" / "index.js").write_text("module.exports = {};\n", encoding="utf-8")

        (root / ".git").mkdir()
        (root / ".git" / "config").write_text("[core]\n", encoding="utf-8")

        (root / "__pycache__").mkdir()
        (root / "__pycache__" / "service.cpython-312.pyc").write_bytes(b"\x00" * 16)

        (root / ".venv").mkdir()
        (root / ".venv" / "bin").mkdir()
        (root / ".venv" / "bin" / "python").write_bytes(b"#!/bin/fake")

        (root / "dist").mkdir()
        (root / "dist" / "bundle.js").write_text("// generated\n", encoding="utf-8")

        (root / "README.md").write_text("# Sample\n", encoding="utf-8")
        (root / "package-lock.json").write_text("{}\n", encoding="utf-8")
        (root / "pyproject.toml").write_text("[project]\n", encoding="utf-8")

        return str(root)

    return build


@pytest.fixture
async def client(tmp_path):
    """An API client backed by an isolated, per-test SQLite database."""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'test.db'}", echo=False
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
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.pop(get_db, None)
    await engine.dispose()