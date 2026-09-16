# RepoLens — Phase 3 Final Report: Relationship Engine

## 1. Project Objective

RepoLens is a codebase investigation and evidence-backed change-impact tool.
It analyzes real source repositories (never executed) and answers "how does
this code actually relate?" with source-level evidence. Phase 3 delivers the
relationship layer that turns Phase 1 ingestion and Phase 2 symbol extraction
into a deterministic, queryable code graph.

## 2. Phase 3 Scope and Definition of Done

Goal: convert parsed symbols/imports/exports into real code relationships
with proof. Done when:

- A relationship engine resolves imports, calls, references, inheritance,
  exports, and test couplings with per-edge source evidence.
- Every edge carries a resolution status (`RESOLVED` / `UNRESOLVED` /
  `EXTERNAL`) so the user can distinguish proven links from gaps.
- Persistence is idempotent: repeated builds are safe and reproducible.
- REST endpoints expose build (summary) and read (list/filter/neighborhood).
- The frontend shows a Relationship Explorer with filters, status/type
  tones, a build button, and clickable evidence.
- Real repositories (itsdangerous, flask) run through the full pipeline with
  recorded metrics.
- Backend suite stays green.

Phase 4 features (impact analysis, AI UI, graph visualization, search, auth)
are intentionally **not** implemented.

## 3. Requirements

- Static analysis only; repository code is never executed, imported, or piped
  to an LLM. Resolver behavior must be deterministic (same input, same edges).
- Resolution rules per type, in priority order: import bindings → same-file
  definitions → receiver method for calls → unique global match only for
  inheritance bases. No speculative "first global match" fallback for calls,
  references, or tests.
- Call span detection must avoid double-counting the same statement
  (a method call on a receiver is one edge, not two).
- References/Builtin names filtered to reduce noise; conservative resolution
  (precision over recall).
- Resource limits: `MAX_RELATIONSHIPS_PER_REPOSITORY = 100_000`, per-file
  source caps; safe filesystem reads (path-escape protection retained).
- Idempotent rebuild: `POST /relationships` deletes prior edges for the repo,
  recomputes, persists under the unique constraint, and updates the
  repository's `relationship_count`.

## 4. Architecture

New `backend/app/analysis/relationships/` package:

```
relationships/
  engine.py            orchestrates the pipeline + persistence + dedup
  index.py             in-memory lookup structures + module/file resolution
  models.py            RelationshipEdge dataclass
  import_resolver.py   resolve_import_targets / build_import_bindings /
                       _unique_exported_symbols (shared by other resolvers)
  defines_resolver.py  DEFINES edges (file→symbol, symbol→child)
  export_resolver.py   EXPORTS edges
  call_resolver.py     CALLS edges (Python + JS/TS)
  reference_resolver.py REFERENCES edges
  inheritance_resolver.py EXTENDS / IMPLEMENTS edges
  test_resolver.py     TESTS edges
```

Pipeline order (engine.py): defines → imports → exports → calls →
inheritance → references → tests → dedup → persist. The engine loads all
parsed data once into a `RelationshipIndex` (symbols, imports, exports,
files + lookup maps including `children_by_parent` for receiver-method
resolution) so resolvers do scoped lookups without repeated DB queries.

## 5. Data Model Changes

`Relationship` table (`app/models/orm.py`, unique constraint
`uq_relationship_edge`):

- FKs: `repository_id` (CASCADE), `source_symbol_id`/`target_symbol_id`
  (CASCADE, nullable), `evidence_file_id` (SET NULL).
- Columns: `type` (DEFINES | IMPORTS | EXPORTS | CALLS | REFERENCES |
  EXTENDS | IMPLEMENTS | TESTS), `resolution_status` (RESOLVED |
  UNRESOLVED | EXTERNAL), `source_type`/`target_type`
  (symbol | file | import), `evidence_file_id`,
  `evidence_start_line`/`evidence_end_line`, `evidence` (human-readable
  proof string), `target_file`.
