"""Tests for AnalysisService: end-to-end parse persistence and counters."""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.analysis.service import AnalysisService, module_of_path
from app.core.database import Base
from app.models.orm import Export, FileRecord, Import, ParseResult, Repository, Symbol
from app.repositories.languages import detect_language


@pytest.fixture
async def db_session(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'test.db'}", echo=False
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


@pytest.fixture
def workspace(tmp_path) -> Path:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text(
        "import json\n\n"
        "class App:\n"
        "    def run(self):\n"
        "        return 1\n\n"
        "def main() -> int:\n"
        "    return 1\n",
        encoding="utf-8",
    )
    (root / "src" / "util.js").write_text(
        "import { x } from './x.js';\n"
        "const helper = require('./helper.js');\n"
        "export function go(a) { return a; }\n",
        encoding="utf-8",
    )
    (root / "src" / "bad.py").write_text("def broken(:\n    pass\n", encoding="utf-8")
    (root / "README.md").write_text("# docs\n", encoding="utf-8")
    huge = root / "src" / "huge.py"
    huge.write_text("# pad\n" + "# " * 2000, encoding="utf-8")
    return root


async def _make_repository(db: AsyncSession, workspace: Path, db_path: Path):
    repo = Repository(
        url="https://github.com/example/example",
        owner="example",
        name="example",
        status="ready",
        file_count=0,
        local_path=str(workspace),
    )
    db.add(repo)
    await db.flush()

    rows = []
    for path in sorted(Path(workspace).rglob("*")):
        if path.is_file():
            rows.append(
                FileRecord(
                    repository_id=repo.id,
                    path=path.relative_to(workspace).as_posix(),
                    language=detect_language(path),
                    line_count=1,
                    size_bytes=path.stat().st_size,
                    analyzed=True,
                )
            )
    db.add_all(rows)
    repo.file_count = len(rows)
    await db.commit()
    return repo.id


def test_module_of_path() -> None:
    assert module_of_path("auth/service.py") == "auth.service"
    assert module_of_path("app/__init__.py") == "app"
    assert module_of_path("util.js") == "util"
    assert module_of_path("x/App.tsx") == "x.App"


@pytest.mark.asyncio
async def test_parse_pipeline_persists_rows_and_counters(
    db_session, workspace, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        AnalysisService(  # noqa: BLE001 - settings singleton override helper
            db_session
        ).settings,
        "max_file_size_bytes",
        512,
    )
    repo_id = await _make_repository(db_session, workspace, tmp_path)

    summary = await AnalysisService(db_session).parse_repository(repo_id)

    assert summary["analyzed"] is True
    assert summary["file_count"] == 5
    assert summary["files_processed"] == 5
    assert summary["statuses"] == {
        "parsed": 2,  # app.py, util.js
        "syntax_error": 1,  # bad.py
        "unsupported": 1,  # README.md
        "failed": 1,  # huge.py over limit
    }
    assert summary["symbol_count"] >= 4  # App, App.run, main, go (each + imports)
    assert summary["import_count"] >= 2  # import json (python) + require (commonjs)
    assert summary["export_count"] >= 1  # export function go

    repo = await db_session.get(Repository, repo_id)
    assert repo.symbol_count == summary["symbol_count"]
    assert repo.import_count == summary["import_count"]
    assert repo.parsed_file_count == 2
    assert repo.syntax_error_count == 1
    assert repo.unsupported_count == 1
    assert repo.parse_error_count == 1
    assert repo.analyzed is True
    assert repo.analyzed_at is not None

    parse_rows = (
        (
            await db_session.execute(
                select(ParseResult).where(ParseResult.repository_id == repo_id)
            )
        )
        .scalars()
        .all()
    )
    statuses = {row.file_id: row.status for row in parse_rows}
    assert len(parse_rows) == 5


