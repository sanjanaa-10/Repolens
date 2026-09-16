"""Diff engine: orchestrates git diff analysis, symbol mapping, and persistence.

The engine runs as:
1. Validate revisions against the stored repository.
2. Run git diff --numstat for per-file summary.
3. Parse unified diff output per file for hunks and changed lines.
4. Map changed NEW-side lines to existing symbol ranges.
5. Classify files (SOURCE/TEST/CONFIG/DOCUMENTATION).
6. Persist all results idempotently.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.diff.classifier import classify_file
from app.analysis.diff.git_service import (
    GitError,
    RevisionNotFoundError,
    deepen_for_revision,
    ensure_revision_resolvable,
    get_diff_output,
    get_numstat,
    validate_revision_in_repo,
)
from app.analysis.diff import limits
from app.core.locks import protected
from app.models.orm import (
    Diff,
    DiffChangedLine,
    DiffFile,
    DiffHunk,
    DiffSymbol,
    FileRecord,
    Repository,
    Symbol,
)

logger = logging.getLogger("repolens.diff.engine")


@dataclass
class HunkInfo:
    """Parsed unified diff hunk."""
    header: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    content: str
    old_lines: list[int] = field(default_factory=list)
    new_lines: list[int] = field(default_factory=list)
    added_lines: list[tuple[int, str]] = field(default_factory=list)
    deleted_lines: list[tuple[int, str]] = field(default_factory=list)
    old_next: int = 0
    new_next: int = 0


@dataclass
class SymbolMapping:
    """A symbol affected by the diff."""
    symbol_id: int
    symbol_name: str
    symbol_kind: str
    file_path: str
    added_lines: int
    deleted_lines: int


def _parse_hunks(diff_output: str) -> list[HunkInfo]:
    """Parse unified diff output into HunkInfo objects.

    Handles standard unified diff format with optional @@ line ranges.
    """
    hunks: list[HunkInfo] = []
    current_hunk: HunkInfo | None = None
    lines = diff_output.split("\n")

    for line in lines:
        # Match @@ -old_start,old_count +new_start,new_count @@
        hunk_match = re.match(
            r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$', line
        )
        if hunk_match:
            if current_hunk:
                hunks.append(current_hunk)
            old_start = int(hunk_match.group(1))
            old_count = int(hunk_match.group(2) or "1")
            new_start = int(hunk_match.group(3))
            new_count = int(hunk_match.group(4) or "1")
            header = hunk_match.group(5).strip()
            current_hunk = HunkInfo(
                header=header,
                old_start=old_start,
                old_count=old_count,
                new_start=new_start,
                new_count=new_count,
                content="",
                old_next=old_start,
                new_next=new_start,
            )
            continue

        if current_hunk is None:
            continue

        current_hunk.content += line + "\n"

        if line.startswith("+") and not line.startswith("+++"):
            # Added line
            new_next = current_hunk.new_next
            current_hunk.new_lines.append(new_next)
            current_hunk.added_lines.append((new_next, line[1:]))
            current_hunk.new_next = new_next + 1
        elif line.startswith("-") and not line.startswith("---"):
            # Deleted line
            old_next = current_hunk.old_next
            current_hunk.old_lines.append(old_next)
            current_hunk.deleted_lines.append((old_next, line[1:]))
            current_hunk.old_next = old_next + 1
        elif line.startswith(" ") or line == "":
            # Context line
            old_next = current_hunk.old_next
            new_next = current_hunk.new_next
            current_hunk.old_lines.append(old_next)
            current_hunk.new_lines.append(new_next)
            current_hunk.old_next = old_next + 1
            current_hunk.new_next = new_next + 1

    if current_hunk:
        hunks.append(current_hunk)

    return hunks


def _map_symbols(
    hunks: list[HunkInfo],
    symbols: list[Symbol],
    file_path: str,
) -> list[SymbolMapping]:
    """Map changed lines (NEW side) to symbol ranges.

    A symbol is "touched" if any added line in any hunk falls within
    [symbol.line_start, symbol.line_end]. Returns counts per symbol.
    """
    added_per_symbol: dict[int, int] = {}
    deleted_per_symbol: dict[int, int] = {}

    for hunk in hunks:
        for line_no, _content in hunk.added_lines:
            for sym in symbols:
                if sym.line_start <= line_no <= sym.line_end:
                    added_per_symbol[sym.id] = added_per_symbol.get(sym.id, 0) + 1
        for line_no, _content in hunk.deleted_lines:
            for sym in symbols:
                if sym.line_start <= line_no <= sym.line_end:
                    deleted_per_symbol[sym.id] = deleted_per_symbol.get(sym.id, 0) + 1

    result: list[SymbolMapping] = []
    all_ids = set(added_per_symbol.keys()) | set(deleted_per_symbol.keys())
    sym_by_id = {s.id: s for s in symbols}
    for sid in sorted(all_ids):
        sym = sym_by_id[sid]
        added = added_per_symbol.get(sid, 0)
        deleted = deleted_per_symbol.get(sid, 0)
        result.append(SymbolMapping(
            symbol_id=sym.id,
            symbol_name=sym.name,
            symbol_kind=sym.kind,
            file_path=file_path,
            added_lines=added,
            deleted_lines=deleted,
        ))

    return result


def _determine_change_type(
    added: int, deleted: int, file_additions: int, file_deletions: int
) -> str:
    """Classify symbol change type based on per-file line counts.

    A symbol is ADDED when its file only gained lines, DELETED when its file
    only lost lines, and MODIFIED for mixed edits.
    """
    if added > 0 and deleted == 0 and file_deletions == 0:
        return "ADDED"
    if deleted > 0 and added == 0 and file_additions == 0:
        return "DELETED"
    return "MODIFIED"


class DiffEngine:
    """Orchestrates the full diff analysis pipeline."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def analyze(
        self,
        repository_id: int,
        base_revision: str,
        head_revision: str,
    ) -> dict:
        """Run the complete diff analysis and persist results.

        Returns a summary dict.
        """
        base_key = base_revision.strip().lower() or "$"
        head_key = head_revision.strip().lower() or "$"
        async with protected(f"diff:{repository_id}:{base_key}..{head_key}"):
            return await self._analyze_unlocked(repository_id, base_revision, head_revision)

    async def _analyze_unlocked(
        self,
        repository_id: int,
        base_revision: str,
        head_revision: str,
    ) -> dict:
        """Run the complete diff analysis and persist results.

        Returns a summary dict.
        """
        # 1. Verify repository
        repo = await self.db.get(Repository, repository_id)
        if repo is None:
            raise LookupError("Repository not found.")

        local_path = Path(repo.local_path) if repo.local_path else None
        if local_path is None or not local_path.is_dir():
            raise ValueError("Repository local path is not available.")

        # 2. Resolve revisions (deepening shallow clones on demand)
        base_sha = await self._resolve_or_deepen(base_revision.strip(), local_path)
        head_sha = await self._resolve_or_deepen(head_revision.strip(), local_path)
        if base_sha == head_sha:
            raise ValueError("Base and head revisions must be different.")

        # 3. Check idempotency by resolved SHAs
        existing = (
            await self.db.execute(
                select(Diff).where(
                    Diff.repository_id == repository_id,
                    Diff.base_revision == base_sha,
                    Diff.head_revision == head_sha,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return await self._summary_from_diff(existing)

        # 4. Get numstat
        changes = get_numstat(base_sha, head_sha, cwd=local_path)

        if len(changes) > limits.MAX_DIFF_FILES:
            raise ValueError(
                f"Diff exceeds maximum file count ({limits.MAX_DIFF_FILES}). "
                f"Received {len(changes)} files."
            )

        total_additions = sum(c.additions for c in changes)
        total_deletions = sum(c.deletions for c in changes)

        # 5. Create Diff record
        diff = Diff(
            repository_id=repository_id,
            base_revision=base_sha,
            head_revision=head_sha,
            files_changed=len(changes),
            insertions=total_additions,
            deletions=total_deletions,
        )
        self.db.add(diff)
        await self.db.flush()

        total_hunks = 0
        total_changed_lines = 0
        total_diff_bytes = 0
        all_symbol_mappings: list[SymbolMapping] = []

        # 6. Process each changed file
        for fc in changes:
            category = classify_file(fc.path)
            diff_file = DiffFile(
                diff_id=diff.id,
                path=fc.path,
                status=fc.status,
                old_path=fc.old_path,
                new_path=fc.path if fc.status in ("RENAMED", "COPIED") else None,
                additions=fc.additions,
                deletions=fc.deletions,
                binary=fc.binary,
                file_category=category,
            )

            # Link to existing file records if available
            if fc.status != "DELETED":
                new_file = await self._find_file_record(repository_id, fc.path)
                if new_file:
                    diff_file.new_file_id = new_file.id

            if fc.old_path and fc.status in ("RENAMED", "COPIED"):
                old_file = await self._find_file_record(repository_id, fc.old_path)
                if old_file:
                    diff_file.old_file_id = old_file.id
            elif fc.status != "ADDED":
                old_file = await self._find_file_record(repository_id, fc.path)
                if old_file:
                    diff_file.old_file_id = old_file.id

            self.db.add(diff_file)
            await self.db.flush()

            # Skip binary files for hunk parsing
            if fc.binary:
                continue

            # 7. Get diff output and parse hunks
            try:
                diff_output = get_diff_output(base_sha, head_sha, local_path, fc.path)
            except GitError:
                logger.warning("Could not get diff output for %s", fc.path)
                continue

            total_diff_bytes += len(diff_output.encode("utf-8", errors="replace"))
            if total_diff_bytes > limits.MAX_DIFF_OUTPUT_BYTES:
                raise ValueError(
                    f"Diff exceeds maximum raw output size "
                    f"({limits.MAX_DIFF_OUTPUT_BYTES} bytes)."
                )

            hunks = _parse_hunks(diff_output)

            total_hunks += len(hunks)
            if total_hunks > limits.MAX_DIFF_HUNKS:
                raise ValueError(
                    f"Diff exceeds maximum hunk count ({limits.MAX_DIFF_HUNKS})."
                )

            for hunk in hunks:
                diff_hunk = DiffHunk(
                    diff_file_id=diff_file.id,
                    header=hunk.header,
                    old_start=hunk.old_start,
                    old_count=hunk.old_count,
                    new_start=hunk.new_start,
                    new_count=hunk.new_count,
                    content=hunk.content,
                )
                self.db.add(diff_hunk)
                await self.db.flush()

                for line_no, _content in hunk.added_lines:
                    total_changed_lines += 1
                    if total_changed_lines > limits.MAX_DIFF_CHANGED_LINES:
                        raise ValueError(
                            f"Diff exceeds maximum changed line count ({limits.MAX_DIFF_CHANGED_LINES})."
                        )
                    self.db.add(DiffChangedLine(
                        diff_hunk_id=diff_hunk.id,
                        side="NEW",
                        line_number=line_no,
                        change_type="ADDED",
                    ))

                for line_no, _content in hunk.deleted_lines:
                    total_changed_lines += 1
                    if total_changed_lines > limits.MAX_DIFF_CHANGED_LINES:
                        raise ValueError(
                            f"Diff exceeds maximum changed line count ({limits.MAX_DIFF_CHANGED_LINES})."
                        )
                    self.db.add(DiffChangedLine(
                        diff_hunk_id=diff_hunk.id,
                        side="OLD",
                        line_number=line_no,
                        change_type="DELETED",
                    ))

            # 8. Symbol mapping for this file
            file_symbols = await self._load_symbols_for_file(
                repository_id, fc.path, diff_file.old_file_id or diff_file.new_file_id
            )
            if file_symbols:
                mappings = _map_symbols(hunks, file_symbols, fc.path)
                for mapping in mappings:
                    change_type = _determine_change_type(
                        mapping.added_lines, mapping.deleted_lines,
                        fc.additions, fc.deletions,
                    )
                    self.db.add(DiffSymbol(
                        diff_id=diff.id,
                        symbol_id=mapping.symbol_id,
                        file_path=mapping.file_path,
                        symbol_name=mapping.symbol_name,
                        symbol_kind=mapping.symbol_kind,
                        change_type=change_type,
                        added_lines=mapping.added_lines,
                        deleted_lines=mapping.deleted_lines,
                    ))
                all_symbol_mappings.extend(mappings)

        # Update diff totals
        diff.symbols_changed = len(all_symbol_mappings)

        await self.db.commit()

        return await self._summary_from_diff(diff)

    async def _resolve_or_deepen(self, revision: str, local_path: Path) -> str:
        """Resolve a revision locally, bumping shallow history if needed.

        Shallow clones (--depth 1) only contain HEAD, so historical commits are
        absent. When a revision is not already present we fetch it with a
        bounded depth (tip + parent) via the configured origin remote. If the
        fetch fails (e.g. offline), the revision is reported as not found.
        """
        if ensure_revision_resolvable(revision, cwd=local_path):
            return validate_revision_in_repo(revision, cwd=local_path)
        try:
            return deepen_for_revision(revision, cwd=local_path)
        except GitError as exc:
            raise RevisionNotFoundError(
                f"Revision '{revision}' is not available locally and could not "
                f"be fetched: {exc}"
            )

    async def _find_file_record(
        self, repository_id: int, path: str
    ) -> FileRecord | None:
        """Find an existing FileRecord for the given path."""
        result = await self.db.execute(
            select(FileRecord).where(
                FileRecord.repository_id == repository_id,
                FileRecord.path == path,
            )
        )
        return result.scalar_one_or_none()

    async def _load_symbols_for_file(
        self, repository_id: int, path: str, file_id: int | None
    ) -> list[Symbol]:
        """Load all symbols for a file, using file_id if available, else path."""
        if file_id is not None:
            result = await self.db.execute(
                select(Symbol).where(Symbol.file_id == file_id)
            )
            syms = list(result.scalars().all())
            if syms:
                return syms

        # Fallback: join through FileRecord
        result = await self.db.execute(
            select(Symbol)
            .join(FileRecord, Symbol.file_id == FileRecord.id)
            .where(
                Symbol.repository_id == repository_id,
                FileRecord.path == path,
            )
        )
        return list(result.scalars().all())

    async def _summary_from_diff(self, diff: Diff) -> dict:
        """Build a summary dict from an existing Diff record."""
        return {
            "id": diff.id,
            "repository_id": diff.repository_id,
            "base_revision": diff.base_revision,
            "head_revision": diff.head_revision,
            "files_changed": diff.files_changed,
            "insertions": diff.insertions,
            "deletions": diff.deletions,
            "symbols_changed": diff.symbols_changed,
            "computed_at": diff.computed_at.isoformat() if diff.computed_at else None,
        }