- `repositories.relationship_count` updated after each build.
- A startup migration in `app/core/database.py` detects a stale
  pre-Phase-3 `relationships` shape (missing `resolution_status` /
  `evidence_file_id`) and rebuilds the table; derived data is regenerated on
  the next build.

## 6. Resolution Rules

- **IMPORTS:** Python absolute/relative/dotted and JS/TS ES-module/CommonJS.
  In-repo resolves to the target file and (for named imports) the matching
  symbols scoped to that file. Out-of-repo modules are `EXTERNAL`;
  unresolvable relative imports are `UNRESOLVED`. ES default/namespace
  imports bind to a file's unique exported function/class.
- **CALLS:** only through import bindings, same-file definitions, or
  receiver methods (`self`/`cls`/`this`/`super`); `super()` resolves against
  resolved base classes. Builtin-like names skipped. Unresolved names
  become `UNRESOLVED` edges (evidence kept), never guessed.
- **REFERENCES:** only `RESOLVED` edges; names inside a definition attributed
  to the innermost owning symbol and resolved only when the file binds the
  name (import or same-file). Header/comment lines excluded.
- **EXTENDS / IMPLEMENTS:** dotted bases (`auth.models.Entity`) via package
  module resolution, then import bindings, same-file, then unique global
  match; multi-line Python base lists and TS generics handled; keyword args
  (e.g. `metaclass=M`) filtered.
- **TESTS:** only when a test file imports an app symbol and a test body
  actually uses that binding — never by name similarity.
- **src/ layout normalization:** modules like `src/itsdangerous/encoding.py`
  register both `src.itsdangerous.encoding` and the absolute alias
  `itsdangerous.encoding` (exact modules win), so `from itsdangerous.encoding
  import ...` from tests/consumers resolves.

## 7. Relationship Types Produced

| Type | Coverage |
| ---- | -------- |
| DEFINES | file→symbol, symbol→nested child |
| IMPORTS | file→symbol (resolved) or file→import (external/unresolved) |
| EXPORTS | file→symbol for named/default ES exports |
| CALLS | function/method → function/method/class |
| REFERENCES | symbol → symbol used in its body |
| EXTENDS | class → base class |
| IMPLEMENTS | TS class → implemented interface |
| TESTS | test file → exercised app symbol |

## 8. API

- `POST /api/repositories/{id}/relationships` → `RelationshipSummary`
  (counts by status, duration, status) — idempotent rebuild.
- `GET /api/repositories/{id}/relationships` with filters `type`, `status`,
  `file`, `source`, `target` (substring), `limit`, `offset` →
  `RelationshipsResponse` (items + total).
- `GET /api/repositories/{id}/symbols/{symbolId}/relationships` → incoming +
  outgoing edges for one symbol.
- Responses include `source_symbol_name` / `target_symbol_name` (joined from
  the symbols table) so the UI can render readable symbol pairs.
- Error mapping: 404 (missing repo / symbol), 409 (needs analysis),
  500 (unexpected), user-safe messages.

## 9. Frontend Integration

`RelationshipExplorer.tsx` (wired into `IngestionResultPage.tsx`):
- Buttons: "Build Relationships" (runs `POST /relationships`, shows resulting
  summary counts) and refresh; a build-in-progress state.
- Filters: All / Calls / Imports / References / Extends / Implements / Tests.
- Table columns: Source (symbol name or file), Relationship (type badge,
  color-coded), Target, Location (`file:line`), Status (badge color-coded).
- Clickable rows expand to show the evidence description plus source/target
  file, target line, and source/target types.
- Types and API client functions were already present; `npx tsc --noEmit`
  clean.

## 10. Security

- Repository code is never executed, imported, or passed to an LLM.
- Resolution reads repository files only through the validated
  repository-relative path (existing path-escape protections retained).
- Relationship build refuses repositories that were never parsed
  (`RuntimeError` → 409).
