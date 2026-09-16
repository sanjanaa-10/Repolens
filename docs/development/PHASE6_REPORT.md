# Phase 6 Report — Change Impact Simulation

**RepoLens** · Phase 6 of 10

---

## 1. Objective

Answer **"WHAT ELSE COULD BE AFFECTED by these changes?"** for a real diff —
deterministically, offline, and backed entirely by the persisted relationship
graph. Given an already-analyzed diff (`base_revision → head_revision`),
RepoLens must classify every entity the change can statically reach as
`DIRECT`, `POTENTIAL`, `TESTS`, `UNRESOLVED`, or `EXTERNAL`, and give the
developer, for each reached entity, an explainable path with per-step
evidence. The explicit boundary: impact simulation is evidence-based static
analysis, never a guarantee of runtime behavior, and never a risk score or
an authorized merge block.

> Impact simulation is deterministic static analysis. It identifies
> potentially affected code based on observed repository relationships; it
> does not guarantee runtime impact or correctness.

## 2. Scope

**In scope:**
- A `POST .../diff/{diff_id}/impact` endpoint that consumes the existing
  `Diff` / `DiffFile` / `DiffSymbol` tables (Phase 5) and the existing
  `Relationship` table (Phase 3). No git is invoked during traversal.
- Depth-bounded, cycle-safe BFS over relationship edges with parent expansion.
- Deterministic classification into `DIRECT / POTENTIAL / TESTS /
  UNRESOLVED / EXTERNAL` plus file-level change flags.
- Explainable dependency paths: every non-root reached node is backed by a
  path whose steps are `{source, relationship, target, evidence}`.
- Idempotent persistence per `(repository_id, diff_id, max_depth)` and a
  `GET .../impact/{analysis_id}` read endpoint.
- Hard caps with explicit `truncated` / `truncated_reason` instead of
  unbounded exploration.