@pytest.mark.asyncio
async def test_symbol_parent_and_qualified_names(
    db_session, workspace, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        AnalysisService(db_session).settings, "max_file_size_bytes", 512
    )
    repo_id = await _make_repository(db_session, workspace, tmp_path)
    await AnalysisService(db_session).parse_repository(repo_id)

    app_row = (
        await db_session.execute(
            select(FileRecord).where(
                FileRecord.repository_id == repo_id,
                FileRecord.path == "src/app.py",
            )
        )
    ).scalar_one()
    symbols = (
        (await db_session.execute(select(Symbol).where(Symbol.file_id == app_row.id)))
        .scalars()
        .all()
    )
    app_sym = next(s for s in symbols if s.name == "App")
    run_sym = next(s for s in symbols if s.name == "run")
    assert run_sym.parent_symbol_id == app_sym.id
    assert run_sym.qualified_name == "src.app.App.run"
    assert run_sym.kind == "FUNCTION"
    assert app_sym.line_start == 3


@pytest.mark.asyncio
async def test_commonjs_and_esm_persisted(db_session, workspace, tmp_path) -> None:
    repo_id = await _make_repository(db_session, workspace, tmp_path)
    await AnalysisService(db_session).parse_repository(repo_id)

    js_row = (
        await db_session.execute(
            select(FileRecord).where(
                FileRecord.repository_id == repo_id,
                FileRecord.path == "src/util.js",
            )
        )
    ).scalar_one()
    imports = (
        (await db_session.execute(select(Import).where(Import.file_id == js_row.id)))
        .scalars()
        .all()
    )
    kinds = {row.kind for row in imports}
    assert kinds == {"es_module", "commonjs"}
    exports = (
        (await db_session.execute(select(Export).where(Export.file_id == js_row.id)))
        .scalars()
        .all()
    )
    assert len(exports) >= 1


@pytest.mark.asyncio
async def test_reparse_is_idempotent(db_session, workspace, tmp_path) -> None:
    repo_id = await _make_repository(db_session, workspace, tmp_path)
    service = AnalysisService(db_session)

    first = await service.parse_repository(repo_id)
    second = await service.parse_repository(repo_id)

    async def count(model) -> int:
        result = await db_session.execute(
            select(func.count()).select_from(model).where(
                model.repository_id == repo_id
            )
        )
        return result.scalar_one()

    assert second["symbol_count"] == first["symbol_count"]
    assert await count(Symbol) == first["symbol_count"]
    assert await count(Import) == first["import_count"]
    assert await count(Export) == first["export_count"]
    assert await count(ParseResult) == first["files_processed"]


@pytest.mark.asyncio
async def test_parse_not_automatic_before_run(db_session, workspace, tmp_path) -> None:
    repo_id = await _make_repository(db_session, workspace, tmp_path)
    summary = await AnalysisService(db_session).get_summary(repo_id)
    assert summary["analyzed"] is False
    assert summary["symbol_count"] == 0


@pytest.mark.asyncio
async def test_parse_missing_repo_raises(db_session) -> None:
    with pytest.raises(LookupError):
        await AnalysisService(db_session).parse_repository(9999)
    with pytest.raises(LookupError):
        await AnalysisService(db_session).get_summary(9999)


@pytest.mark.asyncio
async def test_parse_not_ready_raises(db_session, tmp_path) -> None:
    repo = Repository(
        url="https://github.com/example/pending",
        owner="example",
        name="pending",
        status="indexing",
        local_path=str(tmp_path),
    )
    db_session.add(repo)
    await db_session.commit()
    with pytest.raises(RuntimeError):
        await AnalysisService(db_session).parse_repository(repo.id)


@pytest.mark.asyncio
async def test_read_source_blocks_escape(db_session, workspace, tmp_path) -> None:
    repo_id = await _make_repository(db_session, workspace, tmp_path)
    repo = await db_session.get(Repository, repo_id)
    from app.repositories.discovery import PathEscapeError

    with pytest.raises(PathEscapeError):
        await AnalysisService(db_session).read_source(repo, "../../etc/passwd")
    body = await AnalysisService(db_session).read_source(repo, "src/app.py")
    assert body.startswith(b"import json")