- All limits retained (per-file 512 KB, per-repo file/tree caps,
  `MAX_RELATIONSHIPS_PER_REPOSITORY`); edges persisted via the ORM with
  FK guarantees.
- Error responses are user-safe; technical detail logged server-side.

## 11. Testing

Full backend suite: **143 passed, 2 skipped (Windows symlinks), 4 deselected
(network)**, 22 s.

- `test_relationships_resolvers.py` (14): isolated resolver tests incl.
  method-self calls, JS same-file calls, no-global-fallback for calls and
  references, dotted + multiline inheritance, TESTS via imports only (no name
  fallback), and a new src-layout absolute-import regression test.
- `test_relationships_engine.py` (8): engine pipeline isolation/reset,
  idempotent rebuild, same-line import dedup, resolved cross-file CALLS via
  symbol join, requires-analysis guard, API build/list/neighborhood endpoints.
- `test_relationships_imports.py` + full existing Phase 1/2 suites.
- Frontend: `npx tsc --noEmit` clean after changes.

## 12. Real-World Verification

Full pipeline run against both productive local repositories:

| Repository | Files | Edges | Resolved | External | Unresolved | Time |
| ---------- | ----- | ----- | -------- | -------- | ---------- | ---- |
| itsdangerous | 50 | 1,080 | 678 | 51 | 351 | ~1.8 s |
| flask | 236 | 7,245 | 3,277 | 349 | 3,619 | ~11 s |

Sample resolved evidence: `login_required calls redirect`,
`create_app calls render_template`, `create_app calls Flask`,
`register calls get_db` (flask); `base64_decode calls want_bytes`,
`base64_decode calls BadData` (itsdangerous); 52 `TESTS` edges total.
Repeated builds (via `POST /relationships`) reproduce identical edge counts.

## 13. Limitations and Known Gaps

- Static-only: runtime/dynamic resolution (`getattr`, `globals()`, dynamic
  imports) is unseen and stays `UNRESOLVED`.
- No installed-package/stdlib resolution: out-of-repo imports are `EXTERNAL`
  and their members are not expanded.
- Conservative name resolution inflates `UNRESOLVED` on large/yield-heavy
  repos (flask ~49% of call edges unresolved) — by design, precision over
  recall, and exposed per edge.
- Minified/generated bundles may exceed limits or parse sparsely; ingestion
  excludes `node_modules`, `dist`, `build`.
- src/ layout aliasing is single-prefix (`src.`, `lib.`, `python.`,
  `site-packages.`); exotic multi-level layouts may still miss.
- No transitive impact analysis (Phase 4) or diff-aware resolution yet.

## 14. Deviations from Spec / Notes

- The pre-Phase-3 `relationships` scaffold table (with a `confidence`
  column) was replaced by the Phase-3 schema; a startup migration handles
  existing SQLite files.
- `RelationshipInfo` now includes `source_symbol_name`/`target_symbol_name`
  populated via left joins — fields existed in the schema but were previously
  always null; without this the frontend columns degrade to file names.
- Python 3.12 note: `sqlalchemy.orm.AsyncSession` is not exported
  (`sqlalchemy.ext.asyncio` is used).
- `@pytest.mark.network` is not registered in `pytest.ini`; the network
  tests are simply deselected (pre-existing minor gap).

## 15. How to Run + Next Steps

```bash
cd backend && .\.venv\Scripts\python -m pytest          # 143 passed
cd backend && .\.venv\Scripts\python -m uvicorn app.main:app --reload
cd frontend && npm run dev
```

1. Ingest a repo (`POST /api/repositories`), parse it, then
   `POST /api/repositories/{id}/relationships`.
2. Open the repository page → Relationship Explorer → Build Relationships
   → inspect/filter edges, expand rows for evidence.

Next steps (out of scope for Phase 3): Phase 4 investigation experience
(click-through impact), diff-aware impact analysis, and the optional AI
explanation layer.