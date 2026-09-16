"""In-memory indexes over parsed repository data for efficient relationship resolution.

Builds lookup structures once per relationship run so resolvers can do
scoped lookups without repeated database queries or O(N²) scans.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.service import module_of_path
from app.models.orm import Export, FileRecord, Import, Symbol


@dataclass
class SymbolEntry:
    id: int
    name: str
    qualified_name: str | None
    kind: str
    file_id: int
    file_path: str
    language: str | None
    line_start: int
    line_end: int
    parent_symbol_id: int | None
    exported: bool
    module: str  # dotted module path


@dataclass
class ImportEntry:
    id: int
    file_id: int
    file_path: str
    source: str
    imported_name: str | None
    alias: str | None
    kind: str  # python_import | python_from_import | es_module | commonjs
    start_line: int
    end_line: int


@dataclass
class ExportEntry:
    id: int
    file_id: int
    file_path: str
    name: str
    kind: str  # named | default | star
    start_line: int
    end_line: int


@dataclass
class FileEntry:
    id: int
    path: str
    language: str | None
    module: str  # dotted module path


_LAYOUT_DIR_PREFIXES = ("src.", "lib.", "python.", "site-packages.")


def _normalize_layout_module(module: str) -> str:
    """Strip a common src-layout prefix so absolute imports still resolve.

    ``src/itsdangerous/encoding.py`` gets module ``src.itsdangerous.encoding``
    from ``module_of_path``, but test/consumer files import it as the absolute
    ``itsdangerous.encoding``. Registering the stripped alias (exact modules
    always win) lets those imports resolve instead of being marked EXTERNAL.
    """
    parts = module.split(".")
    if len(parts) > 1 and (parts[0] + ".") in _LAYOUT_DIR_PREFIXES:
        return ".".join(parts[1:])
    return module


@dataclass
class RelationshipIndex:
    """All parsed data for a repository, indexed for fast lookup."""

    symbols: list[SymbolEntry] = field(default_factory=list)
    imports: list[ImportEntry] = field(default_factory=list)
    exports: list[ExportEntry] = field(default_factory=list)
    files: list[FileEntry] = field(default_factory=list)

    # Indexes built after loading
    symbols_by_name: dict[str, list[SymbolEntry]] = field(default_factory=dict)
    symbols_by_module: dict[str, SymbolEntry] = field(default_factory=dict)
    symbols_by_file: dict[int, list[SymbolEntry]] = field(default_factory=dict)
    symbols_by_qualified: dict[str, SymbolEntry] = field(default_factory=dict)
    classes_by_name: dict[str, list[SymbolEntry]] = field(default_factory=dict)
    files_by_path: dict[str, FileEntry] = field(default_factory=dict)
    files_by_module: dict[str, FileEntry] = field(default_factory=dict)
    imports_by_file: dict[int, list[ImportEntry]] = field(default_factory=dict)
    exports_by_file: dict[int, list[ExportEntry]] = field(default_factory=dict)
    symbols_by_id: dict[int, SymbolEntry] = field(default_factory=dict)
    children_by_parent: dict[int, list[SymbolEntry]] = field(default_factory=dict)

    def build(self) -> None:
        """Build all lookup indexes from loaded data."""
        self.symbols_by_name.clear()
        self.symbols_by_module.clear()
        self.symbols_by_file.clear()
        self.symbols_by_qualified.clear()
        self.classes_by_name.clear()
        self.files_by_path.clear()
        self.files_by_module.clear()
        self.imports_by_file.clear()
        self.exports_by_file.clear()
        self.symbols_by_id.clear()
        self.children_by_parent.clear()

        for sym in self.symbols:
            self.symbols_by_id[sym.id] = sym
            self.symbols_by_name.setdefault(sym.name, []).append(sym)
            if sym.qualified_name:
                self.symbols_by_module[sym.qualified_name] = sym
                self.symbols_by_qualified[sym.qualified_name] = sym
            self.symbols_by_file.setdefault(sym.file_id, []).append(sym)
            if sym.kind == "CLASS":
                self.classes_by_name.setdefault(sym.name, []).append(sym)
            if sym.parent_symbol_id is not None:
                self.children_by_parent.setdefault(sym.parent_symbol_id, []).append(sym)

        for f in self.files:
            self.files_by_path[f.path] = f
            self.files_by_module[f.module] = f

        # Second pass: register src/lib-layout aliases so absolute imports
        # into a package (e.g. ``itsdangerous.encoding``) resolve. Exact
        # module names registered in the first pass always take precedence.
        for f in self.files:
            alias = _normalize_layout_module(f.module)
            if alias != f.module and alias not in self.files_by_module:
                self.files_by_module[alias] = f

        for imp in self.imports:
            self.imports_by_file.setdefault(imp.file_id, []).append(imp)

        for exp in self.exports:
            self.exports_by_file.setdefault(exp.file_id, []).append(exp)

    def find_symbol_by_qualified(self, qualified_name: str) -> SymbolEntry | None:
        """Exact qualified name match."""
        return self.symbols_by_qualified.get(qualified_name)

    def find_symbols_by_name(self, name: str) -> list[SymbolEntry]:
        """All symbols with this exact name."""
        return self.symbols_by_name.get(name, [])

    def find_file_by_module(self, module: str) -> FileEntry | None:
        """Exact dotted module path match."""
        return self.files_by_module.get(module)

    def find_file_by_path(self, path: str) -> FileEntry | None:
        """Exact relative path match."""
        return self.files_by_path.get(path)

    def resolve_module_to_file(self, module: str) -> FileEntry | None:
        """Try to resolve a dotted module path to a file."""
        candidate = self.files_by_module.get(module)
        if candidate:
            return candidate
        # Try as package __init__.py
        return self.files_by_module.get(module + ".__init__")


async def load_index(db: AsyncSession, repository_id: int) -> RelationshipIndex:
    """Load all parsed data for a repository into a RelationshipIndex."""
    index = RelationshipIndex()

    files = (
        (
            await db.execute(
                select(FileRecord).where(FileRecord.repository_id == repository_id)
            )
        )
        .scalars()
        .all()
    )
    for f in files:
        mod = module_of_path(f.path)
        index.files.append(FileEntry(id=f.id, path=f.path, language=f.language, module=mod))

    symbols = (
        (
            await db.execute(
                select(Symbol, FileRecord.path)
                .join(FileRecord, Symbol.file_id == FileRecord.id)
                .where(Symbol.repository_id == repository_id)
            )
        )
        .all()
    )
    for sym, path in symbols:
        mod = module_of_path(path)
        index.symbols.append(
            SymbolEntry(
                id=sym.id,
                name=sym.name,
                qualified_name=sym.qualified_name,
                kind=sym.kind,
                file_id=sym.file_id,
                file_path=path,
                language=sym.language,
                line_start=sym.line_start,
                line_end=sym.line_end,
                parent_symbol_id=sym.parent_symbol_id,
                exported=sym.exported,
                module=mod,
            )
        )

    imports = (
        (
            await db.execute(
                select(Import, FileRecord.path)
                .join(FileRecord, Import.file_id == FileRecord.id)
                .where(Import.repository_id == repository_id)
            )
        )
        .all()
    )
    for imp, path in imports:
        index.imports.append(
            ImportEntry(
                id=imp.id,
                file_id=imp.file_id,
                file_path=path,
                source=imp.source,
                imported_name=imp.imported_name,
                alias=imp.alias,
                kind=imp.kind,
                start_line=imp.start_line,
                end_line=imp.end_line,
            )
        )

    exports = (
        (
            await db.execute(
                select(Export, FileRecord.path)
                .join(FileRecord, Export.file_id == FileRecord.id)
                .where(Export.repository_id == repository_id)
            )
        )
        .all()
    )
    for exp, path in exports:
        index.exports.append(
            ExportEntry(
                id=exp.id,
                file_id=exp.file_id,
                file_path=path,
                name=exp.name,
                kind=exp.kind,
                start_line=exp.start_line,
                end_line=exp.end_line,
            )
        )

    index.build()
    return index
