"""Git diff engine: full pipeline tests with a deterministic fixture repository.

Covers revision validation, file/hunk/line extraction (modified, added,
deleted, renamed, binary), symbol mapping, classification, idempotency,
repository isolation, limits, security, and the API surface.

The fixture is a *real* git repository built with two commits. Diff results
are verified against `git diff` output computed at test time — production code
never hardcodes diff content.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.analysis.diff import git_service as diff_git_service
from app.analysis.diff.engine import DiffEngine
from app.analysis.diff.git_service import (
    RevisionValidationError,
    deepen_for_revision,
)
from app.analysis.service import AnalysisService
from app.core.database import Base, get_db
from app.core.git_safety import run_git_capture
from app.main import app
from app.models.orm import (
    Diff,
    DiffChangedLine,
    DiffFile,
    DiffHunk,
    DiffSymbol,
    FileRecord,
    Repository,
)
from app.repositories.languages import detect_language

# The Well-Known Empty Tree (git's canonical object for a tree with no entries).
# Used to diff a repository from its birth (full-snapshot comparison).
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


# --- Fixture helpers ----------------------------------------------------------


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _ensure_empty_tree(root: Path) -> str:
    """Store and return the well-known empty-tree object in the fixture repo."""
    result = subprocess.run(
        ["git", "-C", str(root), "hash-object", "-t", "tree", "-w", "--stdin"],
        input="",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _write(path: Path, content: str, *, binary: bytes | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if binary is not None:
        path.write_bytes(binary)
    else:
        path.write_text(content, encoding="utf-8")


def _build_git_fixture(root: Path) -> tuple[str, str]:
    """Create a deterministic two-commit git repository.

    Returns (sha_a, sha_b).

    Commit A → auth/service.py (AuthService + legacy_login + create_user),
                auth/obsolete.py, auth/util.py, tests/test_auth.py, README.md.
    Commit B → modify service.py (validate_token + refresh_token + create_user),
                add auth/newfeature.py, tests/test_newfeature.py,
                requirements.txt, assets/logo.bin (binary),
                delete auth/obsolete.py, rename auth/util.py -> auth/utils.py.
    """
    root.mkdir(parents=True, exist_ok=True)
    assert _git(root, "init", "-q", "-b", "main").returncode == 0
    _git(root, "config", "user.email", "fixture@example.com")
    _git(root, "config", "user.name", "Fixture")

    _write(root / "auth" / "__init__.py", "")
    _write(
        root / "auth" / "service.py",
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
    _write(root / "auth" / "obsolete.py", "class ObsoleteService:\n    pass\n")
    _write(root / "auth" / "util.py", "def helper():\n    return 1\n")
    _write(
        root / "tests" / "test_auth.py",
        "from auth.service import AuthService\n"
        "def test_auth():\n"
        "    svc = AuthService()\n"
        "    assert svc.validate_token('t') == 't'\n",
    )
    _write(root / "README.md", "# Fixture\n")

    assert _git(root, "add", "-A").returncode == 0
    assert _git(root, "commit", "-q", "-m", "feat: initial auth service").returncode == 0
    sha_a = _git(root, "rev-parse", "HEAD").stdout.strip()

    _write(
        root / "auth" / "service.py",
        "class AuthService:\n"
        "    def validate_token(self, token):\n"
        "        if not token:\n"
        "            raise ValueError('token required')\n"
        "        return token.upper()\n"
        "\n"
        "    def refresh_token(self, token):\n"
        "        return token\n"
        "\n"
        "def create_user(name):\n"
        "    return f'user:{name}'\n",
    )
    _write(root / "auth" / "newfeature.py", "class Feature:\n    def run(self):\n        return 42\n")
    _write(
        root / "tests" / "test_newfeature.py",
        "from auth.newfeature import Feature\n"
        "def test_feature():\n"
        "    assert Feature().run() == 42\n",
    )
    _write(root / "requirements.txt", "fastapi==0.110.0\n")
    _write(root / "assets" / "logo.bin", "", binary=b"\x89PNG\r\n\x1a\n" + bytes(range(32)))
    _write(root / "README.md", "# Fixture\n\n## Token validation\n")

    ut = root / "auth" / "util.py"
    ut.rename(root / "auth" / "utils.py")
    (root / "auth" / "obsolete.py").unlink()

    assert _git(root, "add", "-A").returncode == 0
    assert _git(
        root, "commit", "-q", "-m", "feat: token validation and new feature"
    ).returncode == 0
    sha_b = _git(root, "rev-parse", "HEAD").stdout.strip()

    assert sha_a != sha_b
    return sha_a, sha_b


@pytest.fixture(scope="module")
def diff_git(tmp_path_factory) -> tuple[Path, str, str]:
    """Module-scoped git fixture repository (built once)."""
    root = tmp_path_factory.mktemp("diff-fixture") / "repo"
    sha_a, sha_b = _build_git_fixture(root)
    return root, sha_a, sha_b


async def _seed_repo(maker, root: Path) -> int:
    """Insert Repository + FileRecord rows and run the parse pipeline."""
    async with maker() as session:
        repo = Repository(
            url="https://github.com/example/diff-fixture",
            owner="example",
            name="diff-fixture",
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
    return repo_id


@pytest.fixture
async def env(tmp_path, diff_git):
    """Isolated API environment with the git fixture seeded + parsed."""
    root, sha_a, sha_b = diff_git
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'diff.db'}", echo=False
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_db():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    repo_id = await _seed_repo(maker, root)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, maker, repo_id, root, sha_a, sha_b
    app.dependency_overrides.pop(get_db, None)
    await engine.dispose()


async def _run_diff(maker, repo_id: int, base: str, head: str) -> dict:
    async with maker() as session:
        return await DiffEngine(session).analyze(repo_id, base, head)


async def _diff_row(maker, diff_id: int) -> Diff:
    async with maker() as session:
        return (await session.execute(select(Diff).where(Diff.id == diff_id))).scalar_one()


# --- Engine tests -------------------------------------------------------------


@pytest.mark.asyncio
async def test_diff_persists_files_and_summary(env):
    client, maker, repo_id, root, sha_a, sha_b = env

    summary = await _run_diff(maker, repo_id, sha_a, sha_b)
    diff = await _diff_row(maker, summary["id"])

    assert diff.repository_id == repo_id
    assert diff.base_revision == _git(root, "rev-parse", sha_a).stdout.strip()
    assert diff.head_revision == _git(root, "rev-parse", sha_b).stdout.strip()
    assert diff.files_changed >= 5

    # Statuses must reflect git's own view of the commit pair.
    git_names = _git(root, "diff", "--name-status", sha_a, sha_b).stdout
    assert "auth/service.py" in git_names
    assert "auth/newfeature.py" in git_names
    assert "auth/obsolete.py" in git_names
    assert "requirements.txt" in git_names

    files = (await _diff_files(maker, summary["id"]))
    by_path = {f.path: f for f in files}
    assert by_path["auth/service.py"].status == "MODIFIED"
    assert by_path["auth/newfeature.py"].status == "ADDED"
    assert by_path["auth/obsolete.py"].status == "DELETED"
    assert by_path["requirements.txt"].status == "ADDED"
    assert by_path["auth/utils.py"].status == "RENAMED"
    assert by_path["auth/utils.py"].old_path == "auth/util.py"
    assert by_path["auth/service.py"].additions > 0
    assert by_path["auth/service.py"].deletions > 0


@pytest.mark.asyncio
async def test_hunks_and_changed_lines(env):
    client, maker, repo_id, root, sha_a, sha_b = env
    summary = await _run_diff(maker, repo_id, sha_a, sha_b)

    async with maker() as session:
        service_file = (
            await session.execute(
                select(DiffFile).where(
                    DiffFile.diff_id == summary["id"],
                    DiffFile.path == "auth/service.py",
                )
            )
        ).scalar_one()
        hunks = (
            await session.execute(
                select(DiffHunk).where(DiffHunk.diff_file_id == service_file.id)
            )
        ).scalars().all()
        assert len(hunks) >= 1
        total_lines = 0
        added_lines = 0
        for h in hunks:
            lines = (
                await session.execute(
                    select(DiffChangedLine).where(DiffChangedLine.diff_hunk_id == h.id)
                )
            ).scalars().all()
            total_lines += len(lines)
            added_lines += sum(1 for l in lines if l.change_type == "ADDED")
        # Echo the actual unified diff to validate counts independently.
        git_diff = _git(root, "diff", "--numstat", sha_a, sha_b, "--", "auth/service.py").stdout
        add_str, del_str, _ = git_diff.split("\t")
        assert total_lines == int(add_str) + int(del_str)
        assert added_lines == int(add_str)


@pytest.mark.asyncio
async def test_binary_file_detection(env):
    client, maker, repo_id, root, sha_a, sha_b = env
    summary = await _run_diff(maker, repo_id, sha_a, sha_b)

    async with maker() as session:
        binary_file = (
            await session.execute(
                select(DiffFile).where(
                    DiffFile.diff_id == summary["id"],
                    DiffFile.path == "assets/logo.bin",
                )
            )
        ).scalar_one()
        assert binary_file.binary is True
        hunks = (
            await session.execute(
                select(DiffHunk).where(DiffHunk.diff_file_id == binary_file.id)
            )
        ).scalars().all()
        assert len(hunks) == 0


@pytest.mark.asyncio
async def test_symbol_mapping_and_classification(env):
    client, maker, repo_id, root, sha_a, sha_b = env
    summary = await _run_diff(maker, repo_id, sha_a, sha_b)

    async with maker() as session:
        symbols = (
            await session.execute(
                select(DiffSymbol).where(DiffSymbol.diff_id == summary["id"])
            )
        ).scalars().all()
        by_name = {s.symbol_name: s for s in symbols}

        # validate_token gained lines in its surviving block.
        vt = by_name.get("validate_token")
        assert vt is not None, [s.symbol_name for s in symbols]
        assert vt.added_lines > 0

        # The new Feature class (added file) maps to an ADDED symbol.
        feature_candidates = [s for s in symbols if s.symbol_name == "Feature"]
        assert feature_candidates, [s.symbol_name for s in symbols]
        feature = next(
            (s for s in feature_candidates if s.file_path == "auth/newfeature.py"),
            None,
        )
        assert feature is not None
        assert feature.change_type == "ADDED"
        assert feature.file_path == "auth/newfeature.py"
        # The import-side mapping lives under the test file.
        import_map = next(
            (s for s in feature_candidates if s.file_path == "tests/test_newfeature.py"),
            None,
        )
        assert import_map is not None

        # Classification by path for each changed file.
        files = (
            await session.execute(
                select(DiffFile).where(DiffFile.diff_id == summary["id"])
            )
        ).scalars().all()
        cats = {f.path: f.file_category for f in files}
        assert cats["auth/service.py"] == "SOURCE"
        assert cats["tests/test_newfeature.py"] == "TEST"
        assert cats["requirements.txt"] == "CONFIG"
        assert cats["README.md"] == "DOCUMENTATION"


@pytest.mark.asyncio
async def test_idempotency(env):
    client, maker, repo_id, root, sha_a, sha_b = env
    first = await _run_diff(maker, repo_id, sha_a, sha_b)
    second = await _run_diff(maker, repo_id, sha_a, sha_b)

    assert second["id"] == first["id"]
    async with maker() as session:
        count = (
            await session.execute(select(Diff).where(Diff.repository_id == repo_id))
        ).scalars().all()
        assert len(count) == 1


@pytest.mark.asyncio
async def test_repository_isolation(env, tmp_path, diff_git):
    client, maker, repo_id, root, sha_a, sha_b = env

    # Second repository: same fixture tree, separate DB rows.
    other_root = diff_git[0]
    async with maker() as session:
        repo2 = Repository(
            url="https://github.com/example/diff-fixture-2",
            owner="example",
            name="diff-fixture-2",
            status="ready",
            local_path=str(other_root),
        )
        session.add(repo2)
        await session.commit()
        other_id = repo2.id

    first = await _run_diff(maker, repo_id, sha_a, sha_b)
    second = await _run_diff(maker, other_id, sha_a, sha_b)

    async with maker() as session:
        d1 = (await session.execute(select(Diff).where(Diff.id == first["id"]))).scalar_one()
        d2 = (await session.execute(select(Diff).where(Diff.id == second["id"]))).scalar_one()
        assert d1.repository_id == repo_id
        assert d2.repository_id == other_id


# --- Regression tests: utf-8 decode + shallow-deepen --------------------------


def test_run_git_capture_decodes_utf8_output(tmp_path):
    """Git output containing non-cp1252 bytes must not kill the capture.

    Regression: ``subprocess.run(..., text=True)`` decoded stdout with the
    locale codepage (cp1252 on Windows). Git output containing UTF-8 bytes with
    no cp1252 mapping (e.g. curly quotes, emoji) made the reader thread raise
    ``UnicodeDecodeError``, leaving ``stdout=None`` and crashing the output-cap
    check with ``AttributeError: 'NoneType' object has no attribute 'encode'``
    (reported as a generic 500 on psf/requests). The capture must decode as
    UTF-8 with ``errors="replace"`` instead.
    """
    root = tmp_path / "repo"
    root.mkdir(parents=True)
    assert _git(root, "init", "-q", "-b", "main").returncode == 0
    _git(root, "config", "user.email", "fixture@example.com")
    _git(root, "config", "user.name", "Fixture")

    (root / "README.md").write_text(
        "# Fixture\n\ncaf\u00e9 \u2018quoted\u2019 rainbow \U0001f308 snowman \u2603\n",
        encoding="utf-8",
    )
    assert _git(root, "add", "-A").returncode == 0
    assert _git(root, "commit", "-q", "-m", "feat: unicode content").returncode == 0
    head = _git(root, "rev-parse", "HEAD").stdout.strip()
    empty = _ensure_empty_tree(root)
    assert empty == EMPTY_TREE

    output = run_git_capture(
        ["diff", "--no-ext-diff", empty, head, "--", "README.md"],
        cwd=root,
        timeout=30,
    )
    assert isinstance(output, str)
    assert "\u2018quoted\u2019" in output
    assert "\U0001f308" in output
    assert "\u2603" in output


def test_deepen_for_revision_fetches_head_sha_for_expressions(monkeypatch):
    """Traversal expressions (HEAD~1, main^2) are not fetch refspecs.

    Regression: `git fetch --depth 2 origin HEAD~1` fails with
    'fatal: invalid refspec -- not our ref'. Deepening must instead resolve
    the local HEAD and fetch that sha at the requested depth, then resolve the
    expression locally.
    """
    result_map = {
        "HEAD": "aaaa1111bbbb2222cccc3333dddd4444eeee5555",
        "HEAD~1": "bbbb2222",
        "main^2": "cccc3333",
    }
    fetched: list[list[str]] = []
    resolved: list[str] = []

    monkeypatch.setattr(
        diff_git_service,
        "validate_revision_in_repo",
        lambda rev, cwd: (resolved.append(rev), result_map[rev])[1],
    )
    monkeypatch.setattr(
        diff_git_service, "run_git", lambda args, cwd: fetched.append(args)
    )

    assert deepen_for_revision("HEAD~1", Path("x")) == "bbbb2222"
    assert deepen_for_revision("main^2", Path("x")) == "cccc3333"
    assert fetched == [
        ["fetch", "--depth", "2", "origin", "aaaa1111bbbb2222cccc3333dddd4444eeee5555"],
        ["fetch", "--depth", "2", "origin", "aaaa1111bbbb2222cccc3333dddd4444eeee5555"],
    ]
    assert resolved == ["HEAD", "HEAD~1", "HEAD", "main^2"]


def test_deepen_for_revision_fetches_plain_revision_directly(monkeypatch):
    """A plain SHA (or branch) refspec is fetched directly, no HEAD indirection."""
    sha = "5460f467b02e49471c0fd6cfc9ca0adab6351f98"
    fetched: list[list[str]] = []

    monkeypatch.setattr(
        diff_git_service, "validate_revision_in_repo", lambda rev, cwd: f"resolved-{rev}"
    )
    monkeypatch.setattr(
        diff_git_service, "run_git", lambda args, cwd: fetched.append(args)
    )

    assert deepen_for_revision(sha, Path("x")) == f"resolved-{sha}"
    assert fetched == [["fetch", "--depth", "2", "origin", sha]]


def test_deepen_for_revision_validates_before_fetch(monkeypatch):
    """Injection candidates must be rejected before any git invocation."""
    called: list[object] = []

    monkeypatch.setattr(
        diff_git_service, "validate_revision_in_repo", lambda rev, cwd: called.append(rev)
    )
    monkeypatch.setattr(
        diff_git_service, "run_git", lambda args, cwd: called.append(args)
    )

    with pytest.raises(RevisionValidationError):
        deepen_for_revision("HEAD; rm -rf /", Path("x"))
    assert called == []


@pytest.mark.asyncio
async def test_empty_tree_to_head_full_snapshot_diff(env):
    """HEAD diffed against the well-known empty tree yields the full snapshot."""
    client, maker, repo_id, root, sha_a, sha_b = env
    assert _ensure_empty_tree(root) == EMPTY_TREE

    summary = await _run_diff(maker, repo_id, EMPTY_TREE, sha_b)
    diff = await _diff_row(maker, summary["id"])

    tracked = [p for p in _git(root, "ls-files").stdout.splitlines() if p]
    assert len(tracked) >= 5
    assert diff.files_changed == len(tracked)
    assert diff.insertions > 0

    files = await _diff_files(maker, summary["id"])
    assert all(f.status == "ADDED" for f in files)
    by_path = {f.path: f for f in files}
    assert "auth/service.py" in by_path
    assert "auth/newfeature.py" in by_path
    assert "README.md" in by_path


# --- Limits / security --------------------------------------------------------


@pytest.mark.asyncio
async def test_revision_validation_rejects_malicious_input(env):
    client, maker, repo_id, root, sha_a, sha_b = env

    bad = [
        "",
        "   ",
        "-x",
        "--cached",
        "abc..def",
        "HEAD; rm -rf /",
        "$(touch pwned)",
        "rev\nrev",
        "a" * 200,
        "refs/..x",
        "bad!char",
    ]
    for rev in bad:
        with pytest.raises(Exception) as excinfo:
            await _run_diff(maker, repo_id, rev, sha_b)
        cls = type(excinfo.value).__name__
        assert cls in (
            "RevisionValidationError",
            "RevisionNotFoundError",
        ), f"{rev!r} raised {cls}"


@pytest.mark.asyncio
async def test_unknown_revision_is_not_found(env):
    client, maker, repo_id, root, sha_a, sha_b = env
    # A well-formed but unknown hex SHA must not be resolved remotely here —
    # the fixture has no remote, so this fails resolution.
    with pytest.raises(Exception):
        await _run_diff(maker, repo_id, "0000000000000000000000000000000000000000", sha_b)


@pytest.mark.asyncio
async def test_same_base_and_head_rejected(env):
    client, maker, repo_id, root, sha_a, sha_b = env
    with pytest.raises(ValueError):
        await _run_diff(maker, repo_id, sha_a, sha_a)


# --- API ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_create_diff(env):
    client, maker, repo_id, root, sha_a, sha_b = env

    resp = await client.post(
        f"/api/repositories/{repo_id}/diff",
        json={"base_revision": sha_a, "head_revision": sha_b},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["repository_id"] == repo_id
    assert data["files_changed"] >= 5
    assert data["insertions"] > 0
    assert data["deletions"] > 0

    diff_id = data["id"]

    detail = await client.get(f"/api/repositories/{repo_id}/diff/{diff_id}")
    assert detail.status_code == 200
    body = detail.json()
    paths = {f["path"] for f in body["files"]}
    assert "auth/service.py" in paths
    assert "auth/utils.py" in paths
    assert len(body["symbols"]) >= 2

    files_resp = await client.get(f"/api/repositories/{repo_id}/diff/{diff_id}/files")
    assert files_resp.status_code == 200
    assert len(files_resp.json()) >= 5

    symbols_resp = await client.get(f"/api/repositories/{repo_id}/diff/{diff_id}/symbols")
    assert symbols_resp.status_code == 200
    assert len(symbols_resp.json()) >= 2

    # Per-file hunks endpoint.
    service_file = next(f for f in body["files"] if f["path"] == "auth/service.py")
    hunks_resp = await client.get(
        f"/api/repositories/{repo_id}/diff/{diff_id}/files/{service_file['id']}"
    )
    assert hunks_resp.status_code == 200
    hunk_body = hunks_resp.json()
    assert hunk_body["file"]["path"] == "auth/service.py"
    assert len(hunk_body["hunks"]) >= 1
    assert hunk_body["hunks"][0]["old_start"] >= 1


@pytest.mark.asyncio
async def test_api_empty_tree_to_head_diff(env):
    """The empty-tree→HEAD API surface returns 201, not the former 500.

    Regression: On a Windows host with cp1252 locale, decoding git diff output
    containing non-ASCII bytes raised UnicodeDecodeError in the reader thread,
    surfacing as a generic 500 via the catch-all handler in routes.py.
    """
    client, maker, repo_id, root, sha_a, sha_b = env
    _ensure_empty_tree(root)
    resp = await client.post(
        f"/api/repositories/{repo_id}/diff",
        json={"base_revision": EMPTY_TREE, "head_revision": sha_b},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["files_changed"] >= 5
    assert data["insertions"] > 0
    assert data["base_revision"] == EMPTY_TREE
    assert data["head_revision"] == sha_b


@pytest.mark.asyncio
async def test_api_idempotent_recreate(env):
    client, maker, repo_id, root, sha_a, sha_b = env

    first = await client.post(
        f"/api/repositories/{repo_id}/diff",
        json={"base_revision": sha_a, "head_revision": sha_b},
    )
    second = await client.post(
        f"/api/repositories/{repo_id}/diff",
        json={"base_revision": sha_a, "head_revision": sha_b},
    )
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]


@pytest.mark.asyncio
async def test_api_idempotent_short_vs_full_sha(env):
    client, maker, repo_id, root, sha_a, sha_b = env
    short = await client.post(
        f"/api/repositories/{repo_id}/diff",
        json={"base_revision": sha_a[:7], "head_revision": sha_b[:7]},
    )
    full = await client.post(
        f"/api/repositories/{repo_id}/diff",
        json={"base_revision": sha_a, "head_revision": sha_b},
    )
    assert short.status_code == 201, short.text
    assert full.status_code == 201, full.text
    assert short.json()["id"] == full.json()["id"]
    async with maker() as session:
        rows = (
            await session.execute(select(Diff).where(Diff.repository_id == repo_id))
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].base_revision == sha_a
        assert rows[0].head_revision == sha_b


@pytest.mark.asyncio
async def test_api_unknown_revision_returns_error(env):
    client, maker, repo_id, root, sha_a, sha_b = env
    resp = await client.post(
        f"/api/repositories/{repo_id}/diff",
        json={"base_revision": "unknown!!", "head_revision": sha_b},
    )
    assert resp.status_code in (400, 422)
    resp2 = await client.post(
        f"/api/repositories/{repo_id}/diff",
        json={"base_revision": "0000000000000000000000000000000000000000", "head_revision": sha_b},
    )
    assert resp2.status_code in (404, 422)


@pytest.mark.asyncio
async def test_api_missing_diff_404(env):
    client, maker, repo_id, root, sha_a, sha_b = env
    resp = await client.get(f"/api/repositories/{repo_id}/diff/999999")
    assert resp.status_code == 404
    resp2 = await client.get(f"/api/repositories/{repo_id}/diff/999999/files")
    assert resp2.status_code == 404
    resp3 = await client.get(f"/api/repositories/{repo_id}/diff/999999/symbols")
    assert resp3.status_code == 404


@pytest.mark.asyncio
async def test_api_unknown_repository_404(env):
    client, maker, repo_id, root, sha_a, sha_b = env
    resp = await client.post(
        "/api/repositories/424242/diff",
        json={"base_revision": sha_a, "head_revision": sha_b},
    )
    assert resp.status_code == 404


async def _diff_files(maker, diff_id: int) -> list[DiffFile]:
    async with maker() as session:
        return (
            await session.execute(
                select(DiffFile).where(DiffFile.diff_id == diff_id).order_by(DiffFile.path)
            )
        ).scalars().all()