- Frontend **Impact** page (layered focus graph + lists + "why is this
  affected?" panel) and a "Simulate impact" deep-link from the Changes page.
- Deterministic fixture-repo tests (2-commit synthetic git repo), limit
  tests, API tests, and real-repo verification on flask.

**Out of scope (explicitly deferred):** risk scores or severity numbers,
review checklists, merge blocking / CI gates, full-repository visualization,
LLM explanation of impact, collaborative or cloud features. None of these
receive static "certainty" claims; paths, evidence, and classification are
the deliverable.

## 3. Requirements Implemented

- `POST /api/repositories/{repository_id}/diff/{diff_id}/impact` →
  `201` with the full analysis; `{max_depth}` body (Pydantic `ge=1 le=6`).
  Idempotent: same (repo, diff, depth) returns the stored row.
- `GET /api/repositories/{repository_id}/impact/{analysis_id}` → `200`
  returns exactly the stored analysis (POST == GET response).
- Impact BFS starters = changed symbols (`DiffSymbol`) ∪ changed files
  (non-symbol `DiffFile` entries marked `is_file_level_change`), failures map
  to clean `404`s.
- Traversal whitelist: `CALLS`, `REFERENCES`, `EXTENDS`, `IMPLEMENTS`,
  `TESTS`, `IMPORTS`. `DEFINES` / `EXPORTS` are structural and excluded (they
  would explode the graph with siblings / not describe impact).
- Parent expansion: when a `CLASS`/`INTERFACE` node is reached, its
  container node is also added to the target set so calls to the class and to
  its methods are both picked up; files reached through `IMPORTS` are kept
  one hop without traversing the whole file's contents.
- Determinism: edges are loaded once, sorted by
  `(type_priority, evidence_file_id, evidence_start_line, id)`, and BFS/queue
  order, node insertion, and final list ordering are fixed. Re-running the
  POST and re-reading the GET produce byte-identical lists.
- UNRESOLVED scoping: only relationships sourced from a changed symbol or a
  changed file (import evidence) are surfaced, e.g. an unresolved call inside
  a changed method. EXTERNAL: only imports from changed files.
- Path reconstruction: BFS parent chain reversed into dependency-direction
  `ImpactStepInfo` steps; capped at `MAX_IMPACT_PATHS=200`; a `truncated`
  flag on the analysis when node or path caps are hit.
- Limits in `limits.py`: `MAX_IMPACT_DEPTH_DEFAULT=2`,
  `MAX_IMPACT_DEPTH_CEILING=6`, `MAX_IMPACT_NODES=1000`,
  `MAX_IMPACT_PATHS=200`, `MAX_RELATIONSHIPS_PER_REPOSITORY=100_000`.
- Frontend: new `/repo/:id/impact` route, layered focus-graph (roots/d1/d2 +
  relationship-labeled edges), class-filtered lists, depth selector,
  "why is this affected?" paths + evidence panel, deterministic-honesty note
  on every page, and a "Simulate impact" button on the Changes summary strip.

## 4. Architecture

```
POST /repositories/{id}/diff/{diffId}/impact {max_depth: 2}
   │
   ▼
impact.engine.run_impact_analysis(db, repo, diff_id, max_depth)
   │  load diff → starters (changed symbols ∪ changed files)  (Phase 5 rows)
   │  load all repo FileRecords + Symbols once (2 queries)
   │  load relationship edges once, single query, sorted deterministically:
   │      * edges with target_symbol_id NOT NULL   → resolved symbol edges
   │      * file→file edges (target_symbol_id IS NULL, target_file NOT NULL)
   │      * UNRESOLVED edges (target_symbol_id IS NULL, type not IMPORTS)
   │      * EXTERNAL edges (IMPORTS, target_file absorbed later)
   │  de-dup edges into deterministic adjacency (edge priority order)
   │
   ▼
BFS(queue of (node_id, depth, path))   ← iterative, deque, visited-set, no recursion
   │  for edges with source in target set:
   │      resolve target: symbol edge target OR file edge (import) OR its parent
   │      if depth < max_depth and not visited → enqueue, record parent chain
   │      if node cap exceeded → mark truncated, stop
   ▼
unresolved/external builder (root-scoped, evidence file + line)
   ▼
persist ImpactAnalysis / ImpactNode / ImpactPath rows
   ▼
analysis_to_info() → ImpactAnalysisInfo (deterministic sort keys)
GET /repositories/{id}/impact/{analysisId} → same JSON
```

The traversal never touches git and never calls an LLM; it consumes the
pre-built relationship table, so a large-repo run is a few SQLite reads and
a bounded BFS (flask full diff ≈ 0.04 s on a laptop).

## 5. Data Model

Three tables were added in `backend/app/models/orm.py`:

- `impact_analyses` — `repository_id`, `diff_id`, `max_depth` with a UNIQUE
  constraint `uq_impact_repo_diff_depth` (idempotency), snapshot
  `base_revision`/`head_revision`, summary counters, `truncated`,
  `truncated_reason`, `created_at`.
- `impact_nodes` — one row per reported entity: `node_type`
  (`SYMBOL|FILE|UNRESOLVED|EXTERNAL`), optional `node_id` (Symbol FK),
  `name`, `kind`, `file_path`, `file_category`, `impact_class`
  (`DIRECT|POTENTIAL|TESTS|UNRESOLVED|EXTERNAL`), `depth`, `lines`,
  `via_type`, `via_source`, `evidence_file`, `evidence_line`,
  `is_test`, `is_file_level_change`, `change_type`.
- `impact_paths` — `root`, `target`, `target_node_type`, `target_node_id`,
  `depth`, and a JSON `steps` array
  (`[{source, relationship, target, evidence}, ...]`).

`ImpactNodeInfo`, `ImpactStepInfo`, `ImpactPathInfo`, `ImpactSummary`, and
`ImpactAnalysisInfo` are the API-facing Pydantic schemas in `schemas.py`.
No column or table from Phases 0–5 was changed by Phase 6.

## 6. Classification and BFS Traversal

**Classification is derived from the starters and the edges actually traversed:**

| Class | Meaning |
| ----- | ------- |
| `DIRECT` | a changed symbol or a changed file (multi-edge VIP parity: symbols listed before files, each group sorted by `(file_path, name)`). |
| `POTENTIAL` | a symbol or file reached from a changed entity via a resolved relationship at depth 1..N. |
| `TESTS` | any reached node whose file is a test file (path heuristic), listed in the tests group. |
| `UNRESOLVED` | observed but statically unresolved dependency of a changed symbol/file — always with evidence `"caller calls name"` at `file:line`; never guessed. |
| `EXTERNAL` | resolved out-of-repository import of a changed file — module name + evidence line. |

**Traversal rules:**

- Start with every `DiffSymbol` (root, depth 0) plus each changed file with no
  symbol mapping (`is_file_level_change=true`).
- A *changed* entity's unresolved / external edges do not traverse; they are
  counted and reported in their own groups.
- For each enqueued node, resolved edges whose `source_symbol_id ∈ target set`
  or whose `source_type='file'` matches a reached file are expanded. The
  resolved target of an edge is its `target_symbol_id`; file-level `IMPORTS`
  edges resolve to the importing file (a reached file).
- When the reached target is a `CLASS`/`INTERFACE` node, its container node is
  included in the target set (parent expansion) so that callers of the class
  and callers of any of its methods are both potentially affected.
- Reached *files* are not expanded through every symbol they contain; only
  file-level edges (`IMPORTS`) traverse from them.
- BFS is iterative (deque, visited set), depth-capped, node-capped, and
  path-capped. Deterministic edge sort guarantees a stable output regardless
  of query planner or row order.

## 7. Explainable Paths and Evidence

Every reached `POTENTIAL` / `TESTS` node has at least one recorded path from a
changed root. Steps are stored in **dependency direction** (reader-friendly),
e.g. for `RequestHandler.handle` reached at depth 2 through `handle_sync`:

```
handle_sync CALLS AuthService            evidence: auth/controller.py:4
RequestHandler.handle CALLS handle_sync  evidence: auth/request.py:5
```

A step is `{source, relationship, target, evidence}` where `evidence` is
`<file_path>:<line>` of the source's call/usage site — the same evidence
carried by the underlying Phase 3 `Relationship` row. `UNRESOLVED` and
`EXTERNAL` items carry their evidence (file + line + the recorded name) even
though they have no path. Paths are capped at `MAX_IMPACT_PATHS`; if the cap
or the node cap is hit, `truncated=true` and `truncated_reason` describe why.

## 8. API

| Method | Endpoint | Request | Response |
| ------ | -------- | ------- | -------- |
| POST | `/api/repositories/{repository_id}/diff/{diff_id}/impact` | `{"max_depth": 2}` | `201` `ImpactAnalysisInfo` (idempotent per repo+diff+depth) |
| GET | `/api/repositories/{id}/impact/{analysis_id}` | — | `200` stored `ImpactAnalysisInfo` |

Errors: `404` for unknown repository / diff / analysis; `422` for
`max_depth < 1` or `> 6` (both Pydantic validation and an explicit ceiling
check). The analysis id is a plain integer primary key; re-POSTing the same
parameters returns the same analysis id and identical body.

## 9. Frontend

- New **Impact** nav entry and `/repo/:id/impact` route (`ImpactPage`).
- `ImpactView` reuses the Phase 5 revision inputs (base/head + presets) and
  additionally exposes a depth selector (1–6, default 2).
- On run, it creates/creates-or-reuses the diff, POSTs the impact, and
  renders: a summary strip (changed symbols/files, direct, potential, tests,
  unresolved, external, truncation flag), a layered focus graph (roots →
  depth 1 → depth 2, relationship-labeled edges, per-class node coloring,
  file-toggle filter), per-class lists, and a **why panel** showing the
  selected entity's classification, evidence, and full dependency path.
- The **Changes** page summary strip now has a "Simulate impact" button that
  deep-links the current diff into the Impact page
  (`/repo/:id/impact?diff=&base=&head=`).
- Every simulation page repeats the honesty line: *"It does not guarantee
  runtime impact or correctness."*

## 10. Security

- No new execution surface: traversal is pure SQLAlchemy reads + in-memory
  BFS. Git is never invoked by the impact path (the Phase 5 diff engine
  already gates git interaction).
- All impact identifiers are validated integers; unknown ids map to 404.
- Evidence strings originate from the relationship table (itself built from
  symbol/line data) and are rendered as plain text, never interpolated into
  shell commands or HTML without escaping.
- Path/name/count caps bound worst-case work on any repository
  (`MAX_IMPACT_NODES`, `MAX_IMPACT_PATHS`,
  `MAX_RELATIONSHIPS_PER_REPOSITORY`).
- No secrets, credentials, or user content are logged by the impact engine.

## 11. Testing

`backend/tests/test_impact_engine.py` (deterministic, offline, no network):

- A dedicated module-scoped `impact_git` fixture (synthetic two-commit git
  repo with a clean signal: `validate_token` body swap that intentionally
  flags only the method + its class) and an `env` fixture that seeds, parses,
  and builds relationships via the real `RelationshipEngine`.
- `test_impact_full_scenario` — exact classification: 2 changed symbols, 2
  changed files, `direct=4`, `potential=6` (depth-1 callers/files/test, depth-2
  class + method + file), `tests=1`, `unresolved≥1` with evidence,
  `external=1` (`base64 import *`), verified path steps + evidence.
- `test_impact_depth_1_limits_traversal` / `test_impact_depth_2_reaches_second_level`
  — depth semantics.
- `test_impact_deterministic_recompute` — delete stored rows, recompute,
  JSON-equal.
- `test_impact_node_limit_truncation` — monkeypatches node cap; asserts
  `truncated=true`, `truncated_reason='impact node limit reached'`, zero
  potential beyond cap.
- `test_impact_multiple_changed_symbols` — second diff editing
  `legacy_login` + `create_user`; asserts both changed, direct-only.
- `test_impact_api_post_get_and_repeat`, `test_impact_api_validation_404s`,
  `test_impact_api_repo_isolation`, `test_impact_stored_rows_render_identical_to_get`
  — API contract, idempotency, validation, isolation.
- `test_impact_ordering_is_deterministic` — symbols-before-files and stable
  per-group ordering.

Full backend suite: **187 passed, 2 skipped, 4 deselected** (11 new tests;
no regressions). Frontend `npm run build` (tsc -b) is green. The run also
uncovered and fixed a real Phase 5 line-numbering bug (see §14).

## 12. Real Repository Metrics

Run against the productive SQLite DB, flask `514fc6b → d73fa1c` (8 changed
files) at depth 2 (`truncated=false`), and the synthetic fixture:

| Metric | flask (real) | fixture (synthetic) |
| ------ | ------------ | ------------------- |
| Changed symbols | 19 | 2 |
| Changed files | 8 | 2 |
| Direct (symbols + files) | 27 | 4 |
| Potential | 97 | 6 |
| Tests to review | 5 | 1 |
| Unresolved | 215 | 2 |
| External | 7 | 1 |
| File-level changes (no symbol mapping) | 2 | 1 |
| Nodes total | 351 | 13 |
| Paths recorded | 102 | 5 |
| Runtime | ~0.04 s | <0.1 s |

Repeated POSTs return the same analysis; the GET of a stored id returns the
identical body. The flask example demonstrates a bounded, evidence-backed
rip: a diff touching `Flask` internals and `Scaffold` expands to 97
potentially affected entities (classes, methods, and files) with dependency
paths like `Flask.wsgi_app CALLS Flask` and depth-2 reachability across
`src/flask/sansio/app.py`, `testing`, `sessions`, and `templating`.

## 13. Limitations and Known Gaps

- **Static coupling, not runtime behavior.** Reached entities are potentially
  affected; the engine makes no breakage/correctness claim.
- **Precision over recall in resolution.** Calls that only resolve at runtime,
  `getattr`/dynamic names, monkey-patching, and C-extension members stay
  UNRESOLVED (counted with evidence) and are never expanded.
- **A changed method also flags its class** (diff lines are mapped onto
  symbol ranges), so `POTENTIAL` includes class-level callers — intentional
  but broader than method-exact.
- **Same-line unresolved calls share the Phase 3 evidence slot** (one
  relationship row per source+target+type+file+line), so only the first is
  named individually at that line.
- **Depth cap and truncation.** Deeper transitive ripples beyond
  `max_depth=2` (default), more than `MAX_IMPACT_NODES`/`MAX_IMPACT_PATHS`,
  or repos with >100k relationships are explicitly truncated, not silently
  page-bounded.
- **No review/risk layer.** Prioritizing, checklist assembly, or blocking
  merges is a later phase and cannot be automated honestly from static
  analysis.
- **Parent-expansion reachability** means reference-resolver edges from a
  reached class to its methods are traversed only through the class container;
  some method-to-method references may be reported at the class level.

## 14. Deviations, Bugs Found, and Notes

- **Bug found (Phase 5 engine) and fixed: `_parse_hunks` off-by-one.** The
  unified-diff parser computed a changed line's position as
  `new_start + len(new_lines) + len(added_lines)`, mis-numbering the second
  and later changed lines in a multi-change hunk (e.g. two edited lines
  reported as old/new 9 and 13 instead of 9 and 12). The impact fixture
  (editing two functions in one commit) exposed that `create_user` was never
  flagged. The fix replaces the accumulated-length arithmetic with running
  `old_next`/`new_next` counters; full Phase 5 diff suite still passes and
  the Phase 6 multi-symbol test now sees both edited functions.
- **Fixture shape had to match real resolver limitations.** The user's
  literal fixture shape (`self.service.validate_token`) does not statically
  resolve (the call resolver handles `self.method`, import-bound receivers,
  and same-file receivers, not arbitrary attribute receivers), so the
  synthetic repo instead uses a module-level `handle_sync` calling
  `AuthService().validate_token(...)` plus file-level imports to produce
  RESOLVED CALLS edges with deterministic evidence.
- **Same-line deduplication forced normalize_token onto its own line.** The
  Phase 3 relationship batch keeps one row per (source, target, type,
  evidence file, evidence line); putting `normalize_token(token)` on its own
  line preserves it as a distinct UNRESOLVED dependency instead of being
  collapsed with the `base64.b64encode(...)` method call on the same line.
- **API shape aligned with the phase spec.** The simulated-impact POST lives
  under the diff (`.../diff/{diff_id}/impact`) and analysis ids are integers
  under `.../impact/{analysis_id}` — matching the Phase 6 API contract and
  the stored-data GET.
- **The impact `env` fixture is independent of the Phase 5 diff fixture**
  (separate module-scoped fixture) so phase suites stay decoupled.
- **Old Phase 4-era placeholder impact/review schemas and client stubs were
  replaced** by the Phase 6 API types/endpoints; no consumer existed.

## 15. Files Changed, Run Instructions and Next Steps

**Backend (new)**
- `backend/app/analysis/impact/__init__.py` — exports
- `backend/app/analysis/impact/limits.py` — caps and depth constants
- `backend/app/analysis/impact/policy.py` — traversal whitelist / priorities
- `backend/app/analysis/impact/engine.py` — BFS engine, path reconstruction,
  persistence, `run_impact_analysis`
- `backend/app/analysis/impact/service.py` — `analysis_to_info`,
  `get_impact_analysis`, `ImpactNotFoundError`
- `backend/tests/test_impact_engine.py` — fixture + 11 tests

**Backend (changed)**
- `backend/app/models/orm.py` — `ImpactAnalysis`, `ImpactNode`, `ImpactPath`
- `backend/app/models/schemas.py` — Phase 6 schemas (replaced unused
  placeholders)
- `backend/app/api/routes.py` — impact POST/GET endpoints
- `backend/app/analysis/diff/engine.py` — `_parse_hunks` line-numbering fix

**Frontend (new)**
- `frontend/src/pages/ImpactPage.tsx`
- `frontend/src/components/analysis/ImpactView.tsx`

**Frontend (changed)**
- `frontend/src/components/layout/AppShell.tsx` — Impact nav entry
- `frontend/src/App.tsx` — `/repo/:id/impact` route
- `frontend/src/components/analysis/ChangeView.tsx` — "Simulate impact" link
- `frontend/src/types/index.ts` — Phase 6 API types
- `frontend/src/api/client.ts` — `simulateImpact`, `getImpactAnalysis`

**Docs**
- `README.md` — Phase 6 status, feature bullet, API rows, limitations section,
  verification metrics, roadmap
- `PHASE6_REPORT.md` — this report

**How to run**
```bash
cd backend
.\.venv\Scripts\python -m uvicorn app.main:app --reload --port 8000

cd frontend
npm run dev
```
Then: open a repository → **Changes** (pick revisions) → **Simulate impact**,
or open the **Impact** page directly and choose revisions + depth.

**Verification**
```bash
cd backend
.\.venv\Scripts\python -m pytest tests/test_impact_engine.py -q
.\.venv\Scripts\python -m pytest tests -q          # full suite: 187 passed
cd frontend && npm run build
```

**Next steps (Phase 7+)**
- Change review checklist surfaced from impacted tests / unresolved surface.
- Optional AI explanation of paths (evidence-scoped only).
- Cross-repo boundary expansion (vendor/packages) and configurable depth-on-demand.
- Frontend polish: difference highlighting between depth runs, larger-repo graph
  grouping, and workspace presets.