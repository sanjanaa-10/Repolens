"""Unit tests for import resolution (Python and JS/TS).

These build a RelationshipIndex directly (no DB) and drive the import
resolver against it, isolated from the parsing pipeline.
"""
from __future__ import annotations

import pytest

from app.analysis.relationships.index import ImportEntry, RelationshipIndex, SymbolEntry
from app.analysis.relationships.import_resolver import resolve_imports


def _file(index, id, path, module, language):
    from app.analysis.relationships.index import FileEntry
    index.files.append(FileEntry(id=id, path=path, language=language, module=module))


def _sym(index, id, name, kind, file_id, path, line_start, line_end, module,
         parent_id=None, qualified=None, exported=False):
    index.symbols.append(SymbolEntry(
        id=id, name=name, qualified_name=qualified, kind=kind, file_id=file_id,
        file_path=path, language=None, line_start=line_start, line_end=line_end,
        parent_symbol_id=parent_id, exported=exported, module=module,
    ))


def _imp(index, id, file_id, path, source, imported_name, alias, kind, start_line, end_line):
    index.imports.append(ImportEntry(
        id=id, file_id=file_id, file_path=path, source=source,
        imported_name=imported_name, alias=alias, kind=kind,
        start_line=start_line, end_line=end_line,
    ))


@pytest.fixture
def py_index():
    index = RelationshipIndex()
    _file(index, 1, "auth/__init__.py", "auth", "Python")
    _file(index, 2, "auth/service.py", "auth.service", "Python")
    _file(index, 3, "auth/controller.py", "auth.controller", "Python")
    _file(index, 4, "users/repository.py", "users.repository", "Python")

    _sym(index, 1, "AuthService", "CLASS", 2, "auth/service.py", 1, 1,
         "auth.service", qualified="auth.service.AuthService")
    _sym(index, 2, "create_user", "FUNCTION", 2, "auth/service.py", 4, 5,
         "auth.service", qualified="auth.service.create_user")
    _sym(index, 3, "UserRepository", "CLASS", 4, "users/repository.py", 1, 1,
         "users.repository", qualified="users.repository.UserRepository")

    _imp(index, 1, 3, "auth/controller.py", "auth.service", "AuthService", None,
         "python_from_import", 1, 1)
    _imp(index, 2, 3, "auth/controller.py", "auth.service", "create_user", None,
         "python_from_import", 2, 2)
    _imp(index, 3, 3, "auth/controller.py", "fastapi", None, None, "python_import", 3, 3)
    _imp(index, 4, 3, "auth/controller.py", "os", None, None, "python_import", 4, 4)

    index.build()
    return index


def test_python_from_import_resolves(py_index):
    edges = resolve_imports(py_index)
    service_edges = [e for e in edges if e.evidence_file_id == 3]
    auth_service = [e for e in service_edges if "AuthService" in (e.evidence or "")]
    assert len(auth_service) == 1
    assert auth_service[0].resolution_status == "RESOLVED"
    assert auth_service[0].target_symbol_id == 1
    assert auth_service[0].target_file.endswith("auth/service.py")
    assert auth_service[0].evidence_start_line == 1

    create_user = [e for e in service_edges if "create_user" in (e.evidence or "")]
    assert create_user and create_user[0].target_symbol_id == 2


def test_external_python_imports(py_index):
    edges = resolve_imports(py_index)
    fastapi = [e for e in edges if "fastapi" in (e.evidence or "")]
    os_imp = [e for e in edges if (e.evidence or "").startswith("os import")]
    assert fastapi and fastapi[0].resolution_status == "EXTERNAL"
    assert os_imp and os_imp[0].resolution_status == "EXTERNAL"


@pytest.fixture
def js_index():
    index = RelationshipIndex()
    _file(index, 1, "web/app.ts", "web.app", "TypeScript")
    _file(index, 2, "web/App.tsx", "web.App", "TypeScript")
    _file(index, 3, "web/dir/index.ts", "web.dir.index", "TypeScript")

    _sym(index, 1, "x", "VARIABLE", 1, "web/app.ts", 1, 1, "web.app",
         qualified="web.app.x", exported=True)

    _imp(index, 1, 2, "web/App.tsx", "./app", "x", None, "es_module", 1, 1)
    _imp(index, 2, 2, "web/App.tsx", "react", "React", None, "es_module", 2, 2)
    _imp(index, 3, 2, "web/App.tsx", "./dir", "y", None, "es_module", 3, 3)

    index.build()
    return index


def test_ts_relative_import_with_extension(js_index):
    edges = resolve_imports(js_index)
    tsx = [e for e in edges if e.evidence_file_id == 2]
    initial_imp = [e for e in tsx if e.evidence_start_line == 1]
    assert initial_imp and initial_imp[0].resolution_status == "RESOLVED"
    assert initial_imp[0].target_file.endswith("web/app.ts")

    react = [e for e in tsx if e.evidence_start_line == 2]
    assert react and react[0].resolution_status == "EXTERNAL"


def test_ts_directory_index_import(js_index):
    edges = resolve_imports(js_index)
    dir_imp = [e for e in edges if e.evidence_start_line == 3]
    assert dir_imp and dir_imp[0].resolution_status == "RESOLVED"
    assert dir_imp[0].target_file.endswith("web/dir/index.ts")