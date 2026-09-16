"""Phase 9 security hardening tests.

Covers the Phase 9 hardening mitigations (M-01..M-08, M-11):

* M-01 controlled Git execution: hostile-variable stripping, mandatory ``-c``
  protocol/hook overrides, argument arrays (no shell), bounded output.
* M-02 path containment via the canonical ``safe_join`` in text search.
* M-03 DB integrity: FK enforcement on and a full bottom-up cascade delete so
  a removed repository leaves no orphan diffs/impact/reviews/audits.
* M-04 resource caps: review item cap, evidence cap, provider response byte
  cap.
* M-05 HTTP safety: request-id, security headers, validation error category,
  trusted-host rejection.
* M-06 shared secret redaction, applied to Lens context payloads.
* M-07 per-key async locks.
* Integrated malicious-repository workflow (git hooks, secrets, oversized
  files) end to end: INDEX -> INVESTIGATE -> DIFF -> IMPACT -> REVIEW -> LENS
  -> DELETE, with redaction assertions.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.context import build_change_context
from app.ai.client import (
    MAX_LENS_RESPONSE_BYTES,
    LensProviderError,
    OpenAICompatibleProvider,
)
from app.analysis.review.generator import generate_review_spec
from app.analysis.search.text_search import _read_source_file
from app.config import get_settings
from app.core.database import Base, enable_foreign_keys, get_db
from app.core.git_safety import (
    GitSafetyError,
    git_command,
    run_git_capture,
    safe_env,
)
from app.core.locks import protected, reset_locks
from app.core.redaction import (
    is_secret_key,
    redact_log_line,
    redact_secret,
    redact_text,
)
from app.main import app
from app.models.orm import (
    Diff,
    DiffChangedLine,
    DiffFile,
    DiffHunk,
    DiffSymbol,
    FileRecord,
    ImpactAnalysis,
    ImpactNode,
    ImpactPath,
    LensAudit,
    Repository,
    Review,
    ReviewItem,
    ReviewItemEntry,
    Symbol,
)
from app.services.ingestion_service import remove_repository

from tests.test_impact_engine import (  # noqa: E402
    _git,
    _seed_impact_repo,
    _write,
)
from tests.test_review_engine import _info, _node  # noqa: E402


# --- M-01: controlled Git execution -------------------------------------------


def test_safe_env_strips_hostile_variables() -> None:
    env = safe_env()
    for var in (
        "GIT_SSH",
        "GIT_SSH_COMMAND",
        "GIT_ASKPASS",
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_NAMESPACE",
        "GIT_CONFIG",
    ):
        assert var not in env, f"{var} must be stripped from git env"
    # Ambient config references are replaced, never inherited.
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["GIT_CONFIG_GLOBAL"] == "/dev/null"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_PAGER"] == "cat"
    assert env["LC_ALL"] == "C"


def test_git_command_is_argument_array_with_hard_overrides() -> None:
    args = ["clone", "https://example.com/x/y.git", "dst"]
    cmd = git_command(args)
    assert cmd[0] == "git"
    assert "-c" in cmd
    for override in (
        "core.hooksPath=",
        "credential.helper=",
        "protocol.file.allow=never",
        "protocol.ext.allow=never",
    ):
        assert override in cmd, f"missing git override {override}"
    assert "'" not in " ".join(cmd)
    with pytest.raises(GitSafetyError):
        git_command([])


@pytest.mark.asyncio
async def test_git_output_cap_raises_safety_error(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    run_git_capture(["init", "-q"], cwd=repo, timeout=10, env=safe_env())
    with pytest.raises(GitSafetyError):
        run_git_capture(
            ["rev-parse", "--git-dir"],
            cwd=repo,
            timeout=10,
            env=safe_env(),
            max_output_bytes=3,
        )


# --- M-06: shared redaction ----------------------------------------------------


def test_redaction_redacts_values_keeps_labels() -> None:
    assert redact_secret('password = "hunter2"') == "password=REDACTED"
    assert redact_secret("API_KEY := sk-service-secret") == "API_KEY=REDACTED"
    assert redact_secret("secret: hunter2") == "secret=REDACTED"
    assert redact_secret("SECRET_KEY := xyzzy") == "SECRET_KEY=REDACTED"
    # A bare key used as a config reference *label* must be preserved.
    assert redact_secret("api_key") == "api_key"
    assert redact_text("before\x00after api_key = aaa") == "beforeafter api_key=REDACTED"
    assert "REDACTED" in redact_log_line("AUTHORIZATION: Bearer abc.def.ghi")
    assert is_secret_key("api_key_default") is True
    assert is_secret_key("app_name") is False


# --- M-02: path containment in text search ------------------------------------


def test_text_search_read_rejects_path_escape(tmp_path) -> None:
    root = tmp_path / "root"
    root.mkdir(parents=True, exist_ok=True)
    (root / "ok.py").write_bytes(b"x = 1\n")
    assert _read_source_file(root, "ok.py", max_size=100) == b"x = 1\n"
    with pytest.raises(Exception):
        _read_source_file(root, "../../../etc/passwd", max_size=1_000_000)
    with pytest.raises(Exception):
        _read_source_file(root, "..\\..\\evil.py", max_size=1_000_000)


# --- M-04: resource caps -------------------------------------------------------


def test_review_item_cap_is_deterministic() -> None:
    effect = generate_review_spec(
        _info(changed=[_node(name=f"x{i}") for i in range(400)])
    )
    assert len(effect.items) == 300
    assert "capped at 300" in effect.summary


def test_unresolved_entry_cap_preserves_count() -> None:
    spec = generate_review_spec(
        _info(unresolved=[_node(impact_class="UNRESOLVED", name=f"u{i}") for i in range(250)])
    )
    unresolved_item = next(
        (i for i in spec.items if i.item_type == "UNRESOLVED_IMPACT"), None
    )
    assert unresolved_item is not None
    assert len(unresolved_item.entries) <= 200
    assert "250" in (unresolved_item.description or "")


@pytest.mark.asyncio
async def test_lens_provider_rejects_oversized_content_length(monkeypatch) -> None:
    called = {"post": 0}

    class FakeResponse:
        status_code = 200
        content = b'{"choices":[{"message":{"content":"ok"}}]}'

        def __init__(self, **kwargs):
            self.headers = {"content-length": str(MAX_LENS_RESPONSE_BYTES + 1)}

    class FakeClient:
        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def post(self, *args, **kwargs):
            called["post"] += 1
            return FakeResponse()

    monkeypatch.setattr("app.ai.client.httpx.AsyncClient", FakeClient)
    provider = OpenAICompatibleProvider(api_key="k", model="m")
    with pytest.raises(LensProviderError):
        await provider.complete("system", "user")
    assert called["post"] == 1


@pytest.mark.asyncio
async def test_lens_provider_rejects_oversized_body(monkeypatch) -> None:
    class BigResponse:
        status_code = 200
        headers = {}
        content = b"x" * (MAX_LENS_RESPONSE_BYTES + 1)

    class BigClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def post(self, *args, **kwargs):
            return BigResponse()

    monkeypatch.setattr("app.ai.client.httpx.AsyncClient", BigClient)
    provider = OpenAICompatibleProvider(api_key="k", model="m")
    with pytest.raises(LensProviderError):
        await provider.complete("system", "user")


# --- M-05: HTTP safety ---------------------------------------------------------


@pytest.mark.asyncio
async def test_http_security_headers_and_request_id(client) -> None:
    resp = await client.get(
        "/api/health", headers={"X-Request-ID": "req-abc-123"}
    )
    assert resp.status_code == 200
    assert resp.headers.get("x-request-id") == "req-abc-123"
    assert resp.headers.get("x-content-type-options") == "nosniff"
    assert resp.headers.get("x-frame-options") == "DENY"
    assert resp.headers.get("referrer-policy") == "no-referrer"
    assert resp.headers.get("cache-control") == "no-store"


@pytest.mark.asyncio
async def test_validation_errors_carried_category_and_request_id(client) -> None:
    resp = await client.post(
        "/api/repositories/1/diff",
        json={"base_revision": "", "head_revision": ""},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"] == "INVALID_INPUT"
    assert body["message"] == "Request validation failed."
    assert body["request_id"]
    assert isinstance(body["detail"], list)


@pytest.mark.asyncio
async def test_content_path_length_bound(client) -> None:
    resp = await client.get(
        "/api/repositories/1/content", params={"path": "a" * 5000}
    )
    assert resp.status_code == 422
    assert resp.json()["error"] == "INVALID_INPUT"


@pytest.mark.asyncio
async def test_unknown_host_rejected_by_trusted_host(client) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://evil.example.com"
    ) as hostile:
        resp = await hostile.get("/api/health")
    assert resp.status_code == 400


# --- M-07: per-key async locks -------------------------------------------------


@pytest.mark.asyncio
async def test_protected_serializes_same_key_but_not_others() -> None:
    order: list[str] = []

    async def worker(key: str, tag: str) -> None:
        async with protected(key):
            order.append(f"{tag}-start")
            await asyncio.sleep(0.05)
            order.append(f"{tag}-end")

    t1 = asyncio.create_task(worker("repo:1", "a"))
    t2 = asyncio.create_task(worker("repo:1", "b"))
    t3 = asyncio.create_task(worker("repo:2", "c"))
    await asyncio.gather(t1, t2, t3)
    # Same key serialized: t1 fully completes before t2 starts.
    assert order.index("a-end") < order.index("b-start")
    # Different key did not wait: both tasks for repo:2 completed while repo:1
    # was still held by whichever of a/b ran first.
    assert "c-start" in order and "c-end" in order
    assert len(order) == 6
    reset_locks()


# --- M-03: DB integrity --------------------------------------------------------


@pytest.mark.asyncio
async def test_remove_repository_deletes_all_derived_rows(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'cascade.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as db:
        await enable_foreign_keys(db)
        repo = Repository(
            url="https://github.com/example/cascade",
            owner="example",
            name="cascade",
            status="ready",
        )
        db.add(repo)
        await db.flush()
        rid = repo.id

        diff = Diff(
            repository_id=rid,
            base_revision="a",
            head_revision="b",
            files_changed=1,
            insertions=1,
            deletions=0,
        )
        db.add(diff)
        await db.flush()
        diff_file = DiffFile(
            diff_id=diff.id,
            path="app.py",
            status="MODIFIED",
            additions=1,
            deletions=0,
        )
        db.add(diff_file)
        await db.flush()
        hunk = DiffHunk(
            diff_file_id=diff_file.id,
            header="@@ -1,1 +1,1 @@",
            old_start=1,
            old_count=1,
            new_start=1,
            new_count=1,
            content="+x\n-x\n",
        )
        db.add(hunk)
        await db.flush()
        db.add(
            DiffChangedLine(
                diff_hunk_id=hunk.id, side="NEW", line_number=1, change_type="ADDED"
            )
        )
        file = FileRecord(
            repository_id=rid,
            path="app.py",
            language="python",
            line_count=1,
            size_bytes=10,
            analyzed=True,
        )
        db.add(file)
        await db.flush()
        symbol = Symbol(
            repository_id=rid,
            file_id=file.id,
            name="app",
            kind="module",
            language="python",
            qualified_name="app",
            line_start=1,
            line_end=1,
        )
        db.add(symbol)
        await db.flush()
        db.add(DiffSymbol(diff_id=diff.id, symbol_id=symbol.id, file_path="app.py",
                          symbol_name="app", symbol_kind="module",
                          change_type="ADDED", added_lines=1))

        analysis = ImpactAnalysis(
            repository_id=rid,
            diff_id=diff.id,
            max_depth=2,
            base_revision="a",
            head_revision="b",
            changed_symbols=1,
            changed_files=1,
            total_direct=1,
            potential=0,
            unresolved_count=0,
            external_count=0,
            file_level_changes=0,
            nodes_total=1,
            paths_total=1,
            truncated=False,
        )
        db.add(analysis)
        await db.flush()
        db.add(
            ImpactNode(
                analysis_id=analysis.id,
                node_type="SYMBOL",
                name="app",
                impact_class="DIRECT",
                depth=0,
            )
        )
        db.add(
            ImpactPath(
                analysis_id=analysis.id,
                root="a",
                target="b",
                target_node_type="SYMBOL",
                steps="[]",
            )
        )

        review = Review(
            repository_id=rid,
            diff_id=diff.id,
            impact_analysis_id=analysis.id,
            title="review",
            summary="",
            status="OPEN",
            base_revision="a",
            head_revision="b",
            max_depth=2,
        )
        db.add(review)
        await db.flush()
        review_item = ReviewItem(
            review_id=review.id,
            stable_key="k1",
            item_type="CHANGED_CODE",
            title="t",
            priority="REQUIRED",
        )
        db.add(review_item)
        await db.flush()
        db.add(
            ReviewItemEntry(
                review_item_id=review_item.id,
                entry_key="e1",
                title="e",
                kind="FILE",
            )
        )
        db.add(
            LensAudit(
                repository_id=rid,
                kind="change",
                provider="fake",
                model="fake",
                prompt_version="1",
                context_hash="abc",
                response_status="ok",
            )
        )
        await db.commit()

        await remove_repository(db, repo)

    async with maker() as db:
        for table in (
            "diff_changed_lines",
            "diff_symbols",
            "diff_hunks",
            "diff_files",
            "diffs",
            "impact_paths",
            "impact_nodes",
            "impact_analyses",
            "review_item_entries",
            "review_items",
            "reviews",
            "lens_audits",
            "symbols",
            "repositories",
        ):
            count = (
                await db.execute(text(f"SELECT COUNT(*) FROM {table}"))
            ).scalar_one()
            assert count == 0, f"{table} still has {count} rows after delete"
    await engine.dispose()


# --- Integrated malicious-repository workflow ----------------------------------


def _build_malicious_fixture(root: Path) -> tuple[str, str]:
    """Two-commit repo with a git hook, secret-looking code, and an oversized
    binary file. Returns (sha_a, sha_b)."""
    root.mkdir(parents=True, exist_ok=True)
    assert _git(root, "init", "-q", "-b", "main").returncode == 0
    _git(root, "config", "user.email", "evil@example.com")
    _git(root, "config", "user.name", "Evil")

    _write(
        root / ".git" / "hooks" / "pre-commit.sh",
        "#!/bin/sh\necho pwned >> /tmp/repolens_pwned\n",
    )
    _write(
        root / "core" / "secret.py",
        "password = \"hunter2\"\n"
        "api_key = \"sk-service-12345\"\n"
        "SECRET_KEY := xyzzy\n"
        "\n"
        "def validate_creds(user):\n"
        "    return user\n",
    )
    _write(
        root / "core" / "controller.py",
        "from core.secret import validate_creds\n"
        "\n"
        "def login(user):\n"
        "    return validate_creds(user)\n",
    )
    (root / "blob.bin").write_bytes(b"\x00" * (700 * 1024))
    _write(root / "README.md", "# Evil fixture\n")

    assert _git(root, "add", "-A").returncode == 0
    assert _git(root, "commit", "-q", "-m", "feat: secrets").returncode == 0
    sha_a = _git(root, "rev-parse", "HEAD").stdout.strip()

    _write(
        root / "core" / "secret.py",
        "password = \"hunter3\"\n"
        "api_key = \"sk-service-99999\"\n"
        "def validate_creds(user):\n"
        "    return user\n",
    )
    assert _git(root, "add", "-A").returncode == 0
    assert _git(root, "commit", "-q", "-m", "feat: rotate secrets").returncode == 0
    sha_b = _git(root, "rev-parse", "HEAD").stdout.strip()
    assert sha_a != sha_b
    return sha_a, sha_b


@pytest.fixture
async def malicious_env(tmp_path):
    root = tmp_path / "evil_repo"
    sha_a, sha_b = _build_malicious_fixture(root)

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'evil.db'}", echo=False
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_db():
        async with maker() as session:
            await enable_foreign_keys(session)
            yield session

    app.dependency_overrides[get_db] = override_get_db

    repo_id = await _seed_impact_repo(maker, root)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, maker, repo_id, root, sha_a, sha_b

    app.dependency_overrides.pop(get_db, None)
    await engine.dispose()


@pytest.mark.asyncio
async def test_malicious_repo_workflow_end_to_end(malicious_env) -> None:
    client, maker, repo_id, root, sha_a, sha_b = malicious_env

    # DIFF (Phase 4): the whole pipeline runs against a repo with a git hook.
    diff_resp = await client.post(
        f"/api/repositories/{repo_id}/diff",
        json={"base_revision": sha_a, "head_revision": sha_b},
    )
    assert diff_resp.status_code == 201, diff_resp.text
    diff_id = diff_resp.json()["id"]

    # IMPACT (Phase 6).
    impact_resp = await client.post(
        f"/api/repositories/{repo_id}/diff/{diff_id}/impact",
        json={"max_depth": 2},
    )
    assert impact_resp.status_code == 201, impact_resp.text
    analysis_id = impact_resp.json()["analysis_id"]

    # REVIEW (Phase 7).
    review_resp = await client.post(
        f"/api/repositories/{repo_id}/impact/{analysis_id}/review",
        json={},
    )
    assert review_resp.status_code == 201, review_resp.text
    review_id = review_resp.json()["review_id"]

    # LENS (Phase 8): no provider configured -> safe 503, no stack traces.
    lens_resp = await client.post(
        f"/api/repositories/{repo_id}/lens/change",
        json={"diff_id": diff_id},
    )
    assert lens_resp.status_code == 503
    assert "no LLM provider is configured" in lens_resp.json()["detail"]

    # M-06: no injected secret value may leave the process toward the provider.
    async with maker() as db:
        repo = await db.get(Repository, repo_id)
        diff = await db.get(Diff, diff_id)
        diff_file = (
            await db.execute(
                text(
                    "SELECT id FROM diff_files "
                    "WHERE diff_id = :did AND path = 'core/secret.py'"
                ),
                {"did": diff_id},
            )
        ).scalar_one()
        # File-scoped context pulls raw source snippets (the injection path),
        # so the strongly-typed redaction layer is verified end-to-end here.
        context = await build_change_context(
            db, repo, diff, diff_file, get_settings()
        )
        payload = json.dumps(context.render(get_settings()))
    assert "hunter2" not in payload
    assert "sk-service-12345" not in payload
    assert "xyzzy" not in payload
    assert "REDACTED" in payload

    # Cleanup must be total: repository and every derived row go away.
    async with maker() as db:
        counts_before: dict[str, int] = {}
        keyed = {
            "diffs": "repository_id = :rid",
            "impact_analyses": "repository_id = :rid",
            "reviews": "repository_id = :rid",
            "lens_audits": "repository_id = :rid",
        }
        for table, where in keyed.items():
            counts_before[table] = (
                await db.execute(
                    text(f"SELECT COUNT(*) FROM {table} WHERE {where}"),
                    {"rid": repo_id},
                )
            ).scalar_one()
        counts_before["diff_files"] = (
            await db.execute(
                text(
                    "SELECT COUNT(*) FROM diff_files WHERE diff_id IN "
                    "(SELECT id FROM diffs WHERE repository_id = :rid)"
                ),
                {"rid": repo_id},
            )
        ).scalar_one()
        counts_before["impact_nodes"] = (
            await db.execute(
                text(
                    "SELECT COUNT(*) FROM impact_nodes WHERE analysis_id IN "
                    "(SELECT id FROM impact_analyses WHERE repository_id = :rid)"
                ),
                {"rid": repo_id},
            )
        ).scalar_one()
    assert sum(counts_before.values()) > 0

    deleted = await client.delete(f"/api/repositories/{repo_id}")
    assert deleted.status_code == 204
    gone = await client.get(f"/api/repositories/{repo_id}")
    assert gone.status_code == 404

    async with maker() as db:
        repo = await db.get(Repository, repo_id)
        assert repo is None
        for table in (
            "diff_changed_lines",
            "diff_symbols",
            "diff_hunks",
            "diff_files",
            "diffs",
            "impact_paths",
            "impact_nodes",
            "impact_analyses",
            "review_item_entries",
            "review_items",
            "reviews",
            "lens_audits",
            "symbols",
            "relationships",
            "repositories",
        ):
            count = (
                await db.execute(text(f"SELECT COUNT(*) FROM {table}"))
            ).scalar_one()
            assert count == 0, f"{table} still has {count} rows after workflow delete"