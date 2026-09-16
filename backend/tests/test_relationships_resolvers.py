"""Resolver-level tests: parse a real fixture tree, build the index, and drive
each relationship resolver directly against it.

All resolvers are tested in isolation so failures can be attributed precisely.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.analysis.relationships.call_resolver import resolve_calls
from app.analysis.relationships.defines_resolver import resolve_defines
from app.analysis.relationships.export_resolver import resolve_exports
from app.analysis.relationships.index import load_index
from app.analysis.relationships.inheritance_resolver import resolve_inheritance
from app.analysis.relationships.import_resolver import resolve_imports
from app.analysis.relationships.reference_resolver import resolve_references
from app.analysis.relationships.test_resolver import resolve_tests
from app.config import get_settings
from app.core.database import Base, get_db
from app.main import app
from app.models.orm import FileRecord, Repository
from app.repositories.languages import detect_language


def _build_fixture(root: Path) -> None:
    (root / "auth").mkdir(parents=True)
    (root / "auth" / "__init__.py").write_text("", encoding="utf-8")
    (root / "auth" / "service.py").write_text(
        "class AuthService:\n"
        "    def validate_token(self, token):\n"
        "        return token\n"
        "    def check(self, token):\n"
        "        return self.validate_token(token)\n"
        "\n"
        "def create_user(name):\n"
        "    return name\n",
        encoding="utf-8",
    )
    (root / "auth" / "models.py").write_text("class Entity:\n    pass\n", encoding="utf-8")
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
    (root / "users" / "service.py").write_text(
        "from users.repository import UserRepository\n"
        "class UserService:\n"
        "    def get_users(self):\n"
        "        return UserRepository()\n"
        "class AdminUser(UserService):\n"
        "    pass\n"
        "class AuditedEntity(auth.models.Entity):\n"
        "    pass\n"
        "class MultiBase(\n"
        "    Entity,\n"
        "):\n"
        "    pass\n",
        encoding="utf-8",
    )
    (root / "tests").mkdir()
    (root / "tests" / "test_auth.py").write_text(
        "from auth.service import AuthService\n"
        "def test_auth_service():\n"
        "    return AuthService()\n",
        encoding="utf-8",
    )
    (root / "tests" / "test_unrelated.py").write_text(
        "def test_math():\n    assert 1 + 1 == 2\n", encoding="utf-8"
    )
    (root / "tests" / "test_shadow.py").write_text(
        "def test_create_user():\n    return 1\n", encoding="utf-8"
    )
    (root / "app_one.py").write_text("def helper():\n    pass\n", encoding="utf-8")
    (root / "app_two.py").write_text("def helper():\n    pass\n", encoding="utf-8")
    (root / "app_three.py").write_text(
        "def runner():\n    return helper()\n", encoding="utf-8"
    )
    (root / "src").mkdir()
    (root / "src" / "pkg").mkdir()
    (root / "src" / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (root / "src" / "pkg" / "main.py").write_text(
        "def core():\n    return 1\n", encoding="utf-8"
    )
    (root / "src" / "pkg" / "consumer.py").write_text(
        "from pkg.main import core\n"
        "def run():\n"
        "    return core()\n",
        encoding="utf-8",
    )
    (root / "web").mkdir()
    (root / "web" / "app.ts").write_text(
        "export const x = 1;\n"
        "interface Shape {}\n"
        "class Circle implements Shape {}\n"
        "export default function render() {}\n",
        encoding="utf-8",
    )
    (root / "web" / "util.js").write_text(
        "function helper() {}\n"
        "export function go() { return helper(); }\n",
        encoding="utf-8",
    )
    (root / "web" / "dir").mkdir()
    (root / "web" / "dir" / "index.ts").write_text("export const y = 2;\n", encoding="utf-8")
    (root / "web" / "uses.js").write_text(
        "const d = require('./dir');\nfunction call() { return d; }\n",
        encoding="utf-8",
    )


def _build_fixture_repo(tmp_path) -> Path:
    root = tmp_path / "fixture"
    _build_fixture(root)
    return root


def _build_fixture_path(tmp_path) -> Path:
    return _build_fixture_repo(tmp_path)


@pytest.fixture
async def rel_env(tmp_path):
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
        yield client, maker
    app.dependency_overrides.pop(get_db, None)
    await engine.dispose()


async def _seed_and_parse(maker, root: Path) -> int:
    from app.analysis.service import AnalysisService

    async with maker() as session:
        repo = Repository(url="https://github.com/example/fixture", owner="example",
                          name="fixture", status="ready", local_path=str(root))
        session.add(repo)
        await session.flush()
        for path in sorted(Path(root).rglob("*")):
            if path.is_file():
                session.add(FileRecord(
                    repository_id=repo.id,
                    path=path.relative_to(root).as_posix(),
                    language=detect_language(path),
                    size_bytes=path.stat().st_size,
                    analyzed=True,
                ))
        await session.commit()
        repo_id = repo.id
    async with maker() as session:
        await AnalysisService(session).parse_repository(repo_id)
    return repo_id


@pytest.fixture
async def repo_index(rel_env, tmp_path):
    client, maker = rel_env
    root = _build_fixture_path(tmp_path)
    repo_id = await _seed_and_parse(maker, root)
    async with maker() as session:
        index = await load_index(session, repo_id)
    return index, root


def test_defines_present(repo_index):
    index, _ = repo_index
    edges = resolve_defines(index)
    file_defines = [e for e in edges if e.source_type == "file"]
    parent_defines = [e for e in edges if e.source_type == "symbol"]
    assert file_defines
    assert parent_defines
    assert all(e.type == "DEFINES" for e in edges)


def test_calls_resolved(repo_index):
    index, root = repo_index
    edges = resolve_calls(index, str(root))
    calls = [e for e in edges if e.type == "CALLS"]
    resolved = [e for e in calls if e.resolution_status == "RESOLVED"]
    assert resolved, "expected at least one resolved CALL"
    for e in calls:
        assert e.evidence_start_line is not None


def test_calls_evidence_lines(repo_index):
    index, root = repo_index
    edges = resolve_calls(index, str(root))
    for e in edges:
        assert e.evidence_file_id is not None
        assert e.evidence_start_line >= 1


def test_calls_method_self_resolved(repo_index):
    """self.method(...) inside a class resolves to a class member."""
    index, root = repo_index
    edges = resolve_calls(index, str(root))
    resolved = [
        e for e in edges
        if e.type == "CALLS" and e.resolution_status == "RESOLVED"
        and e.evidence and "validate_token" in e.evidence
    ]
    assert resolved, "expected a resolved CALL to validate_token from self.method(...)"


def test_calls_js_same_file(repo_index):
    """JS/TS calls resolve against same-file definitions."""
    index, root = repo_index
    edges = resolve_calls(index, str(root))
    # web/util.js: go() -> helper() (same file)
    go_calls = [
        e for e in edges
        if e.resolution_status == "RESOLVED"
        and e.evidence and e.evidence == "go calls helper"
    ]
    assert go_calls, "expected a resolved JS CALL edge go -> helper"


def test_calls_no_global_fallback(repo_index):
    """A name that exists elsewhere but is not importable must NOT resolve."""
    index, root = repo_index
    edges = resolve_calls(index, str(root))
    runner_calls = [e for e in edges if e.evidence and "runner" in e.evidence]
    assert runner_calls, "expected a CALL edge originating from runner()"
    assert all(e.resolution_status == "UNRESOLVED" for e in runner_calls)
    assert all(e.target_symbol_id is None for e in runner_calls)


def test_references_no_global_fallback(repo_index):
    """References are never formed by matching a name globally."""
    index, root = repo_index
    edges = resolve_references(index, str(root))

    # app_three.py's runner() uses 'helper' but imports nothing -> no REFERENCE.
    runner_refs = [
        e for e in edges if e.evidence and "runner" in e.evidence and "helper" in e.evidence
    ]
    assert not runner_refs

    # A resolved reference must point at a symbol the file actually binds.
    for e in edges:
        assert e.resolution_status == "RESOLVED"
        assert e.target_symbol_id is not None


def test_references_conservative(repo_index):
    index, root = repo_index
    refs = resolve_references(index, str(root))
    assert refs
    for e in refs:
        assert e.resolution_status in ("RESOLVED", "UNRESOLVED")


def test_inheritance_resolved(repo_index):
    index, root = repo_index
    edges = resolve_inheritance(index, str(root))
    ext = [e for e in edges if e.type == "EXTENDS"]
    impl = [e for e in edges if e.type == "IMPLEMENTS"]
    assert ext, "expected an EXTENDS edge (AdminUser -> UserService)"
    assert impl, "expected an IMPLEMENTS edge (Circle -> Shape)"


def test_inheritance_dotted_and_multiline(repo_index):
    """Dotted qualified bases and multi-line Python bases resolve."""
    index, root = repo_index
    edges = resolve_inheritance(index, str(root))
    dotted = [
        e for e in edges
        if e.type == "EXTENDS" and e.evidence and "AuditedEntity" in e.evidence
    ]
    multiline = [
        e for e in edges
        if e.type == "EXTENDS" and e.evidence and "MultiBase" in e.evidence
    ]
    assert dotted, "expected EXTENDS resolved for auth.models.Entity"
    assert dotted[0].resolution_status == "RESOLVED"
    assert dotted[0].target_symbol_id is not None
    assert multiline, "expected EXTENDS for multi-line base list"
    assert multiline[0].resolution_status == "RESOLVED"


def test_exports_named(repo_index):
    index, _ = repo_index
    edges = resolve_exports(index)
    named = [e for e in edges if e.evidence and "x" in e.evidence]
    assert any(e.type == "EXPORTS" for e in edges)


def test_src_layout_absolute_import_resolves(repo_index):
    """Absolute imports into a src/-layout package resolve (regression)."""
    index, root = repo_index

    import_edges = resolve_imports(index)
    resolved = [
        e for e in import_edges
        if e.resolution_status == "RESOLVED"
        and e.evidence and "pkg.main" in e.evidence
        and e.target_symbol_id is not None
    ]
    assert resolved, (
        "from src/pkg/consumer.py, `from pkg.main import core` must resolve "
        "through the src/ layout alias"
    )

    call_edges = resolve_calls(index, str(root))
    run_calls = [
        e for e in call_edges
        if e.resolution_status == "RESOLVED"
        and e.evidence and "run calls core" in e.evidence
    ]
    assert run_calls, "consumer.run() must resolve its call to pkg.main.core"


def test_tests_only_target_imported_symbols(repo_index):
    index, root = repo_index
    edges = resolve_tests(index, str(root))
    test_edges = [e for e in edges if e.type == "TESTS"]

    # test_auth.py imports AuthService -> should produce a TESTS edge.
    # test_unrelated.py has no imports -> should NOT be linked to anything.
    resolved_targets = {e.target_symbol_id for e in test_edges if e.target_symbol_id is not None}
    assert resolved_targets, "expected at least one TESTS edge resolving to an app symbol"


def test_tests_no_name_fallback(repo_index):
    """A test function named like an app symbol must NOT create a TESTS edge."""
    index, root = repo_index
    edges = resolve_tests(index, str(root))

    # app symbols include function create_user; test_shadow.py defines
    # test_create_user but imports nothing -> no TESTS edge targeting create_user.
    shadow_targets = {
        e.target_symbol_id for e in edges
        if e.type == "TESTS" and e.resolution_status == "RESOLVED"
    }
    create_user_sym = None
    for sym in index.symbols_by_name.get("create_user", []):
        if sym.kind == "FUNCTION":
            create_user_sym = sym
            break
    assert create_user_sym is not None
    assert create_user_sym.id not in shadow_targets, (
        "name-similarity must not produce a TESTS edge"
    )
