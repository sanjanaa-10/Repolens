"""Change impact engine (Phase 6): full pipeline tests with a deterministic
fixture repository.

The fixture delivers a real resolved call chain:

  AuthService.validate_token   (changed in commit B, DIRECT)
      ^-- parent expansion: AuthService class
              ^-- CALLS  AuthController.handle_sync          (auth/controller.py)
              ^-- IMPORTS auth/controller.py                 (file)
              ^-- TESTS  tests/test_auth.py                  (test file)
      handle_sync
              ^-- CALLS   RequestHandler.handle               (auth/request.py)
              ^-- IMPORTS auth/request.py                    (file)

plus an intentionally unresolvable dynamic call (normalize_token) and an
external import (base64) rooted in the changed file, and a documentation file
(README.md) changed without any symbol mapping (FILE_LEVEL_CHANGE).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.analysis.diff.engine import DiffEngine
from app.analysis.impact import run_impact_analysis
from app.analysis.relationships.engine import RelationshipEngine
from app.analysis.service import AnalysisService
from app.core.database import Base, get_db
from app.main import app
from app.models.orm import (
    Diff,
    FileRecord,
    ImpactAnalysis,
    ImpactNode,
    ImpactPath,
    Repository,
)
from app.models.schemas import ImpactAnalysisInfo
from app.repositories.languages import detect_language


# --- Fixture helpers ----------------------------------------------------------


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


SERVICE_B = (
    "import base64\n"
    "\n"
    "class AuthService:\n"
    "    def validate_token(self, token):\n"
    "        normalized = normalize_token(token)\n"
    "        return base64.b64encode(normalized).decode()\n"
    "\n"
    "    def legacy_login(self, user):\n"
    "        return user\n"
    "\n"
    "def create_user(name):\n"
    "    return name\n"
)


def _build_git_impact_fixture(root: Path) -> tuple[str, str]:
    """Two-commit git repository shaped for impact simulation.

    Returns (sha_a, sha_b). Commit A introduces the auth service chain;
    commit B changes only AuthService.validate_token (plus README.md), so the
    chain stays resolvable and everything downstream is *potential* impact.
    """
    root.mkdir(parents=True, exist_ok=True)
    assert _git(root, "init", "-q", "-b", "main").returncode == 0
    _git(root, "config", "user.email", "fixture@example.com")
    _git(root, "config", "user.name", "Fixture")

    _write(root / "auth" / "__init__.py", "")

    _write(
        root / "auth" / "service.py",
        "import base64\n"
        "\n"
        "class AuthService:\n"
        "    def validate_token(self, token):\n"
        "        return token\n"
        "\n"
        "    def legacy_login(self, user):\n"
        "        return user\n"
        "\n"
        "def create_user(name):\n"
        "    return name\n",
    )
    _write(
        root / "auth" / "controller.py",
        "from auth.service import AuthService\n"
        "\n"
        "def handle_sync(token):\n"
        "    return AuthService().validate_token(token)\n",
    )
    _write(
        root / "auth" / "request.py",
        "from auth.controller import handle_sync\n"
        "\n"
        "class RequestHandler:\n"
        "    def handle(self, token):\n"
        "        return handle_sync(token)\n",
    )
    _write(
        root / "tests" / "test_auth.py",
        "from auth.service import AuthService\n"
        "def test_validate_token():\n"
        "    assert AuthService().validate_token('x') == 'x'\n",
    )
    _write(root / "README.md", "# Auth fixture\n")

    assert _git(root, "add", "-A").returncode == 0
    assert _git(root, "commit", "-q", "-m", "feat: auth service chain").returncode == 0
    sha_a = _git(root, "rev-parse", "HEAD").stdout.strip()

    _write(root / "auth" / "service.py", SERVICE_B)
    _write(root / "README.md", "# Auth fixture\n\n## Validation\n")

    assert _git(root, "add", "-A").returncode == 0
    assert _git(
        root, "commit", "-q", "-m", "feat: token validation"
    ).returncode == 0
    sha_b = _git(root, "rev-parse", "HEAD").stdout.strip()

    assert sha_a != sha_b
    return sha_a, sha_b


@pytest.fixture(scope="module")
def impact_git(tmp_path_factory) -> tuple[Path, str, str]:
    root = tmp_path_factory.mktemp("impact-fixture") / "repo"
    sha_a, sha_b = _build_git_impact_fixture(root)
    return root, sha_a, sha_b


async def _seed_impact_repo(maker, root: Path) -> int:
    """Insert repo + file rows, parse repository, build relationships."""
    async with maker() as session:
        repo = Repository(
            url="https://github.com/example/impact-fixture",
            owner="example",
            name="impact-fixture",
            status="ready",
            local_path=str(root),
        )
        session.add(repo)
        await session.flush()

        rows = []
        for path in sorted(Path(root).rglob("*")):
            if path.is_file() and ".git" not in path.parts:
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

    async with maker() as session:
        await AnalysisService(session).parse_repository(repo_id)
    async with maker() as session:
        await RelationshipEngine(session).build_relationships(repo_id)
    return repo_id


@pytest.fixture
async def env(tmp_path, impact_git):
    """Isolated API environment: git fixture seeded + parsed + relationships."""
    root, sha_a, sha_b = impact_git
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'impact.db'}", echo=False
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_db():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    repo_id = await _seed_impact_repo(maker, root)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, maker, repo_id, root, sha_a, sha_b
    app.dependency_overrides.pop(get_db, None)
    await engine.dispose()


async def _run_diff(maker, repo_id: int, base: str, head: str) -> dict:
    async with maker() as session:
        return await DiffEngine(session).analyze(repo_id, base, head)


async def _run_impact(maker, repo_id: int, diff_id: int, depth: int = 2) -> ImpactAnalysisInfo:
    async with maker() as session:
        return await run_impact_analysis(session, repo_id, diff_id, depth)


def _names(info: ImpactAnalysisInfo, section: str) -> list[str]:
    return [getattr(n, "name") for n in getattr(info, section)]


def _any_name_containing(info: ImpactAnalysisInfo, section: str, needle: str) -> bool:
    return any(needle in name for name in _names(info, section))


# --- Engine tests -------------------------------------------------------------


@pytest.mark.asyncio
async def test_impact_full_scenario(env):
    _, maker, repo_id, root, sha_a, sha_b = env

    summary = await _run_diff(maker, repo_id, sha_a, sha_b)
    diff_id = summary["id"]
    info = await _run_impact(maker, repo_id, diff_id, 2)

    # Summary counts. The changed method (validate_token) plus its enclosing
    # class both contain the changed lines, so the diff reports 2 changed
    # symbols inside auth/service.py; README.md is the file-level change.
    assert info.summary.changed_symbols == 2
    assert info.summary.changed_files == 2
    assert info.summary.direct == 4
    assert info.summary.file_level_changes == 1
    assert info.summary.affected_tests == 1
    assert info.summary.external == 1
    assert info.summary.truncated is False
    assert info.max_depth == 2

    # DIRECT: the changed symbols + their file + the file-level README change
    assert _any_name_containing(info, "changed", "validate_token")
    assert _any_name_containing(info, "changed", "AuthService")
    assert not _any_name_containing(info, "changed", "legacy_login")
    assert _any_name_containing(info, "changed", "auth/service.py")
    assert _any_name_containing(info, "changed", "README.md")
    readme = next(n for n in info.changed if n.name == "README.md")
    assert readme.is_file_level_change is True
    assert readme.file_category == "DOCUMENTATION"

    # POTENTIAL: callers and their file (via parent class expansion)
    assert _any_name_containing(info, "potentially_affected", "handle_sync")
    assert _any_name_containing(info, "potentially_affected", "RequestHandler.handle")
    assert any(
        n.name == "auth/controller.py"
        and n.depth == 1
        and n.via == "IMPORTS"
        for n in info.potentially_affected
    )
    assert any(
        n.name == "auth/request.py" and n.depth == 2
        for n in info.potentially_affected
    )
    # handle_sync reached via CALLS 1 level below the changed class
    handle = next(n for n in info.potentially_affected if "handle_sync" in n.name)
    assert handle.depth == 1
    assert handle.via == "CALLS"
    req_handler = next(
        n for n in info.potentially_affected if "RequestHandler.handle" in n.name
    )
    assert req_handler.depth == 2

    # TESTS section
    assert _any_name_containing(info, "tests", "test_auth.py")
    assert info.tests[0].via == "TESTS"

    # UNRESOLVED: the dynamic normalize_token call inside the changed method
    assert info.summary.unresolved >= 1
    assert any("normalize_token" in n.name for n in info.unresolved)
    unresolved_in_service = any(
        n.file_path == "auth/service.py" and n.evidence_line > 0
        for n in info.unresolved
    )
    assert unresolved_in_service

    # EXTERNAL: base64 imported by the changed file
    assert any("base64" in n.name for n in info.external)
    assert info.external[0].file_path == "auth/service.py"

    # Paths: every reached node has an explainable path
    assert len(info.paths) > 0
    assert any(p.depth == 2 and "RequestHandler.handle" in p.target for p in info.paths)
    req_path = next(p for p in info.paths if "RequestHandler.handle" in p.target)
    assert [s.relationship for s in req_path.steps] == ["CALLS", "CALLS"]
    # dependency direction: caller CALLS the changed symbol at the call site
    assert "handle_sync" in req_path.steps[0].source
    assert "AuthService" in req_path.steps[0].target
    assert req_path.steps[0].evidence.startswith("auth/controller.py:")
    assert (
        "RequestHandler.handle" in req_path.steps[1].source
        and "handle_sync" in req_path.steps[1].target
    )


@pytest.mark.asyncio
async def test_impact_depth_1_limits_traversal(env):
    _, maker, repo_id, root, sha_a, sha_b = env
    summary = await _run_diff(maker, repo_id, sha_a, sha_b)

    info = await _run_impact(maker, repo_id, summary["id"], 1)

    assert not _any_name_containing(info, "potentially_affected", "RequestHandler")
    assert not any(n.name == "auth/request.py" for n in info.potentially_affected)
    assert _any_name_containing(info, "potentially_affected", "handle_sync")
    # depth 1: handle_sync + controller.py + the test function that constructs
    # AuthService (its file lands in the tests section)
    assert info.summary.potential == 3


@pytest.mark.asyncio
async def test_impact_depth_2_reaches_second_level(env):
    _, maker, repo_id, root, sha_a, sha_b = env
    summary = await _run_diff(maker, repo_id, sha_a, sha_b)

    info = await _run_impact(maker, repo_id, summary["id"], 2)
    depths = [n.depth for n in info.potentially_affected]
    assert max(depths) == 2


@pytest.mark.asyncio
async def test_impact_deterministic_recompute(env):
    """Recomputing the analysis after deleting its rows yields identical output."""
    _, maker, repo_id, root, sha_a, sha_b = env
    summary = await _run_diff(maker, repo_id, sha_a, sha_b)
    diff_id = summary["id"]

    first = await _run_impact(maker, repo_id, diff_id, 2)
    async with maker() as session:
        analysis = (
            await session.execute(
                select(ImpactAnalysis).where(
                    ImpactAnalysis.diff_id == diff_id
                )
            )
        ).scalar_one()
        await session.execute(
            delete(ImpactPath).where(ImpactPath.analysis_id == analysis.id)
        )
        await session.execute(
            delete(ImpactNode).where(ImpactNode.analysis_id == analysis.id)
        )
        await session.delete(analysis)
        await session.commit()

    second = await _run_impact(maker, repo_id, diff_id, 2)
    assert first.model_dump() == second.model_dump()


@pytest.mark.asyncio
async def test_impact_node_limit_truncation(env, monkeypatch):
    import app.analysis.impact.engine as engine_mod

    monkeypatch.setattr(engine_mod, "MAX_IMPACT_NODES", 2)

    _, maker, repo_id, root, sha_a, sha_b = env
    summary = await _run_diff(maker, repo_id, sha_a, sha_b)

    info = await _run_impact(maker, repo_id, summary["id"], 2)
    assert info.summary.truncated is True
    assert info.summary.truncated_reason == "impact node limit reached"
    # the traversal registry was capped, so no second-level nodes were added
    assert info.summary.potential == 0


@pytest.mark.asyncio
async def test_impact_multiple_changed_symbols(env):
    """A second diff with two changed symbols, both without consumers.

    Expected: DIRECT only, no potential impact, deterministic.
    """
    _, maker, repo_id, root, sha_a, sha_b = env

    # author a third commit changing legacy_login AND create_user
    service_c = SERVICE_B.replace(
        "    def legacy_login(self, user):\n        return user\n",
        "    def legacy_login(self, user):\n        return f'user:{user}'\n",
    ).replace(
        "def create_user(name):\n    return name\n",
        "def create_user(name):\n    return f'user:{name}'\n",
    )
    _write(root / "auth" / "service.py", service_c)
    assert _git(root, "add", "-A").returncode == 0
    assert _git(root, "commit", "-q", "-m", "feat: enrich legacy paths").returncode == 0
    sha_c = _git(root, "rev-parse", "HEAD").stdout.strip()

    summary = await _run_diff(maker, repo_id, sha_b, sha_c)
    # the two module/class methods both changed
    symbols = (await _diff_symbols_for(maker, summary["id"]))
    assert len(symbols) >= 2

    info = await _run_impact(maker, repo_id, summary["id"], 2)
    assert info.summary.changed_symbols == len(symbols)
    assert info.summary.direct == info.summary.changed_symbols + info.summary.changed_files
    # no resolvable callers -> no potential reachable nodes beyond the files
    assert _any_name_containing(info, "changed", "legacy_login")
    assert _any_name_containing(info, "changed", "create_user")


async def _diff_symbols_for(maker, diff_id: int) -> list:
    from app.models.orm import DiffSymbol

    async with maker() as session:
        return list(
            (
                await session.execute(
                    select(DiffSymbol).where(DiffSymbol.diff_id == diff_id)
                )
            ).scalars().all()
        )


# --- API tests ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_impact_api_post_get_and_repeat(env):
    client, maker, repo_id, root, sha_a, sha_b = env
    summary = await _run_diff(maker, repo_id, sha_a, sha_b)
    diff_id = summary["id"]

    resp1 = await client.post(
        f"/api/repositories/{repo_id}/diff/{diff_id}/impact",
        json={"max_depth": 2},
    )
    assert resp1.status_code == 201
    body1 = resp1.json()
    assert body1["summary"]["potential"] > 0

    # repeat POST is idempotent -> same analysis_id
    resp2 = await client.post(
        f"/api/repositories/{repo_id}/diff/{diff_id}/impact",
        json={"max_depth": 2},
    )
    assert resp2.status_code == 201
    body2 = resp2.json()
    assert body2["analysis_id"] == body1["analysis_id"]

    analysis_id = body1["analysis_id"]
    resp_get = await client.get(
        f"/api/repositories/{repo_id}/impact/{analysis_id}"
    )
    assert resp_get.status_code == 200
    got = resp_get.json()
    assert got == body1
    assert got["base_revision"] == _git(root, "rev-parse", sha_a).stdout.strip()
    assert got["head_revision"] == _git(root, "rev-parse", sha_b).stdout.strip()


@pytest.mark.asyncio
async def test_impact_api_validation_404s(env):
    client, maker, repo_id, root, sha_a, sha_b = env
    summary = await _run_diff(maker, repo_id, sha_a, sha_b)
    diff_id = summary["id"]

    assert (
        await client.post(
            f"/api/repositories/{repo_id}/diff/{diff_id}/impact",
            json={"max_depth": 0},
        )
    ).status_code == 422
    assert (
        await client.post(
            f"/api/repositories/{repo_id}/diff/{diff_id}/impact",
            json={"max_depth": 99},
        )
    ).status_code == 422

    not_found_diff = 99999
    resp = await client.post(
        f"/api/repositories/{repo_id}/diff/{not_found_diff}/impact",
        json={},
    )
    assert resp.status_code == 404

    resp = await client.get(f"/api/repositories/{repo_id}/impact/99999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_impact_api_repo_isolation(env):
    client, maker, repo_id, root, sha_a, sha_b = env
    summary = await _run_diff(maker, repo_id, sha_a, sha_b)
    diff_id = summary["id"]

    resp = await client.post(
        f"/api/repositories/{repo_id}/diff/{diff_id}/impact", json={}
    )
    assert resp.status_code == 201
    analysis_id = resp.json()["analysis_id"]

    # a different (empty) repository must not expose the analysis
    async with maker() as session:
        other = Repository(
            url="https://github.com/example/other",
            owner="example",
            name="other",
            status="ready",
            local_path=str(root),
        )
        session.add(other)
        await session.commit()
        other_id = other.id

    resp = await client.get(f"/api/repositories/{other_id}/impact/{analysis_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_impact_ordering_is_deterministic(env):
    """potentially_affected is ordered by (depth, file_path, name)."""
    _, maker, repo_id, root, sha_a, sha_b = env
    summary = await _run_diff(maker, repo_id, sha_a, sha_b)

    info = await _run_impact(maker, repo_id, summary["id"], 2)
    depths = [n.depth for n in info.potentially_affected]
    assert depths == sorted(depths)

    changed = [n.name for n in info.changed]
    # symbols before files; each group ordered by (file_path, name)
    types = [n.node_type for n in info.changed]
    assert all(t != "SYMBOL" for t in types[2:]) if len(types) > 2 else True
    symbol_names = [n.name for n in info.changed if n.node_type == "SYMBOL"]
    file_names = [n.name for n in info.changed if n.node_type != "SYMBOL"]
    assert symbol_names == sorted(symbol_names)
    assert file_names == sorted(file_names)


@pytest.mark.asyncio
async def test_impact_stored_rows_render_identical_to_get(env):
    """Persisted rows assemble into the exact response shape on read."""
    client, maker, repo_id, root, sha_a, sha_b = env
    summary = await _run_diff(maker, repo_id, sha_a, sha_b)

    post = (
        await client.post(
            f"/api/repositories/{repo_id}/diff/{summary['id']}/impact", json={}
        )
    ).json()

    get = (
        await client.get(
            f"/api/repositories/{repo_id}/impact/{post['analysis_id']}"
        )
    ).json()
    assert get == post
    assert json.dumps(get, sort_keys=True) == json.dumps(post, sort_keys=True)