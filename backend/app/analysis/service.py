"""Analysis service: repository-wide symbol/import/export extraction.

Orchestrates the deterministic parse pipeline: reset prior results, parse every
stored file through the parser registry, persist normalized rows in one
transaction, and update repository counters. Blocking parsing runs in a thread
executor so the event loop stays responsive; repository files are treated as
untrusted text (never imported or executed).
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.kinds import ParserStatus
from app.analysis.models import ExportNode, ImportNode, SymbolNode
from app.analysis.parsers.registry import (
    get_parser,
    parse_language_for,
    parser_versions,
)
from app.config import get_settings
from app.models.orm import Export, FileRecord, Import, ParseResult, Repository, Symbol
from app.repositories.discovery import PathEscapeError, safe_join


class RepositoryNotFoundError(LookupError):
    pass


class RepositoryNotReadyError(RuntimeError):
    pass


class RepositoryBusyError(RuntimeError):
    pass


@dataclass
class ParsedFile:
    """Result of parsing a single repository file."""

    file_id: int
    path: str
    language: Optional[str] = None  # detected display language (e.g. "Python")
    parse_language: Optional[str] = None  # grammar identifier (e.g. "python")
    size_bytes: int = 0
    status: str = ParserStatus.PARSED.value
    error_message: Optional[str] = None
    parser_id: Optional[str] = None
    symbols: list[SymbolNode] = field(default_factory=list)
    imports: list[ImportNode] = field(default_factory=list)
    exports: list[ExportNode] = field(default_factory=list)


def module_of_path(path: str) -> str:
    """Derive a dotted module name from a repository-relative file path.

    ``auth/service.py`` → ``auth.service``, ``app/__init__.py`` → ``app``.
    """
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    base = parts[-1] if parts else ""
    stem = base.rsplit(".", 1)[0] if "." in base else base
    if stem == "__init__":
        parts = parts[:-1]
    else:
        parts = parts[:-1] + [stem]
    return ".".join(parts)


def _read_source_bytes(root: Path, path: str, max_size: int) -> bytes:
    """Read a file under a workspace root, blocking path escapes."""
    full = safe_join(root, path)
    if full.stat().st_size > max_size:
        raise OverflowError(f"file exceeds maximum parse size ({max_size} bytes)")
    return full.read_bytes()


class AnalysisService:
    """Runs the deterministic parse pipeline for one repository."""

    _running_repositories: set[int] = set()

    def __init__(self, db: AsyncSession):
        self.db = db
        self.settings = get_settings()

    # --- public orchestration -------------------------------------------------

    async def parse_repository(self, repository_id: int) -> dict:
        start = time.perf_counter()
        repository = await self.db.get(Repository, repository_id)
        if repository is None:
            raise RepositoryNotFoundError(f"repository {repository_id} not found")
        if repository.status != "ready":
            raise RepositoryNotReadyError(
                f"repository {repository_id} is not ready (status={repository.status})"
            )
        if repository_id in self._running_repositories:
            raise RepositoryBusyError(f"repository {repository_id} is already parsing")

        self._running_repositories.add(repository_id)
        try:
            return await self._run(repository, start)
        finally:
            self._running_repositories.discard(repository_id)

    async def analyze_repository(self, repository: Repository) -> dict:
        """Run the parse pipeline for a repository row owned by the caller.

        Used by the ingestion pipeline, which holds the repository row while
        its status is still ``indexing`` (so the ``ready``-only guard on
        :meth:`parse_repository` does not apply). Idempotent: prior parse
        results are reset and rebuilt in one transaction.
        """
        start = time.perf_counter()
        return await self._run(repository, start)

    async def _run(self, repository: Repository, start: float) -> dict:
        root = Path(repository.local_path) if repository.local_path else None
        if root is None or not root.is_dir():
            raise RepositoryNotReadyError(
                "repository working tree is missing; re-ingest and try again"
            )

        files = (
            (
                await self.db.execute(
                    select(FileRecord)
                    .where(FileRecord.repository_id == repository.id)
                    .order_by(FileRecord.id)
                )
            )
            .scalars()
            .all()
        )

        # Clear results from any previous run so re-parsing is idempotent.
        await self._reset(repository.id)
        await self.db.commit()

        max_size = self.settings.max_file_size_bytes
        parsed_files = await asyncio.to_thread(
            self._parse_all, root, files, max_size
        )

        await self._persist(repository, parsed_files)
        await self.db.commit()

        duration_ms = int((time.perf_counter() - start) * 1000)
        return self._build_summary(repository, duration_ms)

    # --- full parse run (blocking, thread executor) ---------------------------

    def _parse_all(
        self, root: Path, files: list[FileRecord], max_size: int
    ) -> list[ParsedFile]:
        results: list[ParsedFile] = []
        for file in files:
            results.append(self._parse_one(root, file, max_size))
        return results

    def _parse_one(
        self, root: Path, file: FileRecord, max_size: int
    ) -> ParsedFile:
        parse_language = parse_language_for(file.language, file.path)
        base = ParsedFile(
            file_id=file.id,
            path=file.path,
            language=file.language,
            parse_language=parse_language,
            size_bytes=file.size_bytes,
        )
        if parse_language is None:
            base.status = ParserStatus.UNSUPPORTED.value
            return base
        try:
            if file.size_bytes > max_size:
                raise OverflowError(
                    f"file exceeds maximum parse size ({max_size} bytes)"
                )
            source = _read_source_bytes(root, file.path, max_size)
        except (OSError, OverflowError, PathEscapeError) as exc:
            base.status = ParserStatus.FAILED.value
            base.error_message = f"{type(exc).__name__}: {exc}"
            return base

        parser = get_parser(parse_language)
        module = module_of_path(file.path)
        try:
            outcome = parser.parse(source, module=module)
        except Exception as exc:  # noqa: BLE001 - a hostile file must not crash the run
            logging.getLogger("repolens.analysis").warning(
                "unhandled parse error for %s: %s", file.path, exc
            )
            base.status = ParserStatus.FAILED.value
            base.error_message = f"{type(exc).__name__}: {exc}"
            return base

        base.parser_id = parser.version()
        base.status = outcome.status.value
        base.error_message = outcome.error_message
        base.symbols = outcome.symbols
        base.imports = outcome.imports
        base.exports = outcome.exports
        return base

    # --- persistence ----------------------------------------------------------

    async def _reset(self, repository_id: int) -> None:
        await self.db.execute(
            ParseResult.__table__.delete().where(
                ParseResult.repository_id == repository_id
            )
        )
        await self.db.execute(
            Symbol.__table__.delete().where(Symbol.repository_id == repository_id)
        )
        await self.db.execute(
            Import.__table__.delete().where(Import.repository_id == repository_id)
        )
        await self.db.execute(
            Export.__table__.delete().where(Export.repository_id == repository_id)
        )

    async def _persist(
        self, repository: Repository, parsed_files: list[ParsedFile]
    ) -> None:
        symbol_total = import_total = export_total = 0
        status_counts = {"parsed": 0, "syntax_error": 0, "unsupported": 0, "failed": 0}

        for parsed in parsed_files:
            status_counts[parsed.status] = status_counts.get(parsed.status, 0) + 1
            symbol_total += len(parsed.symbols)
            import_total += len(parsed.imports)
            export_total += len(parsed.exports)

            self.db.add(
                ParseResult(
                    repository_id=repository.id,
                    file_id=parsed.file_id,
                    language=parsed.parse_language,
                    parser_id=parsed.parser_id,
                    status=parsed.status,
                    source_size_bytes=parsed.size_bytes,
                    symbol_count=len(parsed.symbols),
                    import_count=len(parsed.imports),
                    export_count=len(parsed.exports),
                    error_message=parsed.error_message,
                )
            )

            await self._persist_symbols(repository.id, parsed)
            if parsed.imports:
                self.db.add_all(
                    Import(
                        repository_id=repository.id,
                        file_id=parsed.file_id,
                        source=row.source,
                        imported_name=row.imported_name,
                        alias=row.alias,
                        kind=row.kind.value,
                        start_line=row.start_line,
                        end_line=row.end_line,
                    )
                    for row in parsed.imports
                )
            if parsed.exports:
                self.db.add_all(
                    Export(
                        repository_id=repository.id,
                        file_id=parsed.file_id,
                        name=row.name,
                        kind=row.kind.value,
                        start_line=row.start_line,
                        end_line=row.end_line,
                    )
                    for row in parsed.exports
                )

        repository.analyzed = True
        repository.analyzed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        repository.symbol_count = symbol_total
        repository.import_count = import_total
        repository.export_count = export_total
        repository.parsed_file_count = status_counts["parsed"]
        repository.syntax_error_count = status_counts["syntax_error"]
        repository.unsupported_count = status_counts["unsupported"]
        repository.parse_error_count = status_counts["failed"]

    async def _persist_symbols(
        self, repository_id: int, parsed: ParsedFile
    ) -> None:
        """Insert symbols in DFS order so parent links can be resolved."""
        rows: dict[int, Symbol] = {}
        for index, node in enumerate(parsed.symbols):
            row = Symbol(
                repository_id=repository_id,
                file_id=parsed.file_id,
                name=node.name,
                qualified_name=node.qualified_name,
                kind=node.kind.value,
                language=parsed.parse_language,
                line_start=node.start_line,
                line_end=node.end_line,
                start_column=node.start_column,
                end_column=node.end_column,
                exported=node.exported,
                signature=node.signature,
            )
            rows[index] = row
            self.db.add(row)

        await self.db.flush()
        for index, row in rows.items():
            parent_index = parsed.symbols[index].parent_index
            if parent_index is not None and parent_index in rows:
                row.parent_symbol_id = rows[parent_index].id

    # --- read paths -----------------------------------------------------------

    async def get_summary(self, repository_id: int) -> dict:
        repository = await self.db.get(Repository, repository_id)
        if repository is None:
            raise RepositoryNotFoundError(f"repository {repository_id} not found")
        return self._build_summary(repository, 0)

    @staticmethod
    def _build_summary(
        repository: Repository,
        duration_ms: int,
    ) -> dict:
        files_processed = (
            repository.parsed_file_count
            + repository.syntax_error_count
            + repository.unsupported_count
            + repository.parse_error_count
        )
        return {
            "repository_id": repository.id,
            "analyzed": repository.analyzed,
            "analyzed_at": repository.analyzed_at,
            "file_count": repository.file_count,
            "files_processed": files_processed,
            "symbol_count": repository.symbol_count,
            "import_count": repository.import_count,
            "export_count": repository.export_count,
            "statuses": {
                "parsed": repository.parsed_file_count,
                "syntax_error": repository.syntax_error_count,
                "unsupported": repository.unsupported_count,
                "failed": repository.parse_error_count,
            },
            "parser_versions": parser_versions(),
            "duration_ms": duration_ms,
        }

    async def read_source(self, repository: Repository, path: str) -> bytes:
        if repository.local_path is None:
            raise FileNotFoundError("repository working tree is missing")
        max_size = self.settings.max_file_size_bytes
        return await asyncio.to_thread(
            _read_source_bytes, Path(repository.local_path), path, max_size
        )