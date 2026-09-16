"""Change review workflow (Phase 7): generator, service, and API tests.

Uses the deterministic two-commit fixture from the Phase 6 impact engine:
change ``AuthService.validate_token`` -> callers, tests, an unresolved dynamic
call (``normalize_token``), an external import (``base64``), and a file-level
documentation change (README.md).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.analysis.review.generator import (
    clean_text,
    generate_review_spec,
    redact_secret,
)
from app.analysis.review.generator import _PathIndex
from app.core.database import Base, get_db
from app.main import app
from app.models.orm import Repository
from app.models.schemas import (
    ImpactAnalysisInfo,
    ImpactNodeInfo,
    ImpactPathInfo,
    ImpactStepInfo,
    ImpactSummary,
)

from tests.test_impact_engine import (  # noqa: E402  (helpers are importable)
    _build_git_impact_fixture,
    _run_diff,
    _run_impact,
    _seed_impact_repo,
)

# --- Fixture ------------------------------------------------------------------


@pytest.fixture(scope="module")
def review_git(tmp_path_factory) -> tuple[Path, str, str]:
    root = tmp_path_factory.mktemp("review-fixture") / "repo"
    sha_a, sha_b = _build_git_impact_fixture(root)
    return root, sha_a, sha_b


@pytest.fixture
async def env(tmp_path, review_git):
    """Isolated API environment: fixture repo seeded, parsed, relationships."""
    root, sha_a, sha_b = review_git
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'review.db'}", echo=False
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


async def _create_review(client, repository_id: int, analysis_id: int, body=None):
    return await client.post(
        f"/api/repositories/{repository_id}/impact/{analysis_id}/review",
        json=body or {},
    )


# --- Generator unit tests -----------------------------------------------------


def _node(
    name="thing",
    impact_class="POTENTIAL",
    node_type="SYMBOL",
    via=None,
    depth=1,
    is_test=False,
    is_file_level_change=False,
    file_category="SOURCE",
    file_path="auth/service.py",
    node_id=None,
    evidence_file=None,
    evidence_line=4,
    kind="function",
    change_type=None,
) -> ImpactNodeInfo:
    return ImpactNodeInfo(
        node_type=node_type,
        node_id=node_id,
        name=name,
        kind=kind,
        file_path=file_path,
        file_category=file_category,
        impact_class=impact_class,
        depth=depth,
        line_start=1,
        line_end=3,
        via=via,
        via_source=None,
        evidence_file=evidence_file,
        evidence_line=evidence_line,
        is_test=is_test,
        is_file_level_change=is_file_level_change,
        change_type=change_type,
    )


def _info(
    changed=None,
    potential=None,
    tests=None,
    unresolved=None,
    external=None,
    paths=None,
) -> ImpactAnalysisInfo:
    return ImpactAnalysisInfo(
        analysis_id=42,
        repository_id=1,
        diff_id=2,
        base_revision="a",
        head_revision="b",
        max_depth=2,
        summary=ImpactSummary(),
        changed=changed or [],
        potentially_affected=potential or [],
        tests=tests or [],
        unresolved=unresolved or [],
        external=external or [],
        paths=paths or [],
    )


def _item(spec, item_type):
    return next((i for i in spec.items if i.item_type == item_type), None)


def test_generator_empty_impact_has_no_items():
    spec = generate_review_spec(_info())
    assert spec.total_items == 0
    assert "no review items" in spec.summary.lower()


def test_generator_priorities_and_types():
    changed = [
        _node(name="validate_token", impact_class="DIRECT", file_category="SOURCE", change_type="MODIFIED"),
    ]
    potential = [
        # direct behavior-linked caller -> REQUIRED AFFECTED_CALLER
        _node(name="handle_sync", via="CALLS", depth=1, file_path="auth/controller.py", node_id=42),
        # deeper caller -> RECOMMENDED AFFECTED_CALLER
        _node(name="RequestHandler.handle", via="CALLS", depth=2, file_path="auth/request.py"),
        # IMPORTS group -> AFFECTED_DEPENDENCY RECOMMENDED
        _node(
            name="auth/controller.py",
            node_type="FILE",
            via="IMPORTS",
            depth=1,
            file_path="auth/controller.py",
        ),
    ]
    tests = [
        _node(
            name="test_validate_token",
            via="TESTS",
            depth=1,
            is_test=True,
            file_path="tests/test_auth.py",
        )
    ]
    unresolved = [
        _node(
            name="normalize_token",
            impact_class="UNRESOLVED",
            node_type="UNRESOLVED",
            evidence_file="auth/service.py",
            evidence_line=5,
            file_path="auth/service.py",
        )
    ]
    external = [
        _node(
            name="base64",
            impact_class="EXTERNAL",
            node_type="EXTERNAL",
            evidence_file="auth/service.py",
            file_path="auth/service.py",
        )
    ]
    changed.append(
        _node(
            name="README.md",
            impact_class="DIRECT",
            node_type="FILE",
            file_category="DOCUMENTATION",
            is_file_level_change=True,
            file_path="README.md",
        )
    )
    changed.append(
        _node(
            name="settings.py",
            impact_class="DIRECT",
            node_type="FILE",
            file_category="CONFIG",
            is_file_level_change=True,
            file_path="config/settings.py",
        )
    )

    spec = generate_review_spec(_info(changed=changed, potential=potential, tests=tests, unresolved=unresolved, external=external))

    changed_item = _item(spec, "CHANGED_CODE")
    assert changed_item is not None
    assert changed_item.priority == "REQUIRED"
    assert "validate_token" in changed_item.stable_key
    assert changed_item.entries[0].kind == "FILE"

    caller = _item(spec, "AFFECTED_CALLER")
    assert caller is not None
    assert caller.group_via == "CALLS"
    assert caller.priority == "REQUIRED"
    assert caller.entries[0].symbol_id  # callers carry symbol ids
    assert "handle_sync" in caller.entries[0].title
    # the depth-2 caller is pushed into a RECOMMENDED transitive group
    transitive = next(
        i for i in spec.items
        if i.item_type == "AFFECTED_CALLER" and "transitive" in i.stable_key
    )
    assert transitive.priority == "RECOMMENDED"
    assert "RequestHandler.handle" in transitive.entries[0].title

    dep = _item(spec, "AFFECTED_DEPENDENCY")
    assert dep is not None
    assert dep.priority == "RECOMMENDED"
    assert dep.group_via == "IMPORTS"

    tests_item = _item(spec, "AFFECTED_TEST")
    assert tests_item is not None
    assert tests_item.priority == "REQUIRED"
    assert all(e.kind == "TEST" for e in tests_item.entries)

    unresolved_item = _item(spec, "UNRESOLVED_IMPACT")
    assert unresolved_item is not None
    assert unresolved_item.priority == "RECOMMENDED"

    external_item = _item(spec, "EXTERNAL_DEPENDENCY")
    assert external_item is not None
    assert external_item.priority == "RECOMMENDED"

    config_item = _item(spec, "CONFIGURATION_CHANGE")
    assert config_item is not None
    assert config_item.priority == "REQUIRED"

    doc_item = _item(spec, "DOCUMENTATION_CHANGE")
    assert doc_item is not None
    assert doc_item.priority == "INFORMATIONAL"

    # Aggregated items must list every entry
    assert any(i.item_type == "AFFECTED_CALLER" for i in spec.items)
    assert any(
        i.item_type == "AFFECTED_DEPENDENCY" and len(i.entries) == 1
        for i in spec.items
    )
    assert all(len(i.entries) <= 200 for i in spec.items)


def test_generator_deterministic_stable_keys():
    changed = [
        _node(name="validate_token", impact_class="DIRECT", change_type="MODIFIED"),
        _node(
            name="README.md", impact_class="DIRECT", node_type="FILE",
            file_category="DOCUMENTATION", is_file_level_change=True,
            file_path="README.md",
        ),
    ]
    potential = [
        _node(name="handle_sync", via="CALLS", depth=1, file_path="auth/controller.py"),
        _node(
            name="auth/controller.py", node_type="FILE", via="IMPORTS",
            depth=1, file_path="auth/controller.py",
        ),
    ]
    tests = [_node(name="test_validate_token", via="TESTS", is_test=True, file_path="tests/test_auth.py")]
    unresolved = [_node(name="normalize_token", impact_class="UNRESOLVED", node_type="UNRESOLVED")]
    external = [_node(name="base64", impact_class="EXTERNAL", node_type="EXTERNAL")]

    base = _info(changed=changed, potential=potential, tests=tests, unresolved=unresolved, external=external)
    first = generate_review_spec(base)
    second = generate_review_spec(base)

    keys = lambda spec: [(i.item_type, i.stable_key) for i in spec.items]  # noqa: E731
    assert keys(first) == keys(second)
    # per-type titles are deterministic too
    assert [i.title for i in first.items if i.item_type == "AFFECTED_TEST"] == [
        i.title for i in second.items if i.item_type == "AFFECTED_TEST"
    ]


def test_generator_entry_cap_limits_large_groups():
    unresolved = [
        _node(
            name=f"unresolved_{i}",
            impact_class="UNRESOLVED",
            node_type="UNRESOLVED",
            evidence_file="auth/service.py",
            evidence_line=i,
        )
        for i in range(250)
    ]
    spec = generate_review_spec(_info(unresolved=unresolved))
    item = _item(spec, "UNRESOLVED_IMPACT")
    assert item is not None
    assert len(item.entries) == 200
    assert "250" in item.title
    assert "capped" in item.description or "first 200" in item.description


def test_generator_steps_attribute_path_to_affected_code():
    path = ImpactPathInfo(
        root="AuthService",
        target="handle_sync",
        target_node_type="SYMBOL",
        target_node_id=3,
        depth=1,
        steps=[
            ImpactStepInfo(
                source="AuthService",
                relationship="CALLS",
                target="handle_sync",
                evidence="auth/controller.py:4",
            )
        ],
    )
    changed = [_node(name="validate_token", impact_class="DIRECT", change_type="MODIFIED")]
    potential = [_node(name="handle_sync", via="CALLS", depth=1, file_path="auth/controller.py")]
    spec = generate_review_spec(_info(changed=changed, potential=potential, paths=[path]))

    caller = _item(spec, "AFFECTED_CALLER")
    entry = next(e for e in caller.entries if "handle_sync" in e.title)
    assert entry.path_steps[0]["relationship"] == "CALLS"
    assert entry.path_steps[0]["evidence"] == "auth/controller.py:4"


def test_generator_secret_redaction_and_sanitization():
    redacted = redact_secret('config_value = "API_KEY=supersecret abc"')
    assert "supersecret" not in redacted
    assert "REDACTED" in redacted
    assert redact_secret("no secrets here") == "no secrets here"

    cleaned = clean_text("evil\x00name<script>alert(1)</script>\x07")
    assert "\x00" not in cleaned and "\x07" not in cleaned

    changed = [
        _node(
            name="settings.py",
            impact_class="DIRECT",
            node_type="FILE",
            file_category="CONFIG",
            is_file_level_change=True,
            change_type="MODIFIED",
            file_path="config/settings.py",
        )
    ]
    spec = generate_review_spec(_info(changed=changed))
    config_item = _item(spec, "CONFIGURATION_CHANGE")
    assert config_item is not None
    assert any(e.file_path == "config/settings.py" for e in config_item.entries)
    # config items reference file+line only; the generator never stores raw
    # config file content, so no assignment appears in any config text
    assert "=" not in config_item.description
    assert all(
        not any(c < " " and c not in "\t\n\r" for c in e.title + e.description)
        for e in config_item.entries
    )

    # hostile file paths are stored as inert text; generator never touches the FS
    hostile = _node(
        name="../../../../etc/passwd",
        impact_class="DIRECT",
        change_type="MODIFIED",
    )
    hostile_spec = generate_review_spec(_info(changed=[hostile]))
    assert _item(hostile_spec, "CHANGED_CODE") is not None


def test_generator_index_shortest_path():
    p1 = ImpactPathInfo(root="A", target="x", target_node_type="SYMBOL", depth=2, steps=[ImpactStepInfo(source="A", relationship="CALLS", target="y", evidence="a.py:1"), ImpactStepInfo(source="y", relationship="CALLS", target="x", evidence="b.py:2")])
    p2 = ImpactPathInfo(root="A", target="x", target_node_type="SYMBOL", depth=1, steps=[ImpactStepInfo(source="A", relationship="CALLS", target="x", evidence="c.py:3")])
    index = _PathIndex(_info(paths=[p1, p2]))
    steps = index.steps_for("x")
    assert len(steps) == 1
    assert steps[0]["evidence"] == "c.py:3"
    assert index.steps_for("missing") == []


# --- API / service integration tests ------------------------------------------


async def _prepared(client, maker, repo_id, sha_a, sha_b):
    """Diff + impact for the fixture; returns (diff_id, analysis_id)."""
    diff = await _run_diff(maker, repo_id, sha_a, sha_b)
    info = await _run_impact(maker, repo_id, diff["id"], 2)
    return diff, info


def _item_of(review, item_type):
    return next((i for i in review["items"] if i["item_type"] == item_type), None)


async def test_review_end_to_end(env):
    client, maker, repo_id, root, sha_a, sha_b = env
    _, info = await _prepared(client, maker, repo_id, sha_a, sha_b)

    response = await _create_review(client, repo_id, info.analysis_id)
    assert response.status_code == 201
    review = response.json()

    assert review["repository_id"] == repo_id
    assert review["diff_id"] == info.diff_id
    assert review["impact_analysis_id"] == info.analysis_id

    # all supported categories backed by actual findings are present
    for item_type in (
        "CHANGED_CODE",
        "AFFECTED_CALLER",
        "AFFECTED_DEPENDENCY",
        "AFFECTED_TEST",
        "UNRESOLVED_IMPACT",
        "EXTERNAL_DEPENDENCY",
        "DOCUMENTATION_CHANGE",
    ):
        assert _item_of(review, item_type) is not None, item_type
    # no config file exists in the fixture, so no CONFIG item is fabricated
    assert _item_of(review, "CONFIGURATION_CHANGE") is None

    # summary counters are internally consistent
    s = review["review_summary"]
    by_priority = {}
    for i in review["items"]:
        by_priority[i["priority"]] = by_priority.get(i["priority"], 0) + 1
    assert s["total_items"] == len(review["items"])
    assert s["required"] == by_priority.get("REQUIRED", 0)
    assert s["recommended"] == by_priority.get("RECOMMENDED", 0)
    assert s["informational"] == by_priority.get("INFORMATIONAL", 0)
    assert s["open"] == len(review["items"])
    assert s["completed"] == 0

    # deterministic ordering: REQUIRED before RECOMMENDED before INFORMATIONAL
    priorities = [i["priority"] for i in review["items"]]
    assert priorities == sorted(
        priorities, key=lambda p: {"REQUIRED": 0, "RECOMMENDED": 1, "INFORMATIONAL": 2}[p]
    )

    # key content checks
    tests_item = _item_of(review, "AFFECTED_TEST")
    assert tests_item["priority"] == "REQUIRED"
    assert all(e["kind"] == "TEST" for e in tests_item["entries"])
    assert any("test_auth" in e["file_path"] or "test_auth" in e["evidence_file"] for e in tests_item["entries"])

    callers = [i for i in review["items"] if i["item_type"] == "AFFECTED_CALLER"]
    assert callers, "expected AFFECTED_CALLER items"
    direct = next(
        i for i in callers if "handle_sync" in " ".join(e["title"] for e in i["entries"])
    )
    assert direct["priority"] == "REQUIRED"
    assert any(e["path_steps"] for e in direct["entries"])
    # depth-2 RequestHandler lives in a separate RECOMMENDED transitive bucket
    transitive = next(
        i for i in callers
        if any("RequestHandler" in e["title"] for e in i["entries"])
    )
    assert transitive["priority"] == "RECOMMENDED"
    assert "transitive" in transitive["title"] or "transitive" in transitive["description"]

    unresolved_item = _item_of(review, "UNRESOLVED_IMPACT")
    assert unresolved_item["priority"] == "RECOMMENDED"
    assert any("normalize_token" in e["title"] for e in unresolved_item["entries"])

    external_item = _item_of(review, "EXTERNAL_DEPENDENCY")
    assert any("base64" in e["title"] for e in external_item["entries"])

    doc_item = _item_of(review, "DOCUMENTATION_CHANGE")
    assert doc_item["priority"] == "INFORMATIONAL"
    assert any("README" in e["title"] for e in doc_item["entries"])

    # summary exists and explains provenance
    assert "no LLM" in review["summary"]
    assert "#" in review["summary"]

    # GET returns a byte-identical body to POST
    get = await client.get(
        f"/api/repositories/{repo_id}/reviews/{review['review_id']}"
    )
    assert get.status_code == 200
    assert get.json() == review


async def test_review_idempotent_reuse(env):
    client, maker, repo_id, root, sha_a, sha_b = env
    _, info = await _prepared(client, maker, repo_id, sha_a, sha_b)

    first = await _create_review(client, repo_id, info.analysis_id)
    second = await _create_review(client, repo_id, info.analysis_id)
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["review_id"] == second.json()["review_id"]


async def test_review_status_and_notes_persistence(env):
    client, maker, repo_id, root, sha_a, sha_b = env
    _, info = await _prepared(client, maker, repo_id, sha_a, sha_b)
    review = (await _create_review(client, repo_id, info.analysis_id)).json()

    item = _item_of(review, "CHANGED_CODE")
    item_id = item["item_id"]
    patch = await client.patch(
        f"/api/repositories/{repo_id}/reviews/{review['review_id']}/items/{item_id}",
        json={"status": "DONE", "notes": "verified against spec"},
    )
    assert patch.status_code == 200
    assert patch.json()["status"] == "DONE"
    assert patch.json()["notes"] == "verified against spec"

    entry = item["entries"][0]
    entry_id = entry["entry_id"]
    epatch = await client.patch(
        f"/api/repositories/{repo_id}/reviews/{review['review_id']}/items/{item_id}/entries/{entry_id}",
        json={"status": "DONE"},
    )
    assert epatch.status_code == 200
    assert epatch.json()["status"] == "DONE"

    get = (await client.get(
        f"/api/repositories/{repo_id}/reviews/{review['review_id']}"
    )).json()
    done_item = _item_of(get, "CHANGED_CODE")
    assert done_item["status"] == "DONE"
    assert done_item["notes"] == "verified against spec"
    assert get["review_summary"]["completed"] == 1
    done_entry = next(e for e in done_item["entries"] if e["entry_id"] == entry_id)
    assert done_entry["status"] == "DONE"


async def test_review_item_validation(env):
    client, maker, repo_id, root, sha_a, sha_b = env
    _, info = await _prepared(client, maker, repo_id, sha_a, sha_b)
    review = (await _create_review(client, repo_id, info.analysis_id)).json()
    item_id = _item_of(review, "CHANGED_CODE")["item_id"]
    url = f"/api/repositories/{repo_id}/reviews/{review['review_id']}/items/{item_id}"

    bad = await client.patch(url, json={"status": "SBROLLING"})
    assert bad.status_code == 422
    too_long = await client.patch(url, json={"notes": "x" * 5000})
    assert too_long.status_code == 422
    entry_bad = await client.patch(
        url + "/entries/1",
        json={"status": "MAYBE"},
    )
    assert entry_bad.status_code == 422


async def test_review_regenerate_carries_state(env):
    client, maker, repo_id, root, sha_a, sha_b = env
    _, info = await _prepared(client, maker, repo_id, sha_a, sha_b)

    review = (await _create_review(client, repo_id, info.analysis_id)).json()
    item = _item_of(review, "CHANGED_CODE")
    await client.patch(
        f"/api/repositories/{repo_id}/reviews/{review['review_id']}/items/{item['item_id']}",
        json={"status": "DONE", "notes": "keep me"},
    )

    fresh = await _create_review(
        client, repo_id, info.analysis_id, body={"regenerate": True}
    )
    assert fresh.status_code == 201
    fresh = fresh.json()
    assert fresh["review_id"] != review["review_id"]
    # stable identity carried item status + notes forward
    carried = _item_of(fresh, "CHANGED_CODE")
    assert carried["status"] == "DONE"
    assert carried["notes"] == "keep me"
    # the old review is untouched and still readable
    old = (await client.get(
        f"/api/repositories/{repo_id}/reviews/{review['review_id']}"
    )).json()
    assert old["review_id"] == review["review_id"]


async def test_review_content_is_deterministic_across_requests(env):
    client, maker, repo_id, root, sha_a, sha_b = env
    _, info = await _prepared(client, maker, repo_id, sha_a, sha_b)
    a = (await _create_review(client, repo_id, info.analysis_id)).json()
    b = (await _create_review(client, repo_id, info.analysis_id, body={"regenerate": True})).json()
    key = lambda r: [(i["item_type"], i["title"], i["priority"]) for i in r["items"]]  # noqa: E731
    assert key(a) == key(b)


async def test_review_repository_isolation(env):
    client, maker, repo_id, root, sha_a, sha_b = env
    _, info = await _prepared(client, maker, repo_id, sha_a, sha_b)
    review = (await _create_review(client, repo_id, info.analysis_id)).json()

    # a second repository gets none of the first repository's reviews
    async with maker() as session:
        other = Repository(
            url="https://github.com/example/other-repo",
            owner="example",
            name="other-repo",
            status="ready",
            local_path=str(root),
        )
        session.add(other)
        await session.flush()
        other_id = other.id
        await session.commit()

    cross = await client.get(
        f"/api/repositories/{other_id}/reviews/{review['review_id']}"
    )
    assert cross.status_code == 404

    # a review cannot be created for another repo's impact analysis
    cross_post = await _create_review(client, other_id, info.analysis_id)
    assert cross_post.status_code == 404

    # item updates are repository-scoped too
    item_id = _item_of(review, "CHANGED_CODE")["item_id"]
    cross_patch = await client.patch(
        f"/api/repositories/{other_id}/reviews/{review['review_id']}/items/{item_id}",
        json={"status": "DONE"},
    )
    assert cross_patch.status_code == 404

    # nonexistent entities 404 cleanly
    assert (await client.get(f"/api/repositories/{repo_id}/reviews/999999")).status_code == 404
    assert (await client.get(f"/api/repositories/999999/reviews/{review['review_id']}")).status_code == 404


async def test_review_patch_unknown_item_404(env):
    client, maker, repo_id, root, sha_a, sha_b = env
    _, info = await _prepared(client, maker, repo_id, sha_a, sha_b)
    review = (await _create_review(client, repo_id, info.analysis_id)).json()
    r = await client.patch(
        f"/api/repositories/{repo_id}/reviews/{review['review_id']}/items/999999",
        json={"status": "DONE"},
    )
    assert r.status_code == 404