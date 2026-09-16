# Phase 4 Report — Real Code Search + Investigation Foundations

**RepoLens** · Phase 4 of 10

## Goal

Build the deterministic, offline **search + investigation** stack: symbol /
file / text search with reproducible ranking, a depth-1 symbol investigation
endpoint, and a functional frontend search workspace with a source view,
breadcrumbs, and `Ctrl/Cmd+K` navigation — all static analysis, no LLM.

## What was delivered

### Backend — Search

- `GET /api/repositories/{id}/search?q=&type=&case_sensitive=&limit=&offset=`
  returns a unified envelope with a ranked hit list. Modes:
  - **symbol** — repository-scoped query on the `symbols` table (name /
    qualified name), ranked deterministically. **Definition gating**: a
    same-named class/function now ranks ahead of an import alias (the
    qualified-name-exact bonus only applies to a real fully-qualified name,
    and a definition-kind tie-break is applied after the match score).
  - **file** — repository-relative path / file-name matches, ranked so exact
    paths and directory prefixes sort ahead of loose substrings; includes
    per-file symbol counts.
  - **text** — streams each stored file's bytes one at a time (never the whole
    repo), returns line-level snippets; substring + optional
    `case_sensitive`; bounded by a scanned-byte budget and match cap.
- Input validation: empty / whitespace / wildcard (`*`) / over-long / control-
  character queries are rejected with `400`. Repository-scoped; unknown repo → `404`.

### Backend — Investigation

- `GET /api/repositories/{id}/symbols/{symbolId}/investigation` assembles the
  depth-1 neighborhood: symbol definition, **callers, callees, references,
  dependencies, tests, imports, exports**, plus **unresolved/external** edges
  and **related files** derived only from real graph relationships (each
  resolved edge now contributes the target symbol's actual file path).
- Returns source context (file + line range) for highlighting. Unknown symbol
  or repo → `404`.

### Frontend — Search workspace (`/repo/:id/investigate`)

- **Three-pane layout**: left search bar + typed results (Symbols / Files /
  Text tabs), center read-only source view (syntax highlighting, line numbers,
  highlight-scroll, click-definition navigation), right investigation context
  panel (callers / callees / references / tests / imports / exports /
  unresolved·external / related files with click-through navigation).
- **Breadcrumbs**: RepoLens / owner-repo / Investigate.
- **Navigation with no dead ends**: search → symbol → investigation → related
  symbol → new investigation.
- **`Ctrl/Cmd+K`** anywhere in the app navigates to the repository's search
  workspace; the header "Search code" button does the same.

## Files changed / added

**Backend**
- `app/api/routes.py` — `search_repository` + `investigate_symbol` endpoints.
- `app/analysis/search/ranking.py` — definition-over-import kind tie-break +
  degenerate-qualified-name guard.
- `app/analysis/search/symbol_search.py` — fixed row unpacking (`row[0]`).
- `app/analysis/search/text_search.py` — `_read_source_file` made synchronous
  for `asyncio.to_thread`.
- `app/analysis/search/service.py` — reject whitespace-only queries.
- `app/analysis/investigation/service.py` — target-symbol file resolution so
  related files include real resolved-edge targets.
- `tests/test_search_api.py` (new, 11 tests), `tests/test_investigation_api.py`
  (new, 7 tests).

**Frontend**
- `src/types/index.ts` — `SearchType`, `SymbolSearchHit`, `FileSearchHit`,
  `TextSearchHit`, `SearchHit`, `SearchResponse`, `RelatedFileInfo`,
  `SourceContext`, `InvestigationGroup`, `InvestigationResponse`.
- `src/api/client.ts` — `search`, `investigateSymbol`.
- `src/components/analysis/SearchWorkspace.tsx` (new), `SourceView.tsx` (new),
  `InvestigationPanel.tsx` (new).
- `src/pages/InvestigatePage.tsx` (new), `src/App.tsx` (route + `Ctrl+K`),
  `src/components/layout/AppShell.tsx` (header button navigation).

## Test suite

Backend: **161 passed, 2 skipped (Windows symlinks), 4 deselected (network)**.
The 18 new Phase 4 tests cover exact-rank-first symbol search, case-insensitive
prefix search, file-path ranking (exact before substring), text snippets,
invalid-query/type rejection, repository isolation, pagination, sensitive text
search, investigation definition/callers/callees/tests/related-files, and
unknown symbol/repo `404`s.

Frontend: `tsc -b && vite build` succeeds cleanly.

## Design / security notes

- Ranking is **pure** — string + coordinate properties only; repeated queries
  are reproducible.
- Search is repository-scoped; every query is length/offset/result capped.
- Text search is plain substring (no regex); path traversal and per-file
  oversize are blocked; scanning respects a byte budget.
- The core never executes repository code and never uses an LLM.

## Real-repo verification

See README "Verification metrics (Phase 4)". Symbol/file searches are single-
digit-to-tens of milliseconds on itsdangerous and flask; text search is
destructive-free and bounded (244 ms to ~1.7 s depending on file count). E.g.
`Serializer` investigation reports 19 callees / 12 refs / 1 test / 8 related
files; `Blueprint` reports 29 callees / 9 refs / 4 files.

## Known gaps (noted for Phase 5+)

- No global symbol-identity merging across modules (same-name definitions in
  tests vs `src/` are distinct rows).
- Text search is not regex; very short needles on huge repos hit the byte cap.
- Related files come from resolved edges only; unresolved/external edges are
  surfaced separately.
- Impact / diff-scoped analysis (Phase 5) intentionally not started.
